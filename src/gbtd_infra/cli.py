from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import csv
import json
from pathlib import Path
from types import SimpleNamespace

import typer
from sqlalchemy import and_, or_, select

from gbtd_infra.config import AppConfig
from gbtd_infra.clients.http import PoliteHttpClient
from gbtd_infra.db import init_db, session_scope, build_session_factory
from gbtd_infra.manifests import ManifestLoader, sync_manifest_to_registry
from gbtd_infra.models import CollectionJob, JobType, RegistryEntry, TrackerFamily, TrackerInstance
from gbtd_infra.adapter_registry import adapter_for_family
from gbtd_infra.orchestrator import Orchestrator
from gbtd_infra.adapters.base import CapabilityError, infer_closed_state
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


def _load_preview_candidates(
    manifest_path: str,
    family: str | None = None,
    instance: str | None = None,
    entry: str | None = None,
    entry_kind: str | None = None,
    max_entries: int | None = None,
):
    loader = ManifestLoader(Path(manifest_path))
    _, candidates = loader.load()
    if family:
        candidates = [c for c in candidates if c.family_slug == family]
    if instance:
        candidates = [c for c in candidates if c.instance_name == instance]
    if entry:
        candidates = [c for c in candidates if c.entry_tracker_id == entry or c.tracker_api_key == entry]
    if entry_kind:
        candidates = [c for c in candidates if c.entry_kind.value == entry_kind]
    if max_entries is not None and max_entries > 0:
        candidates = candidates[:max_entries]
    return candidates


def _preview_instance_from_candidate(cand) -> SimpleNamespace:
    return SimpleNamespace(
        id=0,
        canonical_name=cand.instance_name,
        base_url=cand.instance_base_url,
        api_base_url=cand.instance_api_base_url,
    )


def _preview_entry_from_candidate(cand, instance_obj: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        id=0,
        family_id=0,
        instance_id=0,
        instance=instance_obj,
        entry_kind=cand.entry_kind,
        name=cand.entry_name,
        tracker_native_id=cand.entry_tracker_id,
        tracker_api_key=cand.tracker_api_key,
        tracker_key=cand.tracker_api_key or cand.entry_tracker_id,
        tracker_url=cand.tracker_url,
        api_url=cand.api_url,
    )


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
    entry_kind: str | None = typer.Option(None, help="project|repo|product|component|module|instance"),
    job_mode: str = typer.Option("all", help="probe|count|collect|all"),
    include_count: bool = typer.Option(False, "--count", help="include count_snapshot job"),
    sample_size: int | None = typer.Option(None, help="limit issues per entry; 0/empty -> all"),
    max_entries: int | None = typer.Option(None, help="limit target entries"),
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

        if entry_kind:
            entries = [e for e in entries if e.entry_kind.value == entry_kind]

        if max_entries is not None and max_entries > 0:
            entries = entries[:max_entries]

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
    entry: str | None = typer.Option(None, help="tracker_native_id or tracker_api_key"),
    entry_kind: str | None = typer.Option(None, help="project|repo|product|component|module|instance"),
    sample_size: int = typer.Option(20, min=1, max=100),
    include_probe: bool = typer.Option(False),
    include_count: bool = typer.Option(False),
    page_size: int = typer.Option(50, min=1, max=100),
    max_entries: int | None = typer.Option(None, help="limit total entries to seed"),
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
                if entry:
                    entries = [e for e in entries if e.tracker_native_id == entry or e.tracker_api_key == entry]
                if entry_kind:
                    entries = [e for e in entries if e.entry_kind.value == entry_kind]
                if max_entries is not None and max_entries > 0:
                    entries = entries[:max_entries]
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
    show_progress: bool = typer.Option(True, "--show-progress/--no-show-progress"),
):
    cfg = _load_config(config_file)
    worker = Orchestrator(cfg, show_progress=show_progress)

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
    entry: str | None = typer.Option(None, help="tracker_native_id or tracker_api_key"),
    entry_kind: str | None = typer.Option(None, help="project|repo|product|component|module|instance"),
    max_entries: int | None = typer.Option(None, help="limit total entries in this run"),
    sample_size: int = typer.Option(20, min=1, max=100),
    iterations: int = typer.Option(600, min=1),
    include_probe: bool = typer.Option(False),
    include_count: bool = typer.Option(False),
    page_size: int = typer.Option(50, min=1, max=100),
    show_progress: bool = typer.Option(True, "--show-progress/--no-show-progress"),
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
                entries = s.execute(select(RegistryEntry).where(RegistryEntry.instance_id == inst.id)).scalars().all()
                if entry:
                    entries = [ent for ent in entries if ent.tracker_native_id == entry or ent.tracker_api_key == entry]
                if entry_kind:
                    entries = [ent for ent in entries if ent.entry_kind.value == entry_kind]
                if max_entries is not None and max_entries > 0:
                    entries = entries[:max_entries]

                for ent in entries:
                    _seed_entry_jobs(
                        s,
                        entry=ent,
                        family_id=fam.id,
                        sample_size=sample_size,
                        page_size=page_size,
                        include_probe=include_probe,
                        include_count=include_count,
                    )

    worker = Orchestrator(cfg, show_progress=show_progress)

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


