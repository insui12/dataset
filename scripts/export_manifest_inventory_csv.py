from __future__ import annotations

import asyncio
import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote

import httpx

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
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and ((value[0] == '"' and value[-1] == '"') or (value[0] == "'" and value[-1] == "'")):
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _preview_instance_from_candidate(cand: ManifestCandidate) -> SimpleNamespace:
    return SimpleNamespace(
        id=0,
        canonical_name=cand.instance_name,
        base_url=cand.instance_base_url,
        api_base_url=cand.instance_api_base_url,
    )


def _preview_entry_from_candidate(cand: ManifestCandidate, instance_obj: SimpleNamespace) -> SimpleNamespace:
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


def _gh_headers(cfg: AppConfig) -> dict[str, str]:
    return {"Authorization": f"Bearer {cfg.github_token}"} if cfg.github_token else {}


def _gl_headers(cfg: AppConfig) -> dict[str, str]:
    return {"PRIVATE-TOKEN": cfg.gitlab_token} if cfg.gitlab_token else {}


def _jira_headers(cfg: AppConfig) -> dict[str, str]:
    return {"Authorization": f"Bearer {cfg.jira_token}"} if cfg.jira_token else {}


def _yt_headers(cfg: AppConfig) -> dict[str, str]:
    if not cfg.youtrack_token:
        return {}
    return {"Authorization": f"Bearer {cfg.youtrack_token}", "Accept": "application/json"}


@dataclass
class EntryInventory:
    family: str
    instance: str
    entry_name: str
    entry_kind: str
    tracker_id: str | None
    tracker_api_key: str | None
    status: str
    method: str | None
    last_issue_id: str | None
    last_issue_key: str | None
    count_value: int | None
    count_mode: str | None
    count_method: str | None
    note: str | None


async def _request_json(http: PoliteHttpClient, url: str, headers: dict[str, str] | None = None, params: dict[str, Any] | None = None) -> tuple[int, Any]:
    try:
        response = await http.get(url, headers=headers, params=params)
    except httpx.HTTPStatusError as exc:
        response = exc.response
    except Exception as exc:
        return 599, {"error": str(exc)}
    try:
        payload = response.json()
    except Exception:
        payload = None
    return response.status_code, payload


async def _bugzilla_last(http: PoliteHttpClient, cand: ManifestCandidate) -> tuple[str, str | None, str | None, str | None]:
    base = (cand.instance_api_base_url or cand.instance_base_url).rstrip("/")
    key = cand.tracker_api_key or cand.entry_tracker_id or cand.entry_name

    status, cfg_payload = await _request_json(http, f"{base}/configuration")
    statuses: list[str] = []
    if status < 400 and isinstance(cfg_payload, dict):
        bug_status = cfg_payload.get("bug_status")
        if isinstance(bug_status, list):
            for item in bug_status:
                if isinstance(item, dict) and item.get("name"):
                    statuses.append(str(item["name"]))
                elif isinstance(item, str):
                    statuses.append(item)

    params: dict[str, Any] = {
        "include_fields": "id",
        "limit": 1,
        "offset": 0,
        "order": "bug_id",
        "sort": "DESC",
    }
    if statuses:
        params["bug_status"] = statuses
    if key:
        params["product"] = key

    status, payload = await _request_json(http, f"{base}/bug", params=params)
    if status >= 400:
        return "error", None, None, f"http {status}"
    bugs = payload.get("bugs") if isinstance(payload, dict) else None
    if isinstance(bugs, list) and bugs:
        bug_id = bugs[0].get("id")
        return "ok", str(bug_id) if bug_id is not None else None, str(bug_id) if bug_id is not None else None, None
    return "empty", None, None, "no bugs in response"


