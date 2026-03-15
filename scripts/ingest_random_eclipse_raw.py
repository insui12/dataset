from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import psycopg
from psycopg.types.json import Jsonb

from gbtd_infra.config import AppConfig


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


def request_json(
    client: httpx.Client,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    max_retries: int = 4,
    sleep_seconds: float = 1.5,
    jitter_seconds: float = 0.5,
) -> tuple[int, Any]:
    last_status = 0
    last_payload: Any = None

    for attempt in range(1, max_retries + 1):
        time.sleep(max(0.0, sleep_seconds + random.uniform(0.0, jitter_seconds)))
        try:
            resp = client.get(url, params=params)
        except httpx.RequestError:
            time.sleep(min(3.0 * attempt, 20.0))
            continue

        last_status = resp.status_code
        try:
            last_payload = resp.json()
        except Exception:
            last_payload = None

        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            wait = min(5.0 * attempt, 120.0)
            if retry_after:
                try:
                    wait = max(wait, float(retry_after))
                except Exception:
                    pass
            time.sleep(wait)
            continue

        if 500 <= resp.status_code < 600:
            time.sleep(min(3.0 * attempt, 30.0))
            continue

        return resp.status_code, last_payload

    return last_status, last_payload


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
    api_url: str,
    source_url: str | None,
    payload: Any,
    http_status: int,
    note: str | None = None,
    is_private: bool = False,
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
            http_status = EXCLUDED.http_status,
            is_private = EXCLUDED.is_private,
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
            http_status,
            is_private,
            note,
        ),
    )


def decode_b64(s: Any) -> tuple[bytes | None, str | None]:
    if not s or not isinstance(s, str):
        return None, None
    try:
        data = base64.b64decode(s)
    except Exception:
        return None, None
    return data, hashlib.sha256(data).hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Random sample Eclipse Bugzilla bugs into raw_issue_documents.")
    parser.add_argument("--last-bug-id", type=int, required=True, help="Upper bound bug id, inclusive.")
    parser.add_argument("--sample-size", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20260312)
    parser.add_argument("--sleep-seconds", type=float, default=1.5)
    parser.add_argument("--jitter-seconds", type=float, default=0.5)
    parser.add_argument("--max-retries", type=int, default=4)
    parser.add_argument("--project-name", default="ECLIPSE")
    parser.add_argument("--tracker-instance", default="eclipse")
    parser.add_argument("--base-url", default="https://bugs.eclipse.org/bugs/rest")
    return parser.parse_args()


