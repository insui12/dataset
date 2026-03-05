from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import typer
from sqlalchemy import and_, or_, select

from gbtd_infra.config import AppConfig
from gbtd_infra.db import init_db, session_scope, build_session_factory
from gbtd_infra.manifests import sync_manifest_to_registry
from gbtd_infra.models import CollectionJob, JobType, RegistryEntry, TrackerFamily, TrackerInstance
from gbtd_infra.orchestrator import Orchestrator
from gbtd_infra.scheduler.lease import JobScheduler

app = typer.Typer(help="Global Bug Tracker Dataset infrastructure")


def _load_config(config_file: str | None) -> AppConfig:
    return AppConfig.load(config_file) if config_file else AppConfig()


def _seed_entry_jobs(session, *, entry: RegistryEntry, family_id: int, sample_size: int | None = None, page_size: int = 100, include_probe: bool = True, include_count: bool = False) -> int:
    count = 0
    if include_probe:
        session.add(
            CollectionJob(
                job_type=JobType.capability_probe,
                family_id=family_id,
                instance_id=entry.instance_id,
                registry_entry_id=entry.id,
            )
        )
        count += 1

    if include_count:
        session.add(
            CollectionJob(
                job_type=JobType.count_snapshot,
                family_id=family_id,
                instance_id=entry.instance_id,
                registry_entry_id=entry.id,
            )
        )
        count += 1

    session.add(
        CollectionJob(
            job_type=JobType.list_page_fetch,
            family_id=family_id,
            instance_id=entry.instance_id,
            registry_entry_id=entry.id,
            payload={
                "registry_entry_id": entry.id,
                "mode": "closed",
                "page": 1,
                "page_size": min(max(1, int(page_size)), 100),
                "cursor": None,
                "sample_limit": sample_size,
                "sample_collected": 0,
            },
        )
    )
    return count + 1


@app.command()
def init_database(
    config_file: str | None = None,
    db_url: str | None = None,
):
    cfg = _load_config(config_file)
    init_db(db_url or cfg.database_url)
    typer.echo("Database initialized")


@app.command()
def bootstrap_manifest(
    manifest_path: str = typer.Argument("manifests/sample.manifest.yaml", help="Manifest YAML path"),
    config_file: str | None = None,
):
    cfg = _load_config(config_file)
    db = build_session_factory(cfg)
    with session_scope(db) as s:
        manifest_record = sync_manifest_to_registry(s, manifest_path)
        typer.echo(f"Loaded manifest {manifest_record.manifest_name} version {manifest_record.version}")


@app.command()
def seed_jobs(
    family: str = typer.Option(...),
    instance: str = typer.Option(...),
    entry: str | None = typer.Option(None, help="tracker_native_id or tracker_api_key"),
    job_mode: str = typer.Option("all", help="probe|count|collect|all"),
    include_count: bool = typer.Option(False, "--count", help="include count_snapshot job"),
    sample_size: int | None = typer.Option(None, help="limit issues per entry; 0/empty -> all"),
    config_file: str | None = None,
    page_size: int = typer.Option(50, min=1, max=100),
):
    if sample_size is not None and sample_size <= 0:
        sample_size = None

    cfg = _load_config(config_file)
    db = build_session_factory(cfg)

    with session_scope(db) as s:
        fam = s.scalar(select(TrackerFamily).where(TrackerFamily.slug == family))
        if fam is None:
            raise typer.BadParameter(f"unknown family: {family}")

        inst = s.scalar(
            select(TrackerInstance).where(
                TrackerInstance.family_id == fam.id,
                TrackerInstance.canonical_name == instance,
            )
        )
        if inst is None:
            raise typer.BadParameter(f"unknown instance: {instance}")

        entries_stmt = select(RegistryEntry).where(RegistryEntry.instance_id == inst.id)
        if entry:
            entries_stmt = entries_stmt.where(
                or_(
                    RegistryEntry.tracker_native_id == entry,
                    RegistryEntry.tracker_api_key == entry,
                )
            )
            # fallback to either tracker_native_id / tracker_api_key
            entries = s.execute(entries_stmt).scalars().all()
            if not entries:
                entries = s.execute(
                    select(RegistryEntry).where(
                        RegistryEntry.instance_id == inst.id,
                        RegistryEntry.tracker_api_key == entry,
                    )
                ).scalars().all()
                if not entries:
                    entries = s.execute(
                        select(RegistryEntry).where(
                            RegistryEntry.instance_id == inst.id,
                            RegistryEntry.tracker_native_id == entry,
                        )
                    ).scalars().all()
                    if not entries:
                        raise typer.BadParameter(f"unknown entry: {entry}")
        else:
            entries = s.execute(entries_stmt).scalars().all()

        include_probe = job_mode in {"probe", "all"}
        include_count_only = job_mode == "count"
        include_collect = job_mode in {"collect", "all"}

        seeded = 0
        for ent in entries:
            if include_count_only:
                session_count_job = CollectionJob(
                    job_type=JobType.count_snapshot,
                    family_id=fam.id,
                    instance_id=inst.id,
                    registry_entry_id=ent.id,
                )
                s.add(session_count_job)
                seeded += 1
                continue

            if include_probe or include_count:
                seeded += _seed_entry_jobs(
                    s,
                    entry=ent,
                    family_id=fam.id,
                    sample_size=sample_size,
                    page_size=page_size,
                    include_probe=include_probe,
                    include_count=include_count,
                )
            elif include_collect:
                s.add(
                    CollectionJob(
                        job_type=JobType.list_page_fetch,
                        family_id=fam.id,
                        instance_id=inst.id,
                        registry_entry_id=ent.id,
                        payload={
                            "registry_entry_id": ent.id,
                            "mode": "closed",
                            "page": 1,
                            "page_size": min(max(1, int(page_size)), 100),
                            "cursor": None,
                            "sample_limit": sample_size,
                            "sample_collected": 0,
                        },
                    )
                )
                seeded += 1

        typer.echo(f"seeded {seeded} jobs")


