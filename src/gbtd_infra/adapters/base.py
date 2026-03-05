from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from gbtd_infra.clients.http import PoliteHttpClient
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


class TrackerAdapter(ABC):
    """Family adapter contract using only official APIs/protocols."""

    family_slug: str = "generic"
    supported_protocols: tuple[ProtocolType, ...] = (ProtocolType.REST,)

    def __init__(self, session_factory, client: PoliteHttpClient):
        self.session_factory = session_factory
        self.client = client

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

    async def build_count_plan(self, entry: RegistryEntry) -> CountPlan:
        return CountPlan(
            mode=CountMode.APPROXIMATE,
            value=None,
            method="not_implemented",
            signature=f"{entry.id}:closed=true",
        )

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
