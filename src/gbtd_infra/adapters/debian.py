from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from gbtd_infra.adapters.base import (
    CountMode,
    CountPlan,
    DiscoveryPlan,
    IssueListPage,
    IssueRecord,
    ProbeResult,
    TrackerAdapter,
)
from gbtd_infra.models import ProtocolType, RegistryEntry, TrackerFamily, TrackerInstance


def _to_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _to_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


class DebianBTSAdapter(TrackerAdapter):
    family_slug = "debian_bts"
    supported_protocols = (ProtocolType.SOAP,)

    async def probe(self, family: TrackerFamily, instance: TrackerInstance, entry: RegistryEntry | None = None) -> ProbeResult:
        # Debian BTS historically exposes SOAP/XML-ish endpoints; this remains versioned and explicit.
        base = (instance.api_base_url or instance.base_url).rstrip("/")
        url = f"{base}/cgi-bin/pkgreport.cgi"
        try:
            response = await self.client.get(url)
            if response.status_code == 200:
                return ProbeResult(
                    family_slug=self.family_slug,
                    instance=instance.canonical_name,
                    protocol=ProtocolType.SOAP,
                    supported=True,
                    blocked=False,
                    count_supported=True,
                    pagination="offset",
                    raw_response_status=response.status_code,
                    details={"status_code": response.status_code},
                    note="Debian BTS endpoint reachable",
                )
            return ProbeResult(
                family_slug=self.family_slug,
                instance=instance.canonical_name,
                protocol=ProtocolType.SOAP,
                supported=False,
                blocked=True,
                count_supported=False,
                raw_response_status=response.status_code,
                details={"status_code": response.status_code},
                note="Debian BTS endpoint not ready",
            )
        except Exception:
            return ProbeResult(
                family_slug=self.family_slug,
                instance=instance.canonical_name,
                protocol=ProtocolType.SOAP,
                supported=False,
                blocked=True,
                note="Debian BTS official API endpoint blocked/unreachable",
            )

    async def discover(self, family: TrackerFamily, instance: TrackerInstance) -> DiscoveryPlan:
        return DiscoveryPlan(
            discovered_entries=[],
            count_plan=CountPlan(
                mode=CountMode.ENUMERATED,
                value=None,
                method="legacy_enumeration",
                signature="debian-bts-packages",
            ),
            errors=["Debian BTS discovery is not auto-expanded in this phase"],
        )

    async def build_count_plan(self, entry: RegistryEntry) -> CountPlan:
        # Debian BTS does not expose a strict canonical closed-count endpoint.
        # We keep an approximate policy by using pkgreport endpoint and metadata-only header if available.
        base = (entry.instance.api_base_url or entry.instance.base_url).rstrip("/")
        project = entry.tracker_native_id or entry.tracker_api_key or entry.name
        if not project:
            return CountPlan(
                mode=CountMode.APPROXIMATE,
                value=None,
                method="debian-bts-count-missing-key",
                signature=f"{entry.id}:missing-key",
            )

        url = f"{base}/cgi-bin/pkgreport.cgi"
        params = {"src": project, "archive": 1, "status": "resolved", "format": "json", "count": 1}
        try:
            response = await self.client.get(url, params=params)
        except httpx.RequestError as exc:
            return CountPlan(
                mode=CountMode.APPROXIMATE,
                value=None,
                method="debian-bts-count-request-error",
                signature=f"{entry.id}:count-request-error",
                metadata={"error": str(exc)},
            )

        if response.status_code >= 400:
            return CountPlan(
                mode=CountMode.APPROXIMATE,
                value=None,
                method="debian-bts-count-http-error",
                signature=f"{entry.id}:count-http-error",
                metadata={"status_code": response.status_code},
            )

        payload = response.json()
        total = payload.get("count") if isinstance(payload, dict) else None
        if isinstance(total, int):
            return CountPlan(
                mode=CountMode.EXACT,
                value=total,
                method="debian-bts-count",
                signature=f"{entry.id}:count",
                metadata={"project": project},
            )

        return CountPlan(
            mode=CountMode.APPROXIMATE,
            value=None,
            method="debian-bts-count-approximate",
            signature=f"{entry.id}:count-unknown",
            metadata={"project": project},
        )

    async def list_issues(
        self,
        entry: RegistryEntry,
        *,
        cursor: str | int | None = None,
        page_size: int = 100,
        mode: str = "closed",
        sample_limit: int | None = None,
    ) -> IssueListPage:
        base = (entry.instance.api_base_url or entry.instance.base_url).rstrip("/")
        project = entry.tracker_native_id or entry.tracker_api_key or entry.name
        if not project:
            return IssueListPage(
                issues=[],
                error="tracker id missing",
                closed_filter_applied=False,
            )

        try:
            start = int(cursor) if cursor is not None else 0
        except Exception:
            start = 0
        per_page = max(1, min(int(page_size), 100))

        params = {
            "src": project,
            "archive": 1,
            "status": "resolved" if mode == "closed" else "",
            "format": "json",
            "offset": start,
            "limit": per_page,
        }

        endpoint = f"{base}/cgi-bin/pkgreport.cgi"
        try:
            response = await self.client.get(endpoint, params=params)
        except httpx.RequestError as exc:
            return IssueListPage(
                issues=[],
                error=f"request_error:{exc}",
                request_url=endpoint,
                request_params=params,
                request_headers={},
            )

        if response.status_code >= 400:
            return IssueListPage(
                issues=[],
                error=f"http_error:{response.status_code}",
                status_code=response.status_code,
                request_url=endpoint,
                request_params=params,
                request_headers={},
                headers=dict(response.headers),
            )

        payload = response.json()
        if not isinstance(payload, dict):
            return IssueListPage(
                issues=[],
                error="unexpected_payload_type",
                status_code=response.status_code,
                request_url=endpoint,
                request_params=params,
                request_headers={},
                headers=dict(response.headers),
            )

        items = payload.get("report") or payload.get("bugs") or []
        if not isinstance(items, list):
            return IssueListPage(
                issues=[],
                error="unexpected_payload_missing_list",
                status_code=response.status_code,
                request_url=endpoint,
                request_params=params,
                request_headers={},
                headers=dict(response.headers),
            )

        records: list[IssueRecord] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            issue_num = item.get("bug_num") or item.get("id")
            if issue_num is None:
                continue
            issue_id = str(issue_num)
            reporter = item.get("submitter")
            assignee = item.get("owner")
            description = item.get("summary") or item.get("subject") or ""
            created_at = _to_dt(item.get("date") or item.get("created"))
            updated_at = _to_dt(item.get("last_modified"))
            closed_at = _to_dt(item.get("done") or item.get("done_date"))

            records.append(
                IssueRecord(
                    tracker_issue_id=issue_id,
                    tracker_issue_key=f"{project}#{issue_id}",
                    title=_to_text(item.get("subject")) or "",
                    body_raw=_to_text(description),
                    body_plaintext=_to_text(description),
                    issue_url=f"{base}/cgi-bin/bugreport.cgi?bug={issue_id}",
                    api_url=f"{base}/cgi-bin/bugreport.cgi?bug={issue_id}",
                    issue_type_raw="bug",
                    state_raw=_to_text(item.get("status")),
                    resolution_raw=_to_text(item.get("fixed_version")),
                    close_reason_raw=_to_text(item.get("close_status") or item.get("archived")),
                    created_at_tracker=created_at,
                    updated_at_tracker=updated_at,
                    closed_at=closed_at,
                    reporter_raw=_to_text(reporter),
                    assignee_raw=_to_text(assignee),
                    is_pull_request=False,
                    is_private_restricted=False,
                    labels=[],
                    raw_payload=item,
                )
            )

        if sample_limit is not None:
            try:
                limit = int(sample_limit)
            except Exception:
                limit = None
            if limit is not None and len(records) > limit:
                records = records[:limit]
                next_cursor = None
            else:
                next_cursor = str(start + len(records)) if len(records) >= per_page else None
        else:
            next_cursor = str(start + len(records)) if len(records) >= per_page else None

        return IssueListPage(
            issues=records,
            next_cursor=next_cursor,
            next_page=start + per_page if next_cursor is not None else None,
            next_params={"offset": start + per_page},
            request_url=endpoint,
            request_params=params,
            request_headers={},
            status_code=response.status_code,
            request_body=payload,
            headers=dict(response.headers),
            closed_filter_applied=(mode == "closed"),
            closed_filter_mode="status resolved",
        )