async def _github_last(http: PoliteHttpClient, cfg: AppConfig, cand: ManifestCandidate) -> tuple[str, str | None, str | None, str | None]:
    base = (cand.instance_api_base_url or "https://api.github.com").rstrip("/")
    repo = cand.tracker_api_key or cand.entry_name
    status, payload = await _request_json(
        http,
        f"{base}/repos/{quote(repo, safe='/')}/issues",
        headers=_gh_headers(cfg),
        params={"state": "all", "sort": "created", "direction": "desc", "per_page": 30, "page": 1},
    )
    if status >= 400:
        return "error", None, None, f"http {status}"
    if not isinstance(payload, list):
        return "error", None, None, "payload not list"
    for item in payload:
        if isinstance(item, dict) and not item.get("pull_request"):
            issue_id = item.get("id")
            issue_key = item.get("number")
            return "ok", str(issue_id) if issue_id is not None else None, str(issue_key) if issue_key is not None else None, None
    return "empty", None, None, "no issues visible"


async def _gitlab_last(http: PoliteHttpClient, cfg: AppConfig, cand: ManifestCandidate) -> tuple[str, str | None, str | None, str | None]:
    base = (cand.instance_api_base_url or "https://gitlab.com/api/v4").rstrip("/")
    project = cand.tracker_api_key or cand.entry_tracker_id or cand.entry_name
    status, payload = await _request_json(
        http,
        f"{base}/projects/{quote(str(project), safe='')}/issues",
        headers=_gl_headers(cfg),
        params={"state": "all", "order_by": "created_at", "sort": "desc", "per_page": 1, "page": 1},
    )
    if status >= 400:
        return "error", None, None, f"http {status}"
    if isinstance(payload, list) and payload:
        issue_id = payload[0].get("id")
        issue_key = payload[0].get("iid")
        return "ok", str(issue_id) if issue_id is not None else None, str(issue_key) if issue_key is not None else None, None
    return "empty", None, None, "no issues visible"


async def _jira_last(http: PoliteHttpClient, cfg: AppConfig, cand: ManifestCandidate) -> tuple[str, str | None, str | None, str | None]:
    base = (cand.instance_api_base_url or cand.instance_base_url).rstrip("/")
    project = cand.tracker_api_key or cand.entry_tracker_id or cand.entry_name
    escaped_project = str(project).replace('"', '\\"')
    jql = f'project="{escaped_project}" ORDER BY created DESC'
    for path in ("/rest/api/2/search", "/rest/api/3/search"):
        status, payload = await _request_json(
            http,
            f"{base}{path}",
            headers=_jira_headers(cfg),
            params={"jql": jql, "startAt": 0, "maxResults": 1, "fields": "id,key"},
        )
        if status >= 400:
            continue
        if isinstance(payload, dict):
            issues = payload.get("issues")
            if isinstance(issues, list) and issues:
                issue_id = issues[0].get("id")
                issue_key = issues[0].get("key")
                return "ok", str(issue_id) if issue_id is not None else None, str(issue_key) if issue_key is not None else None, None
            return "empty", None, None, "no issues visible"
    return "error", None, None, "jira search failed"


async def _redmine_last(http: PoliteHttpClient, cand: ManifestCandidate) -> tuple[str, str | None, str | None, str | None]:
    base = (cand.instance_api_base_url or cand.instance_base_url).rstrip("/")
    project_id = cand.tracker_api_key or cand.entry_tracker_id
    status, payload = await _request_json(
        http,
        f"{base}/issues.json",
        params={"project_id": project_id, "status_id": "*", "limit": 1, "offset": 0, "sort": "id:desc"},
    )
    if status >= 400:
        return "error", None, None, f"http {status}"
    if isinstance(payload, dict):
        issues = payload.get("issues")
        if isinstance(issues, list) and issues:
            issue_id = issues[0].get("id")
            return "ok", str(issue_id) if issue_id is not None else None, str(issue_id) if issue_id is not None else None, None
    return "empty", None, None, "no issues visible"


