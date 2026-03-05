from __future__ import annotations

from gbtd_infra.adapters.base import CountPlan, DiscoveryPlan, ProbeResult, TrackerAdapter
from gbtd_infra.models import CountMode, ProtocolType, RegistryEntry, TrackerFamily, TrackerInstance


class RedmineAdapter(TrackerAdapter):
    family_slug = "redmine"
    supported_protocols = (ProtocolType.REST,)

    async def probe(self, family: TrackerFamily, instance: TrackerInstance, entry: RegistryEntry | None = None) -> ProbeResult:
        base = (instance.api_base_url or instance.base_url).rstrip("/")
        url = f"{base}/projects.json"
        try:
            response = await self.client.get(url)
            response.raise_for_status()
        except Exception:
            return ProbeResult(
                family_slug=self.family_slug,
                instance=instance.canonical_name,
                protocol=ProtocolType.REST,
                supported=False,
                blocked=True,
                note="Redmine REST API unavailable",
            )
        return ProbeResult(
            family_slug=self.family_slug,
            instance=instance.canonical_name,
            protocol=ProtocolType.REST,
            supported=True,
            count_supported=True,
            pagination="offset_limit",
        )

    async def discover(self, family: TrackerFamily, instance: TrackerInstance) -> DiscoveryPlan:
        return DiscoveryPlan(
            discovered_entries=[],
            count_plan=CountPlan(
                mode=CountMode.APPROXIMATE,
                value=None,
                method="projects.json+offset",
                signature=f"redmine-{instance.canonical_name}-projects",
            ),
        )
