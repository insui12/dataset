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


class LaunchpadAdapter(TrackerAdapter):
    family_slug = "launchpad"
    supported_protocols = (ProtocolType.REST,)

    def _api_base(self, instance: TrackerInstance) -> str:
        return (instance.api_base_url or instance.base_url).rstrip("/")

    def _entry_key(self, entry: RegistryEntry) -> str | None:
        return entry.tracker_api_key or entry.tracker_native_id or entry.name

    def _auth_headers(self) -> dict[str, str]:
        token = self.config.launchpad_token
        return {"Authorization": f"Bearer {token}"} if token else {}

    async def probe(
        self,
        family: TrackerFamily,
        instance: TrackerInstance,
        entry: RegistryEntry | None = None,
    ) -> ProbeResult:
        base = self._api_base(instance)
        url = f"{base}/1.0/"
        try:
            response = await self.client.get(url, headers=self._auth_headers())
        except httpx.RequestError as exc:
            return ProbeResult(
                family_slug=self.family_slug,
                instance=instance.canonical_name,
                protocol=ProtocolType.REST,
                supported=False,
                blocked=True,
                note=f"official Launchpad API request error: {exc}",
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
                note="auth required or restricted",
                details={"status_code": response.status_code},
            )

        if response.status_code == 404:
            return ProbeResult(
                family_slug=self.family_slug,
                instance=instance.canonical_name,
                protocol=ProtocolType.REST,
                supported=False,
                blocked=True,
                note="api root not found",
                raw_response_status=response.status_code,
                details={"status_code": response.status_code},
            )

        if response.status_code >= 400:
            return ProbeResult(
                family_slug=self.family_slug,
                instance=instance.canonical_name,
                protocol=ProtocolType.REST,
                supported=False,
                blocked=True,
                note=f"http {response.status_code}",
                raw_response_status=response.status_code,
                details={"status_code": response.status_code},
            )

        return ProbeResult(
            family_slug=self.family_slug,
            instance=instance.canonical_name,
            protocol=ProtocolType.REST,
            supported=True,
            count_supported=True,
            pagination="offset",
            raw_response_status=response.status_code,
            note="launchpad api reachable",
            details={"status_code": response.status_code},
        )

    async def discover(self, family: TrackerFamily, instance: TrackerInstance) -> DiscoveryPlan:
        base = self._api_base(instance)
        url = f"{base}/1.0/projects"
        try:
            response = await self.client.get(url, headers=self._auth_headers())
        except httpx.RequestError as exc:
            return DiscoveryPlan(
                discovered_entries=[],
                count_plan=CountPlan(
                    mode=CountMode.APPROXIMATE,
                    value=None,
                    method="launchpad-projects-request-error",
                    signature=f"launchpad:{instance.canonical_name}:discover-error",
                    metadata={"error": str(exc)},
                ),
            )

        if response.status_code >= 400:
            return DiscoveryPlan(
                discovered_entries=[],
                count_plan=CountPlan(
                    mode=CountMode.APPROXIMATE,
                    value=None,
                    method="launchpad-projects-http-error",
                    signature=f"launchpad:{instance.canonical_name}:discover-http",
                    metadata={"status_code": response.status_code},
                ),
                errors=[f"HTTP {response.status_code}"],
            )

        payload = response.json()
        if not isinstance(payload, dict):
            return DiscoveryPlan(
                discovered_entries=[],
                count_plan=CountPlan(
                    mode=CountMode.APPROXIMATE,
                    value=None,
                    method="launchpad-projects-unknown-format",
                    signature=f"launchpad:{instance.canonical_name}:discover-format",
                    metadata={"payload_type": type(payload).__name__},
                ),
                errors=["unexpected payload format"],
            )

        entries = []
        projects = payload.get("entries", [])
        if isinstance(projects, list):
            for item in projects:
                if not isinstance(item, dict):
                    continue
                name = item.get("name") or item.get("self_link")
                if name:
                    entries.append(
                        {
                            "kind": "project",
                            "name": str(name),
                            "tracker_id": str(name),
                            "note": "launchpad-project-listing",
                        }
                    )

        return DiscoveryPlan(
            discovered_entries=entries,
            count_plan=CountPlan(
                mode=CountMode.ENUMERATED,
                value=len(entries),
                method="launchpad-projects",
                signature=f"launchpad:{instance.canonical_name}:projects",
                metadata={"entries": len(entries)},
            ),
            errors=[],
        )

    async def build_count_plan(self, entry: RegistryEntry) -> CountPlan:
        base = self._api_base(entry.instance)
        project = self._entry_key(entry)
        if not project:
            return CountPlan(
                mode=CountMode.APPROXIMATE,
                value=None,
                method="launchpad-count-missing-key",
                signature=f"{entry.id}:missing-key",
            )

        endpoint = f"{base}/1.0/{project}/bugtasks"
        params = {"ws.size": 0}
        try:
            response = await self.client.get(endpoint, params=params, headers=self._auth_headers())
        except httpx.RequestError as exc:
            return CountPlan(
                mode=CountMode.APPROXIMATE,
                value=None,
                method="launchpad-count-request-error",
                signature=f"{entry.id}:count-request-error",
                metadata={"error": str(exc)},
            )

        if response.status_code >= 400:
            return CountPlan(
                mode=CountMode.APPROXIMATE,
                value=None,
                method="launchpad-count-http-error",
                signature=f"{entry.id}:count-http-error",
                metadata={"status_code": response.status_code},
            )

        payload = response.json()
        total = None
        if isinstance(payload, dict):
            total = payload.get("total_size")
            if total is None:
                total = payload.get("total_size_linked")
        if isinstance(total, int):
            return CountPlan(
                mode=CountMode.EXACT,
                value=total,
                method="launchpad-bugtasks-total_size",
                signature=f"{entry.id}:bugtask-total",
                metadata={"endpoint": endpoint},
            )

        return CountPlan(
            mode=CountMode.APPROXIMATE,
            value=None,
            method="launchpad-count-approximate",
            signature=f"{entry.id}:bugtask-count-unknown",
            metadata={"endpoint": endpoint},
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
        per_page = max(1, min(int(page_size), 200))
        try:
            start = int(cursor) if cursor is not None else 0
        except Exception:
            start = 0

        endpoint = f"{base}/1.0/{project}/bugtasks"
        params: dict[str, Any] = {
            "ws.start": start,
            "ws.size": per_page,
        }
        if mode == "closed":
            params["status"] = "Fix Released"

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

        entries = payload.get("entries")
        if not isinstance(entries, list):
            return IssueListPage(
                issues=[],
                error="unexpected_payload_missing_entries",
                status_code=response.status_code,
                request_url=endpoint,
                request_params=params,
                request_headers=self._auth_headers(),
                headers=dict(response.headers),
            )

        records: list[IssueRecord] = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            bug_id = item.get("id")
            if bug_id is None:
                continue
            bug_id = str(bug_id)
            task = item.get("bug") if isinstance(item.get("bug"), dict) else item
            status = item.get("status")
            date_created = _to_dt(item.get("date_created"))
            date_last_updated = _to_dt(item.get("date_last_updated"))
            date_closed = _to_dt(item.get("date_closed")) if status else None
            assignee = item.get("assignee")
            assignee_raw = assignee.get("name") if isinstance(assignee, dict) else None
            title = item.get("title") or item.get("name") or ""
            description = (
                task.get("description") if isinstance(task, dict) else ""
            )
            tracker_key = item.get("bug") and item["bug"].get("web_link") if isinstance(item.get("bug"), dict) else None

            records.append(
                IssueRecord(
                    tracker_issue_id=bug_id,
                    tracker_issue_key=_to_text(tracker_key) or bug_id,
                    title=title,
                    body_raw=_to_text(description),
                    body_plaintext=_to_text(description),
                    issue_url=(item.get("web_link") or task.get("web_link") or "").strip(),
                    api_url=(item.get("web_link") or "").strip(),
                    issue_type_raw="bug",
                    state_raw=_to_text(status),
                    resolution_raw=_to_text(item.get("importance")) if item.get("importance") else None,
                    close_reason_raw=_to_text(item.get("status")),
                    created_at_tracker=date_created,
                    updated_at_tracker=date_last_updated,
                    closed_at=date_closed,
                    reporter_raw=task.get("owner", {}).get("name") if isinstance(task, dict) else None,
                    assignee_raw=_to_text(assignee_raw),
                    is_pull_request=False,
                    is_private_restricted=False,
                    labels=[item.get("importance"), item.get("importance")] if item.get("importance") else [],
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
            next_cursor = str(start + len(records)) if len(records) >= per_page else None

        return IssueListPage(
            issues=records,
            next_cursor=next_cursor,
            next_page=start + per_page if next_cursor is not None else None,
            next_params={"cursor": next_cursor},
            request_url=endpoint,
            request_params=params,
            request_headers=self._auth_headers(),
            status_code=response.status_code,
            request_body=payload,
            headers=dict(response.headers),
            closed_filter_applied=(mode == "closed"),
            closed_filter_mode="status=Fix Released",
        )
