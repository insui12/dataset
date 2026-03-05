from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from urllib.parse import quote_plus

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


class BugzillaAdapter(TrackerAdapter):
    """Bugzilla official protocol fallback chain: REST -> JSON-RPC -> XML-RPC."""

    family_slug = "bugzilla"
    supported_protocols = (
        ProtocolType.REST,
        ProtocolType.JSON_RPC,
        ProtocolType.XML_RPC,
    )

    def _api_base(self, instance: TrackerInstance) -> str:
        return (instance.api_base_url or instance.base_url).rstrip("/")

    def _public_base(self, instance: TrackerInstance) -> str:
        return instance.base_url.rstrip("/")

    def _entry_key(self, entry: RegistryEntry) -> str | None:
        return entry.tracker_api_key or entry.tracker_native_id or entry.name

    def _closed_statuses(self) -> list[str]:
        return [
            "RESOLVED",
            "VERIFIED",
            "CLOSED",
            "FIXED",
            "WONTFIX",
            "DUPLICATE",
            "INVALID",
            "NOTABUG",
            "BYDESIGN",
        ]

    def _open_statuses(self) -> list[str]:
        return [
            "UNCONFIRMED",
            "NEW",
            "ASSIGNED",
            "REOPENED",
            "NEEDSINFO",
        ]

    def _select_statuses(self, mode: str) -> list[str]:
        if mode == "closed":
            return self._closed_statuses()
        return self._closed_statuses() + self._open_statuses()

    async def probe(
        self,
        family: TrackerFamily,
        instance: TrackerInstance,
        entry: RegistryEntry | None = None,
    ) -> ProbeResult:
        base = self._api_base(instance)

        rest_checks: list[tuple[ProtocolType, str]] = [
            (ProtocolType.REST, f"{base}/version"),
        ]
        # If REST is blocked, we still record API reachability and move to fallback probes.
        for protocol, endpoint in rest_checks:
            try:
                response = await self.client.get(endpoint)
            except httpx.RequestError as exc:
                return ProbeResult(
                    family_slug=self.family_slug,
                    instance=instance.canonical_name,
                    protocol=protocol,
                    supported=False,
                    blocked=True,
                    note=str(exc),
                )

            if response.status_code in {401, 403}:
                return ProbeResult(
                    family_slug=self.family_slug,
                    instance=instance.canonical_name,
                    protocol=protocol,
                    supported=True,
                    blocked=True,
                    auth_required=response.status_code == 401,
                    count_supported=False,
                    pagination="offset",
                    note="authentication required or access restricted",
                    raw_response_status=response.status_code,
                    details={"status_code": response.status_code},
                )

            if response.status_code == 429:
                return ProbeResult(
                    family_slug=self.family_slug,
                    instance=instance.canonical_name,
                    protocol=protocol,
                    supported=False,
                    blocked=True,
                    note="rate limited",
                    raw_response_status=response.status_code,
                    details={"status_code": response.status_code},
                )

            if response.status_code >= 400:
                break

            return ProbeResult(
                family_slug=self.family_slug,
                instance=instance.canonical_name,
                protocol=protocol,
                supported=True,
                blocked=False,
                count_supported=True,
                pagination="offset",
                raw_response_status=response.status_code,
                note="probe success",
                details={"status_code": response.status_code},
            )

        fallbacks: list[tuple[ProtocolType, str]] = [
            (ProtocolType.JSON_RPC, f"{base}/jsonrpc.cgi"),
            (ProtocolType.XML_RPC, f"{base}/xmlrpc.cgi"),
        ]

        for protocol, endpoint in fallbacks:
            try:
                response = await self.client.get(endpoint)
            except httpx.RequestError as exc:
                continue

            if response.status_code == 405:
                return ProbeResult(
                    family_slug=self.family_slug,
                    instance=instance.canonical_name,
                    protocol=protocol,
                    supported=True,
                    blocked=True,
                    note="method-not-allowed; write requests required for this protocol",
                    raw_response_status=response.status_code,
                    details={"status_code": response.status_code},
                )

            if response.status_code in {401, 403}:
                return ProbeResult(
                    family_slug=self.family_slug,
                    instance=instance.canonical_name,
                    protocol=protocol,
                    supported=True,
                    blocked=True,
                    auth_required=response.status_code == 401,
                    count_supported=False,
                    pagination="offset",
                    note="authentication required or access restricted",
                    raw_response_status=response.status_code,
                    details={"status_code": response.status_code},
                )

            if response.status_code < 400:
                return ProbeResult(
                    family_slug=self.family_slug,
                    instance=instance.canonical_name,
                    protocol=protocol,
                    supported=True,
                    blocked=False,
                    count_supported=True,
                    pagination="offset",
                    note="fallback protocol reachable",
                    raw_response_status=response.status_code,
                    details={"status_code": response.status_code},
                )

        return ProbeResult(
            family_slug=self.family_slug,
            instance=instance.canonical_name,
            protocol=ProtocolType.UNKNOWN,
            supported=False,
            blocked=True,
            note="no official protocol reachable",
        )

    async def discover(self, family: TrackerFamily, instance: TrackerInstance) -> DiscoveryPlan:
        """Discover Bugzilla products (safe, idempotent, bounded-instance strategy)."""
        base = self._api_base(instance)
        try:
            response = await self.client.get(f"{base}/product", params={"include_fields": "name,id"})
            if response.status_code >= 400:
                return DiscoveryPlan(
                    discovered_entries=[],
                    count_plan=CountPlan(
                        mode=CountMode.APPROXIMATE,
                        value=None,
                        method="bugzilla-discover-products-http-error",
                        signature=f"{self.family_slug}:{instance.canonical_name}:product-list",
                        metadata={"status_code": response.status_code},
                    ),
                    errors=["product listing endpoint returned non-success"],
                )

            payload = response.json()
            products = payload.get("products") if isinstance(payload, dict) else None
            discovered = []
            if isinstance(products, list):
                for item in products:
                    if not isinstance(item, dict):
                        continue
                    name = item.get("name") or item.get("id")
                    if not name:
                        continue
                    discovered.append(
                        {
                            "kind": "product",
                            "name": str(name),
                            "tracker_id": str(name),
                            "note": "discover-from-bugzilla-rest-product",
                        }
                    )

                return DiscoveryPlan(
                    discovered_entries=discovered,
                    count_plan=CountPlan(
                        mode=CountMode.ENUMERATED,
                        value=len(discovered),
                        method="bugzilla-product-listing",
                        signature=f"{self.family_slug}:{instance.canonical_name}:product-list",
                        metadata={"products_discovered": len(discovered)},
                    ),
                    errors=[],
                )

            return DiscoveryPlan(
                discovered_entries=[],
                count_plan=CountPlan(
                    mode=CountMode.APPROXIMATE,
                    value=None,
                    method="bugzilla-product-payload-shape-unknown",
                    signature=f"{self.family_slug}:{instance.canonical_name}:product-list-none",
                    metadata={"payload_type": type(payload).__name__},
                ),
                errors=["unexpected payload format for /rest/product"],
            )
        except httpx.RequestError as exc:
            return DiscoveryPlan(
                discovered_entries=[],
                count_plan=CountPlan(
                    mode=CountMode.APPROXIMATE,
                    value=None,
                    method="bugzilla-discover-exception",
                    signature=f"{self.family_slug}:{instance.canonical_name}:product-list-error",
                    metadata={"error": str(exc)},
                ),
                errors=[str(exc)],
            )

    async def build_count_plan(self, entry: RegistryEntry) -> CountPlan:
        tracker_key = self._entry_key(entry)
        if not tracker_key:
            return CountPlan(
                mode=CountMode.APPROXIMATE,
                value=None,
                method="bugzilla-count-missing-key",
                signature=f"{entry.id}:missing-key",
            )

        base = self._api_base(entry.instance)
        endpoint = f"{base}/bug"
        params = {
            "product": tracker_key,
            "bug_status": self._closed_statuses(),
            "limit": 1,
            "offset": 0,
            "include_fields": "id",
        }
        try:
            response = await self.client.get(endpoint, params=params)
            if response.status_code >= 400:
                return CountPlan(
                    mode=CountMode.APPROXIMATE,
                    value=None,
                    method="bugzilla-count-unavailable",
                    signature=f"{entry.id}:count-http-error",
                    metadata={"status_code": response.status_code},
                )
            payload = response.json()
            total = payload.get("total") if isinstance(payload, dict) else None
            if total is None and isinstance(payload, dict):
                total = payload.get("total_matches")
            if isinstance(total, int):
                return CountPlan(
                    mode=CountMode.EXACT,
                    value=total,
                    method="bugzilla-rest-bug-closed-count",
                    signature=f"{entry.id}:closed-by-status",
                    metadata={"endpoint": endpoint},
                )
        except httpx.RequestError as exc:
            return CountPlan(
                mode=CountMode.APPROXIMATE,
                value=None,
                method="bugzilla-count-request-error",
                signature=f"{entry.id}:count-request-error",
                metadata={"error": str(exc)},
            )

        return CountPlan(
            mode=CountMode.APPROXIMATE,
            value=None,
            method="bugzilla-count-unknown",
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
        tracker_key = self._entry_key(entry)
        if not tracker_key:
            return IssueListPage(
                issues=[],
                error="tracker id missing",
                status_code=None,
                closed_filter_applied=(mode == "closed"),
            )

        base = self._api_base(entry.instance)
        per_page = max(1, min(int(page_size), 200))
        try:
            offset = int(cursor) if cursor is not None else 0
        except Exception:
            offset = 0
        endpoint = f"{base}/bug"

        if mode == "closed":
            selected_status = self._closed_statuses()
        else:
            selected_status = self._select_statuses(mode)

        params = {
            "product": tracker_key,
            "include_fields": (
                "id,alias,summary,description,status,resolution,"
                "priority,creator,assigned_to,whiteboard,creation_time,last_change_time,"
                "cf_last_closed"
            ),
            "limit": per_page,
            "offset": max(0, offset),
            "bug_status": selected_status,
            "order": "changeddate",
            "sort": "ASC",
        }

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

        bugs = payload.get("bugs")
        if not isinstance(bugs, list):
            return IssueListPage(
                issues=[],
                error="unexpected_payload_missing_bugs",
                status_code=response.status_code,
                request_url=endpoint,
                request_params=params,
                request_headers={},
                headers=dict(response.headers),
            )

        records: list[IssueRecord] = []
        for item in bugs:
            if not isinstance(item, dict):
                continue
            bug_id = item.get("id")
            if bug_id is None:
                continue
            bug_id = str(bug_id)
            tracker_issue_key = item.get("alias") or f"{tracker_key}#{bug_id}"

            created_at = _to_dt(item.get("creation_time"))
            updated_at = _to_dt(item.get("last_change_time"))
            closed_at = _to_dt(item.get("cf_last_closed")) or (
                updated_at
                if str(item.get("status", "")).strip().upper() in self._closed_statuses()
                else None
            )

            description = item.get("summary") or item.get("description") or ""
            summary = item.get("summary") or ""
            if len(summary) > 1024:
                summary = summary[:1020] + "..."

            reporter = item.get("creator")
            assignee = item.get("assigned_to")
            reporter_raw = _to_text(reporter.get("name") if isinstance(reporter, dict) else reporter)
            assignee_raw = _to_text(assignee.get("name") if isinstance(assignee, dict) else assignee)

            whiteboard = item.get("whiteboard")
            labels: list[str] = []
            if isinstance(whiteboard, str) and whiteboard:
                labels = [token.strip() for token in whiteboard.replace(",", " ").split() if token.strip()]
            elif isinstance(whiteboard, list):
                labels = [str(v).strip() for v in whiteboard if str(v).strip()]

            records.append(
                IssueRecord(
                    tracker_issue_id=bug_id,
                    tracker_issue_key=_to_text(tracker_issue_key),
                    title=_to_text(summary) or "",
                    body_raw=_to_text(description),
                    body_plaintext=_to_text(description),
                    issue_url=f"{self._public_base(entry.instance)}/show_bug.cgi?id={quote_plus(bug_id)}",
                    api_url=f"{base}/bug/{quote_plus(bug_id)}",
                    issue_type_raw="bug",
                    state_raw=_to_text(item.get("status")),
                    resolution_raw=_to_text(item.get("resolution")),
                    close_reason_raw=_to_text(item.get("resolution")),
                    created_at_tracker=created_at,
                    updated_at_tracker=updated_at,
                    closed_at=closed_at,
                    reporter_raw=reporter_raw,
                    assignee_raw=assignee_raw,
                    is_pull_request=False,
                    is_private_restricted=False,
                    labels=labels,
                    raw_payload=item,
                )
            )

        if sample_limit is not None:
            try:
                limit_int = int(sample_limit)
            except Exception:
                limit_int = None
            if limit_int is not None and limit_int >= 0 and len(records) > limit_int:
                records = records[:limit_int]
                next_cursor = None
            else:
                next_cursor = str(offset + len(records)) if len(records) >= per_page else None
        else:
            next_cursor = str(offset + len(records)) if len(records) >= per_page else None

        return IssueListPage(
            issues=records,
            next_cursor=next_cursor,
            next_page=offset + per_page if next_cursor is not None else None,
            next_params={"offset": offset + per_page},
            request_url=endpoint,
            request_params=params,
            request_headers={},
            status_code=response.status_code,
            request_body=payload,
            headers=dict(response.headers),
            closed_filter_applied=(mode == "closed"),
            closed_filter_mode="bug_status in closed_set" if mode == "closed" else "status not closed only",
        )
