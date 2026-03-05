from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import and_, select

from gbtd_infra.models import CollectionJob, JobStatus, JobType
from gbtd_infra.models import JobLease


class JobLeaseError(RuntimeError):
    pass


class JobScheduler:
    """Worker-side SQL lease helper for collection jobs."""

    def __init__(self, session_factory, worker_id: str, lease_seconds: int = 900):
        self.session_factory = session_factory
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds

    def claim_job(
        self,
        desired_types: Iterable[JobType] | None = None,
        batch: int = 1,
        family_ids: Iterable[int] | None = None,
        instance_ids: Iterable[int] | None = None,
        entry_ids: Iterable[int] | None = None,
    ):
        session = self.session_factory()
        now = datetime.now(timezone.utc)
        try:
            conditions = [
                CollectionJob.status == JobStatus.pending,
                CollectionJob.next_run_at <= now,
            ]
            if family_ids:
                ids = list(family_ids)
                if ids:
                    conditions.append(CollectionJob.family_id.in_(ids))
                else:
                    session.close()
                    return []

            if instance_ids:
                ids = list(instance_ids)
                if ids:
                    conditions.append(CollectionJob.instance_id.in_(ids))
                else:
                    session.close()
                    return []

            if entry_ids:
                ids = list(entry_ids)
                if ids:
                    conditions.append(CollectionJob.registry_entry_id.in_(ids))
                else:
                    session.close()
                    return []

            query = (
                select(CollectionJob)
                .where(and_(*conditions))
                .order_by(CollectionJob.priority.desc(), CollectionJob.created_at.asc())
                .limit(batch)
                .with_for_update(skip_locked=True)
            )
            if desired_types:
                query = query.where(CollectionJob.job_type.in_(desired_types))

            jobs = list(session.execute(query).scalars().all())
            if not jobs:
                session.close()
                return []

            now = datetime.now(timezone.utc)
            expires = now + timedelta(seconds=self.lease_seconds)
            lease_token = f"{self.worker_id}:{int(now.timestamp())}"

            for job in jobs:
                job.status = JobStatus.running
                job.started_at = now
                job.lease_owner = self.worker_id
                job.lease_id = lease_token
                job.lease_expires_at = expires
                job.attempt_count = job.attempt_count + 1

                session.add(
                    JobLease(
                        lease_token=lease_token,
                        collection_job_id=job.id,
                        owner=self.worker_id,
                        expires_at=expires,
                        state="active",
                    )
                )
            session.commit()
            return jobs
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def renew_lease(self, job_id: int) -> None:
        session = self.session_factory()
        try:
            job = session.get(CollectionJob, job_id)
            if not job or not job.lease_id:
                raise JobLeaseError(f"job {job_id} does not hold lease")
            job.lease_expires_at = datetime.now(timezone.utc) + timedelta(seconds=self.lease_seconds)
            session.commit()
        finally:
            session.close()

    def fail_job(self, job_id: int, reason: str, retry_delay_seconds: int = 300) -> None:
        session = self.session_factory()
        try:
            job = session.get(CollectionJob, job_id)
            if job is None:
                raise JobLeaseError(f"job {job_id} not found")
            if job.attempt_count >= job.max_attempts:
                job.status = JobStatus.dead
            else:
                job.status = JobStatus.pending
                job.next_run_at = datetime.now(timezone.utc) + timedelta(seconds=retry_delay_seconds)
            job.last_error = reason
            job.started_at = None
            job.lease_id = None
            job.lease_owner = None
            job.lease_expires_at = None
            session.commit()
        finally:
            session.close()

    def complete_job(self, job_id: int) -> None:
        session = self.session_factory()
        try:
            job = session.get(CollectionJob, job_id)
            if job is None:
                raise JobLeaseError(f"job {job_id} not found")
            job.status = JobStatus.succeeded
            job.completed_at = datetime.now(timezone.utc)
            job.lease_id = None
            job.lease_owner = None
            job.lease_expires_at = None
            session.commit()
        finally:
            session.close()

    @staticmethod
    def reclaim_timed_out_jobs(session, now):
        timed_out = session.execute(
            select(CollectionJob).where(
                and_(
                    CollectionJob.status == JobStatus.running,
                    CollectionJob.lease_expires_at != None,
                    CollectionJob.lease_expires_at <= now,
                )
            )
        ).scalars().all()

        for job in timed_out:
            job.status = JobStatus.pending
            job.started_at = None
            job.lease_id = None
            job.lease_owner = None
            job.lease_expires_at = None
            job.next_run_at = now
            job.last_error = "lease_recovered_after_timeout"
        return len(timed_out)
