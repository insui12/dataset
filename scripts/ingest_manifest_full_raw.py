from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from gbtd_infra.adapter_registry import adapter_for_family
from gbtd_infra.clients.http import PoliteHttpClient
from gbtd_infra.config import AppConfig
from gbtd_infra.manifests import ManifestCandidate, ManifestLoader


def load_env_file(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and ((val[0] == '"' and val[-1] == '"') or (val[0] == "'" and val[-1] == "'")):
            val = val[1:-1]
        os.environ.setdefault(key, val)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def payload_sha256(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def to_year_month() -> tuple[int, int]:
    current = now_utc()
    return current.year, current.month


def build_storage_path(project_name: str, year: int, month: int, doc_type: str, bug_id: str, item_id: str | None = None) -> str:
    base = f"{project_name}/{year:04d}/{month:02d}/{doc_type}"
    if item_id:
        return f"{base}/{bug_id}_{item_id}.json"
    return f"{base}/{bug_id}.json"


def canonical_project_name(candidate: ManifestCandidate) -> str:
    value = f"{candidate.family_slug}_{candidate.instance_name}_{candidate.entry_name}"
    return value.replace("/", "_").replace(" ", "_").replace("-", "_").upper()


def extract_product_component(raw_payload: Any) -> tuple[str | None, str | None]:
    if not isinstance(raw_payload, dict):
        return None, None
    product = (
        raw_payload.get("product")
        or raw_payload.get("repository")
        or raw_payload.get("project")
        or raw_payload.get("project_id")
    )
    component = raw_payload.get("component")
    if isinstance(product, dict):
        product = product.get("name") or product.get("path_with_namespace")
    if isinstance(component, dict):
        component = component.get("name")
    p = str(product).strip() if product is not None and str(product).strip() else None
    c = str(component).strip() if component is not None and str(component).strip() else None
    return p, c


def build_runtime_models(candidate: ManifestCandidate) -> tuple[Any, Any, Any]:
    family = SimpleNamespace(
        id=0,
        slug=candidate.family_slug,
        name=candidate.family_name,
        default_protocol=candidate.protocol,
    )
    instance = SimpleNamespace(
        id=0,
        family_id=0,
        canonical_name=candidate.instance_name,
        base_url=candidate.instance_base_url,
        api_base_url=candidate.instance_api_base_url,
        tier=candidate.tier,
        collection_mode=candidate.collection_mode,
        dataset_role=candidate.dataset_role,
        protocol=candidate.protocol,
        visibility=candidate.visibility,
        status=candidate.status,
    )
    entry = SimpleNamespace(
        id=0,
        family_id=0,
        instance_id=0,
        entry_kind=candidate.entry_kind,
        name=candidate.entry_name,
        tracker_native_id=candidate.entry_tracker_id,
        tracker_api_key=candidate.tracker_api_key,
        tracker_url=candidate.tracker_url,
        api_url=candidate.api_url,
        tier=candidate.tier,
        collection_mode=candidate.collection_mode,
        dataset_role=candidate.dataset_role,
        protocol=candidate.protocol,
        visibility=candidate.visibility,
        status=candidate.status,
        is_bounded_instance=candidate.is_bounded,
        instance=instance,
        family=family,
    )
    return family, instance, entry


def ensure_state_table(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS raw_collection_state (
                state_key TEXT PRIMARY KEY,
                family_slug TEXT NOT NULL,
                instance_name TEXT NOT NULL,
                entry_name TEXT NOT NULL,
                mode TEXT NOT NULL,
                cursor TEXT,
                page_size INT NOT NULL,
                pages_completed BIGINT NOT NULL DEFAULT 0,
                issues_collected BIGINT NOT NULL DEFAULT 0,
                completed BOOLEAN NOT NULL DEFAULT FALSE,
                last_issue_id TEXT,
                last_error TEXT,
                state_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    conn.commit()


def state_key(candidate: ManifestCandidate, mode: str) -> str:
    return f"{candidate.family_slug}|{candidate.instance_name}|{candidate.entry_name}|{mode}"


def load_state(conn: psycopg.Connection, key: str) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute("SELECT state_json FROM raw_collection_state WHERE state_key = %s", (key,))
        row = cur.fetchone()
        return row[0] if row else None


def save_state(
    conn: psycopg.Connection,
    *,
    key: str,
    candidate: ManifestCandidate,
    mode: str,
    cursor: str | None,
    page_size: int,
    pages_completed: int,
    issues_collected: int,
    completed: bool,
    last_issue_id: str | None,
    last_error: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    state_json = {
        "cursor": cursor,
        "page_size": page_size,
        "pages_completed": pages_completed,
        "issues_collected": issues_collected,
        "completed": completed,
        "last_issue_id": last_issue_id,
        "last_error": last_error,
        "updated_at_utc": now_utc().isoformat(),
    }
    if extra:
        state_json.update(extra)

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw_collection_state (
                state_key,
                family_slug,
                instance_name,
                entry_name,
                mode,
                cursor,
                page_size,
                pages_completed,
                issues_collected,
                completed,
                last_issue_id,
                last_error,
                state_json,
                updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (state_key) DO UPDATE SET
                cursor = EXCLUDED.cursor,
                page_size = EXCLUDED.page_size,
                pages_completed = EXCLUDED.pages_completed,
                issues_collected = EXCLUDED.issues_collected,
                completed = EXCLUDED.completed,
                last_issue_id = EXCLUDED.last_issue_id,
                last_error = EXCLUDED.last_error,
                state_json = EXCLUDED.state_json,
                updated_at = now()
            """,
            (
                key,
                candidate.family_slug,
                candidate.instance_name,
                candidate.entry_name,
                mode,
                cursor,
                page_size,
                pages_completed,
                issues_collected,
                completed,
                last_issue_id,
                last_error,
                Jsonb(state_json),
            ),
        )
    conn.commit()


def insert_base_doc(
    cur,
    *,
    candidate: ManifestCandidate,
    issue,
) -> None:
    project_name = canonical_project_name(candidate)
    year, month = to_year_month()
    product_name, component_name = extract_product_component(issue.raw_payload)
    bug_id = str(issue.tracker_issue_id)
    bug_key = issue.tracker_issue_key
    storage_path = build_storage_path(project_name, year, month, "BASE", bug_id)
    payload = issue.raw_payload or {
        "tracker_issue_id": issue.tracker_issue_id,
        "tracker_issue_key": issue.tracker_issue_key,
        "title": issue.title,
        "body_raw": issue.body_raw,
        "body_plaintext": issue.body_plaintext,
        "issue_url": issue.issue_url,
        "api_url": issue.api_url,
        "issue_type_raw": issue.issue_type_raw,
        "state_raw": issue.state_raw,
        "resolution_raw": issue.resolution_raw,
        "close_reason_raw": issue.close_reason_raw,
        "created_at_tracker": issue.created_at_tracker.isoformat() if issue.created_at_tracker else None,
        "updated_at_tracker": issue.updated_at_tracker.isoformat() if issue.updated_at_tracker else None,
        "closed_at": issue.closed_at.isoformat() if issue.closed_at else None,
        "reporter_raw": issue.reporter_raw,
        "assignee_raw": issue.assignee_raw,
        "labels": issue.labels,
    }

    cur.execute(
        """
        INSERT INTO raw_issue_documents (
            source_family,
            tracker_instance,
            project_name,
            product_name,
            component_name,
            year,
            month,
            doc_type,
            bug_id,
            bug_key,
            item_id,
            storage_path,
            api_url,
            source_url,
            payload_sha256,
            raw_payload,
            fetched_at,
            http_status,
            is_private,
            note
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'BASE', %s, %s, NULL, %s, %s, %s, %s, %s, now(), %s, %s, %s)
        ON CONFLICT (project_name, doc_type, bug_id, COALESCE(item_id, ''))
        DO UPDATE SET
            product_name = EXCLUDED.product_name,
            component_name = EXCLUDED.component_name,
            year = EXCLUDED.year,
            month = EXCLUDED.month,
            storage_path = EXCLUDED.storage_path,
            api_url = EXCLUDED.api_url,
            source_url = EXCLUDED.source_url,
            payload_sha256 = EXCLUDED.payload_sha256,
            raw_payload = EXCLUDED.raw_payload,
            fetched_at = now(),
            http_status = EXCLUDED.http_status,
            is_private = EXCLUDED.is_private,
            note = EXCLUDED.note
        """,
        (
            candidate.family_slug,
            candidate.instance_name,
            project_name,
            product_name,
            component_name,
            year,
            month,
            bug_id,
            bug_key,
            storage_path,
            issue.api_url,
            issue.issue_url,
            payload_sha256(payload),
            Jsonb(payload),
            200,
            bool(issue.is_private_restricted),
            f"entry={candidate.entry_name}; mode=full-list",
        ),
    )


async def collect_entry(
    *,
    conn: psycopg.Connection,
    client: PoliteHttpClient,
    cfg: AppConfig,
    candidate: ManifestCandidate,
    mode: str,
    page_size: int,
    max_pages: int | None,
) -> None:
    adapter_cls = adapter_for_family(candidate.family_slug)
    if adapter_cls is None:
        print(f"[SKIP] {candidate.family_slug}:{candidate.entry_name} unsupported_family")
        return

    adapter = adapter_cls(session_factory=None, client=client, config=cfg)
    _, _, entry = build_runtime_models(candidate)
    key = state_key(candidate, mode)
    state = load_state(conn, key) or {}

    if state.get("completed"):
        print(f"[DONE] {candidate.family_slug}:{candidate.entry_name} already completed")
        return

    cursor = state.get("cursor")
    pages_completed = int(state.get("pages_completed", 0))
    issues_collected = int(state.get("issues_collected", 0))
    page_loops = 0

    print(
        f"[START] {candidate.family_slug}:{candidate.instance_name}:{candidate.entry_name} "
        f"cursor={cursor!r} pages_completed={pages_completed} issues_collected={issues_collected}"
    )

    while True:
        page = await adapter.list_issues(
            entry,
            cursor=cursor,
            page_size=page_size,
            mode=mode,
            sample_limit=None,
        )

        if page.error:
            save_state(
                conn,
                key=key,
                candidate=candidate,
                mode=mode,
                cursor=cursor,
                page_size=page_size,
                pages_completed=pages_completed,
                issues_collected=issues_collected,
                completed=False,
                last_issue_id=state.get("last_issue_id"),
                last_error=page.error,
                extra={
                    "request_url": page.request_url,
                    "request_params": page.request_params,
                    "status_code": page.status_code,
                },
            )
            print(f"[ERR] {candidate.family_slug}:{candidate.entry_name} error={page.error}")
            return

        inserted_this_page = 0
        last_issue_id = None
        with conn.cursor() as cur:
            for issue in page.issues:
                if not issue.tracker_issue_id:
                    continue
                insert_base_doc(cur, candidate=candidate, issue=issue)
                inserted_this_page += 1
                last_issue_id = str(issue.tracker_issue_id)
        conn.commit()

        issues_collected += inserted_this_page
        pages_completed += 1
        page_loops += 1

        next_cursor = page.next_cursor
        if next_cursor is None and page.next_page is not None:
            next_cursor = str(page.next_page)
        if next_cursor is None and page.next_params:
            if "offset" in page.next_params:
                next_cursor = str(page.next_params["offset"])
            elif "page" in page.next_params:
                next_cursor = str(page.next_params["page"])
            elif "$skip" in page.next_params:
                next_cursor = str(page.next_params["$skip"])
            elif "startAt" in page.next_params:
                next_cursor = str(page.next_params["startAt"])

        completed = next_cursor is None
        save_state(
            conn,
            key=key,
            candidate=candidate,
            mode=mode,
            cursor=next_cursor,
            page_size=page_size,
            pages_completed=pages_completed,
            issues_collected=issues_collected,
            completed=completed,
            last_issue_id=last_issue_id,
            last_error=None,
            extra={
                "inserted_this_page": inserted_this_page,
                "request_url": page.request_url,
                "request_params": page.request_params,
            },
        )

        print(
            f"[PAGE] {candidate.family_slug}:{candidate.entry_name} "
            f"page={pages_completed} inserted={inserted_this_page} total={issues_collected} next_cursor={next_cursor!r}"
        )

        if completed:
            print(f"[DONE] {candidate.family_slug}:{candidate.entry_name} issues_collected={issues_collected}")
            return

        if max_pages is not None and page_loops >= max_pages:
            print(f"[STOP] {candidate.family_slug}:{candidate.entry_name} reached max_pages={max_pages}")
            return

        cursor = next_cursor


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manifest full raw collector with resume into raw_issue_documents.")
    parser.add_argument("--manifest-path", default="manifests/sample.manifest.yaml")
    parser.add_argument("--mode", choices=["closed", "all"], default="closed")
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--families", default=None, help="comma-separated family slugs")
    parser.add_argument("--entries", default=None, help="comma-separated entry names")
    parser.add_argument("--max-pages", type=int, default=None, help="per-entry page cap for test runs")
    return parser.parse_args()


async def async_main() -> None:
    load_env_file(".env")
    args = parse_args()
    cfg = AppConfig()
    db_url = cfg.database_url.replace("postgresql+psycopg://", "postgresql://", 1)

    _, candidates = ManifestLoader(Path(args.manifest_path)).load()
    if args.families:
        family_set = {x.strip() for x in args.families.split(",") if x.strip()}
        candidates = [c for c in candidates if c.family_slug in family_set]
    if args.entries:
        entry_set = {x.strip() for x in args.entries.split(",") if x.strip()}
        candidates = [c for c in candidates if c.entry_name in entry_set]

    client = PoliteHttpClient(cfg)
    try:
        with psycopg.connect(db_url) as conn:
            ensure_state_table(conn)
            for candidate in candidates:
                await collect_entry(
                    conn=conn,
                    client=client,
                    cfg=cfg,
                    candidate=candidate,
                    mode=args.mode,
                    page_size=args.page_size,
                    max_pages=args.max_pages,
                )
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(async_main())