@app.command()
def seed_sample(
    manifest_path: str = typer.Option("manifests/sample.manifest.yaml", help="Manifest YAML path"),
    family: str | None = typer.Option(None),
    instance: str | None = None,
    sample_size: int = typer.Option(20, min=1, max=100),
    include_probe: bool = typer.Option(False),
    include_count: bool = typer.Option(False),
    page_size: int = typer.Option(50, min=1, max=100),
    config_file: str | None = None,
):
    cfg = _load_config(config_file)
    db = build_session_factory(cfg)

    with session_scope(db) as s:
        sync_manifest_to_registry(s, manifest_path)
        fams = s.execute(select(TrackerFamily)).scalars().all()
        targeted = [f for f in fams if family is None or f.slug == family]
        if not targeted:
            raise typer.BadParameter(f"unknown family: {family}")

        seeded = 0
        for fam in targeted:
            insts = s.execute(
                select(TrackerInstance).where(
                    TrackerInstance.family_id == fam.id,
                    TrackerInstance.canonical_name == instance,
                )
                if instance is not None
                else select(TrackerInstance).where(TrackerInstance.family_id == fam.id)
            ).scalars().all()
            for inst in insts:
                entries = s.execute(
                    select(RegistryEntry).where(RegistryEntry.instance_id == inst.id)
                ).scalars().all()
                for ent in entries:
                    seeded += _seed_entry_jobs(
                        s,
                        entry=ent,
                        family_id=fam.id,
                        sample_size=sample_size,
                        page_size=page_size,
                        include_probe=include_probe,
                        include_count=include_count,
                    )

        typer.echo(f"seeded {seeded} jobs for sample run")


@app.command()
def reclaim_jobs(config_file: str | None = None):
    cfg = _load_config(config_file)
    session_factory = build_session_factory(cfg)
    scheduler = JobScheduler(session_factory, cfg.runner_id, cfg.lease_seconds)
    with session_scope(session_factory) as s:
        count = scheduler.reclaim_timed_out_jobs(s, datetime.now(timezone.utc))
    typer.echo(f"Reclaimed jobs: {count}")


@app.command()
def run_worker(
    config_file: str | None = None,
    iterations: int = typer.Option(0, help="0=forever"),
):
    cfg = _load_config(config_file)
    worker = Orchestrator(cfg)

    async def loop():
        if iterations > 0:
            for _ in range(iterations):
                n = await worker.claim_and_run_once()
                if n == 0:
                    await asyncio.sleep(2)
            return
        await worker.run_forever()

    asyncio.run(loop())


@app.command()
def smoke_collect(
    manifest_path: str = typer.Option("manifests/sample.manifest.yaml", help="Manifest YAML path"),
    family: str | None = None,
    instance: str | None = None,
    sample_size: int = typer.Option(20, min=1, max=100),
    iterations: int = typer.Option(600, min=1),
    include_probe: bool = typer.Option(False),
    include_count: bool = typer.Option(False),
    page_size: int = typer.Option(50, min=1, max=100),
    config_file: str | None = None,
):
    cfg = _load_config(config_file)
    db = build_session_factory(cfg)
    with session_scope(db) as s:
        sync_manifest_to_registry(s, manifest_path)

        query = select(TrackerFamily)
        if family:
            query = query.where(TrackerFamily.slug == family)
        families = s.execute(query).scalars().all()

        for fam in families:
            inst_query = select(TrackerInstance).where(TrackerInstance.family_id == fam.id)
            if instance:
                inst_query = inst_query.where(TrackerInstance.canonical_name == instance)
            for inst in s.execute(inst_query).scalars().all():
                for entry in s.execute(select(RegistryEntry).where(RegistryEntry.instance_id == inst.id)).scalars().all():
                    _seed_entry_jobs(
                        s,
                        entry=entry,
                        family_id=fam.id,
                        sample_size=sample_size,
                        page_size=page_size,
                        include_probe=include_probe,
                        include_count=include_count,
                    )

    worker = Orchestrator(cfg)

    async def loop():
        if iterations > 0:
            for _ in range(iterations):
                n = await worker.claim_and_run_once()
                if n == 0:
                    break
                await asyncio.sleep(0.25)
        else:
            while True:
                n = await worker.claim_and_run_once()
                if n == 0:
                    break

    asyncio.run(loop())
    typer.echo(f"smoke_collect finished. sample_size={sample_size}")


if __name__ == "__main__":
    app()
