from __future__ import annotations

from gbtd_infra.adapters.base import CountPlan, DiscoveryPlan, ProbeResult, TrackerAdapter
from gbtd_infra.models import CountMode, ProtocolType, RegistryEntry, TrackerFamily, TrackerInstance


class PhabricatorAdapter(TrackerAdapter):
    family_slug = "phabricator"
    supported_protocols = (ProtocolType.JSON_RPC,)

    async def probe(self, family: TrackerFamily, instance: TrackerInstance, entry: RegistryEntry | None = None) -> ProbeResult:
        base = (instance.api_base_url or instance.base_url).rstrip("/")
        url = f"{base}/api/user.whoami"
        try:
            response = await self.client.get(url)
            if response.status_code == 200:
                return ProbeResult(
                    family_slug=self.family_slug,
                    instance=instance.canonical_name,
                    protocol=ProtocolType.JSON_RPC,
                    supported=True,
                    count_supported=False,
                    pagination="offset",
                )
        except Exception:
            return ProbeResult(
                family_slug=self.family_slug,
                instance=instance.canonical_name,
                protocol=ProtocolType.JSON_RPC,
                supported=False,
                blocked=True,
                note="official JSON-RPC endpoint unavailable",
            )
        return ProbeResult(
            family_slug=self.family_slug,
            instance=instance.canonical_name,
            protocol=ProtocolType.JSON_RPC,
            supported=True,
            count_supported=False,
            note="discover requires task-specific API calls",
        )

    async def discover(self, family: TrackerFamily, instance: TrackerInstance) -> DiscoveryPlan:
        return DiscoveryPlan(
            discovered_entries=[],
            count_plan=CountPlan(
                mode=CountMode.APPROXIMATE,
                value=None,
                method="maniphest_search",
                signature="phabricator-placeholder",
            ),
        )
