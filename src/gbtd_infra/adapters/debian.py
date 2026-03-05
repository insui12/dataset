from __future__ import annotations

from gbtd_infra.adapters.base import CountPlan, DiscoveryPlan, ProbeResult, TrackerAdapter
from gbtd_infra.models import CountMode, ProtocolType, RegistryEntry, TrackerFamily, TrackerInstance


class DebianBTSAdapter(TrackerAdapter):
    family_slug = "debian_bts"
    supported_protocols = (ProtocolType.SOAP,)

    async def probe(self, family: TrackerFamily, instance: TrackerInstance, entry: RegistryEntry | None = None) -> ProbeResult:
        # Debian BTS historically exposes SOAP/XML-ish endpoints; this remains versioned and explicit.
        base = (instance.api_base_url or instance.base_url).rstrip("/")
        url = f"{base}/cgi-bin/pkgreport.cgi"
        try:
            response = await self.client.get(url)
            if response.status_code == 200:
                return ProbeResult(
                    family_slug=self.family_slug,
                    instance=instance.canonical_name,
                    protocol=ProtocolType.SOAP,
                    supported=True,
                    count_supported=True,
                    pagination="offset",
                )
        except Exception:
            return ProbeResult(
                family_slug=self.family_slug,
                instance=instance.canonical_name,
                protocol=ProtocolType.SOAP,
                supported=False,
                blocked=True,
                note="Debian BTS official API endpoint blocked/unreachable",
            )
        return ProbeResult(
            family_slug=self.family_slug,
            instance=instance.canonical_name,
            protocol=ProtocolType.SOAP,
            supported=True,
            count_supported=False,
            note="non-standard payload, parser required",
        )

    async def discover(self, family: TrackerFamily, instance: TrackerInstance) -> DiscoveryPlan:
        return DiscoveryPlan(
            discovered_entries=[],
            count_plan=CountPlan(
                mode=CountMode.ENUMERATED,
                value=None,
                method="legacy_enumeration",
                signature="debian-bts-packages",
            ),
        )
