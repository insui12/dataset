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


def _to_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _to_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


class RedmineAdapter(TrackerAdapter):
    family_slug = "redmine"
    supported_protocols = (ProtocolType.REST,)

    def _api_base(self, instance: TrackerInstance) -> str:
        return (instance.api_base_url or instance.base_url).rstrip("/")

    def _entry_key(self, entry: RegistryEntry) -> str | None:
        return entry.tracker_api_key or entry.tracker_native_id or entry.name

    def _auth_headers(self) -> dict[str, str]:
        token = self.config.redmine_token
        return {"X-Redmine-API-Key": token} if token else {}

    async def probe(self, family: TrackerFamily, instance: TrackerInstance, entry: RegistryEntry | None = None) -> ProbeResult:
        base = self._api_base(instance)
        url = f"{base}/projects.json"
        try:
            response = await self.client.get(url, headers=self._auth_headers())
        except httpx.RequestError as exc:
            return ProbeResult(
                family_slug=self.family_slug,
                instance=instance.canonical_name,
                protocol=ProtocolType.REST,
                supported=False,
                blocked=True,
                note=f"request_error:{exc}",
            )

        if response.status_code in {401, 403}:
            return ProbeResult(
                family_slug=self.family_slug,
                instance=instance.canonical_name,
                protocol=ProtocolType.REST,
                supported=True,
                blocked=True,
                auth_required=response.status_code == 401,
                count_supported=False,
                pagination="offset",
                raw_response_status=response.status_code,
                details={"status_code": response.status_code},
                note="auth required",
            )

        if response.status_code >= 400:
            return ProbeResult(
                family_slug=self.family_slug,
                instance=instance.canonical_name,
                protocol=ProtocolType.REST,
                supported=False,
                blocked=True,
                pagination="offset",
                raw_response_status=response.status_code,
                details={"status_code": response.status_code},
                note=f"http_error:{response.status_code}",
            )

        return ProbeResult(
            family_slug=self.family_slug,
            instance=instance.canonical_name,
            protocol=ProtocolType.REST,
            supported=True,
            blocked=False,
            count_supported=True,
            pagination="offset",
            raw_response_status=response.status_code,
            details={"status_code": response.status_code},
            note="redmine api reachable",
        )

    async def discover(self, family: TrackerFamily, instance: TrackerInstance) -> DiscoveryPlan:
        return DiscoveryPlan(
            discovered_entries=[],
            count_plan=CountPlan(
                mode=CountMode.APPROXIMATE,
                value=None,
                method="redmine-discover-manifest",
                signature=f"redmine-{instance.canonical_name}-projects",
            ),
            errors=[],
        )

    async def build_count_plan(self, entry: RegistryEntry) -> CountPlan:
        project = self._entry_key(entry)
        if not project:
            return CountPlan(
                mode=CountMode.APPROXIMATE,
                value=None,
                method="redmine-count-missing-project",
                signature=f"{entry.id}:missing-project",
            )

        base = self._api_base(entry.instance)
        endpoint = f"{base}/issues.json"
        params = {
            "project_id": project,
            "limit": 1,
            "offset": 0,
            "status_id": "closed",
        }
        try:
            response = await self.client.get(endpoint, params=params, headers=self._auth_headers())
        except httpx.RequestError as exc:
            return CountPlan(
                mode=CountMode.APPROXIMATE,
                value=None,
                method="redmine-count-request-error",
                signature=f"{entry.id}:count-request-error",
                metadata={"error": str(exc)},
            )

        if response.status_code >= 400:
            return CountPlan(
                mode=CountMode.APPROXIMATE,
                value=None,
                method="redmine-count-http-error",
                signature=f"{entry.id}:count-http-error",
                metadata={"status_code": response.status_code},
            )

        payload = response.json()
        total = payload.get("total_count") if isinstance(payload, dict) else None
        if isinstance(total, int):
            return CountPlan(
                mode=CountMode.EXACT,
                value=total,
                method="redmine-issues-total_count",
                signature=f"{entry.id}:issues-count",
                metadata={"project": project},
            )

        return CountPlan(
            mode=CountMode.APPROXIMATE,
            value=None,
            method="redmine-count-approximate",
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
        project = self._entry_key(entry)
        if not project:
            return IssueListPage(
                issues=[],
                error="tracker id missing",
                status_code=None,
                closed_filter_applied=False,
            )

        base = self._api_base(entry.instance)
        endpoint = f"{base}/issues.json"
        try:
            offset = int(cursor) if cursor is not None else 0
        except Exception:
            offset = 0

        per_page = max(1, min(int(page_size), 100))
        params: dict[str, Any] = {
            "project_id": project,
            "limit": per_page,
            "offset": max(0, offset),
            "sort": "updated_on:desc",
        }
        if mode == "closed":
            params["status_id"] = "closed"
        else:
            params["status_id"] = "*"

        try:
            response = await self.client.get(endpoint, params=params, headers=self._auth_headers())
        except httpx.RequestError as exc:
            return IssueListPage(
                issues=[],
                error=f"request_error:{exc}",
                request_url=endpoint,
                request_params=params,
                request_headers=self._auth_headers(),
            )

        if response.status_code >= 400:
            return IssueListPage(
                issues=[],
                error=f"http_error:{response.status_code}",
                status_code=response.status_code,
                request_url=endpoint,
                request_params=params,
                request_headers=self._auth_headers(),
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
                request_headers=self._auth_headers(),
                headers=dict(response.headers),
            )

        items = payload.get("issues")
        if not isinstance(items, list):
            return IssueListPage(
                issues=[],
                error="unexpected_payload_missing_issues",
                status_code=response.status_code,
                request_url=endpoint,
                request_params=params,
                request_headers=self._auth_headers(),
                headers=dict(response.headers),
            )

        records: list[IssueRecord] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            issue_id = item.get("id")
            if issue_id is None:
                continue
            issue_id = str(issue_id)
            subject = item.get("subject") or ""
            description = item.get("description") or ""
            status = item.get("status") if isinstance(item.get("status"), dict) else item.get("status")
            created_at = _to_dt(item.get("created_on"))
            updated_at = _to_dt(item.get("updated_on"))
            closed_at = _to_dt(item.get("closed_on"))
            reporter = item.get("author")
            assignee = item.get("assigned_to")
            status_name = status.get("name") if isinstance(status, dict) else status
            journal = item.get("journals") if isinstance(item.get("journals"), list) else []

            records.append(
                IssueRecord(
                    tracker_issue_id=issue_id,
                    tracker_issue_key=f"{project}#{issue_id}",
                    title=_to_text(subject) or "",
                    body_raw=_to_text(description),
                    body_plaintext=_to_text(description),
                    issue_url=item.get("url") or "",
                    api_url=item.get("url") or "",
                    issue_type_raw="issue",
                    state_raw=_to_text(status_name),
                    resolution_raw=_to_text(status.get("closed") if isinstance(status, dict) else None),
                    close_reason_raw=_to_text(item.get("notes") if isinstance(item.get("notes"), str) else None),
                    created_at_tracker=created_at,
                    updated_at_tracker=updated_at,
                    closed_at=closed_at,
                    reporter_raw=_to_text(reporter.get("name") if isinstance(reporter, dict) else reporter),
                    assignee_raw=_to_text(assignee.get("name") if isinstance(assignee, dict) else assignee),
                    is_pull_request=False,
                    is_private_restricted=False,
                    labels=[_to_text(j.get("user", {}).get("name")) for j in journal if isinstance(j, dict) and j.get("user")],
                    raw_payload=item,
                )
            )

        try:
            limit = int(sample_limit) if sample_limit is not None else None
        except Exception:
            limit = None
        if limit is not None and len(records) > limit:
            records = records[:limit]
            next_cursor = None
        else:
            total = payload.get("total_count")
            if isinstance(total, int) and total > offset + len(records):
                next_cursor = str(offset + per_page)
            else:
                next_cursor = str(offset + len(records)) if len(records) >= per_page else None

        return IssueListPage(
            issues=records,
            next_cursor=next_cursor,
            next_page=offset + per_page if next_cursor is not None else None,
            next_params={"offset": offset + per_page},
            request_url=endpoint,
            request_params=params,
            request_headers=self._auth_headers(),
            status_code=response.status_code,
            request_body=payload,
            headers=dict(response.headers),
            closed_filter_applied=(mode == "closed"),
            closed_filter_mode="status_id=closed",
        )
