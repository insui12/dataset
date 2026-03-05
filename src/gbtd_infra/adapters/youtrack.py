from __future__ import annotations

from gbtd_infra.adapters.base import CountPlan, DiscoveryPlan, ProbeResult, TrackerAdapter
from gbtd_infra.models import CountMode, ProtocolType, RegistryEntry, TrackerFamily, TrackerInstance


class YouTrackAdapter(TrackerAdapter):
    family_slug = "youtrack"
    supported_protocols = (ProtocolType.REST,)

    async def probe(self, family: TrackerFamily, instance: TrackerInstance, entry: RegistryEntry | None = None) -> ProbeResult:
        base = (instance.api_base_url or instance.base_url).rstrip("/")
        url = f"{base}/api/admin/info"
        try:
            response = await self.client.get(url)
            if response.status_code in {401, 403}:
                return ProbeResult(
                    family_slug=self.family_slug,
                    instance=instance.canonical_name,
                    protocol=ProtocolType.REST,
                    supported=True,
                    blocked=True,
                    auth_required=response.status_code == 401,
                    note="auth required",
                )
            response.raise_for_status()
        except Exception:
            return ProbeResult(
                family_slug=self.family_slug,
                instance=instance.canonical_name,
                protocol=ProtocolType.REST,
                supported=False,
                blocked=True,
                note="YouTrack REST API unavailable",
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
                method="projects_api",
                signature="youtrack-projects",
            ),
        )
