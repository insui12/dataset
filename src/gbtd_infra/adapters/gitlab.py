from __future__ import annotations

from gbtd_infra.adapters.base import CountPlan, DiscoveryPlan, ProbeResult, TrackerAdapter
from gbtd_infra.models import CountMode, ProtocolType, RegistryEntry, TrackerFamily, TrackerInstance


class GitLabIssuesAdapter(TrackerAdapter):
    family_slug = "gitlab"
    supported_protocols = (ProtocolType.REST,)

    async def probe(self, family: TrackerFamily, instance: TrackerInstance, entry: RegistryEntry | None = None) -> ProbeResult:
        base = (instance.api_base_url or "https://gitlab.com/api/v4").rstrip("/")
        try:
            response = await self.client.get(f"{base}/version")
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
            if response.status_code == 429:
                return ProbeResult(
                    family_slug=self.family_slug,
                    instance=instance.canonical_name,
                    protocol=ProtocolType.REST,
                    supported=False,
                    blocked=True,
                    note="rate limited",
                )
            response.raise_for_status()
        except Exception:
            return ProbeResult(
                family_slug=self.family_slug,
                instance=instance.canonical_name,
                protocol=ProtocolType.REST,
                supported=False,
                blocked=True,
                note="official REST /version not reachable",
            )

        return ProbeResult(
            family_slug=self.family_slug,
            instance=instance.canonical_name,
            protocol=ProtocolType.REST,
            supported=True,
            count_supported=True,
            pagination="keyset",
        )

    async def discover(self, family: TrackerFamily, instance: TrackerInstance) -> DiscoveryPlan:
        # For mega-host, keep curated-manifest mode explicit.
        return DiscoveryPlan(
            discovered_entries=[],
            count_plan=CountPlan(
                mode=CountMode.APPROXIMATE,
                value=None,
                method="manifest_exhaustive",
                signature="gitlab-manifest-mode",
                metadata={"instance": instance.canonical_name},
            ),
        )
