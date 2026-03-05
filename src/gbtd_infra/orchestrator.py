from __future__ import annotations

from datetime import datetime, timezone
import asyncio

from gbtd_infra.adapter_registry import adapter_for_family
from gbtd_infra.adapters.base import CountPlan, ProbeResult, TrackerAdapter
from gbtd_infra.clients.http import PoliteHttpClient
from gbtd_infra.config import AppConfig
from gbtd_infra.db import build_session_factory
from gbtd_infra.models import (
    BlockReason,
    CapabilityProbe,
    CollectionJob,
    CountSnapshot,
    JobType,
    RegistryEntry,
    TrackerFamily,
    TrackerInstance,
)
from gbtd_infra.scheduler.lease import JobScheduler


class Orchestrator:
    def __init__(self, config: AppConfig):
        self.config = config
        self.session_factory = build_session_factory(config)
        self.http = PoliteHttpClient(config)
        self.scheduler = JobScheduler(self.session_factory, config.runner_id, config.lease_seconds)

    def _make_adapter(self, job_family_slug: str):
        adapter_cls = adapter_for_family(job_family_slug)
        if adapter_cls is None:
            return None
        return adapter_cls(self.session_factory, self.http)

    async def claim_and_run_once(self):
        jobs = self.scheduler.claim_job(batch=8)
        if not jobs:
            return 0
        for job in jobs:
            try:
                await self._process_job(job)
            except Exception as exc:
                self.scheduler.fail_job(job.id, repr(exc), retry_delay_seconds=60)
            else:
                self.scheduler.complete_job(job.id)
        return len(jobs)

    async def _process_job(self, job: CollectionJob) -> None:
        session = self.session_factory()
        try:
            family = session.get(TrackerFamily, job.family_id)
            instance = session.get(TrackerInstance, job.instance_id) if job.instance_id else None
            entry = session.get(RegistryEntry, job.registry_entry_id) if job.registry_entry_id else None

            if not family or not instance:
                return

            adapter: TrackerAdapter | None = self._make_adapter(family.slug)
            if adapter is None:
                self.scheduler.fail_job(job.id, f"no adapter for family={family.slug}", retry_delay_seconds=60)
                return

            if job.job_type == JobType.capability_probe:
                result = await adapter.probe(family, instance, entry)
                self._upsert_probe(session, family, instance, entry, result)

            elif job.job_type == JobType.count_snapshot:
                if entry is None:
                    return
                count_plan = await adapter.build_count_plan(entry)
                self._upsert_count(session, entry, count_plan)

            elif job.job_type == JobType.list_page_fetch:
                if entry is None:
                    return
                # Placeholder for real page walk:
                session.add(
                    CollectionJob(
                        job_type=JobType.issue_detail_fetch,
                        family_id=family.id,
                        instance_id=instance.id,
                        registry_entry_id=entry.id,
                        payload={"entry_id": entry.id, "placeholder": True},
                        priority=80,
                    )
                )

            elif job.job_type == JobType.issue_detail_fetch:
                if entry is None:
                    return
                for jtype in (JobType.comments_fetch, JobType.attachments_fetch):
                    session.add(
                        CollectionJob(
                            job_type=jtype,
                            family_id=family.id,
                            instance_id=instance.id,
                            registry_entry_id=entry.id,
                            payload={"entry_id": entry.id},
                            priority=60,
                        )
                    )

            elif job.job_type in (JobType.comments_fetch, JobType.attachments_fetch):
                pass

            elif job.job_type == JobType.incremental_sync:
                # placeholder for future manifest-driven incremental mode
                pass

            session.commit()
        finally:
            session.close()

    def _upsert_probe(self, session, family, instance, entry, result: ProbeResult) -> None:
        session.add(
            CapabilityProbe(
                family_id=family.id,
                instance_id=instance.id if instance else None,
                registry_entry_id=entry.id if entry else None,
                probe_scope="entry" if entry else "instance",
                protocol=result.protocol,
                protocol_supported=result.supported,
                pagination_scheme=result.pagination,
                count_supported=result.count_supported,
                auth_required=result.auth_required,
                block_reason=BlockReason.blocked if result.blocked else BlockReason.ok,
                blocked_fields={"note": result.note, "details": result.details},
            )
        )

    def _upsert_count(self, session, entry: RegistryEntry, plan: CountPlan) -> None:
        session.add(
            CountSnapshot(
                registry_entry_id=entry.id,
                query_signature=plan.signature,
                count_mode=plan.mode,
                count_method=plan.method,
                count_value=plan.value,
                notes={"metadata": plan.metadata, "count_error": plan.count_error},
                manifest_version_id=entry.manifest_version_id,
            )
        )

    async def reclaim_timed_out(self, now: datetime | None = None) -> int:
        now = now or datetime.now(timezone.utc)
        session = self.session_factory()
        try:
            count = self.scheduler.reclaim_timed_out_jobs(session, now)
            session.commit()
            return count
        finally:
            session.close()

    async def run_forever(self):
        while True:
            claimed = await self.claim_and_run_once()
            if claimed == 0:
                await self.reclaim_timed_out()
                await asyncio.sleep(5)
