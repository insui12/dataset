from __future__ import annotations

from datetime import datetime, timezone
import asyncio
import hashlib
import json
import logging
import time
import uuid

from sqlalchemy import select

from gbtd_infra.adapter_registry import adapter_for_family
from gbtd_infra.adapters.base import (
    ClosedAssessment,
    IssueListPage,
    ProbeResult,
    TrackerAdapter,
    infer_closed_state,
)
from gbtd_infra.clients.http import PoliteHttpClient
from gbtd_infra.config import AppConfig
from gbtd_infra.db import build_session_factory
from gbtd_infra.models import (
    BlockReason as BlockReasonEnum,
    CapabilityProbe,
    CollectionError,
    CollectionJob,
    CountMode,
    CountSnapshot,
    Issue,
    JobType,
    RawApiPage,
    RawApiPayload,
    RegistryEntry,
    TrackerFamily,
    TrackerInstance,
)
from gbtd_infra.scheduler.lease import JobScheduler


class Orchestrator:
    def __init__(
        self,
        config: AppConfig,
        show_progress: bool = True,
        family_ids: list[int] | None = None,
        instance_ids: list[int] | None = None,
        entry_ids: list[int] | None = None,
    ):
        self.config = config
        self.session_factory = build_session_factory(config)
        self.http = PoliteHttpClient(config)
        self.scheduler = JobScheduler(self.session_factory, config.runner_id, config.lease_seconds)
        self.show_progress = show_progress
        self.family_ids = family_ids
        self.instance_ids = instance_ids
        self.entry_ids = entry_ids
        self._logger = logging.getLogger("gbtd.orchestrator")
        if show_progress and not logging.getLogger().handlers:
            logging.basicConfig(
                level=logging.INFO,
                format="%(asctime)s [%(levelname)s] %(message)s",
            )

    def _make_adapter(self, job_family_slug: str):
        adapter_cls = adapter_for_family(job_family_slug)
        if adapter_cls is None:
            return None
        return adapter_cls(self.session_factory, self.http, self.config)

    def _log(self, msg: str, level: int = logging.INFO) -> None:
        if not self.show_progress:
            return
        self._logger.log(level, msg)

    async def claim_and_run_once(self) -> int:
        cycle_start = time.perf_counter()
        jobs = self.scheduler.claim_job(
            batch=self.config.worker_concurrency,
            family_ids=self.family_ids,
            instance_ids=self.instance_ids,
            entry_ids=self.entry_ids,
        )
        if not jobs:
            return 0

        cycle_total = len(jobs)
        cycle_ok = 0
        cycle_failed = 0
        for job in jobs:
            session = self.session_factory()
            try:
                family = session.get(TrackerFamily, job.family_id)
                instance = session.get(TrackerInstance, job.instance_id) if job.instance_id else None
                entry = session.get(RegistryEntry, job.registry_entry_id) if job.registry_entry_id else None
                label = self._format_job_label(family, instance, entry, job)
                self._log(f"{label} START type={job.job_type.value} id={job.id}")
                await self._process_job(session, job)
                self.scheduler.complete_job(job.id)
                self._log(f"{label} DONE type={job.job_type.value} id={job.id}")
                cycle_ok += 1
                session.commit()
            except Exception as exc:
                family = session.get(TrackerFamily, job.family_id)
                instance = session.get(TrackerInstance, job.instance_id) if job.instance_id else None
                entry = session.get(RegistryEntry, job.registry_entry_id) if job.registry_entry_id else None
                label = self._format_job_label(family, instance, entry, job)
                self._log(f"{label} FAIL type={job.job_type.value} id={job.id} reason={exc}", logging.WARNING)
                session.rollback()
                self._record_collection_error(
                    session=session,
                    job=job,
                    error=exc,
                    family=session.get(TrackerFamily, job.family_id),
                    instance=session.get(TrackerInstance, job.instance_id) if job.instance_id else None,
                    entry=session.get(RegistryEntry, job.registry_entry_id) if job.registry_entry_id else None,
                )
                session.commit()
                self.scheduler.fail_job(job.id, repr(exc), retry_delay_seconds=120)
                cycle_failed += 1
            finally:
                session.close()

        elapsed_ms = (time.perf_counter() - cycle_start) * 1000
        self._log(
            (
                f"cycle end: claimed={cycle_total} ok={cycle_ok} failed={cycle_failed}"
                f" filters="
                f"families={len(self.family_ids) if self.family_ids is not None else 'all'}"
                f", instances={len(self.instance_ids) if self.instance_ids is not None else 'all'}"
                f", entries={len(self.entry_ids) if self.entry_ids is not None else 'all'}"
                f", elapsed_ms={elapsed_ms:.1f}"
            )
        )
        return len(jobs)

    @staticmethod
    def _format_job_label(
        family: TrackerFamily | None,
        instance: TrackerInstance | None,
        entry: RegistryEntry | None,
        job: CollectionJob | None = None,
    ) -> str:
        parts = [
            f"family={family.slug if family else 'unknown'}",
            f"instance={instance.canonical_name if instance else 'unknown'}",
            f"entry={entry.name if entry else 'n/a'}",
            f"job={job.id if job else 'n/a'}",
        ]
        return " ".join(parts)

    async def _process_job(self, session, job: CollectionJob) -> None:
        family = session.get(TrackerFamily, job.family_id)
        instance = session.get(TrackerInstance, job.instance_id) if job.instance_id else None
        entry = session.get(RegistryEntry, job.registry_entry_id) if job.registry_entry_id else None

        if not family:
            raise RuntimeError(f"missing family for job {job.id}")

        if job.instance_id and not instance:
            raise RuntimeError(f"missing instance for job {job.id}")

        adapter: TrackerAdapter | None = self._make_adapter(family.slug)
        if adapter is None:
            raise RuntimeError(f"no adapter for family={family.slug}")

        payload = job.payload or {}

        if job.job_type == JobType.capability_probe:
            result = await adapter.probe(family, instance, entry)
            self._upsert_probe(session, family, instance, entry, result)
            return

        if job.job_type == JobType.count_snapshot:
            if entry is None:
                raise RuntimeError(f"count_snapshot requires registry_entry_id for job {job.id}")
            plan = await adapter.build_count_plan(entry)
            self._upsert_count(session, entry, plan)
            return

        if job.job_type == JobType.list_page_fetch:
            if instance is None or entry is None:
                raise RuntimeError(f"list_page_fetch requires family+instance+entry for job {job.id}")
            page_stats = await self._process_list_page(session, family, instance, entry, payload, job)
            self._log(
                (
                    f"{self._format_job_label(family, instance, entry, job)} "
                    f"page={payload.get('page', 1)} items={page_stats['items']} "
                    f"issues={page_stats['issues']} next_cursor={page_stats['next_cursor']}"
                )
            )
            return

        if job.job_type == JobType.issue_detail_fetch:
            return

        if job.job_type in (JobType.comments_fetch, JobType.attachments_fetch):
            return

        raise RuntimeError(f"unsupported job_type={job.job_type}")

    async def _process_list_page(
        self,
        session,
        family: TrackerFamily,
        instance: TrackerInstance,
        entry: RegistryEntry,
        payload: dict,
        job: CollectionJob,
    ) -> None:
        mode = payload.get("mode", "closed")
        cursor = payload.get("cursor")
        page_size = payload.get("page_size", 100)
        sample_limit = payload.get("sample_limit")
        sample_collected = payload.get("sample_collected", 0)

        try:
            page_size = max(1, min(int(page_size), 100))
        except Exception:
            page_size = 100

        if sample_limit is not None:
            try:
                sample_limit = int(sample_limit)
            except Exception:
                sample_limit = None

        if sample_limit is not None and sample_limit <= 0:
            sample_limit = None

        try:
            sample_collected = int(sample_collected)
        except Exception:
            sample_collected = 0

        adapter = self._make_adapter(family.slug)
        if adapter is None:
            raise RuntimeError(f"no adapter for family={family.slug}")

        # avoid scanning beyond requested sample limit
        effective_sample = None
        if sample_limit is not None:
            effective_sample = max(0, sample_limit - sample_collected)
            if effective_sample <= 0:
                self._log(f"{self._format_job_label(family, instance, entry)} sample limit reached: {sample_collected}")
                return

        page_no = payload.get("page", 1)
        self._log(
            f"{self._format_job_label(family, instance, entry)} page_fetch_start page={page_no} "
            f"cursor={cursor or 'init'} limit={effective_sample if sample_limit is not None else 'unlimited'}"
        )
        page = await adapter.list_issues(
            entry,
            cursor=cursor,
            page_size=page_size,
            mode=mode,
            sample_limit=effective_sample,
        )

        if page.error:
            self._log(f"list_issues error: family={family.slug} instance={instance.canonical_name} entry={entry.name} {page.error}", logging.WARNING)
            raise RuntimeError(page.error)

        raw_payload_id = self._persist_raw_payload(session, family, instance, entry, page)
        self._persist_raw_page(session, entry.id, page, raw_payload_id, payload.get("page", 1))
        self._log(
            (
                f"{self._format_job_label(family, instance, entry)} raw_page_stored payload_id={raw_payload_id}"
                f" url={page.request_url or ''} status={page.status_code or 0}"
            )
        )

        issue_rows = 0
        closed_rows = 0
        needs_review_rows = 0
        for issue_record in page.issues:
            if effective_sample is not None and sample_collected >= sample_limit:
                break
            assessment = infer_closed_state(
                state_raw=issue_record.state_raw,
                resolution_raw=issue_record.resolution_raw,
                close_reason_raw=issue_record.close_reason_raw,
                closed_at=issue_record.closed_at,
                closed_filter_applied=page.closed_filter_applied,
                closed_filter_mode=page.closed_filter_mode,
            )
            self._upsert_issue(session, family, instance, entry, issue_record, raw_payload_id, assessment)
            sample_collected += 1
            issue_rows += 1
            if assessment.is_closed:
                closed_rows += 1
            if assessment.needs_review:
                needs_review_rows += 1

        if sample_limit is not None:
            payload["sample_collected"] = sample_collected
            job.payload = payload

        if page.next_cursor and (sample_limit is None or sample_collected < sample_limit):
            next_payload = {
                "registry_entry_id": entry.id,
                "mode": mode,
                "cursor": page.next_cursor,
                "page_size": page_size,
                "page": payload.get("page", 1) + 1,
                "sample_limit": sample_limit,
                "sample_collected": sample_collected,
            }
            session.add(
                CollectionJob(
                    job_type=JobType.list_page_fetch,
                    family_id=family.id,
                    instance_id=instance.id,
                    registry_entry_id=entry.id,
                    payload=next_payload,
                    priority=max(1, job.priority - 1),
                )
            )

        page_index = payload.get("page", 1)
        self._log(
            (
                f"{self._format_job_label(family, instance, entry)} "
                f"page={page_index} issue_count={len(page.issues)} inserted={issue_rows} "
                f"closed={closed_rows} needs_review={needs_review_rows} "
                f"sample_collected={sample_collected}"
            )
        )

        return {
            "items": len(page.request_body) if isinstance(page.request_body, list) else 0,
            "issues": len(page.issues),
            "next_cursor": page.next_cursor,
            "page": page_index,
        }

    def _upsert_probe(self, session, family: TrackerFamily, instance: TrackerInstance | None, entry: RegistryEntry | None, result: ProbeResult) -> None:
        existing = session.scalar(
            select(CapabilityProbe).where(
                CapabilityProbe.family_id == family.id,
                CapabilityProbe.instance_id == (instance.id if instance else None),
                CapabilityProbe.registry_entry_id == (entry.id if entry else None),
                CapabilityProbe.probe_scope == ("entry" if entry else "instance"),
                CapabilityProbe.protocol == result.protocol,
            )
        )

        status = BlockReasonEnum.ok if result.supported else (BlockReasonEnum.auth_required if result.auth_required else BlockReasonEnum.blocked)

        if existing is None:
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
                    block_reason=status,
                    blocked_fields={"note": result.note, "details": result.details},
                    raw_response_status=result.raw_response_status or None,
                    raw_response_body_id=None,
                )
            )
            return

        existing.protocol = result.protocol
        existing.protocol_supported = result.supported
        existing.pagination_scheme = result.pagination
        existing.count_supported = result.count_supported
        existing.auth_required = result.auth_required
        existing.block_reason = status
        existing.blocked_fields = {"note": result.note, "details": result.details}
        existing.raw_response_status = result.raw_response_status or existing.raw_response_status

    def _upsert_count(self, session, entry: RegistryEntry, plan) -> None:
        existing = session.scalar(
            select(CountSnapshot).where(
                CountSnapshot.registry_entry_id == entry.id,
                CountSnapshot.query_signature == plan.signature,
            )
        )

        if existing is None:
            session.add(
                CountSnapshot(
                    registry_entry_id=entry.id,
                    manifest_version_id=entry.manifest_version_id,
                    query_signature=plan.signature,
                    count_mode=plan.mode,
                    count_method=plan.method,
                    count_value=plan.value,
                    notes={"metadata": plan.metadata, "count_error": plan.count_error},
                    comparator=plan.count_error,
                )
            )
            return

        existing.count_mode = plan.mode if isinstance(plan.mode, CountMode) else CountMode(plan.mode)
        existing.count_method = plan.method
        existing.count_value = plan.value
        existing.notes = {"metadata": plan.metadata, "count_error": plan.count_error}
        existing.comparator = plan.count_error

    def _record_collection_error(
        self,
        session,
        job: CollectionJob,
        error: Exception,
        family: TrackerFamily | None,
        instance: TrackerInstance | None,
        entry: RegistryEntry | None,
        raw_payload_id: uuid.UUID | None = None,
    ) -> None:
        session.add(
            CollectionError(
                family_id=family.id if family else job.family_id,
                instance_id=instance.id if instance else job.instance_id,
                registry_entry_id=entry.id if entry else job.registry_entry_id,
                job_id=job.id,
                raw_payload_id=raw_payload_id,
                error_type="job_processing_error",
                status_code=None,
                retryable=(job.attempt_count < job.max_attempts),
                message=type(error).__name__,
                detail={"message": str(error)},
                source_url=None,
            )
        )

    @staticmethod
    def _request_hash(method: str, url: str, params: dict | None = None, headers: dict[str, str] | None = None) -> str:
        payload = {
            "method": method,
            "url": url,
            "params": params or {},
            "headers": headers or {},
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()

    def _persist_raw_payload(self, session, family: TrackerFamily, instance: TrackerInstance, entry: RegistryEntry, page: IssueListPage) -> int:
        raw_headers = page.headers or {}
        raw_body = page.request_body
        normalized_body = raw_body
        if isinstance(raw_body, list):
            normalized_body = {"items": raw_body}

        raw_json = normalized_body if isinstance(normalized_body, dict) else None
        raw_text = None
        if raw_json is None and raw_body is not None:
            raw_text = json.dumps(raw_body, ensure_ascii=False)

        raw = RawApiPayload(
            family_id=family.id,
            instance_id=instance.id,
            registry_entry_id=entry.id,
            source_url=page.request_url or "",
            api_url=page.request_url,
            http_method="GET",
            request_params_hash=self._request_hash("GET", page.request_url or "", page.request_params, page.request_headers),
            request_headers_hash=self._request_hash("GET-HEADERS", "", None, page.request_headers),
            response_status_code=page.status_code or 0,
            response_headers={str(k): str(v) for k, v in (raw_headers or {}).items()},
            response_body_json=raw_json,
            response_body_raw=raw_text,
            response_body_sha256=(
                hashlib.sha256(json.dumps(raw_body, sort_keys=True, default=str).encode()).hexdigest()
                if raw_body is not None
                else None
            ),
        )
        session.add(raw)
        session.flush()
        return raw.id

    @staticmethod
    def _persist_raw_page(session, entry_id: int, page: IssueListPage, raw_payload_id: int, page_no: int) -> None:
        session.add(
            RawApiPage(
                registry_entry_id=entry_id,
                source_url=page.request_url or "",
                page_type="issues.list",
                request_params=page.request_params,
                request_params_hash=Orchestrator._request_hash("GET", page.request_url or "", page.request_params),
                raw_payload_id=raw_payload_id,
                next_cursor=str(page.next_cursor) if page.next_cursor is not None else None,
                page_index=page_no,
                is_last_page=page.next_cursor is None,
            )
        )

    def _upsert_issue(
        self,
        session,
        family: TrackerFamily,
        instance: TrackerInstance,
        entry: RegistryEntry,
        issue_record,
        raw_payload_id: int,
        assessment: ClosedAssessment,
    ) -> None:
        existing = session.scalar(
            select(Issue).where(
                Issue.tracker_instance_id == instance.id,
                Issue.tracker_issue_id == issue_record.tracker_issue_id,
            )
        )

        if existing is None:
            session.add(
                Issue(
                    source_family_id=family.id,
                    tracker_instance_id=instance.id,
                    registry_entry_id=entry.id,
                    tracker_issue_id=issue_record.tracker_issue_id,
                    tracker_issue_key=issue_record.tracker_issue_key or issue_record.tracker_issue_id,
                    title=issue_record.title,
                    body_raw=issue_record.body_raw,
                    body_plaintext=issue_record.body_plaintext,
                    issue_url=issue_record.issue_url,
                    api_url=issue_record.api_url,
                    issue_type_raw=issue_record.issue_type_raw,
                    state_raw=issue_record.state_raw,
                    resolution_raw=issue_record.resolution_raw,
                    close_reason_raw=issue_record.close_reason_raw,
                    created_at_tracker=issue_record.created_at_tracker,
                    updated_at_tracker=issue_record.updated_at_tracker,
                    closed_at=issue_record.closed_at,
                    reporter_raw=issue_record.reporter_raw,
                    assignee_raw=issue_record.assignee_raw,
                    is_closed=assessment.is_closed,
                    needs_review=assessment.needs_review,
                    is_pull_request=issue_record.is_pull_request,
                    is_private_restricted=issue_record.is_private_restricted,
                    raw_payload_id=raw_payload_id,
                )
            )
            return

        existing.source_family_id = family.id
        existing.title = issue_record.title
        existing.tracker_issue_key = issue_record.tracker_issue_key or existing.tracker_issue_key
        existing.body_raw = issue_record.body_raw
        existing.body_plaintext = issue_record.body_plaintext
        existing.issue_url = issue_record.issue_url
        existing.api_url = issue_record.api_url
        existing.issue_type_raw = issue_record.issue_type_raw
        existing.state_raw = issue_record.state_raw
        existing.resolution_raw = issue_record.resolution_raw
        existing.close_reason_raw = issue_record.close_reason_raw
        existing.created_at_tracker = issue_record.created_at_tracker
        existing.updated_at_tracker = issue_record.updated_at_tracker
        existing.closed_at = issue_record.closed_at
        existing.reporter_raw = issue_record.reporter_raw
        existing.assignee_raw = issue_record.assignee_raw
        existing.is_closed = assessment.is_closed
        existing.needs_review = assessment.needs_review
        existing.is_pull_request = issue_record.is_pull_request
        existing.is_private_restricted = issue_record.is_private_restricted
        existing.raw_payload_id = raw_payload_id

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
