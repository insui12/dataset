from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx

from gbtd_infra.adapters.base import CountMode, CountPlan, DiscoveryPlan, IssueListPage, IssueRecord, ProbeResult, TrackerAdapter
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


def _strip_unsafe(text: str | None) -> str | None:
    if text is None:
        return None
    return str(text).strip()


class GitHubIssuesAdapter(TrackerAdapter):
    family_slug = "github"
    supported_protocols = (ProtocolType.REST, ProtocolType.GRAPHQL)

    def _api_base(self, instance: TrackerInstance) -> str:
        return (instance.api_base_url or "https://api.github.com").rstrip("/")

    def _repo_identifier(self, entry: RegistryEntry) -> str:
        return entry.tracker_api_key or entry.tracker_native_id or entry.name

    def _auth_headers(self) -> dict[str, str]:
        token = self.config.github_token
        if not token:
            return {}
        return {"Authorization": f"Bearer {token}"}

    async def probe(self, family: TrackerFamily, instance: TrackerInstance, entry: RegistryEntry | None = None) -> ProbeResult:
        base = self._api_base(instance)
        endpoint = f"{base}/rate_limit"
        try:
            response = await self.client.get(endpoint, headers=self._auth_headers())
        except httpx.RequestError:
            return ProbeResult(
                family_slug=self.family_slug,
                instance=instance.canonical_name,
                protocol=ProtocolType.REST,
                supported=False,
                blocked=True,
                note="cannot reach GitHub API",
            )

        if response.status_code in {401, 403}:
            return ProbeResult(
                family_slug=self.family_slug,
                instance=instance.canonical_name,
                protocol=ProtocolType.REST,
                supported=True,
                blocked=response.status_code in {401, 403},
                auth_required=response.status_code == 401,
                count_supported=True,
                pagination="page",
                note="auth/visibility restriction by GitHub",
                details={"status_code": response.status_code},
            )

        if response.status_code >= 400:
            return ProbeResult(
                family_slug=self.family_slug,
                instance=instance.canonical_name,
                protocol=ProtocolType.REST,
                supported=False,
                blocked=True,
                note=f"non-success response: {response.status_code}",
                details={"status_code": response.status_code},
            )

        return ProbeResult(
            family_slug=self.family_slug,
            instance=instance.canonical_name,
            protocol=ProtocolType.REST,
            supported=True,
            count_supported=True,
            pagination="page",
            details={"status_code": response.status_code},
        )

    async def discover(self, family: TrackerFamily, instance: TrackerInstance) -> DiscoveryPlan:
        return DiscoveryPlan(
            discovered_entries=[],
            count_plan=CountPlan(
                mode=CountMode.APPROXIMATE,
                value=None,
                method="github-manifest-mode",
                signature=f"github:{instance.canonical_name}:no-auto-discovery",
                metadata={"reason": "manifest-exhaustive for mega-host"},
            ),
            errors=["No auto-discovery for mega-host without manifest entries"],
        )

    async def build_count_plan(self, entry: RegistryEntry) -> CountPlan:
        repo = self._repo_identifier(entry)
        if not repo:
            return CountPlan(
                mode=CountMode.APPROXIMATE,
                value=None,
                method="missing-repo-id",
                signature=f"{entry.id}:missing-repo",
            )

        query = f"repo:{repo} is:issue is:closed"
        base = self._api_base(entry.instance)
        try:
            response = await self.client.get(
                f"{base}/search/issues",
                headers=self._auth_headers(),
                params={"q": query, "per_page": 1},
            )
            if response.status_code >= 400:
                return CountPlan(
                    mode=CountMode.APPROXIMATE,
                    value=None,
                    method="github-search-fallback",
                    signature=f"{entry.id}:search-fallback",
                    metadata={"status": response.status_code},
                )
            payload = response.json()
            total = payload.get("total_count")
            if isinstance(total, int):
                return CountPlan(
                    mode=CountMode.APPROXIMATE,
                    value=total,
                    method="github-search-total_count",
                    signature=f"{entry.id}:state-closed",
                    metadata={"source": "search/issues"},
                )
        except httpx.RequestError:
            pass

        return CountPlan(
            mode=CountMode.APPROXIMATE,
            value=None,
            method="github-search-error",
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
        repo = self._repo_identifier(entry)
        if not repo:
            return IssueListPage(
                issues=[],
                error="repo identifier missing",
                status_code=None,
                closed_filter_applied=False,
            )

        base = self._api_base(entry.instance)
        page = int(cursor) if cursor is not None else 1
        per_page = max(1, min(int(page_size), 100))

        endpoint = f"{base}/repos/{quote(repo, safe='/')}/issues"
        params = {
            "state": "closed" if mode == "closed" else "all",
            "per_page": per_page,
            "page": page,
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

        status_code = response.status_code
        if status_code >= 400:
            return IssueListPage(
                issues=[],
                error=f"http_error:{status_code}",
                status_code=status_code,
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
                status_code=status_code,
                request_url=endpoint,
                request_params=params,
                request_headers=self._auth_headers(),
                headers=dict(response.headers),
            )

        records: list[IssueRecord] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            labels = []
            for label in item.get("labels") or []:
                if isinstance(label, dict):
                    label_name = label.get("name")
                else:
                    label_name = str(label)
                if label_name:
                    labels.append(str(label_name))

            closed_at = _to_dt(item.get("closed_at"))
            created_at = _to_dt(item.get("created_at"))
            updated_at = _to_dt(item.get("updated_at"))

            number = str(item.get("number")) if item.get("number") is not None else ""
            if not number:
                continue

            records.append(
                IssueRecord(
                    tracker_issue_id=number,
                    tracker_issue_key=item.get("title") and f"{repo}#{number}" or number,
                    title=item.get("title") or "",
                    body_raw=_strip_unsafe(item.get("body")),
                    body_plaintext=_strip_unsafe(item.get("body")),
                    issue_url=item.get("html_url") or "",
                    api_url=item.get("url") or "",
                    issue_type_raw="pull_request" if item.get("pull_request") else "issue",
                    state_raw=item.get("state"),
                    resolution_raw=item.get("state_reason"),
                    close_reason_raw=item.get("state_reason"),
                    created_at_tracker=created_at,
                    updated_at_tracker=updated_at,
                    closed_at=closed_at,
                    reporter_raw=(item.get("user") or {}).get("login") if isinstance(item.get("user"), dict) else None,
                    assignee_raw=(item.get("assignee") or {}).get("login") if isinstance(item.get("assignee"), dict) else None,
                    is_pull_request=bool(item.get("pull_request")),
                    is_private_restricted=False,
                    labels=labels,
                    raw_payload=item,
                )
            )

        next_cursor = str(page + 1) if len(payload) >= per_page else None
        limited_records = records
        if sample_limit is not None:
            remaining = max(0, sample_limit)
            if len(records) > remaining:
                limited_records = records[:remaining]
                next_cursor = None

        return IssueListPage(
            issues=limited_records,
            next_cursor=next_cursor,
            next_page=page + 1 if next_cursor else None,
            next_params={"page": page + 1},
            request_url=endpoint,
            request_params=params,
            request_headers=self._auth_headers(),
            status_code=status_code,
            headers=dict(response.headers),
            request_body=payload,
            closed_filter_applied=(mode == "closed"),
            closed_filter_mode="state=closed",
        )