@app.command()
def preview_collect_csv(
    manifest_path: str = typer.Argument("manifests/sample.manifest.yaml", help="Manifest YAML path"),
    family: str | None = None,
    instance: str | None = None,
    entry: str | None = typer.Option(None, help="tracker_native_id or tracker_api_key"),
    entry_kind: str | None = typer.Option(None, help="project|repo|product|component|module|instance"),
    max_entries: int | None = typer.Option(None, help="limit total entries in this preview run"),
    sample_size: int = typer.Option(20, min=1, max=100),
    max_pages: int = typer.Option(5, min=1, help="max pages per entry for safety"),
    page_size: int = typer.Option(50, min=1, max=100),
    output_dir: str = typer.Option("artifacts/preview_csv", help="directory for csv outputs"),
    show_progress: bool = typer.Option(True, "--show-progress/--no-show-progress"),
    config_file: str | None = None,
):
    cfg = _load_config(config_file)
    candidates = _load_preview_candidates(
        manifest_path,
        family=family,
        instance=instance,
        entry=entry,
        entry_kind=entry_kind,
        max_entries=max_entries,
    )
    if not candidates:
        raise typer.BadParameter("no candidates matched")

    if len(candidates) > 1 and not family:
        typer.echo("warning: family not set; preview will run all families in manifest", err=True)

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_family = family or "all"
    safe_instance = instance or "all"
    response_csv_path = out_dir / f"preview_raw_responses_{safe_family}_{safe_instance}_{stamp}.csv"
    issue_csv_path = out_dir / f"preview_issues_{safe_family}_{safe_instance}_{stamp}.csv"

    response_fields = [
        "family",
        "instance",
        "entry_name",
        "entry_tracker_id",
        "entry_api_key",
        "entry_kind",
        "page_no",
        "request_url",
        "request_params_json",
        "status_code",
        "error",
        "response_headers_json",
        "request_headers_json",
        "response_body_json",
        "next_cursor",
    ]

    issue_fields = [
        "family",
        "instance",
        "entry_name",
        "entry_tracker_id",
        "entry_api_key",
        "page_no",
        "sample_position",
        "tracker_issue_id",
        "tracker_issue_key",
        "title",
        "issue_url",
        "api_url",
        "issue_type_raw",
        "state_raw",
        "resolution_raw",
        "close_reason_raw",
        "reporter_raw",
        "assignee_raw",
        "is_closed",
        "needs_review",
        "is_pull_request",
        "is_private_restricted",
        "labels",
        "created_at_tracker",
        "updated_at_tracker",
        "closed_at",
        "body_raw",
    ]

    async def run_preview():
        http = PoliteHttpClient(cfg)
        total_pages = 0
        total_issues = 0
        skipped = 0

        with response_csv_path.open("w", encoding="utf-8", newline="") as fp_resp, issue_csv_path.open(
            "w",
            encoding="utf-8",
            newline="",
        ) as fp_issue:
            response_writer = csv.DictWriter(fp_resp, fieldnames=response_fields)
            issue_writer = csv.DictWriter(fp_issue, fieldnames=issue_fields)
            response_writer.writeheader()
            issue_writer.writeheader()

            for cand in candidates:
                adapter_cls = adapter_for_family(cand.family_slug)
                if adapter_cls is None:
                    skipped += 1
                    typer.echo(f"skip (no adapter): {cand.family_slug}::{cand.instance_name}::{cand.entry_name}", err=True)
                    continue

                adapter = adapter_cls(None, http, cfg)
                inst = _preview_instance_from_candidate(cand)
                ent = _preview_entry_from_candidate(cand, inst)

                cursor = None
                sample_collected = 0

                for page_no in range(1, max_pages + 1):
                    remaining = sample_size - sample_collected if sample_size is not None else None
                    if remaining is not None and remaining <= 0:
                        break

                    try:
                        page = await adapter.list_issues(
                            ent,
                            cursor=cursor,
                            page_size=page_size,
                            mode="closed",
                            sample_limit=remaining,
                        )
                    except CapabilityError as exc:
                        skipped += 1
                        typer.echo(
                            f"skip (list_issues unsupported): {cand.family_slug}::{cand.instance_name}::{cand.entry_name} ({exc})",
                            err=True,
                        )
                        break
                    except Exception as exc:
                        skipped += 1
                        typer.echo(
                            f"error fetch: {cand.family_slug}::{cand.instance_name}::{cand.entry_name}: {exc}",
                            err=True,
                        )
                        break

                    response_writer.writerow(
                        {
                            "family": cand.family_slug,
                            "instance": cand.instance_name,
                            "entry_name": cand.entry_name,
                            "entry_tracker_id": cand.entry_tracker_id or "",
                            "entry_api_key": cand.tracker_api_key or "",
                            "entry_kind": cand.entry_kind.value,
                            "page_no": page_no,
                            "request_url": page.request_url or "",
                            "request_params_json": json.dumps(page.request_params or {}, ensure_ascii=False),
                            "status_code": page.status_code or 0,
                            "error": page.error or "",
                            "response_headers_json": json.dumps(page.headers or {}, ensure_ascii=False),
                            "request_headers_json": json.dumps(page.request_headers or {}, ensure_ascii=False),
                            "response_body_json": json.dumps(page.request_body, ensure_ascii=False, default=str)
                            if page.request_body is not None
                            else "",
                            "next_cursor": page.next_cursor or "",
                        }
                    )
                    total_pages += 1

                    for issue in page.issues:
                        if sample_collected >= sample_size:
                            break
                        sample_collected += 1
                        total_issues += 1
                        assessment = infer_closed_state(
                            state_raw=issue.state_raw,
                            resolution_raw=issue.resolution_raw,
                            close_reason_raw=issue.close_reason_raw,
                            closed_at=issue.closed_at,
                            closed_filter_applied=page.closed_filter_applied,
                            closed_filter_mode=page.closed_filter_mode,
                        )
                        issue_writer.writerow(
                            {
                                "family": cand.family_slug,
                                "instance": cand.instance_name,
                                "entry_name": cand.entry_name,
                                "entry_tracker_id": cand.entry_tracker_id or "",
                                "entry_api_key": cand.tracker_api_key or "",
                                "page_no": page_no,
                                "sample_position": sample_collected,
                                "tracker_issue_id": issue.tracker_issue_id,
                                "tracker_issue_key": issue.tracker_issue_key or "",
                                "title": issue.title,
                                "issue_url": issue.issue_url,
                                "api_url": issue.api_url,
                                "issue_type_raw": issue.issue_type_raw or "",
                                "state_raw": issue.state_raw or "",
                                "resolution_raw": issue.resolution_raw or "",
                                "close_reason_raw": issue.close_reason_raw or "",
                                "reporter_raw": issue.reporter_raw or "",
                                "assignee_raw": issue.assignee_raw or "",
                                "is_closed": assessment.is_closed,
                                "needs_review": assessment.needs_review,
                                "is_pull_request": issue.is_pull_request,
                                "is_private_restricted": issue.is_private_restricted,
                                "labels": ",".join(issue.labels),
                                "created_at_tracker": issue.created_at_tracker.isoformat() if issue.created_at_tracker else "",
                                "updated_at_tracker": issue.updated_at_tracker.isoformat() if issue.updated_at_tracker else "",
                                "closed_at": issue.closed_at.isoformat() if issue.closed_at else "",
                                "body_raw": issue.body_raw or "",
                            }
                        )

                    if page.next_cursor is None:
                        break
                    cursor = page.next_cursor

        await http.close()
        typer.echo(f"preview finished: responses={total_pages}, issues={total_issues}, skipped={skipped}")
        typer.echo(f"output responses: {response_csv_path}")
        typer.echo(f"output issues: {issue_csv_path}")

    asyncio.run(run_preview())


if __name__ == "__main__":
    app()
