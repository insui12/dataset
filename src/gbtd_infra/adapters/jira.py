from __future__ import annotations

from gbtd_infra.adapters.base import CountPlan, DiscoveryPlan, ProbeResult, TrackerAdapter
from gbtd_infra.models import CountMode, ProtocolType, RegistryEntry, TrackerFamily, TrackerInstance


class JiraAdapter(TrackerAdapter):
    family_slug = "jira"
    supported_protocols = (ProtocolType.REST,)

    async def probe(self, family: TrackerFamily, instance: TrackerInstance, entry: RegistryEntry | None = None) -> ProbeResult:
        base = (instance.api_base_url or instance.base_url).rstrip("/")
        url = f"{base}/rest/api/2/serverInfo"
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
                note="official REST serverInfo blocked/unreachable",
            )
        return ProbeResult(
            family_slug=self.family_slug,
            instance=instance.canonical_name,
            protocol=ProtocolType.REST,
            supported=True,
            count_supported=True,
            pagination="cursor",
        )

    async def discover(self, family: TrackerFamily, instance: TrackerInstance) -> DiscoveryPlan:
        return DiscoveryPlan(
            discovered_entries=[],
            count_plan=CountPlan(
                mode=CountMode.APPROXIMATE,
                value=None,
                method="manifest_exhaustive_or_instance_projects",
                signature=f"jira-{instance.canonical_name}",
            ),
        )