def main() -> None:
    load_env_file(".env")
    args = parse_args()
    cfg = AppConfig()
    db_url = cfg.database_url.replace("postgresql+psycopg://", "postgresql://", 1)

    rng = random.Random(args.seed)
    sample_size = max(1, min(args.sample_size, args.last_bug_id))
    bug_ids = sorted(rng.sample(range(1, args.last_bug_id + 1), sample_size))

    print(f"[INFO] sampled_bug_ids={bug_ids}")

    with httpx.Client(timeout=cfg.timeout_seconds, headers={"User-Agent": cfg.user_agent}) as client, psycopg.connect(db_url) as conn:
        ok_count = 0
        skip_count = 0

        for bug_id_int in bug_ids:
            bug_id = str(bug_id_int)
            year, month = to_year_month()

            bug_url = f"{args.base_url}/bug/{bug_id}"
            status, bug_payload = request_json(
                client,
                bug_url,
                params={
                    "include_fields": (
                        "id,summary,description,status,resolution,product,component,version,"
                        "priority,severity,creator,assigned_to,creation_time,last_change_time,is_open,"
                        "creator_detail,assigned_to_detail"
                    )
                },
                max_retries=args.max_retries,
                sleep_seconds=args.sleep_seconds,
                jitter_seconds=args.jitter_seconds,
            )

            bugs = bug_payload.get("bugs") if isinstance(bug_payload, dict) else None
            if status >= 400 or not isinstance(bugs, list) or not bugs or not isinstance(bugs[0], dict):
                with conn.cursor() as cur:
                    insert_raw_doc(
                        cur,
                        source_family="bugzilla",
                        tracker_instance=args.tracker_instance,
                        project_name=args.project_name,
                        product_name=None,
                        component_name=None,
                        year=year,
                        month=month,
                        doc_type="BASE",
                        bug_id=bug_id,
                        bug_key=bug_id,
                        item_id=None,
                        storage_path=build_storage_path(args.project_name, year, month, "BASE", bug_id),
                        api_url=bug_url,
                        source_url=f"https://bugs.eclipse.org/bugs/show_bug.cgi?id={bug_id}",
                        payload=bug_payload if bug_payload is not None else {"error": "no_json_payload"},
                        http_status=status,
                        note="bug endpoint missing or inaccessible",
                    )
                conn.commit()
                skip_count += 1
                print(f"[SKIP] bug_id={bug_id} status={status}")
                continue

            bug_obj = bugs[0]
            product_name = bug_obj.get("product")
            component_name = bug_obj.get("component")
            issue_url = f"https://bugs.eclipse.org/bugs/show_bug.cgi?id={bug_id}"

            with conn.cursor() as cur:
                insert_raw_doc(
                    cur,
                    source_family="bugzilla",
                    tracker_instance=args.tracker_instance,
                    project_name=args.project_name,
                    product_name=product_name,
                    component_name=component_name,
                    year=year,
                    month=month,
                    doc_type="BASE",
                    bug_id=bug_id,
                    bug_key=bug_id,
                    item_id=None,
                    storage_path=build_storage_path(args.project_name, year, month, "BASE", bug_id),
                    api_url=bug_url,
                    source_url=issue_url,
                    payload=bug_payload,
                    http_status=status,
                )

                desc_payload = {
                    "id": bug_obj.get("id"),
                    "summary": bug_obj.get("summary"),
                    "description": bug_obj.get("description"),
                }
                insert_raw_doc(
                    cur,
                    source_family="bugzilla",
                    tracker_instance=args.tracker_instance,
                    project_name=args.project_name,
                    product_name=product_name,
                    component_name=component_name,
                    year=year,
                    month=month,
                    doc_type="DESC",
                    bug_id=bug_id,
                    bug_key=bug_id,
                    item_id=None,
                    storage_path=build_storage_path(args.project_name, year, month, "DESC", bug_id),
                    api_url=bug_url,
                    source_url=issue_url,
                    payload=desc_payload,
                    http_status=status,
                )

                comments_url = f"{args.base_url}/bug/{bug_id}/comment"
                c_status, comments_payload = request_json(
                    client,
                    comments_url,
                    max_retries=args.max_retries,
                    sleep_seconds=args.sleep_seconds,
                    jitter_seconds=args.jitter_seconds,
                )
                comments_obj = comments_payload.get("bugs", {}).get(bug_id) if isinstance(comments_payload, dict) else None
                comments = comments_obj.get("comments", []) if isinstance(comments_obj, dict) else []
                if isinstance(comments_payload, dict):
                    for comment in comments:
                        if not isinstance(comment, dict):
                            continue
                        item_id = str(comment.get("id")) if comment.get("id") is not None else None
                        if not item_id:
                            continue
                        insert_raw_doc(
                            cur,
                            source_family="bugzilla",
                            tracker_instance=args.tracker_instance,
                            project_name=args.project_name,
                            product_name=product_name,
                            component_name=component_name,
                            year=year,
                            month=month,
                            doc_type="CMT",
                            bug_id=bug_id,
                            bug_key=bug_id,
                            item_id=item_id,
                            storage_path=build_storage_path(args.project_name, year, month, "CMT", bug_id, item_id),
                            api_url=comments_url,
                            source_url=issue_url,
                            payload=comment,
                            http_status=c_status,
                        )

                history_url = f"{args.base_url}/bug/{bug_id}/history"
                h_status, history_payload = request_json(
                    client,
                    history_url,
                    max_retries=args.max_retries,
                    sleep_seconds=args.sleep_seconds,
                    jitter_seconds=args.jitter_seconds,
                )
                history_bugs = history_payload.get("bugs", []) if isinstance(history_payload, dict) else []
                if isinstance(history_bugs, list):
                    hist_seq = 0
                    for hist_bug in history_bugs:
                        if not isinstance(hist_bug, dict):
                            continue
                        for hist in hist_bug.get("history", []):
                            if not isinstance(hist, dict):
                                continue
                            changes = hist.get("changes", [])
                            if not isinstance(changes, list):
                                continue
                            for change in changes:
                                if not isinstance(change, dict):
                                    continue
                                hist_seq += 1
                                item_id = str(hist_seq)
                                row_payload = {
                                    "who": hist.get("who"),
                                    "when": hist.get("when"),
                                    "change": change,
                                }
                                insert_raw_doc(
                                    cur,
                                    source_family="bugzilla",
                                    tracker_instance=args.tracker_instance,
                                    project_name=args.project_name,
                                    product_name=product_name,
                                    component_name=component_name,
                                    year=year,
                                    month=month,
                                    doc_type="HIST",
                                    bug_id=bug_id,
                                    bug_key=bug_id,
                                    item_id=item_id,
                                    storage_path=build_storage_path(args.project_name, year, month, "HIST", bug_id, item_id),
                                    api_url=history_url,
                                    source_url=issue_url,
                                    payload=row_payload,
                                    http_status=h_status,
                                )

                attach_url = f"{args.base_url}/bug/{bug_id}/attachment"
                a_status, attach_payload = request_json(
                    client,
                    attach_url,
                    max_retries=args.max_retries,
                    sleep_seconds=args.sleep_seconds,
                    jitter_seconds=args.jitter_seconds,
                )
                attachments = attach_payload.get("bugs", {}).get(bug_id, []) if isinstance(attach_payload, dict) else []
                if isinstance(attachments, list):
                    for attachment in attachments:
                        if not isinstance(attachment, dict):
                            continue
                        item_id = str(attachment.get("id")) if attachment.get("id") is not None else None
                        if not item_id:
                            continue
                        insert_raw_doc(
                            cur,
                            source_family="bugzilla",
                            tracker_instance=args.tracker_instance,
                            project_name=args.project_name,
                            product_name=product_name,
                            component_name=component_name,
                            year=year,
                            month=month,
                            doc_type="ATTACH",
                            bug_id=bug_id,
                            bug_key=bug_id,
                            item_id=item_id,
                            storage_path=build_storage_path(args.project_name, year, month, "ATTACH", bug_id, item_id),
                            api_url=attach_url,
                            source_url=issue_url,
                            payload=attachment,
                            http_status=a_status,
                        )

                        detail_url = f"{args.base_url}/bug/attachment/{item_id}"
                        d_status, detail_payload = request_json(
                            client,
                            detail_url,
                            max_retries=args.max_retries,
                            sleep_seconds=args.sleep_seconds,
                            jitter_seconds=args.jitter_seconds,
                        )
                        detail_obj = detail_payload.get("attachments", {}).get(item_id) if isinstance(detail_payload, dict) else None
                        if isinstance(detail_obj, dict):
                            data_bytes, data_sha256 = decode_b64(detail_obj.get("data"))
                            attach_data_payload = {
                                **detail_obj,
                                "_binary_present": data_bytes is not None,
                                "_binary_sha256": data_sha256,
                            }
                            insert_raw_doc(
                                cur,
                                source_family="bugzilla",
                                tracker_instance=args.tracker_instance,
                                project_name=args.project_name,
                                product_name=product_name,
                                component_name=component_name,
                                year=year,
                                month=month,
                                doc_type="ATTACH_DATA",
                                bug_id=bug_id,
                                bug_key=bug_id,
                                item_id=item_id,
                                storage_path=build_storage_path(args.project_name, year, month, "ATTACH_DATA", bug_id, item_id),
                                api_url=detail_url,
                                source_url=issue_url,
                                payload=attach_data_payload,
                                http_status=d_status,
                            )

            conn.commit()
            ok_count += 1
            print(f"[OK] bug_id={bug_id}")

        print(f"[DONE] ok={ok_count} skipped={skip_count} sampled={len(bug_ids)}")


if __name__ == "__main__":
    main()
