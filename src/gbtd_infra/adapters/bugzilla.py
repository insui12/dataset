from __future__ import annotations

from typing import Any

import httpx

from gbtd_infra.adapters.base import CountPlan, DiscoveryPlan, ProbeResult, TrackerAdapter
from gbtd_infra.models import CountMode, ProtocolType, RegistryEntry, TrackerFamily, TrackerInstance


class BugzillaAdapter(TrackerAdapter):
    """Bugzilla official protocol fallback chain: REST -> JSON-RPC -> XML-RPC."""

    family_slug = "bugzilla"
    supported_protocols = (
        ProtocolType.REST,
        ProtocolType.JSON_RPC,
        ProtocolType.XML_RPC,
    )

    async def probe(self, family: TrackerFamily, instance: TrackerInstance, entry: RegistryEntry | None = None) -> ProbeResult:
        base = (instance.api_base_url or instance.base_url).rstrip("/")
        checks: list[tuple[ProtocolType, str]] = [
            (ProtocolType.REST, f"{base}/rest/version"),
            (ProtocolType.JSON_RPC, f"{base}/jsonrpc.cgi"),
            (ProtocolType.XML_RPC, f"{base}/xmlrpc.cgi"),
        ]

        for protocol, endpoint in checks:
            try:
                response = await self.client.get(endpoint)
            except httpx.HTTPError:
                continue

            if response.status_code in {401, 403}:
                return ProbeResult(
                    family_slug=self.family_slug,
                    instance=instance.canonical_name,
                    protocol=protocol,
                    supported=True,
                    blocked=True,
                    auth_required=response.status_code == 401,
                    count_supported=protocol == ProtocolType.REST,
                    pagination="offset",
                    note="authentication required or blocked",
                )
            if response.status_code == 429:
                return ProbeResult(
                    family_slug=self.family_slug,
                    instance=instance.canonical_name,
                    protocol=protocol,
                    supported=False,
                    blocked=True,
                    auth_required=False,
                    note="rate-limited",
                )
            if response.status_code >= 400:
                continue

            return ProbeResult(
                family_slug=self.family_slug,
                instance=instance.canonical_name,
                protocol=protocol,
                supported=True,
                blocked=False,
                count_supported=True,
                pagination="offset",
                note="probe success",
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
        # placeholder: real implementation should discover products/components via official endpoints.
        return DiscoveryPlan(
            discovered_entries=[
                {
                    "kind": "product",
                    "name": "Bugzilla Product Placeholder",
                    "tracker_id": "placeholder",
                    "note": "replace with official product discovery in phase-2",
                }
            ],
            count_plan=CountPlan(
                mode=CountMode.ENUMERATED,
                value=None,
                method="bugzilla-product-listing-probe",
                signature="bugzilla-product-count-enum",
                metadata={"note": "placeholder"},
            ),
        )
