from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import typer
from sqlalchemy import select

from gbtd_infra.config import AppConfig
from gbtd_infra.db import init_db, session_scope, build_session_factory
from gbtd_infra.manifests import sync_manifest_to_registry
from gbtd_infra.models import (
    CollectionJob,
    JobType,
    RegistryEntry,
    TrackerFamily,
    TrackerInstance,
)
from gbtd_infra.orchestrator import Orchestrator
from gbtd_infra.scheduler.lease import JobScheduler

app = typer.Typer(help="Global Bug Tracker Dataset infrastructure")


@app.command()
def init_database(config_file: str | None = None, db_url: str | None = None):
    cfg = AppConfig.load(config_file) if config_file else AppConfig()
    init_db(db_url or cfg.database_url)
    typer.echo("Database initialized")


@app.command()
def bootstrap_manifest(
    manifest_path: str = typer.Argument("manifests/sample.manifest.yaml", help="Manifest YAML path"),
    config_file: str | None = None,
):
    cfg = AppConfig.load(config_file) if config_file else AppConfig()
    db = build_session_factory(cfg)
    with session_scope(db) as s:
        manifest_record = sync_manifest_to_registry(s, manifest_path)
        typer.echo(f"Loaded manifest {manifest_record.manifest_name} version {manifest_record.version}")


@app.command()
def seed_jobs(
    family: str | None = typer.Option(None),
    instance: str | None = None,
    entry: str | None = None,
    config_file: str | None = None,
):
    cfg = AppConfig.load(config_file) if config_file else AppConfig()
    db = build_session_factory(cfg)

    with session_scope(db) as s:
        if family is None or instance is None:
            raise typer.BadParameter("family and instance are required")

        fam = s.scalar(select(TrackerFamily).where(TrackerFamily.slug == family))
        if not fam:
            raise typer.BadParameter(f"Unknown family: {family}")

        inst = s.scalar(
            select(TrackerInstance).where(
                TrackerInstance.family_id == fam.id,
                TrackerInstance.canonical_name == instance,
            )
        )
        if not inst:
            raise typer.BadParameter(f"Unknown instance: {instance}")

        if entry:
            ent = s.scalar(
                select(RegistryEntry).where(
                    RegistryEntry.instance_id == inst.id,
                    RegistryEntry.tracker_native_id == entry,
                )
            )
            if not ent:
                raise typer.BadParameter(f"Unknown entry: {entry}")
            jobs = [
                CollectionJob(
                    job_type=JobType.capability_probe,
                    family_id=fam.id,
                    instance_id=inst.id,
                    registry_entry_id=ent.id,
                )
            ]
        else:
            jobs = [
                CollectionJob(
                    job_type=JobType.capability_probe,
                    family_id=fam.id,
                    instance_id=inst.id,
                )
            ]

        s.add_all(jobs)
        typer.echo(f"Seeded {len(jobs)} jobs")


@app.command()
def reclaim_jobs(config_file: str | None = None):
    cfg = AppConfig.load(config_file) if config_file else AppConfig()
    session_factory = build_session_factory(cfg)
    scheduler = JobScheduler(session_factory, cfg.runner_id, cfg.lease_seconds)
    with session_scope(session_factory) as s:
        count = scheduler.reclaim_timed_out_jobs(
            s,
            datetime.now(timezone.utc),
        )
    typer.echo(f"Reclaimed jobs: {count}")


@app.command()
def run_worker(
    config_file: str | None = None,
    iterations: int = typer.Option(0, help="0=forever"),
):
    cfg = AppConfig.load(config_file) if config_file else AppConfig()
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


if __name__ == "__main__":
    app()
