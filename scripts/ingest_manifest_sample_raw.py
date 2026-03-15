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
from gbtd_infra.manifests import ManifestLoader, ManifestCandidate


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


def safe_upper_name(candidate: ManifestCandidate) -> str:
    return candidate.entry_name.replace("/", "_").replace(" ", "_").upper()


def extract_product_component(raw_payload: Any) -> tuple[str | None, str | None]:
    if not isinstance(raw_payload, dict):
        return None, None
    product = raw_payload.get("product") or raw_payload.get("repository") or raw_payload.get("project")
    component = raw_payload.get("component")
    if isinstance(product, dict):
        product = product.get("name") or product.get("path_with_namespace")
    if isinstance(component, dict):
        component = component.get("name")
    return (
        str(product) if product is not None and str(product).strip() else None,
        str(component) if component is not None and str(component).strip() else None,
    )


def insert_raw_doc(
    cur,
    *,
    source_family: str,
    tracker_instance: str,
    project_name: str,
    product_name: str | None,
    component_name: str | None,
    year: int,
    month: int,
    doc_type: str,
    bug_id: str,
    bug_key: str | None,
    item_id: str | None,
    storage_path: str,
    api_url: str | None,
    source_url: str | None,
    payload: Any,
    note: str | None = None,
) -> None:
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
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), %s, %s, %s)
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
            note = EXCLUDED.note
        """,
        (
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
            payload_sha256(payload),
            Jsonb(payload),
            200,
            False,
            note,
        ),
    )


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


async def collect_one_candidate(
    *,
    adapter,
    candidate: ManifestCandidate,
    per_entry: int,
    mode: str,
    cur,
) -> tuple[int, str | None]:
    family, instance, entry = build_runtime_models(candidate)
    page = await adapter.list_issues(entry, cursor=None, page_size=per_entry, mode=mode, sample_limit=per_entry)
    if page.error:
        return 0, page.error

    project_name = safe_upper_name(candidate)
    year, month = to_year_month()
    inserted = 0
    for issue in page.issues:
        bug_id = issue.tracker_issue_id
        if not bug_id:
            continue
        product_name, component_name = extract_product_component(issue.raw_payload)
        payload = issue.raw_payload or {
            "tracker_issue_id": issue.tracker_issue_id,
            "tracker_issue_key": issue.tracker_issue_key,
            "title": issue.title,
            "body_raw": issue.body_raw,
            "state_raw": issue.state_raw,
            "resolution_raw": issue.resolution_raw,
            "close_reason_raw": issue.close_reason_raw,
            "created_at": issue.created_at_tracker.isoformat() if issue.created_at_tracker else None,
            "updated_at": issue.updated_at_tracker.isoformat() if issue.updated_at_tracker else None,
            "closed_at": issue.closed_at.isoformat() if issue.closed_at else None,
        }
        insert_raw_doc(
            cur,
            source_family=candidate.family_slug,
            tracker_instance=candidate.instance_name,
            project_name=project_name,
            product_name=product_name,
            component_name=component_name,
            year=year,
            month=month,
            doc_type="BASE",
            bug_id=str(bug_id),
            bug_key=issue.tracker_issue_key,
            item_id=None,
            storage_path=build_storage_path(project_name, year, month, "BASE", str(bug_id)),
            api_url=issue.api_url,
            source_url=issue.issue_url,
            payload=payload,
            note=f"manifest_entry={candidate.entry_name}",
        )
        inserted += 1
    return inserted, None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect N sample issues per manifest entry into raw_issue_documents.")
    parser.add_argument("--manifest-path", default="manifests/sample.manifest.yaml")
    parser.add_argument("--per-entry", type=int, default=10)
    parser.add_argument("--mode", choices=["closed", "all"], default="all")
    parser.add_argument("--families", default=None, help="comma-separated family slugs to include")
    parser.add_argument("--entries", default=None, help="comma-separated entry names to include")
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
            with conn.cursor() as cur:
                for idx, candidate in enumerate(candidates, start=1):
                    adapter_cls = adapter_for_family(candidate.family_slug)
                    if adapter_cls is None:
                        print(f"[SKIP] {idx}/{len(candidates)} {candidate.family_slug}:{candidate.entry_name} unsupported_family")
                        continue

                    adapter = adapter_cls(session_factory=None, client=client, config=cfg)
                    inserted, error = await collect_one_candidate(
                        adapter=adapter,
                        candidate=candidate,
                        per_entry=args.per_entry,
                        mode=args.mode,
                        cur=cur,
                    )
                    conn.commit()
                    if error:
                        print(f"[ERR]  {idx}/{len(candidates)} {candidate.family_slug}:{candidate.entry_name} error={error}")
                    else:
                        print(f"[OK]   {idx}/{len(candidates)} {candidate.family_slug}:{candidate.entry_name} inserted={inserted}")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(async_main())
