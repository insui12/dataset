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


def _to_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v) for v in value if str(v).strip()]


def _closed_status_filter() -> str:
    return '(status in (Closed, "Ready for QA", "Resolved", "Done", "Verified"))'


def _jql_escape(text: str) -> str:
    return text.replace('"', '\\"')


class JiraAdapter(TrackerAdapter):
    family_slug = "jira"
    supported_protocols = (ProtocolType.REST,)

    def _api_base(self, instance: TrackerInstance) -> str:
        return (instance.api_base_url or instance.base_url).rstrip("/")

    def _entry_key(self, entry: RegistryEntry) -> str | None:
        return entry.tracker_api_key or entry.tracker_native_id or entry.name

    def _auth_headers(self) -> dict[str, str]:
        token = self.config.jira_token
        if not token:
            return {}
        return {"Authorization": f"Bearer {token}"}

    async def probe(self, family: TrackerFamily, instance: TrackerInstance, entry: RegistryEntry | None = None) -> ProbeResult:
        base = self._api_base(instance)
        url = f"{base}/rest/api/2/serverInfo"
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
                pagination="cursor",
                raw_response_status=response.status_code,
                note="auth required",
                details={"status_code": response.status_code},
            )
        if response.status_code >= 400:
            return ProbeResult(
                family_slug=self.family_slug,
                instance=instance.canonical_name,
                protocol=ProtocolType.REST,
                supported=False,
                blocked=True,
                count_supported=False,
                pagination="cursor",
                raw_response_status=response.status_code,
                note=f"http_error:{response.status_code}",
                details={"status_code": response.status_code},
            )
        return ProbeResult(
            family_slug=self.family_slug,
            instance=instance.canonical_name,
            protocol=ProtocolType.REST,
            supported=True,
            raw_response_status=response.status_code,
            details={"status_code": response.status_code},
            count_supported=True,
            pagination="cursor",
            note="jira api reachable",
        )

    async def discover(self, family: TrackerFamily, instance: TrackerInstance) -> DiscoveryPlan:
        return DiscoveryPlan(
            discovered_entries=[],
            count_plan=CountPlan(
                mode=CountMode.APPROXIMATE,
                value=None,
                method="manifest_exhaustive_or_instance_projects",
                signature=f"jira-{instance.canonical_name}",
                metadata={"source": "manifest-first"},
            ),
            errors=[],
        )

    async def build_count_plan(self, entry: RegistryEntry) -> CountPlan:
        base = self._api_base(entry.instance)
        key = self._entry_key(entry)
        if not key:
            return CountPlan(
                mode=CountMode.APPROXIMATE,
                value=None,
                method="jira-count-missing-key",
                signature=f"{entry.id}:missing-key",
            )

        endpoint = f"{base}/rest/api/2/search"
        jql = f'project="{_jql_escape(key)}" and {_closed_status_filter()}'
        params = {
            "jql": jql,
            "startAt": 0,
            "maxResults": 1,
            "validateQuery": True,
            "fields": "id",
        }
        try:
            response = await self.client.get(endpoint, headers=self._auth_headers(), params=params)
        except httpx.RequestError as exc:
            return CountPlan(
                mode=CountMode.APPROXIMATE,
                value=None,
                method="jira-count-request-error",
                signature=f"{entry.id}:count-request-error",
                metadata={"error": str(exc)},
            )
        if response.status_code >= 400:
            return CountPlan(
                mode=CountMode.APPROXIMATE,
                value=None,
                method="jira-count-http-error",
                signature=f"{entry.id}:count-http-error",
                metadata={"status_code": response.status_code},
            )

        payload = response.json()
        total = payload.get("total")
        if isinstance(total, int):
            return CountPlan(
                mode=CountMode.EXACT,
                value=total,
                method="jira-search-total",
                signature=f"{entry.id}:search-total",
                metadata={"project": key},
            )
        return CountPlan(
            mode=CountMode.APPROXIMATE,
            value=None,
            method="jira-count-approximate",
            signature=f"{entry.id}:count-unknown",
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
        project = self._entry_key(entry)
        if not project:
            return IssueListPage(
                issues=[],
                error="tracker id missing",
                status_code=None,
                closed_filter_applied=False,
            )

        try:
            start_at = int(cursor) if cursor is not None else 0
        except Exception:
            start_at = 0

        per_page = max(1, min(int(page_size), 100))
        jql = f'project="{_jql_escape(project)}"'
        if mode == "closed":
            jql = f"{jql} AND {_closed_status_filter()}"
        endpoint = f"{base}/rest/api/2/search"
        params = {
            "jql": jql,
            "startAt": start_at,
            "maxResults": per_page,
            "fields": "id,key,summary,description,status,resolution,issuetype,assignee,creator,created,updated,closedSprints,labels,labels",
            "validateQuery": True,
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

        issues = payload.get("issues")
        if not isinstance(issues, list):
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
        for item in issues:
            if not isinstance(item, dict):
                continue
            issue_id = item.get("id")
            if issue_id is None:
                continue
            fields = item.get("fields", {})
            if not isinstance(fields, dict):
                fields = {}
            status = fields.get("status") if isinstance(fields.get("status"), dict) else {}
            resolution = fields.get("resolution") if isinstance(fields.get("resolution"), dict) else {}
            issuetype = fields.get("issuetype") if isinstance(fields.get("issuetype"), dict) else {}
            assignee = fields.get("assignee") if isinstance(fields.get("assignee"), dict) else {}
            creator = fields.get("creator") if isinstance(fields.get("creator"), dict) else {}
            state_name = status.get("name")
            resolution_name = resolution.get("name") if isinstance(resolution, dict) else None
            labels = _to_str_list(fields.get("labels"))

            records.append(
                IssueRecord(
                    tracker_issue_id=str(issue_id),
                    tracker_issue_key=_to_text(item.get("key")) or str(issue_id),
                    title=_to_text(fields.get("summary")) or "",
                    body_raw=_to_text(fields.get("description")),
                    body_plaintext=_to_text(fields.get("description")),
                    issue_url=f"{base}/browse/{item.get('key')}" if item.get("key") else "",
                    api_url=f"{base}/rest/api/2/issue/{issue_id}",
                    issue_type_raw=_to_text(issuetype.get("name") if isinstance(issuetype, dict) else None),
                    state_raw=_to_text(state_name),
                    resolution_raw=_to_text(resolution_name),
                    close_reason_raw=_to_text(resolution_name),
                    created_at_tracker=_to_dt(fields.get("created")),
                    updated_at_tracker=_to_dt(fields.get("updated")),
                    closed_at=_to_dt(fields.get("resolutiondate")),
                    reporter_raw=_to_text(creator.get("displayName") if isinstance(creator, dict) else None),
                    assignee_raw=_to_text(assignee.get("displayName") if isinstance(assignee, dict) else None),
                    is_pull_request=False,
                    is_private_restricted=False,
                    labels=labels,
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
                total = payload.get("total")
                if isinstance(total, int) and start_at + len(records) < total:
                    next_cursor = str(start_at + per_page)
                else:
                    next_cursor = None
        else:
            total = payload.get("total")
            if isinstance(total, int) and start_at + len(records) < total:
                next_cursor = str(start_at + per_page)
            else:
                next_cursor = str(start_at + len(records)) if len(records) >= per_page else None

        return IssueListPage(
            issues=records,
            next_cursor=next_cursor,
            next_page=start_at + per_page if next_cursor is not None else None,
            next_params={"startAt": next_cursor},
            request_url=endpoint,
            request_params=params,
            request_headers=self._auth_headers(),
            status_code=response.status_code,
            request_body=payload,
            headers=dict(response.headers),
            closed_filter_applied=(mode == "closed"),
            closed_filter_mode="jql closed status filter",
        )