async def _youtrack_last(http: PoliteHttpClient, cfg: AppConfig, cand: ManifestCandidate) -> tuple[str, str | None, str | None, str | None]:
    base = (cand.instance_api_base_url or cand.instance_base_url).rstrip("/")
    project = cand.tracker_api_key or cand.entry_tracker_id or cand.entry_name
    status, payload = await _request_json(
        http,
        f"{base}/issues",
        headers=_yt_headers(cfg),
        params={
            "$top": 1,
            "$skip": 0,
            "query": f"project:{project} sort by: created desc",
            "fields": "id,idReadable,numberInProject,created",
        },
    )
    if status >= 400:
        return "error", None, None, f"http {status}"
    if isinstance(payload, list) and payload:
        issue_id = payload[0].get("id")
        issue_key = payload[0].get("idReadable") or payload[0].get("numberInProject")
        return "ok", str(issue_id) if issue_id is not None else None, str(issue_key) if issue_key is not None else None, None
    return "empty", None, None, "no issues visible"


async def _find_last_visible_issue(http: PoliteHttpClient, cfg: AppConfig, cand: ManifestCandidate) -> tuple[str, str | None, str | None, str | None, str | None]:
    if cand.family_slug == "bugzilla":
        status, issue_id, issue_key, note = await _bugzilla_last(http, cand)
        return status, issue_id, issue_key, "bugzilla_bug_desc_product", note
    if cand.family_slug == "github":
        status, issue_id, issue_key, note = await _github_last(http, cfg, cand)
        return status, issue_id, issue_key, "github_created_desc", note
    if cand.family_slug == "gitlab":
        status, issue_id, issue_key, note = await _gitlab_last(http, cfg, cand)
        return status, issue_id, issue_key, "gitlab_created_desc", note
    if cand.family_slug == "jira":
        status, issue_id, issue_key, note = await _jira_last(http, cfg, cand)
        return status, issue_id, issue_key, "jira_created_desc", note
    if cand.family_slug == "redmine":
        status, issue_id, issue_key, note = await _redmine_last(http, cand)
        return status, issue_id, issue_key, "redmine_id_desc", note
    if cand.family_slug == "youtrack":
        status, issue_id, issue_key, note = await _youtrack_last(http, cfg, cand)
        return status, issue_id, issue_key, "youtrack_created_desc", note
    return "unsupported_family", None, None, None, "last issue handler not implemented"


async def _count_plan_for_candidate(http: PoliteHttpClient, cfg: AppConfig, cand: ManifestCandidate) -> tuple[int | None, str | None, str | None, str | None]:
    adapter_cls = adapter_for_family(cand.family_slug)
    if adapter_cls is None:
        return None, None, None, "no adapter"

    instance_obj = _preview_instance_from_candidate(cand)
    entry_obj = _preview_entry_from_candidate(cand, instance_obj)
    adapter = adapter_cls(None, http, cfg)

    try:
        plan = await adapter.build_count_plan(entry_obj)
    except Exception as exc:
        return None, None, None, str(exc)

    count_mode = getattr(plan.mode, "value", str(plan.mode)) if getattr(plan, "mode", None) is not None else None
    return plan.value, count_mode, getattr(plan, "method", None), None


async def _inventory_row(http: PoliteHttpClient, cfg: AppConfig, cand: ManifestCandidate) -> EntryInventory:
    count_value, count_mode, count_method, count_error = await _count_plan_for_candidate(http, cfg, cand)
    last_status, last_issue_id, last_issue_key, last_method, last_note = await _find_last_visible_issue(http, cfg, cand)

    note_parts = []
    if count_error:
        note_parts.append(f"count_error={count_error}")
    if last_note:
        note_parts.append(f"last_note={last_note}")

    return EntryInventory(
        family=cand.family_slug,
        instance=cand.instance_name,
        entry_name=cand.entry_name,
        entry_kind=cand.entry_kind.value,
        tracker_id=cand.entry_tracker_id,
        tracker_api_key=cand.tracker_api_key,
        status=last_status,
        method=last_method,
        last_issue_id=last_issue_id,
        last_issue_key=last_issue_key,
        count_value=count_value,
        count_mode=count_mode,
        count_method=count_method,
        note="; ".join(note_parts) if note_parts else None,
    )


