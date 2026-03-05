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


class GoogleIssueTrackerAdapter(TrackerAdapter):
    family_slug = "google_issue_tracker"
    supported_protocols = (ProtocolType.REST,)

    def _api_base(self, instance: TrackerInstance) -> str:
        return (instance.api_base_url or instance.base_url).rstrip("/")

    def _entry_key(self, entry: RegistryEntry) -> str | None:
        return entry.tracker_api_key or entry.tracker_native_id or entry.name

    def _headers(self) -> dict[str, str]:
        token = self.config.google_token
        headers: dict[str, str] = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    @staticmethod
    def _normalize_base_api_url(api_base_url: str | None) -> str:
        if not api_base_url:
            return "https://issuetracker.googleapis.com/v1"
        base = api_base_url.rstrip("/")
        if base.endswith("/v1"):
            return base
        if "issuetracker.googleapis.com" in base:
            if not base.endswith("/v1"):
                return f"{base}/v1"
            return base
        return "https://issuetracker.googleapis.com/v1"

    def _endpoint(self, entry: RegistryEntry) -> str:
        base = self._normalize_base_api_url(self._api_base(entry.instance))
        project = self._entry_key(entry) or ""
        return f"{base}/projects/{project}/issues"

    async def probe(
        self,
        family: TrackerFamily,
        instance: TrackerInstance,
        entry: RegistryEntry | None = None,
    ) -> ProbeResult:
        base = self._normalize_base_api_url(instance.api_base_url)
        endpoint = f"{base}/projects"
        try:
            response = await self.client.get(endpoint, headers=self._headers())
        except httpx.RequestError as exc:
            return ProbeResult(
                family_slug=self.family_slug,
                instance=instance.canonical_name,
                protocol=ProtocolType.REST,
                supported=False,
                blocked=True,
                note=f"google issue tracker unreachable: {exc}",
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
                pagination="cursor",
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
                count_supported=False,
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
            pagination="cursor",
            raw_response_status=response.status_code,
            details={"status_code": response.status_code},
            note="google issue tracker reachable",
        )

    async def discover(self, family: TrackerFamily, instance: TrackerInstance) -> DiscoveryPlan:
        """API-first: discover projects from configured project list only."""
        base = self._normalize_base_api_url(instance.api_base_url)
        endpoint = f"{base}/projects"
        try:
            response = await self.client.get(endpoint, headers=self._headers())
        except httpx.RequestError as exc:
            return DiscoveryPlan(
                discovered_entries=[],
                count_plan=CountPlan(
                    mode=CountMode.APPROXIMATE,
                    value=None,
                    method="google-discover-request-error",
                    signature=f"{self.family_slug}:{instance.canonical_name}:discover-error",
                    metadata={"error": str(exc)},
                ),
            )

        if response.status_code >= 400:
            return DiscoveryPlan(
                discovered_entries=[],
                count_plan=CountPlan(
                    mode=CountMode.APPROXIMATE,
                    value=None,
                    method="google-discover-http-error",
                    signature=f"{self.family_slug}:{instance.canonical_name}:discover-http",
                    metadata={"status_code": response.status_code},
                ),
                errors=[f"HTTP {response.status_code}"],
            )

        payload = response.json()
        projects = payload.get("projects") if isinstance(payload, dict) else None
        if not isinstance(projects, list):
            return DiscoveryPlan(
                discovered_entries=[],
                count_plan=CountPlan(
                    mode=CountMode.APPROXIMATE,
                    value=None,
                    method="google-discover-format",
                    signature=f"{self.family_slug}:{instance.canonical_name}:discover-format",
                    metadata={"payload_type": type(payload).__name__},
                ),
                errors=["unexpected project list format"],
            )

        discovered = []
        for project in projects:
            if not isinstance(project, dict):
                continue
            name = project.get("name") or project.get("projectId")
            if not name:
                continue
            discovered.append(
                {
                    "kind": "project",
                    "name": str(name),
                    "tracker_id": str(name),
                    "note": "google-issuetracker-project-discovery",
                }
            )
        return DiscoveryPlan(
            discovered_entries=discovered,
            count_plan=CountPlan(
                mode=CountMode.ENUMERATED,
                value=len(discovered),
                method="google-issues-project-list",
                signature=f"{self.family_slug}:{instance.canonical_name}:projects",
            ),
            errors=[],
        )

    async def build_count_plan(self, entry: RegistryEntry) -> CountPlan:
        endpoint = self._endpoint(entry)
        params = {
            "q": "status:closed",
            "pageSize": 1,
            "pageToken": "",
        }
        try:
            response = await self.client.get(endpoint, params=params, headers=self._headers())
        except httpx.RequestError as exc:
            return CountPlan(
                mode=CountMode.APPROXIMATE,
                value=None,
                method="google-count-request-error",
                signature=f"{entry.id}:count-request-error",
                metadata={"error": str(exc)},
            )

        if response.status_code >= 400:
            return CountPlan(
                mode=CountMode.APPROXIMATE,
                value=None,
                method="google-count-http-error",
                signature=f"{entry.id}:count-http-error",
                metadata={"status_code": response.status_code},
            )

        payload = response.json()
        if isinstance(payload, dict):
            total = payload.get("totalSize")
            if total is None and payload.get("nextPageToken") is not None:
                # API shape sometimes omits totalSize; fallback to approximate.
                total = None
            if isinstance(total, int):
                return CountPlan(
                    mode=CountMode.EXACT,
                    value=total,
                    method="google-issues-search-totalSize",
                    signature=f"{entry.id}:q=status:closed",
                    metadata={"endpoint": endpoint},
                )

        return CountPlan(
            mode=CountMode.APPROXIMATE,
            value=None,
            method="google-count-approximate",
            signature=f"{entry.id}:q=status:closed",
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
                closed_filter_applied=False,
            )

        endpoint = self._endpoint(entry)
        per_page = max(1, min(int(page_size), 100))
        params = {
            "pageSize": per_page,
            "pageToken": cursor or "",
        }

        if mode == "closed":
            params["q"] = "status:closed"
        else:
            params["q"] = ""

        try:
            response = await self.client.get(endpoint, params=params, headers=self._headers())
        except httpx.RequestError as exc:
            return IssueListPage(
                issues=[],
                error=f"request_error:{exc}",
                request_url=endpoint,
                request_params=params,
                request_headers=self._headers(),
            )

        if response.status_code >= 400:
            return IssueListPage(
                issues=[],
                error=f"http_error:{response.status_code}",
                status_code=response.status_code,
                request_url=endpoint,
                request_params=params,
                request_headers=self._headers(),
                headers=dict(response.headers),
            )

        payload = response.json()
        items = payload.get("issues") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            return IssueListPage(
                issues=[],
                error="unexpected_payload_type",
                status_code=response.status_code,
                request_url=endpoint,
                request_params=params,
                request_headers=self._headers(),
                headers=dict(response.headers),
            )

        records: list[IssueRecord] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            issue_id = item.get("id") or item.get("name")
            if issue_id is None:
                continue
            issue_id = str(issue_id)

            state = item.get("state")
            state_name = state.get("name") if isinstance(state, dict) else None
            reporter = item.get("reporter")
            assignee = item.get("assignee")
            reporter_raw = reporter.get("displayName") if isinstance(reporter, dict) else None
            assignee_raw = assignee.get("displayName") if isinstance(assignee, dict) else None

            summary = item.get("title") or item.get("summary") or ""
            body = item.get("description") or ""
            created = _to_dt(item.get("createTime"))
            updated = _to_dt(item.get("updateTime"))
            closed = _to_dt(item.get("closeTime"))
            labels = item.get("labels") or []
            if not isinstance(labels, list):
                labels = [str(labels)] if labels is not None else []

            records.append(
                IssueRecord(
                    tracker_issue_id=issue_id,
                    tracker_issue_key=item.get("name") or issue_id,
                    title=_to_text(summary) or "",
                    body_raw=_to_text(body),
                    body_plaintext=_to_text(body),
                    issue_url=item.get("name") or "",
                    api_url=(payload.get("name", "") or "").strip(),
                    issue_type_raw="issue",
                    state_raw=_to_text(state_name or item.get("state")),
                    resolution_raw=(state.get("verificationDetails") if isinstance(state, dict) else None),
                    close_reason_raw=(state.get("verificationState") if isinstance(state, dict) else None),
                    created_at_tracker=created,
                    updated_at_tracker=updated,
                    closed_at=closed,
                    reporter_raw=_to_text(reporter_raw),
                    assignee_raw=_to_text(assignee_raw),
                    is_pull_request=False,
                    is_private_restricted=False,
                    labels=[str(v) for v in labels if str(v).strip()],
                    raw_payload=item,
                )
            )

        next_cursor = payload.get("nextPageToken") if isinstance(payload, dict) else None

        if sample_limit is not None:
            try:
                limit = int(sample_limit)
            except Exception:
                limit = None
            if limit is not None and len(records) > limit:
                records = records[:limit]
                next_cursor = None

        if sample_limit is not None:
            try:
                requested = int(sample_limit)
            except Exception:
                requested = None
            if requested is not None and requested <= 0:
                next_cursor = None

        if isinstance(sample_limit, int) and sample_limit <= 0:
            next_cursor = None

        return IssueListPage(
            issues=records,
            next_cursor=next_cursor,
            next_page=None,
            next_params={"cursor": next_cursor},
            request_url=endpoint,
            request_params=params,
            request_headers=self._headers(),
            status_code=response.status_code,
            request_body=payload,
            headers=dict(response.headers),
            closed_filter_applied=(mode == "closed"),
            closed_filter_mode="q=status:closed",
        )
