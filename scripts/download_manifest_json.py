from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

# Portable Python (._pth)에서는 PYTHONPATH가 무시되므로 직접 추가
_src = str(Path(__file__).resolve().parent.parent / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

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


def canonical_project_name(candidate: ManifestCandidate) -> str:
    return f"{candidate.family_slug}_{candidate.instance_name}_{candidate.entry_name}".replace("/", "_").replace("-", "_").upper()


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


def state_path(output_root: Path, candidate: ManifestCandidate, mode: str) -> Path:
    safe = f"{candidate.family_slug}__{candidate.instance_name}__{candidate.entry_name}__{mode}".replace("/", "__")
    return output_root / "_state" / f"{safe}.json"


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def issue_output_path(output_root: Path, candidate: ManifestCandidate, issue_id: str) -> Path:
    return document_output_path(
        output_root=output_root,
        candidate=candidate,
        doc_type="BASE",
        issue_id=issue_id,
    )


def sanitize_file_token(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value).strip())
    return cleaned or "item"


def document_output_path(
    *,
    output_root: Path,
    candidate: ManifestCandidate,
    doc_type: str,
    issue_id: str,
    item_id: str | None = None,
) -> Path:
    now = now_utc()
    project_name = canonical_project_name(candidate)
    file_name = sanitize_file_token(issue_id)
    if item_id:
        file_name = f"{file_name}_{sanitize_file_token(item_id)}"
    return output_root / project_name / f"{now.year:04d}" / f"{now.month:02d}" / doc_type / f"{file_name}.json"


def issue_envelope(candidate: ManifestCandidate, issue) -> dict[str, Any]:
    return {
        "meta": {
            "family": candidate.family_slug,
            "instance": candidate.instance_name,
            "entry": candidate.entry_name,
            "tracker_id": candidate.entry_tracker_id,
            "tracker_api_key": candidate.tracker_api_key,
            "downloaded_at_utc": now_utc().isoformat(),
            "issue_id": issue.tracker_issue_id,
            "issue_key": issue.tracker_issue_key,
            "issue_url": issue.issue_url,
            "api_url": issue.api_url,
        },
        "payload": issue.raw_payload,
    }


