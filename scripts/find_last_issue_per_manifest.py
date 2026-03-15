from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import yaml


def load_env_file(path: str = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()
        if len(v) >= 2 and ((v[0] == '"' and v[-1] == '"') or (v[0] == "'" and v[-1] == "'")):
            v = v[1:-1]
        os.environ.setdefault(k, v)


class ApiClient:
    def __init__(self, timeout: float = 20.0):
        self.c = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": os.getenv("GBTD_USER_AGENT", "GBTD-FindLast/0.1")},
            follow_redirects=True,
        )

    def close(self) -> None:
        self.c.close()

    def get_json(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        for attempt in range(1, 6):
            try:
                r = self.c.get(url, params=params, headers=headers)
            except httpx.RequestError:
                time.sleep(min(2 * attempt, 20))
                continue

            if r.status_code == 429:
                ra = r.headers.get("Retry-After")
                wait = 2 * attempt
                if ra:
                    try:
                        wait = max(wait, int(float(ra)))
                    except Exception:
                        pass
                time.sleep(min(wait, 120))
                continue

            if 500 <= r.status_code < 600:
                time.sleep(min(2 * attempt, 30))
                continue

            try:
                return r.status_code, r.json()
            except Exception:
                return r.status_code, None

        return 599, None


def gh_headers() -> dict[str, str]:
    t = os.getenv("GBTD_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")
    return {"Authorization": f"Bearer {t}"} if t else {}


def gl_headers() -> dict[str, str]:
    t = os.getenv("GBTD_GITLAB_TOKEN") or os.getenv("GITLAB_TOKEN")
    return {"PRIVATE-TOKEN": t} if t else {}


def jira_headers() -> dict[str, str]:
    t = os.getenv("GBTD_JIRA_TOKEN") or os.getenv("JIRA_TOKEN")
    return {"Authorization": f"Bearer {t}"} if t else {}


def yt_headers() -> dict[str, str]:
    t = os.getenv("GBTD_YOUTRACK_TOKEN") or os.getenv("YOUTRACK_TOKEN")
    if not t:
        return {}
    return {"Authorization": f"Bearer {t}", "Accept": "application/json"}


def find_bugzilla_last(api: ApiClient, inst: dict[str, Any], ent: dict[str, Any]) -> dict[str, Any]:
    base = (inst.get("api_base_url") or inst.get("base_url") or "").rstrip("/")
    key = ent.get("tracker_api_key") or ent.get("tracker_id") or ent.get("name")
    if not key:
        return {"status": "error", "note": "missing product key"}

    st, cfg = api.get_json(f"{base}/configuration")
    statuses: list[str] = []
    if st < 400 and isinstance(cfg, dict):
        bs = cfg.get("bug_status")
        if isinstance(bs, list):
            for x in bs:
                if isinstance(x, dict) and x.get("name"):
                    statuses.append(str(x["name"]))
                elif isinstance(x, str):
                    statuses.append(x)

    def fetch_page(offset: int, limit: int = 1) -> tuple[str, list[dict[str, Any]], str | None]:
        params: dict[str, Any] = {
            "product": key,
            "include_fields": "id,summary,product",
            "limit": limit,
            "offset": max(0, offset),
        }
        if statuses:
            params["bug_status"] = statuses
        st_page, payload_page = api.get_json(f"{base}/bug", params=params)
        if st_page >= 400:
            return "error", [], f"http {st_page}"
        if not isinstance(payload_page, dict):
            return "error", [], "payload not dict"
        bugs = payload_page.get("bugs")
        if not isinstance(bugs, list):
            return "error", [], "bugs field missing"
        normalized = [b for b in bugs if isinstance(b, dict) and b.get("id") is not None]
        if not normalized:
            return "empty", [], None
        return "ok", normalized, None

    state0, bugs0, note0 = fetch_page(0, 1)
    if state0 == "error":
        return {"status": "error", "note": note0}
    if state0 == "empty":
        return {"status": "empty", "note": "no visible bugs for product"}

    lo = 0
    hi = 1
    while True:
        state_hi, bugs_hi, note_hi = fetch_page(hi, 1)
        if state_hi == "error":
            return {"status": "error", "note": note_hi or f"probe failed at offset {hi}"}
        if state_hi == "empty":
            break
        lo = hi
        hi *= 2
        if hi > 100_000_000:
            return {"status": "error", "note": "offset probe exceeded hard cap"}

    while lo + 1 < hi:
        mid = (lo + hi) // 2
        state_mid, bugs_mid, note_mid = fetch_page(mid, 1)
        if state_mid == "error":
            return {"status": "error", "note": note_mid or f"probe failed at offset {mid}"}
        if state_mid == "empty":
            hi = mid
        else:
            lo = mid

    state_last, bugs_last, note_last = fetch_page(lo, 1)
    if state_last != "ok" or not bugs_last:
        return {"status": "error", "note": note_last or "final fetch failed"}

    last_bug = bugs_last[-1]
    return {
        "status": "ok",
        "last_issue_id": last_bug.get("id"),
        "last_issue_key": str(last_bug.get("id")) if last_bug.get("id") is not None else None,
        "method": "bugzilla_offset_probe",
        "note": f"last_visible_offset={lo}",
    }


def find_github_last(api: ApiClient, inst: dict[str, Any], ent: dict[str, Any]) -> dict[str, Any]:
    base = (inst.get("api_base_url") or "https://api.github.com").rstrip("/")
    repo = ent.get("tracker_api_key") or ent.get("name")
    url = f"{base}/repos/{quote(repo, safe='/')}/issues"
    st, payload = api.get_json(
        url,
        params={"state": "all", "sort": "created", "direction": "desc", "per_page": 30, "page": 1},
        headers=gh_headers(),
    )
    if st >= 400:
        return {"status": "error", "note": f"http {st}"}
    if not isinstance(payload, list):
        return {"status": "error", "note": "payload not list"}

    for it in payload:
        if isinstance(it, dict) and not it.get("pull_request"):
            return {
                "status": "ok",
                "last_issue_id": it.get("id"),
                "last_issue_key": str(it.get("number")) if it.get("number") is not None else None,
                "method": "github_created_desc",
            }
    return {"status": "empty", "note": "no issue (only PR or empty)"}


def find_gitlab_last(api: ApiClient, inst: dict[str, Any], ent: dict[str, Any]) -> dict[str, Any]:
    base = (inst.get("api_base_url") or "https://gitlab.com/api/v4").rstrip("/")
    proj = ent.get("tracker_api_key") or ent.get("tracker_id") or ent.get("name")
    url = f"{base}/projects/{quote(str(proj), safe='')}/issues"
    st, payload = api.get_json(
        url,
        params={"state": "all", "order_by": "created_at", "sort": "desc", "per_page": 1, "page": 1},
        headers=gl_headers(),
    )
    if st >= 400:
        return {"status": "error", "note": f"http {st}"}
    if isinstance(payload, list) and payload:
        it = payload[0]
        return {
            "status": "ok",
            "last_issue_id": it.get("id"),
            "last_issue_key": str(it.get("iid")) if it.get("iid") is not None else None,
            "method": "gitlab_created_desc",
        }
    return {"status": "empty", "note": "no issues"}


def find_jira_last(api: ApiClient, inst: dict[str, Any], ent: dict[str, Any]) -> dict[str, Any]:
    base = (inst.get("api_base_url") or inst.get("base_url") or "").rstrip("/")
    proj = ent.get("tracker_api_key") or ent.get("tracker_id") or ent.get("name")
    proj_escaped = str(proj).replace('"', '\\"')
    jql = f'project="{proj_escaped}" ORDER BY created DESC'

    for path in ("/rest/api/2/search", "/rest/api/3/search"):
        st, payload = api.get_json(
            f"{base}{path}",
            params={"jql": jql, "startAt": 0, "maxResults": 1, "fields": "id,key"},
            headers=jira_headers(),
        )
        if st >= 400:
            continue
        if isinstance(payload, dict):
            issues = payload.get("issues")
            if isinstance(issues, list) and issues:
                it = issues[0]
                return {
                    "status": "ok",
                    "last_issue_id": it.get("id"),
                    "last_issue_key": it.get("key"),
                    "method": f"jira_created_desc:{path}",
                }
            return {"status": "empty", "note": "no issues"}
    return {"status": "error", "note": "jira search failed"}


def find_redmine_last(api: ApiClient, inst: dict[str, Any], ent: dict[str, Any]) -> dict[str, Any]:
    base = (inst.get("api_base_url") or inst.get("base_url") or "").rstrip("/")
    project_id = ent.get("tracker_id") or ent.get("tracker_api_key")
    st, payload = api.get_json(
        f"{base}/issues.json",
        params={"project_id": project_id, "status_id": "*", "limit": 1, "offset": 0, "sort": "id:desc"},
    )
    if st >= 400:
        return {"status": "error", "note": f"http {st}"}
    if isinstance(payload, dict):
        issues = payload.get("issues")
        if isinstance(issues, list) and issues:
            it = issues[0]
            return {
                "status": "ok",
                "last_issue_id": it.get("id"),
                "last_issue_key": str(it.get("id")) if it.get("id") is not None else None,
                "method": "redmine_id_desc",
            }
    return {"status": "empty", "note": "no issues"}


def find_youtrack_last(api: ApiClient, inst: dict[str, Any], ent: dict[str, Any]) -> dict[str, Any]:
    base = (inst.get("api_base_url") or inst.get("base_url") or "").rstrip("/")
    project = ent.get("tracker_api_key") or ent.get("tracker_id") or ent.get("name")
    st, payload = api.get_json(
        f"{base}/issues",
        params={
            "$top": 1,
            "$skip": 0,
            "query": f"project:{project} sort by: created desc",
            "fields": "id,idReadable,numberInProject,created",
        },
        headers=yt_headers(),
    )
    if st >= 400:
        return {"status": "error", "note": f"http {st}"}
    if isinstance(payload, list) and payload:
        it = payload[0]
        return {
            "status": "ok",
            "last_issue_id": it.get("id"),
            "last_issue_key": it.get("idReadable") or (f"{project}-{it.get('numberInProject')}" if it.get("numberInProject") else None),
            "method": "youtrack_created_desc",
        }
    return {"status": "empty", "note": "no issues"}


def main() -> None:
    load_env_file(".env")
    manifest_path = os.getenv("GBTD_MANIFEST_PATH", "manifests/sample.manifest.yaml")
    m = yaml.safe_load(Path(manifest_path).read_text(encoding="utf-8"))

    api = ApiClient(timeout=float(os.getenv("GBTD_TIMEOUT_SECONDS", "20")))
    results: list[dict[str, Any]] = []

    try:
        for fam in m.get("families", []):
            fslug = fam.get("slug")
            for inst in fam.get("instances", []):
                iname = inst.get("name")
                for ent in inst.get("entries", []):
                    row = {
                        "family": fslug,
                        "instance": iname,
                        "entry": ent.get("name"),
                        "tracker_id": ent.get("tracker_id"),
                        "tracker_api_key": ent.get("tracker_api_key"),
                    }

                    if fslug == "bugzilla":
                        row.update(find_bugzilla_last(api, inst, ent))
                    elif fslug == "github":
                        row.update(find_github_last(api, inst, ent))
                    elif fslug == "gitlab":
                        row.update(find_gitlab_last(api, inst, ent))
                    elif fslug == "jira":
                        row.update(find_jira_last(api, inst, ent))
                    elif fslug == "redmine":
                        row.update(find_redmine_last(api, inst, ent))
                    elif fslug == "youtrack":
                        row.update(find_youtrack_last(api, inst, ent))
                    else:
                        row.update({"status": "unsupported_family", "note": "not implemented in this script"})

                    results.append(row)
                    print(json.dumps(row, ensure_ascii=False))
    finally:
        api.close()

    out = Path("artifacts/last_issue_per_manifest.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()
