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


class PhabricatorAdapter(TrackerAdapter):
    family_slug = "phabricator"
    supported_protocols = (ProtocolType.JSON_RPC,)

    def _api_base(self, instance: TrackerInstance) -> str:
        return (instance.api_base_url or instance.base_url).rstrip("/")

    def _method_endpoint(self, instance: TrackerInstance, method: str) -> str:
        base = self._api_base(instance)
        if base.endswith("/api"):
            return f"{base}/{method}"
        return f"{base}/api/{method}"

    def _entry_key(self, entry: RegistryEntry) -> str | None:
        return entry.tracker_api_key or entry.tracker_native_id or entry.name

    def _token(self) -> str | None:
        return self.config.phabricator_token

    async def _post_api(
        self,
        instance: TrackerInstance,
        method: str,
        params: dict[str, Any],
    ) -> httpx.Response | None:
        endpoint = self._method_endpoint(instance, method)
        body = dict(params)
        token = self._token()
        if token:
            body["api.token"] = token
        try:
            return await self.client.post(endpoint, json=body)
        except httpx.RequestError:
            return None

    def _next_cursor(self, result: dict[str, Any] | None, page_size: int) -> str | None:
        if not isinstance(result, dict):
            return None
        cursor = result.get("cursor")
        if not isinstance(cursor, dict):
            return None
        if cursor.get("after"):
            return str(cursor["after"])
        if isinstance(cursor.get("pageSize"), int) and cursor.get("after") is None:
            return None
        if cursor.get("remaining") in (0, None):
            return None
        return str(cursor.get("after", "")) if cursor.get("after") else None

    async def probe(self, family: TrackerFamily, instance: TrackerInstance, entry: RegistryEntry | None = None) -> ProbeResult:
        # Lightweight probe: user profile endpoint confirms API readiness.
        result = await self._post_api(instance, "user.whoami", {})
        if result is None:
            return ProbeResult(
                family_slug=self.family_slug,
                instance=instance.canonical_name,
                protocol=ProtocolType.JSON_RPC,
                supported=False,
                blocked=True,
                note="user.whoami request error",
            )

        if result.status_code in {401, 403}:
            return ProbeResult(
                family_slug=self.family_slug,
                instance=instance.canonical_name,
                protocol=ProtocolType.JSON_RPC,
                supported=True,
                blocked=True,
                auth_required=result.status_code == 401,
                count_supported=False,
                pagination="cursor",
                raw_response_status=result.status_code,
                note="authentication required or restricted",
                details={"status_code": result.status_code},
            )

        if result.status_code >= 400:
            return ProbeResult(
                family_slug=self.family_slug,
                instance=instance.canonical_name,
                protocol=ProtocolType.JSON_RPC,
                supported=False,
                blocked=True,
                count_supported=False,
                pagination="cursor",
                raw_response_status=result.status_code,
                note=f"http_error:{result.status_code}",
                details={"status_code": result.status_code},
            )

        return ProbeResult(
            family_slug=self.family_slug,
            instance=instance.canonical_name,
            protocol=ProtocolType.JSON_RPC,
            supported=True,
            count_supported=True,
            pagination="cursor",
            raw_response_status=result.status_code,
            details={"status_code": result.status_code},
            note="phabricator user.whoami reachable",
        )

    async def discover(self, family: TrackerFamily, instance: TrackerInstance) -> DiscoveryPlan:
        result = await self._post_api(instance, "project.search", {"limit": 100})
        if result is None:
            return DiscoveryPlan(
                discovered_entries=[],
                count_plan=CountPlan(
                    mode=CountMode.APPROXIMATE,
                    value=None,
                    method="phabricator-project-search-request-error",
                    signature=f"phabricator:{instance.canonical_name}:discover-error",
                    metadata={"error": "request_error"},
                ),
            )

        if result.status_code >= 400:
            return DiscoveryPlan(
                discovered_entries=[],
                count_plan=CountPlan(
                    mode=CountMode.APPROXIMATE,
                    value=None,
                    method="phabricator-project-search-http-error",
                    signature=f"phabricator:{instance.canonical_name}:discover-http",
                    metadata={"status_code": result.status_code},
                ),
                errors=[f"HTTP {result.status_code}"],
            )

        payload = result.json()
        result_obj = payload.get("result", {}) if isinstance(payload, dict) else {}
        projects = result_obj.get("data")
        if not isinstance(projects, list):
            return DiscoveryPlan(
                discovered_entries=[],
                count_plan=CountPlan(
                    mode=CountMode.APPROXIMATE,
                    value=None,
                    method="phabricator-project-search-format",
                    signature=f"phabricator:{instance.canonical_name}:discover-format",
                    metadata={"payload_type": type(payload).__name__},
                ),
                errors=["unexpected project search format"],
            )

        discovered = []
        for item in projects:
            if not isinstance(item, dict):
                continue
            fields = item.get("fields", {})
            if not isinstance(fields, dict):
                continue
            name = fields.get("name")
            phid = item.get("phid")
            if not name:
                continue
            discovered.append(
                {
                    "kind": "project",
                    "name": str(name),
                    "tracker_id": str(phid or name),
                    "note": "phabricator-project-search",
                }
            )

        return DiscoveryPlan(
            discovered_entries=discovered,
            count_plan=CountPlan(
                mode=CountMode.ENUMERATED,
                value=len(discovered),
                method="phabricator-project-search",
                signature=f"phabricator:{instance.canonical_name}:projects",
                metadata={"entries": len(discovered)},
            ),
            errors=[],
        )

    async def build_count_plan(self, entry: RegistryEntry) -> CountPlan:
        params: dict[str, Any] = {"limit": 1}
        project = self._entry_key(entry)
        if project:
            params["constraints[projects]"] = [project]

        result = await self._post_api(entry.instance, "maniphest.search", params)
        if result is None:
            return CountPlan(
                mode=CountMode.APPROXIMATE,
                value=None,
                method="phabricator-count-request-error",
                signature=f"{entry.id}:count-request-error",
            )
        if result.status_code >= 400:
            return CountPlan(
                mode=CountMode.APPROXIMATE,
                value=None,
                method="phabricator-count-http-error",
                signature=f"{entry.id}:count-http-error",
                metadata={"status_code": result.status_code},
            )

        payload = result.json()
        result_obj = payload.get("result", {}) if isinstance(payload, dict) else {}
        cursor = result_obj.get("cursor", {})
        total = cursor.get("total")
        if isinstance(total, int):
            return CountPlan(
                mode=CountMode.EXACT,
                value=total,
                method="phabricator-maniphest-count",
                signature=f"{entry.id}:maniphest-search",
            )

        return CountPlan(
            mode=CountMode.APPROXIMATE,
            value=None,
            method="phabricator-count-approximate",
            signature=f"{entry.id}:maniphest-search",
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
        endpoint = self._method_endpoint(entry.instance, "maniphest.search")
        per_page = max(1, min(int(page_size), 100))

        params: dict[str, Any] = {
            "limit": per_page,
        }
        if cursor:
            params["after"] = cursor

        if mode == "closed":
            params["queryKey"] = "closed"
        else:
            params["queryKey"] = "all"

        project = self._entry_key(entry)
        if project:
            params["constraints[projects]"] = [project]

        result = await self._post_api(entry.instance, "maniphest.search", params)
        if result is None:
            return IssueListPage(
                issues=[],
                error="request_error: phabricator endpoint request failed",
                request_url=endpoint,
                request_params=params,
                request_headers={},
            )
        if result.status_code >= 400:
            return IssueListPage(
                issues=[],
                error=f"http_error:{result.status_code}",
                status_code=result.status_code,
                request_url=endpoint,
                request_params=params,
                request_headers={},
                headers=dict(result.headers),
            )

        payload = result.json()
        result_obj = payload.get("result", {}) if isinstance(payload, dict) else {}
        issues = result_obj.get("data")
        if not isinstance(issues, list):
            return IssueListPage(
                issues=[],
                error="unexpected_payload_type",
                status_code=result.status_code,
                request_url=endpoint,
                request_params=params,
                request_headers={},
                headers=dict(result.headers),
                request_body=payload,
            )

        records: list[IssueRecord] = []
        for item in issues:
            if not isinstance(item, dict):
                continue
            issue_id = item.get("id")
            if issue_id is None:
                continue
            fields = item.get("fields")
            if not isinstance(fields, dict):
                fields = {}
            state = fields.get("status", {})
            owner = fields.get("owner", {})
            assignee = fields.get("assigned", {})
            created_at = _to_dt(fields.get("dateCreated"))
            updated_at = _to_dt(fields.get("dateModified"))
            closed_at = _to_dt(fields.get("closedDate"))
            projects = fields.get("projects", [])
            labels: list[str] = []
            if isinstance(projects, list):
                for p in projects:
                    if isinstance(p, dict):
                        name = p.get("fullName") or p.get("name")
                        if name:
                            labels.append(str(name))

            records.append(
                IssueRecord(
                    tracker_issue_id=str(issue_id),
                    tracker_issue_key=_to_text(item.get("phid")) or str(issue_id),
                    title=_to_text(fields.get("name")) or "",
                    body_raw=_to_text(fields.get("description")) or "",
                    body_plaintext=_to_text(fields.get("description")) or "",
                    issue_url=_to_text(item.get("uri")) or "",
                    api_url=endpoint,
                    issue_type_raw="task",
                    state_raw=_to_text(state.get("value") if isinstance(state, dict) else state),
                    resolution_raw=None,
                    close_reason_raw=None,
                    created_at_tracker=created_at,
                    updated_at_tracker=updated_at,
                    closed_at=closed_at,
                    reporter_raw=_to_text(owner.get("name") if isinstance(owner, dict) else None),
                    assignee_raw=_to_text(assignee.get("name") if isinstance(assignee, dict) else None),
                    is_pull_request=False,
                    is_private_restricted=False,
                    labels=labels,
                    raw_payload=item,
                )
            )

        if sample_limit is not None:
            try:
                remaining = int(sample_limit)
            except Exception:
                remaining = None
            if remaining is not None and remaining >= 0 and len(records) > remaining:
                records = records[:remaining]
                next_cursor = None
            else:
                next_cursor = self._next_cursor(result_obj, per_page)
        else:
            next_cursor = self._next_cursor(result_obj, per_page)

        return IssueListPage(
            issues=records,
            next_cursor=next_cursor,
            next_page=None,
            next_params={"after": next_cursor} if next_cursor else None,
            request_url=endpoint,
            request_params=params,
            request_headers={},
            status_code=result.status_code,
            request_body=result_obj,
            headers=dict(result.headers),
            closed_filter_applied=(mode == "closed"),
            closed_filter_mode="queryKey=closed",
        )