def tracker_document_envelope(
    *,
    candidate: ManifestCandidate,
    issue,
    doc_type: str,
    payload: Any,
    api_url: str,
    item_id: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    return {
        "meta": {
            "family": candidate.family_slug,
            "instance": candidate.instance_name,
            "entry": candidate.entry_name,
            "tracker_id": candidate.entry_tracker_id,
            "tracker_api_key": candidate.tracker_api_key,
            "downloaded_at_utc": now_utc().isoformat(),
            "doc_type": doc_type,
            "issue_id": issue.tracker_issue_id,
            "issue_key": issue.tracker_issue_key,
            "item_id": item_id,
            "issue_url": issue.issue_url,
            "api_url": api_url,
            "note": note,
        },
        "payload": payload,
    }


def _extract_first_comment_text(payload: Any, issue_id: str) -> str | None:
    if not isinstance(payload, dict):
        return None
    bugs = payload.get("bugs")
    if not isinstance(bugs, dict):
        return None
    bug_obj = bugs.get(str(issue_id))
    if not isinstance(bug_obj, dict):
        return None
    comments = bug_obj.get("comments")
    if not isinstance(comments, list) or not comments:
        return None
    first = comments[0]
    if not isinstance(first, dict):
        return None
    for key in ("text", "body", "comment"):
        value = first.get(key)
        if value is not None:
            text = str(value)
            return text if text.strip() else None
    return None


async def _fetch_json(client: PoliteHttpClient, url: str, params: dict[str, Any] | None = None) -> tuple[int | None, Any, str | None]:
    try:
        response = await client.get(url, params=params)
    except Exception as exc:
        return None, None, str(exc)
    try:
        payload = response.json()
    except Exception:
        payload = None
    return response.status_code, payload, None


async def save_bugzilla_full_issue(
    *,
    candidate: ManifestCandidate,
    issue,
    output_root: Path,
    client: PoliteHttpClient,
) -> int:
    base_url = candidate.instance_api_base_url.rstrip("/")
    issue_id = str(issue.tracker_issue_id)
    saved_count = 0

    # ── Phase 1: Fetch detail, comments, history, attachments list IN PARALLEL ──
    detail_url = f"{base_url}/bug/{issue_id}"
    comments_url = f"{base_url}/bug/{issue_id}/comment"
    history_url = f"{base_url}/bug/{issue_id}/history"
    attachments_url = f"{base_url}/bug/{issue_id}/attachment"

    (
        (detail_status, detail_payload, detail_error),
        (comments_status, comments_payload, comments_error),
        (history_status, history_payload, history_error),
        (attachments_status, attachments_payload, attachments_error),
    ) = await asyncio.gather(
        _fetch_json(
            client,
            detail_url,
            params={
                "include_fields": (
                    "id,alias,summary,description,status,resolution,product,component,version,"
                    "priority,severity,creator,assigned_to,creation_time,last_change_time,is_open,"
                    "creator_detail,assigned_to_detail"
                )
            },
        ),
        _fetch_json(client, comments_url),
        _fetch_json(client, history_url),
        _fetch_json(client, attachments_url),
    )

    # ── Save BASE ──
    base_out = document_output_path(
        output_root=output_root,
        candidate=candidate,
        doc_type="BASE",
        issue_id=issue_id,
    )
    base_out.parent.mkdir(parents=True, exist_ok=True)
    base_out.write_text(
        json.dumps(
            tracker_document_envelope(
                candidate=candidate,
                issue=issue,
                doc_type="BASE",
                payload=detail_payload if detail_payload is not None else issue.raw_payload,
                api_url=detail_url,
                note=detail_error or (f"http_status={detail_status}" if detail_status and detail_status >= 400 else None),
            ),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    saved_count += 1

    # ── Save DESC ──
    detail_bug = None
    if isinstance(detail_payload, dict):
        bugs = detail_payload.get("bugs")
        if isinstance(bugs, list) and bugs and isinstance(bugs[0], dict):
            detail_bug = bugs[0]

    description_text = None
    if isinstance(detail_bug, dict):
        raw_desc = detail_bug.get("description")
        if raw_desc is not None and str(raw_desc).strip():
            description_text = str(raw_desc)
    if not description_text:
        description_text = _extract_first_comment_text(comments_payload, issue_id)

    desc_url = detail_url if description_text else comments_url
    desc_note = None
    if not description_text:
        desc_note = comments_error or "description unavailable"
    desc_out = document_output_path(
        output_root=output_root,
        candidate=candidate,
        doc_type="DESC",
        issue_id=issue_id,
    )
    desc_out.parent.mkdir(parents=True, exist_ok=True)
    desc_out.write_text(
        json.dumps(
            tracker_document_envelope(
                candidate=candidate,
                issue=issue,
                doc_type="DESC",
                payload={
                    "id": issue_id,
                    "title": issue.title,
                    "description": description_text,
                },
                api_url=desc_url,
                note=desc_note,
            ),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    saved_count += 1

    # ── Save CMT ──
    if isinstance(comments_payload, dict):
        bug_comments = comments_payload.get("bugs", {}).get(issue_id)
        if isinstance(bug_comments, dict):
            comments = bug_comments.get("comments")
            if isinstance(comments, list):
                for idx, comment in enumerate(comments, start=1):
                    if not isinstance(comment, dict):
                        continue
                    comment_id = str(comment.get("id") or idx)
                    out_path = document_output_path(
                        output_root=output_root,
                        candidate=candidate,
                        doc_type="CMT",
                        issue_id=issue_id,
                        item_id=comment_id,
                    )
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    out_path.write_text(
                        json.dumps(
                            tracker_document_envelope(
                                candidate=candidate,
                                issue=issue,
                                doc_type="CMT",
                                payload=comment,
                                api_url=comments_url,
                                item_id=comment_id,
                                note=comments_error or (f"http_status={comments_status}" if comments_status and comments_status >= 400 else None),
                            ),
                            ensure_ascii=False,
                            indent=2,
                        ),
                        encoding="utf-8",
                    )
                    saved_count += 1

    # ── Save HIST ──
    if isinstance(history_payload, dict):
        bugs = history_payload.get("bugs")
        if isinstance(bugs, list):
            seq = 0
            for bug_obj in bugs:
                if not isinstance(bug_obj, dict):
                    continue
                events = bug_obj.get("history")
                if not isinstance(events, list):
                    continue
                for event in events:
                    if not isinstance(event, dict):
                        continue
                    seq += 1
                    out_path = document_output_path(
                        output_root=output_root,
                        candidate=candidate,
                        doc_type="HIST",
                        issue_id=issue_id,
                        item_id=str(seq),
                    )
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    out_path.write_text(
                        json.dumps(
                            tracker_document_envelope(
                                candidate=candidate,
                                issue=issue,
                                doc_type="HIST",
                                payload=event,
                                api_url=history_url,
                                item_id=str(seq),
                                note=history_error or (f"http_status={history_status}" if history_status and history_status >= 400 else None),
                            ),
                            ensure_ascii=False,
                            indent=2,
                        ),
                        encoding="utf-8",
                    )
                    saved_count += 1

    # ── Save ATTACH + Phase 2: Fetch ATTACH_DATA IN PARALLEL ──
    attachment_list: list[dict[str, Any]] = []
    if isinstance(attachments_payload, dict):
        bug_attachments = attachments_payload.get("bugs", {}).get(issue_id)
        if isinstance(bug_attachments, list):
            attachment_list = [a for a in bug_attachments if isinstance(a, dict)]

    for attachment in attachment_list:
        attachment_id = str(attachment.get("id") or "unknown")
        attach_out = document_output_path(
            output_root=output_root,
            candidate=candidate,
            doc_type="ATTACH",
            issue_id=issue_id,
            item_id=attachment_id,
        )
        attach_out.parent.mkdir(parents=True, exist_ok=True)
        attach_out.write_text(
            json.dumps(
                tracker_document_envelope(
                    candidate=candidate,
                    issue=issue,
                    doc_type="ATTACH",
                    payload=attachment,
                    api_url=attachments_url,
                    item_id=attachment_id,
                    note=attachments_error or (f"http_status={attachments_status}" if attachments_status and attachments_status >= 400 else None),
                ),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        saved_count += 1

    # Fetch all attachment details in parallel
    if attachment_list:
        attach_ids = [str(a.get("id") or "unknown") for a in attachment_list]
        attach_detail_results = await asyncio.gather(
            *[_fetch_json(client, f"{base_url}/bug/attachment/{aid}") for aid in attach_ids]
        )
        for attachment_id, (ad_status, ad_payload, ad_error) in zip(attach_ids, attach_detail_results):
            detail_payload_one = ad_payload
            if isinstance(ad_payload, dict):
                amap = ad_payload.get("attachments")
                if isinstance(amap, dict) and attachment_id in amap:
                    detail_payload_one = amap[attachment_id]
            attach_detail_out = document_output_path(
                output_root=output_root,
                candidate=candidate,
                doc_type="ATTACH_DATA",
                issue_id=issue_id,
                item_id=attachment_id,
            )
            attach_detail_out.parent.mkdir(parents=True, exist_ok=True)
            attach_detail_out.write_text(
                json.dumps(
                    tracker_document_envelope(
                        candidate=candidate,
                        issue=issue,
                        doc_type="ATTACH_DATA",
                        payload=detail_payload_one,
                        api_url=f"{base_url}/bug/attachment/{attachment_id}",
                        item_id=attachment_id,
                        note=ad_error or (f"http_status={ad_status}" if ad_status and ad_status >= 400 else None),
                    ),
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            saved_count += 1

    return saved_count


async def save_github_full_issue(
    *,
    candidate: ManifestCandidate,
    issue,
    output_root: Path,
    client: PoliteHttpClient,
) -> int:
    issue_id = str(issue.tracker_issue_id)
    saved_count = 0

    base_out = document_output_path(
        output_root=output_root,
        candidate=candidate,
        doc_type="BASE",
        issue_id=issue_id,
    )
    base_out.parent.mkdir(parents=True, exist_ok=True)
    base_out.write_text(
        json.dumps(issue_envelope(candidate, issue), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    saved_count += 1

    desc_out = document_output_path(
        output_root=output_root,
        candidate=candidate,
        doc_type="DESC",
        issue_id=issue_id,
    )
    desc_out.parent.mkdir(parents=True, exist_ok=True)
    desc_out.write_text(
        json.dumps(
            tracker_document_envelope(
                candidate=candidate,
                issue=issue,
                doc_type="DESC",
                payload={
                    "id": issue_id,
                    "title": issue.title,
                    "description": issue.body_plaintext,
                },
                api_url=issue.api_url,
            ),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    saved_count += 1

    comments_url = None
    if isinstance(issue.raw_payload, dict):
        comments_url = issue.raw_payload.get("comments_url")
    if comments_url:
        comments_status, comments_payload, comments_error = await _fetch_json(client, comments_url)
        if isinstance(comments_payload, list):
            for idx, comment in enumerate(comments_payload, start=1):
                if not isinstance(comment, dict):
                    continue
                comment_id = str(comment.get("id") or idx)
                out_path = document_output_path(
                    output_root=output_root,
                    candidate=candidate,
                    doc_type="CMT",
                    issue_id=issue_id,
                    item_id=comment_id,
                )
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(
                    json.dumps(
                        tracker_document_envelope(
                            candidate=candidate,
                            issue=issue,
                            doc_type="CMT",
                            payload=comment,
                            api_url=comments_url,
                            item_id=comment_id,
                            note=comments_error or (f"http_status={comments_status}" if comments_status and comments_status >= 400 else None),
                        ),
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                saved_count += 1

    return saved_count


async def save_gitlab_full_issue(
    *,
    candidate: ManifestCandidate,
    issue,
    output_root: Path,
    client: PoliteHttpClient,
) -> int:
    issue_id = str(issue.tracker_issue_id)
    saved_count = 0

    base_out = document_output_path(
        output_root=output_root,
        candidate=candidate,
        doc_type="BASE",
        issue_id=issue_id,
    )
    base_out.parent.mkdir(parents=True, exist_ok=True)
    base_out.write_text(
        json.dumps(issue_envelope(candidate, issue), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    saved_count += 1

    description = None
    if isinstance(issue.raw_payload, dict):
        description = issue.raw_payload.get("description")
    desc_out = document_output_path(
        output_root=output_root,
        candidate=candidate,
        doc_type="DESC",
        issue_id=issue_id,
    )
    desc_out.parent.mkdir(parents=True, exist_ok=True)
    desc_out.write_text(
        json.dumps(
            tracker_document_envelope(
                candidate=candidate,
                issue=issue,
                doc_type="DESC",
                payload={
                    "id": issue_id,
                    "title": issue.title,
                    "description": description,
                },
                api_url=issue.api_url,
            ),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    saved_count += 1

    notes_url = None
    if isinstance(issue.raw_payload, dict):
        links = issue.raw_payload.get("_links")
        if isinstance(links, dict):
            notes_url = links.get("notes")
    if notes_url:
        notes_status, notes_payload, notes_error = await _fetch_json(client, notes_url)
        if isinstance(notes_payload, list):
            for idx, note in enumerate(notes_payload, start=1):
                if not isinstance(note, dict):
                    continue
                note_id = str(note.get("id") or idx)
                out_path = document_output_path(
                    output_root=output_root,
                    candidate=candidate,
                    doc_type="CMT",
                    issue_id=issue_id,
                    item_id=note_id,
                )
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(
                    json.dumps(
                        tracker_document_envelope(
                            candidate=candidate,
                            issue=issue,
                            doc_type="CMT",
                            payload=note,
                            api_url=notes_url,
                            item_id=note_id,
                            note=notes_error or (f"http_status={notes_status}" if notes_status and notes_status >= 400 else None),
                        ),
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                saved_count += 1

    return saved_count


async def download_single_page(
    *,
    candidate: ManifestCandidate,
    entry,
    adapter,
    client: PoliteHttpClient,
    output_root: Path,
    mode: str,
    page_size: int,
) -> dict[str, Any]:
    s_path = state_path(output_root, candidate, mode)
    state = load_state(s_path)
    if state.get("completed"):
        return {
            "attempted": False,
            "saved_this_page": 0,
            "issues_saved": int(state.get("issues_saved", 0)),
            "next_cursor": state.get("cursor"),
            "completed": True,
            "last_issue_id": state.get("last_issue_id"),
        }

    cursor = state.get("cursor")
    pages_completed = int(state.get("pages_completed", 0))
    issues_saved = int(state.get("issues_saved", 0))
    page = await adapter.list_issues(
        entry,
        cursor=cursor,
        page_size=page_size,
        mode=mode,
        sample_limit=None,
    )

    if page.error:
        save_state(
            s_path,
            {
                "family": candidate.family_slug,
                "instance": candidate.instance_name,
                "entry": candidate.entry_name,
                "mode": mode,
                "cursor": cursor,
                "pages_completed": pages_completed,
                "issues_saved": issues_saved,
                "completed": False,
                "last_error": page.error,
                "updated_at_utc": now_utc().isoformat(),
            },
        )
        return {
            "attempted": True,
            "saved_this_page": 0,
            "issues_saved": issues_saved,
            "next_cursor": cursor,
            "completed": False,
            "last_issue_id": None,
            "error": page.error,
        }

    saved_this_page = 0
    last_issue_id = None

    # Parallelize per-issue saving within a page
    async def _save_one_issue(issue):
        iid = str(issue.tracker_issue_id)
        if candidate.family_slug == "bugzilla":
            return iid, await save_bugzilla_full_issue(
                candidate=candidate, issue=issue,
                output_root=output_root, client=client,
            )
        elif candidate.family_slug == "github":
            return iid, await save_github_full_issue(
                candidate=candidate, issue=issue,
                output_root=output_root, client=client,
            )
        elif candidate.family_slug == "gitlab":
            return iid, await save_gitlab_full_issue(
                candidate=candidate, issue=issue,
                output_root=output_root, client=client,
            )
        else:
            out_path = issue_output_path(output_root, candidate, iid)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(
                json.dumps(issue_envelope(candidate, issue), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return iid, 1

    if page.issues:
        results = await asyncio.gather(*[_save_one_issue(iss) for iss in page.issues])
        for iid, count in results:
            saved_this_page += count
            last_issue_id = iid

    issues_saved += saved_this_page
    pages_completed += 1

    # Respect the adapter's explicit pagination decision.
    # Some adapters always populate next_params for diagnostics even on the final page.
    next_cursor = page.next_cursor

    completed = next_cursor is None
    save_state(
        s_path,
        {
            "family": candidate.family_slug,
            "instance": candidate.instance_name,
            "entry": candidate.entry_name,
            "mode": mode,
            "cursor": next_cursor,
            "pages_completed": pages_completed,
            "issues_saved": issues_saved,
            "completed": completed,
            "last_issue_id": last_issue_id,
            "updated_at_utc": now_utc().isoformat(),
        },
    )
    return {
        "attempted": True,
        "saved_this_page": saved_this_page,
        "issues_saved": issues_saved,
        "next_cursor": next_cursor,
        "completed": completed,
        "last_issue_id": last_issue_id,
    }


async def download_candidate(
    *,
    candidate: ManifestCandidate,
    adapter,
    client: PoliteHttpClient,
    output_root: Path,
    mode: str,
    page_size: int,
    max_pages: int | None,
) -> None:
    _, _, entry = build_runtime_models(candidate)
    state = load_state(state_path(output_root, candidate, mode))
    if state.get("completed"):
        print(f"[DONE] {candidate.family_slug}:{candidate.entry_name} already completed")
        return

    print(
        f"[START] {candidate.family_slug}:{candidate.instance_name}:{candidate.entry_name} "
        f"cursor={state.get('cursor')!r} pages_completed={int(state.get('pages_completed', 0))} "
        f"issues_saved={int(state.get('issues_saved', 0))}"
    )

    page_loops = 0
    while True:
        page_result = await download_single_page(
            candidate=candidate,
            entry=entry,
            adapter=adapter,
            client=client,
            output_root=output_root,
            mode=mode,
            page_size=page_size,
        )
        if page_result.get("error"):
            print(f"[ERR] {candidate.family_slug}:{candidate.entry_name} error={page_result['error']}")
            return

        print(
            f"[PAGE] {candidate.family_slug}:{candidate.entry_name} "
            f"page={load_state(state_path(output_root, candidate, mode)).get('pages_completed')} "
            f"saved={page_result['saved_this_page']} total={page_result['issues_saved']} "
            f"next_cursor={page_result['next_cursor']!r}"
        )

        page_loops += 1
        if page_result["completed"]:
            print(f"[DONE] {candidate.family_slug}:{candidate.entry_name} issues_saved={page_result['issues_saved']}")
            return

        if max_pages is not None and page_loops >= max_pages:
            print(f"[STOP] {candidate.family_slug}:{candidate.entry_name} reached max_pages={max_pages}")
            return


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download manifest entry issues to local JSON files with resume.")
    parser.add_argument("--manifest-path", default="manifests/sample.manifest.yaml")
    parser.add_argument("--output-dir", default="artifacts/json_downloads")
    parser.add_argument("--mode", choices=["closed", "all"], default="closed")
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--families", default=None, help="comma-separated family slugs")
    parser.add_argument("--entries", default=None, help="comma-separated entry names")
    parser.add_argument("--max-pages", type=int, default=None)
    return parser.parse_args()


async def async_main() -> None:
    load_env_file(".env")
    args = parse_args()
    cfg = AppConfig()

    _, candidates = ManifestLoader(Path(args.manifest_path)).load()
    if args.families:
        family_set = {x.strip() for x in args.families.split(",") if x.strip()}
        candidates = [c for c in candidates if c.family_slug in family_set]
    if args.entries:
        entry_set = {x.strip() for x in args.entries.split(",") if x.strip()}
        candidates = [c for c in candidates if c.entry_name in entry_set]

    output_root = Path(args.output_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    client = PoliteHttpClient(cfg)
    try:
        for candidate in candidates:
            adapter_cls = adapter_for_family(candidate.family_slug)
            if adapter_cls is None:
                print(f"[SKIP] {candidate.family_slug}:{candidate.entry_name} unsupported_family")
                continue
            adapter = adapter_cls(session_factory=None, client=client, config=cfg)
            await download_candidate(
                candidate=candidate,
                adapter=adapter,
                client=client,
                output_root=output_root,
                mode=args.mode,
                page_size=args.page_size,
                max_pages=args.max_pages,
            )
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(async_main())
