from __future__ import annotations

from gbtd_infra.adapters.base import CountPlan, DiscoveryPlan, ProbeResult, TrackerAdapter
from gbtd_infra.models import CountMode, ProtocolType, RegistryEntry, TrackerFamily, TrackerInstance


class GoogleIssueTrackerAdapter(TrackerAdapter):
    family_slug = "google_issues"
    supported_protocols = (ProtocolType.REST,)

    async def probe(self, family: TrackerFamily, instance: TrackerInstance, entry: RegistryEntry | None = None) -> ProbeResult:
        base = (instance.api_base_url or instance.base_url).rstrip("/")
        if not base:
            return ProbeResult(
                family_slug=self.family_slug,
                instance=instance.canonical_name,
                protocol=ProtocolType.REST,
                supported=False,
                blocked=True,
                note="no base url",
            )
        try:
            response = await self.client.get(f"{base}/issues")
            if response.is_success:
                return ProbeResult(
                    family_slug=self.family_slug,
                    instance=instance.canonical_name,
                    protocol=ProtocolType.REST,
                    supported=True,
                    count_supported=True,
                    pagination="cursor",
                )
        except Exception:
            pass
        return ProbeResult(
            family_slug=self.family_slug,
            instance=instance.canonical_name,
            protocol=ProtocolType.REST,
            supported=False,
            blocked=True,
            note="blocked/unreachable",
        )

    async def discover(self, family: TrackerFamily, instance: TrackerInstance) -> DiscoveryPlan:
        return DiscoveryPlan(
            discovered_entries=[],
            count_plan=CountPlan(
                mode=CountMode.APPROXIMATE,
                value=None,
                method="google_issues_api",
                signature="google-issues-placeholder",
            ),
            errors=["family is special; confirm legal API endpoint availability per product."],
        )
