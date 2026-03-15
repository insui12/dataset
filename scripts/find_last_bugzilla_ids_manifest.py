from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

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
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and ((val[0] == '"' and val[-1] == '"') or (val[0] == "'" and val[-1] == "'")):
            val = val[1:-1]
        os.environ.setdefault(key, val)


class ApiClient:
    def __init__(self, timeout: float = 20.0):
        self.c = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": os.getenv("GBTD_USER_AGENT", "GBTD-Bugzilla-LastOffset/0.1")},
            follow_redirects=True,
        )

    def close(self) -> None:
        self.c.close()

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> tuple[int, Any]:
        for attempt in range(1, 6):
            try:
                r = self.c.get(url, params=params)
            except httpx.RequestError:
                time.sleep(min(2 * attempt, 20))
                continue

            if r.status_code == 429:
                retry_after = r.headers.get("Retry-After")
                wait = 2 * attempt
                if retry_after:
                    try:
                        wait = max(wait, int(float(retry_after)))
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


def extract_statuses(api: ApiClient, base: str) -> list[str]:
    st, payload = api.get_json(f"{base}/configuration")
    if st >= 400 or not isinstance(payload, dict):
        return []
    bug_status = payload.get("bug_status")
    if not isinstance(bug_status, list):
        return []
    values: list[str] = []
    for item in bug_status:
        if isinstance(item, dict) and item.get("name"):
            values.append(str(item["name"]))
        elif isinstance(item, str):
            values.append(item)
    return values


def fetch_bug_page(
    api: ApiClient,
    *,
    base: str,
    product: str,
    statuses: list[str],
    offset: int,
    limit: int = 1,
) -> tuple[str, list[dict[str, Any]], str | None]:
    params: dict[str, Any] = {
        "product": product,
        "include_fields": "id,summary,product",
        "limit": limit,
        "offset": max(0, offset),
    }
    if statuses:
        params["bug_status"] = statuses

    st, payload = api.get_json(f"{base}/bug", params=params)
    if st >= 400:
        return "error", [], f"http {st}"
    if not isinstance(payload, dict):
        return "error", [], "payload not dict"

    bugs = payload.get("bugs")
    if not isinstance(bugs, list):
        return "error", [], "bugs field missing"
    if not bugs:
        return "empty", [], None

    normalized: list[dict[str, Any]] = []
    for bug in bugs:
        if isinstance(bug, dict) and bug.get("id") is not None:
            normalized.append(bug)

    if not normalized:
        return "empty", [], None
    return "ok", normalized, None


def find_last_bugzilla_visible_id(api: ApiClient, *, base: str, product: str) -> dict[str, Any]:
    statuses = extract_statuses(api, base)

    first_state, first_bugs, first_note = fetch_bug_page(
        api,
        base=base,
        product=product,
        statuses=statuses,
        offset=0,
        limit=1,
    )
    if first_state == "error":
        return {"status": "error", "note": first_note}
    if first_state == "empty":
        return {"status": "empty", "note": "no visible bugs for product"}

    lo = 0
    hi = 1

    while True:
        state, bugs, note = fetch_bug_page(
            api,
            base=base,
            product=product,
            statuses=statuses,
            offset=hi,
            limit=1,
        )
        if state == "error":
            return {"status": "error", "note": note or f"probe failed at offset {hi}"}
        if state == "empty":
            break
        lo = hi
        hi *= 2
        if hi > 100_000_000:
            return {"status": "error", "note": "offset probe exceeded hard cap"}

    while lo + 1 < hi:
        mid = (lo + hi) // 2
        state, bugs, note = fetch_bug_page(
            api,
            base=base,
            product=product,
            statuses=statuses,
            offset=mid,
            limit=1,
        )
        if state == "error":
            return {"status": "error", "note": note or f"probe failed at offset {mid}"}
        if state == "empty":
            hi = mid
        else:
            lo = mid

    final_state, final_bugs, final_note = fetch_bug_page(
        api,
        base=base,
        product=product,
        statuses=statuses,
        offset=lo,
        limit=1,
    )
    if final_state != "ok" or not final_bugs:
        return {"status": "error", "note": final_note or "final fetch failed"}

    last_bug = final_bugs[-1]
    return {
        "status": "ok",
        "last_issue_id": last_bug.get("id"),
        "last_issue_key": str(last_bug.get("id")) if last_bug.get("id") is not None else None,
        "method": "bugzilla_offset_probe",
        "note": f"last_visible_offset={lo}",
    }


def main() -> None:
    load_env_file(".env")
    manifest_path = os.getenv("GBTD_MANIFEST_PATH", "manifests/sample.manifest.yaml")
    payload = yaml.safe_load(Path(manifest_path).read_text(encoding="utf-8"))

    api = ApiClient(timeout=float(os.getenv("GBTD_TIMEOUT_SECONDS", "20")))
    results: list[dict[str, Any]] = []

    try:
        for family in payload.get("families", []):
            if family.get("slug") != "bugzilla":
                continue
            for inst in family.get("instances", []):
                base = (inst.get("api_base_url") or inst.get("base_url") or "").rstrip("/")
                for ent in inst.get("entries", []):
                    product = ent.get("tracker_api_key") or ent.get("tracker_id") or ent.get("name")
                    row = {
                        "family": "bugzilla",
                        "instance": inst.get("name"),
                        "entry": ent.get("name"),
                        "tracker_id": ent.get("tracker_id"),
                        "tracker_api_key": ent.get("tracker_api_key"),
                    }
                    row.update(find_last_bugzilla_visible_id(api, base=base, product=str(product)))
                    results.append(row)
                    print(json.dumps(row, ensure_ascii=False))
    finally:
        api.close()

    out = Path("artifacts/last_bugzilla_issue_per_manifest.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()
