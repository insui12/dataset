from __future__ import annotations

from typing import Any

import httpx

from gbtd_infra.adapters.base import CountPlan, DiscoveryPlan, ProbeResult, TrackerAdapter
from gbtd_infra.models import CountMode, ProtocolType, RegistryEntry, TrackerFamily, TrackerInstance


class GitHubIssuesAdapter(TrackerAdapter):
    family_slug = "github"
    supported_protocols = (ProtocolType.REST, ProtocolType.GRAPHQL)

    async def probe(self, family: TrackerFamily, instance: TrackerInstance, entry: RegistryEntry | None = None) -> ProbeResult:
        base = (instance.api_base_url or "https://api.github.com").rstrip("/")
        for protocol, path in [
            (ProtocolType.REST, "/rate_limit"),
            (ProtocolType.GRAPHQL, "/graphql"),
        ]:
            try:
                response = await self.client.get(f"{base}{path}")
            except httpx.HTTPStatusError as exc:
                if exc.response is not None and exc.response.status_code in {401, 403, 429}:
                    return ProbeResult(
                        family_slug=self.family_slug,
                        instance=instance.canonical_name,
                        protocol=protocol,
                        supported=False,
                        blocked=True,
                        auth_required=exc.response.status_code == 401,
                        note="access blocked or auth required",
                    )
                continue
            if response.status_code in {401, 403, 404}:
                return ProbeResult(
                    family_slug=self.family_slug,
                    instance=instance.canonical_name,
                    protocol=protocol,
                    supported=True,
                    blocked=response.status_code in {401, 403},
                    auth_required=response.status_code == 401,
                )
            return ProbeResult(
                family_slug=self.family_slug,
                instance=instance.canonical_name,
                protocol=protocol,
                supported=True,
                count_supported=True,
                pagination="cursor",
                details={"x": response.status_code},
            )

        return ProbeResult(
            family_slug=self.family_slug,
            instance=instance.canonical_name,
            protocol=ProtocolType.UNKNOWN,
            supported=False,
            blocked=True,
            note="cannot reach official GitHub API",
        )

    async def discover(self, family: TrackerFamily, instance: TrackerInstance) -> DiscoveryPlan:
        # Real discovery depends on registry manifest for GitHub mega-host and does not auto-scan all users.
        return DiscoveryPlan(
            discovered_entries=[],
            count_plan=CountPlan(
                mode=CountMode.EXACT,
                value=None,
                method="manifest_exhaustive_no_auto_discovery",
                signature="github-manifest-mode",
            ),
            errors=["no auto discovery for mega-host unless manifest provides repos"],
        )
