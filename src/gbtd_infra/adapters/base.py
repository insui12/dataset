from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from gbtd_infra.clients.http import PoliteHttpClient
from gbtd_infra.config import AppConfig
from gbtd_infra.models import CountMode, JobType, ProtocolType, RegistryEntry, TrackerFamily, TrackerInstance


class ProbeResult(BaseModel):
    family_slug: str
    instance: str
    protocol: ProtocolType
    supported: bool
    blocked: bool = False
    auth_required: bool = False
    count_supported: bool = False
    pagination: str | None = None
    note: str | None = None
    details: dict[str, Any] | None = None


class CountPlan(BaseModel):
    mode: CountMode
    value: int | None
    method: str
    signature: str
    count_error: float | None = None
    metadata: dict[str, Any] | None = None


class DiscoveryPlan(BaseModel):
    discovered_entries: list[dict[str, Any]] = Field(default_factory=list)
    count_plan: CountPlan | None = None
    errors: list[str] = Field(default_factory=list)


class JobPlan(BaseModel):
    jobs: list[dict[str, Any]] = Field(default_factory=list)
    notes: dict[str, Any] | None = None


class CapabilityError(RuntimeError):
    pass


class ClosedAssessment(BaseModel):
    is_closed: bool
    needs_review: bool
    method: str
    reason: str


class IssueRecord(BaseModel):
    tracker_issue_id: str
    tracker_issue_key: str | None = None
    title: str
    body_raw: str | None = None
    body_plaintext: str | None = None
    issue_url: str
    api_url: str
    issue_type_raw: str | None = None
    state_raw: str | None = None
    resolution_raw: str | None = None
    close_reason_raw: str | None = None
    created_at_tracker: datetime | None = None
    updated_at_tracker: datetime | None = None
    closed_at: datetime | None = None
    reporter_raw: str | None = None
    assignee_raw: str | None = None
    is_pull_request: bool = False
    is_private_restricted: bool = False
    labels: list[str] = Field(default_factory=list)
    raw_payload: dict[str, Any] | list[Any] | None = None


class IssueListPage(BaseModel):
    issues: list[IssueRecord] = Field(default_factory=list)
    next_cursor: str | None = None
    next_page: int | None = None
    next_params: dict[str, Any] | None = None
    request_url: str | None = None
    request_params: dict[str, Any] | None = None
    request_headers: dict[str, str] | None = None
    status_code: int | None = None
    request_body: dict[str, Any] | list[Any] | None = None
    headers: dict[str, str] | None = None
    closed_filter_applied: bool = True
    closed_filter_mode: str | None = None
    error: str | None = None


def _normalize_state_token(value: str | None) -> str:
    return (value or "").strip().lower().replace(" ", "")


def infer_closed_state(
    *,
    state_raw: str | None,
    resolution_raw: str | None,
    close_reason_raw: str | None,
    closed_at: datetime | None,
    closed_filter_applied: bool,
    closed_filter_mode: str | None = None,
) -> ClosedAssessment:
    if closed_filter_applied:
        return ClosedAssessment(
            is_closed=True,
            needs_review=False,
            method="closed_filter",
            reason=closed_filter_mode or "provider_closed_filter",
        )

    state = _normalize_state_token(state_raw)
    resolution = _normalize_state_token(resolution_raw)
    reason = _normalize_state_token(close_reason_raw)

    closed_state_values = {
        "closed",
        "resolved",
        "verified",
        "fixreview",
        "fixed",
        "done",
        "implemented",
        "completed",
        "complete",
        "wontfix",
        "duplicate",
    }
    closed_resolution_values = {
        "fixed",
        "wontfix",
        "resolved",
        "verified",
        "closed",
        "implemented",
        "done",
        "notabug",
        "bydesign",
    }
    closed_reason_values = {
        "wontfix",
        "resolved",
        "verified",
        "duplicate",
        "fixed",
        "implemented",
        "bydesign",
        "completed",
        "done",
        "notabug",
    }
    open_state_values = {
        "open",
        "new",
        "reopened",
        "assigned",
        "inprogress",
        "inprogressing",
        "pending",
        "needinfo",
        "backlog",
    }

    if closed_at is not None:
        return ClosedAssessment(
            is_closed=True,
            needs_review=False,
            method="closed_at_present",
            reason="closed_at is present",
        )
    if state in closed_state_values:
        return ClosedAssessment(is_closed=True, needs_review=False, method="state", reason=f"state={state_raw}")
    if resolution and resolution in closed_resolution_values:
        return ClosedAssessment(
            is_closed=True,
            needs_review=False,
            method="resolution",
            reason=f"resolution={resolution_raw}",
        )
    if reason and reason in closed_reason_values:
        return ClosedAssessment(
            is_closed=True,
            needs_review=False,
            method="close_reason",
            reason=f"close_reason={close_reason_raw}",
        )
    if state in open_state_values:
        return ClosedAssessment(is_closed=False, needs_review=False, method="state", reason=f"state={state_raw}")

    return ClosedAssessment(
        is_closed=False,
        needs_review=True,
        method="heuristic_fallback",
        reason="ambiguous status fields",
    )


class TrackerAdapter(ABC):
    """Family adapter contract using only official APIs/protocols."""

    family_slug: str = "generic"
    supported_protocols: tuple[ProtocolType, ...] = (ProtocolType.REST,)

    def __init__(self, session_factory, client: PoliteHttpClient, config: AppConfig | None = None):
        self.session_factory = session_factory
        self.client = client
        self.config = config or AppConfig()

    @abstractmethod
    async def probe(
        self,
        family: TrackerFamily,
        instance: TrackerInstance,
        entry: RegistryEntry | None = None,
    ) -> ProbeResult:
        raise NotImplementedError

    @abstractmethod
    async def discover(self, family: TrackerFamily, instance: TrackerInstance) -> DiscoveryPlan:
        raise NotImplementedError

    async def list_issues(
        self,
        entry: RegistryEntry,
        *,
        cursor: str | int | None = None,
        page_size: int = 100,
        mode: str = "closed",
        sample_limit: int | None = None,
    ) -> IssueListPage:
        raise CapabilityError(f"list_issues not implemented for {self.family_slug}")

    async def build_count_plan(self, entry: RegistryEntry) -> CountPlan:
        return CountPlan(
            mode=CountMode.APPROXIMATE,
            value=None,
            method="not_implemented",
            signature=f"{entry.id}:closed=true",
        )

    def _auth_headers(self, token: str | None) -> dict[str, str]:
        if not token:
            return {}
        return {"Authorization": f"token {token}"}

    def _job_seed_page(self, entry: RegistryEntry, *, mode: str = "closed", sample_size: int | None = None) -> dict[str, Any]:
        return {
            "registry_entry_id": entry.id,
            "mode": mode,
            "cursor": None,
            "page": 1,
            "sample_limit": sample_size,
            "sample_collected": 0,
        }

    async def seed_jobs(self, entry: RegistryEntry, mode: str = "closed") -> list[dict[str, Any]]:
        return [
            {
                "job_type": JobType.list_page_fetch.value,
                "payload": {
                    "registry_entry_id": entry.id,
                    "mode": mode,
                },
            }
        ]
