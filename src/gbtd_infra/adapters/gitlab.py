from __future__ import annotations

from datetime import datetime
from urllib.parse import quote_plus

import httpx

from gbtd_infra.adapters.base import CountMode, CountPlan, DiscoveryPlan, IssueListPage, IssueRecord, ProbeResult, TrackerAdapter
from gbtd_infra.models import ProtocolType, RegistryEntry, TrackerFamily, TrackerInstance


def _to_dt(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


class GitLabIssuesAdapter(TrackerAdapter):
    family_slug = "gitlab"
    supported_protocols = (ProtocolType.REST,)

    def _api_base(self, instance: TrackerInstance) -> str:
        return (instance.api_base_url or "https://gitlab.com/api/v4").rstrip("/")

    def _project_id(self, entry: RegistryEntry) -> str:
        return entry.tracker_api_key or entry.tracker_native_id or entry.name

    def _auth_headers(self) -> dict[str, str]:
        token = self.config.gitlab_token
        if not token:
            return {}
        return {"PRIVATE-TOKEN": token}

    async def probe(self, family: TrackerFamily, instance: TrackerInstance, entry: RegistryEntry | None = None) -> ProbeResult:
        base = self._api_base(instance)
        try:
            response = await self.client.get(f"{base}/version", headers=self._auth_headers())
            if response.status_code in {401, 403}:
                return ProbeResult(
                    family_slug=self.family_slug,
                    instance=instance.canonical_name,
                    protocol=ProtocolType.REST,
                    supported=True,
                    blocked=True,
                    auth_required=response.status_code in {401, 403},
                    note="auth required or restricted for GitLab API",
                    details={"status_code": response.status_code},
                )
            if response.status_code >= 400:
                return ProbeResult(
                    family_slug=self.family_slug,
                    instance=instance.canonical_name,
                    protocol=ProtocolType.REST,
                    supported=False,
                    blocked=True,
                    note="version endpoint unreachable",
                    details={"status_code": response.status_code},
                )
            return ProbeResult(
                family_slug=self.family_slug,
                instance=instance.canonical_name,
                protocol=ProtocolType.REST,
                supported=True,
                count_supported=True,
                pagination="page",
                details={"version": response.status_code},
            )
        except httpx.RequestError as exc:
            return ProbeResult(
                family_slug=self.family_slug,
                instance=instance.canonical_name,
                protocol=ProtocolType.REST,
                supported=False,
                blocked=True,
                note=f"request_error:{exc}",
            )

    async def discover(self, family: TrackerFamily, instance: TrackerInstance) -> DiscoveryPlan:
        return DiscoveryPlan(
            discovered_entries=[],
            count_plan=CountPlan(
                mode=CountMode.APPROXIMATE,
                value=None,
                method="gitlab-manifest-mode",
                signature=f"gitlab:{instance.canonical_name}:no-auto-discovery",
            ),
            errors=["No auto-discovery for mega-host without manifest entries"],
        )

    async def build_count_plan(self, entry: RegistryEntry) -> CountPlan:
        base = self._api_base(entry.instance)
        project = self._project_id(entry)
        if not project:
            return CountPlan(
                mode=CountMode.APPROXIMATE,
                value=None,
                method="missing_project",
                signature=f"{entry.id}:missing-project",
            )

        encoded = quote_plus(project)
        endpoint = f"{base}/projects/{encoded}/issues_statistics"
        try:
            response = await self.client.get(
                endpoint,
                headers=self._auth_headers(),
                params={"state": "closed"},
            )
            if response.status_code >= 400:
                return CountPlan(
                    mode=CountMode.APPROXIMATE,
                    value=None,
                    method="issues_statistics-unavailable",
                    signature=f"{entry.id}:issues-statistics",
                    metadata={"status_code": response.status_code},
                )
            payload = response.json()
            stats = payload.get("statistics", {}).get("counts", {}).get("closed") if isinstance(payload, dict) else None
            if isinstance(stats, int):
                return CountPlan(
                    mode=CountMode.EXACT,
                    value=stats,
                    method="projects/issues_statistics(state=closed)",
                    signature=f"{entry.id}:issues-closed",
                    metadata={"endpoint": endpoint},
                )
        except httpx.RequestError:
            pass

        return CountPlan(
            mode=CountMode.APPROXIMATE,
            value=None,
            method="issues_statistics-error",
            signature=f"{entry.id}:count-unavailable",
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
        base = self._api_base(entry.instance)
        project = self._project_id(entry)
        if not project:
            return IssueListPage(
                issues=[],
                error="project identifier missing",
                status_code=None,
                closed_filter_applied=False,
            )

        encoded = quote_plus(project)
        page = int(cursor) if cursor is not None else 1
        per_page = max(1, min(int(page_size), 100))
        endpoint = f"{base}/projects/{encoded}/issues"
        params = {
            "state": "closed" if mode == "closed" else "all",
            "scope": "all",
            "per_page": per_page,
            "page": page,
            "order_by": "created_at",
            "sort": "asc",
        }

        try:
            response = await self.client.get(endpoint, headers=self._auth_headers(), params=params)
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
        if not isinstance(payload, list):
            return IssueListPage(
                issues=[],
                error="unexpected_payload_type",
                status_code=response.status_code,
                request_url=endpoint,
                request_params=params,
                request_headers=self._auth_headers(),
                headers=dict(response.headers),
            )

        records: list[IssueRecord] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            number = item.get("iid")
            if number is None:
                continue
            number = str(number)
            created_at = _to_dt(item.get("created_at"))
            updated_at = _to_dt(item.get("updated_at"))
            closed_at = _to_dt(item.get("closed_at"))
            reporter = (item.get("author") or {}).get("username") if isinstance(item.get("author"), dict) else None
            assignee = (item.get("assignee") or {}).get("username") if isinstance(item.get("assignee"), dict) else None
            labels = [str(v) for v in (item.get("labels") or [])]

            records.append(
                IssueRecord(
                    tracker_issue_id=number,
                    tracker_issue_key=f"{project}#{number}",
                    title=item.get("title") or "",
                    body_raw=(item.get("description") or ""),
                    body_plaintext=(item.get("description") or ""),
                    issue_url=item.get("web_url") or "",
                    api_url=item.get("web_url") or "",
                    issue_type_raw="issue",
                    state_raw=item.get("state"),
                    resolution_raw=item.get("state"),
                    close_reason_raw=item.get("state"),
                    created_at_tracker=created_at,
                    updated_at_tracker=updated_at,
                    closed_at=closed_at,
                    reporter_raw=reporter,
                    assignee_raw=assignee,
                    is_pull_request=False,
                    labels=labels,
                    raw_payload=item,
                )
            )

        next_cursor = str(page + 1) if len(payload) >= per_page else None
        if sample_limit is not None:
            remaining = max(0, sample_limit)
            if len(records) > remaining:
                records = records[:remaining]
                next_cursor = None

        return IssueListPage(
            issues=records,
            next_cursor=next_cursor,
            next_page=page + 1 if next_cursor else None,
            next_params={"page": page + 1},
            request_url=endpoint,
            request_params=params,
            request_headers=self._auth_headers(),
            status_code=response.status_code,
            headers=dict(response.headers),
            request_body=payload,
            closed_filter_applied=(mode == "closed"),
            closed_filter_mode="state=closed",
        )
