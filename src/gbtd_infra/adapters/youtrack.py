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


class YouTrackAdapter(TrackerAdapter):
    family_slug = "youtrack"
    supported_protocols = (ProtocolType.REST,)

    def _api_base(self, instance: TrackerInstance) -> str:
        return (instance.api_base_url or instance.base_url).rstrip("/")

    def _entry_key(self, entry: RegistryEntry) -> str | None:
        return entry.tracker_api_key or entry.tracker_native_id or entry.name

    def _auth_headers(self) -> dict[str, str]:
        token = self.config.youtrack_token
        if not token:
            return {}
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    async def probe(self, family: TrackerFamily, instance: TrackerInstance, entry: RegistryEntry | None = None) -> ProbeResult:
        base = self._api_base(instance)
        endpoint = f"{base}/admin/configuration"
        try:
            response = await self.client.get(endpoint, headers=self._auth_headers())
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
                count_supported=True,
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
            count_supported=True,
            pagination="cursor",
            raw_response_status=response.status_code,
            details={"status_code": response.status_code},
            note="youtrack api reachable",
        )

    async def discover(self, family: TrackerFamily, instance: TrackerInstance) -> DiscoveryPlan:
        return DiscoveryPlan(
            discovered_entries=[],
            count_plan=CountPlan(
                mode=CountMode.APPROXIMATE,
                value=None,
                method="youtrack-manifest-centric",
                signature=f"youtrack:{instance.canonical_name}:projects",
            ),
            errors=[],
        )

    async def build_count_plan(self, entry: RegistryEntry) -> CountPlan:
        project = self._entry_key(entry)
        if not project:
            return CountPlan(
                mode=CountMode.APPROXIMATE,
                value=None,
                method="youtrack-count-missing-key",
                signature=f"{entry.id}:missing-key",
            )

        base = self._api_base(entry.instance)
        endpoint = f"{base}/issues/count"
        params = {"query": f'project:{project}'}
        try:
            response = await self.client.get(endpoint, params=params, headers=self._auth_headers())
        except httpx.RequestError as exc:
            return CountPlan(
                mode=CountMode.APPROXIMATE,
                value=None,
                method="youtrack-count-request-error",
                signature=f"{entry.id}:count-request-error",
                metadata={"error": str(exc)},
            )

        if response.status_code >= 400:
            return CountPlan(
                mode=CountMode.APPROXIMATE,
                value=None,
                method="youtrack-count-http-error",
                signature=f"{entry.id}:count-http-error",
                metadata={"status_code": response.status_code},
            )

        payload = response.json()
        total = payload.get("count") if isinstance(payload, dict) else None
        if isinstance(total, int):
            return CountPlan(
                mode=CountMode.EXACT,
                value=total,
                method="youtrack-issues-count",
                signature=f"{entry.id}:issues-count",
                metadata={"endpoint": endpoint},
            )

        total_header = response.headers.get("X-YouTrack-Count")
        if total_header and total_header.isdigit():
            return CountPlan(
                mode=CountMode.EXACT,
                value=int(total_header),
                method="youtrack-count-header",
                signature=f"{entry.id}:issues-count-header",
                metadata={"endpoint": endpoint},
            )

        return CountPlan(
            mode=CountMode.APPROXIMATE,
            value=None,
            method="youtrack-count-approximate",
            signature=f"{entry.id}:count-unknown",
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

        base = self._api_base(entry.instance)
        endpoint = f"{base}/issues"
        try:
            skip = int(cursor) if cursor is not None else 0
        except Exception:
            skip = 0
        limit = max(1, min(int(page_size), 100))

        query = f'project:{project}'
        if mode == "closed":
            query = f"{query} #Resolved"

        params = {
            "$top": limit,
            "$skip": skip,
            "query": query,
            "fields": "id,idReadable,summary,description,reporter(login),updater(login),created,updated,resolved,numberInProject,project(shortName),customFields(name,value(name))",
        }

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
        if not isinstance(payload, list):
            return IssueListPage(
                issues=[],
                error="unexpected_payload_type",
                status_code=response.status_code,
                request_url=endpoint,
                request_params=params,
                request_headers=self._auth_headers(),
                headers=dict(response.headers),
                request_body=payload,
            )

        records: list[IssueRecord] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            issue_id = item.get("id")
            if issue_id is None:
                continue
            item_id = str(issue_id)
            state_name = None
            custom_fields = item.get("customFields")
            if isinstance(custom_fields, list):
                for field in custom_fields:
                    if not isinstance(field, dict):
                        continue
                    if field.get("name") != "State":
                        continue
                    value = field.get("value")
                    if isinstance(value, dict) and value.get("name"):
                        state_name = str(value["name"])
                        break
            reporter = item.get("reporter") if isinstance(item.get("reporter"), dict) else None
            updater = item.get("updater") if isinstance(item.get("updater"), dict) else None
            description = item.get("description")

            records.append(
                IssueRecord(
                    tracker_issue_id=item_id,
                    tracker_issue_key=f"{project}-{item.get('numberInProject', item_id)}",
                    title=_to_text(item.get("summary")) or "",
                    body_raw=_to_text(description),
                    body_plaintext=_to_text(description),
                    issue_url=f"{entry.instance.base_url.rstrip('/')}/issue/{item.get('idReadable') or item_id}",
                    api_url=f"{base}/issue/{item_id}",
                    issue_type_raw="issue",
                    state_raw=_to_text(state_name),
                    resolution_raw=_to_text(item.get("resolution")),
                    close_reason_raw=_to_text(item.get("closeReason")),
                    created_at_tracker=_to_dt(item.get("created")),
                    updated_at_tracker=_to_dt(item.get("updated")),
                    closed_at=_to_dt(item.get("resolved")),
                    reporter_raw=_to_text(reporter.get("login") if isinstance(reporter, dict) else reporter),
                    assignee_raw=_to_text(updater.get("login") if isinstance(updater, dict) else updater),
                    is_pull_request=False,
                    is_private_restricted=False,
                    labels=[str(state_name)] if state_name else [],
                    raw_payload=item,
                )
            )

        try:
            limit_rows = int(sample_limit) if sample_limit is not None else None
        except Exception:
            limit_rows = None
        if limit_rows is not None and len(records) > limit_rows:
            records = records[:limit_rows]
            next_cursor = None
        else:
            total_from_header = response.headers.get("X-YouTrack-Total-Count")
            if total_from_header and total_from_header.isdigit():
                next_cursor = str(skip + len(records)) if (skip + len(records)) < int(total_from_header) else None
            else:
                next_cursor = str(skip + len(records)) if len(records) >= limit else None

        return IssueListPage(
            issues=records,
            next_cursor=next_cursor,
            next_page=skip + limit if next_cursor is not None else None,
            next_params={"$skip": next_cursor},
            request_url=endpoint,
            request_params=params,
            request_headers=self._auth_headers(),
            status_code=response.status_code,
            request_body=payload,
            headers=dict(response.headers),
            closed_filter_applied=(mode == "closed"),
            closed_filter_mode="query includes #Resolved",
        )