def _write_entries_csv(path: Path, rows: list[EntryInventory]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "family",
                "instance",
                "entry_name",
                "entry_kind",
                "tracker_id",
                "tracker_api_key",
                "status",
                "method",
                "last_issue_id",
                "last_issue_key",
                "count_value",
                "count_mode",
                "count_method",
                "note",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row.__dict__)


def _write_summary_csv(path: Path, rows: list[EntryInventory]) -> None:
    grouped: dict[str, list[EntryInventory]] = defaultdict(list)
    for row in rows:
        grouped[row.family].append(row)

    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "family",
                "instance_count",
                "entry_count",
                "ok_entries",
                "unsupported_entries",
                "entries_with_count",
                "exact_count_sum",
                "approximate_or_other_count_sum",
            ],
        )
        writer.writeheader()
        for family, items in sorted(grouped.items()):
            exact_sum = 0
            mixed_sum = 0
            exact_entries = 0
            instances = {item.instance for item in items}
            ok_entries = sum(1 for item in items if item.status == "ok")
            unsupported_entries = sum(1 for item in items if item.status == "unsupported_family")
            for item in items:
                if item.count_value is None:
                    continue
                if item.count_mode == "exact":
                    exact_sum += item.count_value
                    exact_entries += 1
                else:
                    mixed_sum += item.count_value
            writer.writerow(
                {
                    "family": family,
                    "instance_count": len(instances),
                    "entry_count": len(items),
                    "ok_entries": ok_entries,
                    "unsupported_entries": unsupported_entries,
                    "entries_with_count": sum(1 for item in items if item.count_value is not None),
                    "exact_count_sum": exact_sum if exact_entries else "",
                    "approximate_or_other_count_sum": mixed_sum if mixed_sum else "",
                }
            )


async def _run(manifest_path: str, output_dir: str) -> None:
    cfg = AppConfig()
    _, candidates = ManifestLoader(Path(manifest_path)).load()
    http = PoliteHttpClient(cfg)
    try:
        rows: list[EntryInventory] = []
        for idx, cand in enumerate(candidates, start=1):
            print(f"[{idx}/{len(candidates)}] {cand.family_slug}:{cand.instance_name}:{cand.entry_name}")
            try:
                rows.append(await _inventory_row(http, cfg, cand))
            except Exception as exc:
                rows.append(
                    EntryInventory(
                        family=cand.family_slug,
                        instance=cand.instance_name,
                        entry_name=cand.entry_name,
                        entry_kind=cand.entry_kind.value,
                        tracker_id=cand.entry_tracker_id,
                        tracker_api_key=cand.tracker_api_key,
                        status="error",
                        method=None,
                        last_issue_id=None,
                        last_issue_key=None,
                        count_value=None,
                        count_mode=None,
                        count_method=None,
                        note=f"unhandled_error={type(exc).__name__}: {exc}",
                    )
                )

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_dir = Path(output_dir) / stamp
        out_dir.mkdir(parents=True, exist_ok=True)

        entries_csv = out_dir / "manifest_entries_inventory.csv"
        summary_csv = out_dir / "manifest_summary_inventory.csv"
        raw_json = out_dir / "manifest_entries_inventory.json"

        _write_entries_csv(entries_csv, rows)
        _write_summary_csv(summary_csv, rows)
        raw_json.write_text(json.dumps([row.__dict__ for row in rows], ensure_ascii=False, indent=2), encoding="utf-8")

        print(f"entries csv: {entries_csv}")
        print(f"summary csv: {summary_csv}")
        print(f"json: {raw_json}")
    finally:
        await http.close()


def main() -> None:
    load_env_file(".env")
    manifest_path = os.getenv("GBTD_MANIFEST_PATH", "manifests/sample.manifest.yaml")
    output_dir = os.getenv("GBTD_INVENTORY_OUTPUT_DIR", "artifacts/manifest_inventory")
    asyncio.run(_run(manifest_path, output_dir))


if __name__ == "__main__":
    main()
