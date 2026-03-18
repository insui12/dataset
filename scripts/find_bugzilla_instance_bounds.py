from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx


DEFAULT_ALL_STATUSES = [
    "UNCONFIRMED",
    "NEW",
    "ASSIGNED",
    "REOPENED",
    "NEEDSINFO",
    "RESOLVED",
    "VERIFIED",
    "CLOSED",
    "FIXED",
    "WONTFIX",
    "DUPLICATE",
    "INVALID",
    "NOTABUG",
    "BYDESIGN",
]


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


def request_json(
    client: httpx.Client,
    url: str,
    *,
    params: Any = None,
    max_retries: int = 5,
) -> tuple[int, Any]:
    last_status = 0
    last_payload: Any = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = client.get(url, params=params)
        except httpx.RequestError:
            time.sleep(min(2 * attempt, 10))
            continue
        last_status = resp.status_code
        try:
            last_payload = resp.json()
        except Exception:
            last_payload = None

        if resp.status_code == 429:
            time.sleep(min(5 * attempt, 30))
            continue
        if 500 <= resp.status_code < 600:
            time.sleep(min(2 * attempt, 10))
            continue
        return last_status, last_payload
    return last_status, last_payload


def list_statuses(client: httpx.Client, base_url: str) -> list[str]:
    status, payload = request_json(client, f"{base_url}/configuration")
    if status < 400 and isinstance(payload, dict):
        bug_status = payload.get("bug_status")
        names: list[str] = []
        if isinstance(bug_status, list):
            for item in bug_status:
                if isinstance(item, dict) and item.get("name"):
                    names.append(str(item["name"]))
                elif isinstance(item, str):
                    names.append(item)
        if names:
            return sorted(set(names))
    return DEFAULT_ALL_STATUSES


def list_product_ids(client: httpx.Client, base_url: str) -> list[str]:
    ids: set[str] = set()
    for path in ("/product_selectable", "/product_accessible", "/product_enterable"):
        status, payload = request_json(client, f"{base_url}{path}")
        if status >= 400 or not isinstance(payload, dict):
            continue
        raw_ids = payload.get("ids")
        if isinstance(raw_ids, list):
            for item in raw_ids:
                ids.add(str(item))
    return sorted(ids, key=lambda x: (len(x), x))


def resolve_product_names(client: httpx.Client, base_url: str, product_ids: list[str]) -> list[str]:
    names: set[str] = set()
    for pid in product_ids:
        status, payload = request_json(client, f"{base_url}/product/{pid}", params={"include_fields": "name,id"})
        if status >= 400 or not isinstance(payload, dict):
            continue
        products = payload.get("products")
        if not isinstance(products, list):
            continue
        for product in products:
            if isinstance(product, dict) and product.get("name"):
                names.add(str(product["name"]))
    return sorted(names)


def bug_page_for_product(
    client: httpx.Client,
    base_url: str,
    *,
    product: str,
    offset: int,
    statuses: list[str],
) -> list[dict[str, Any]]:
    params: list[tuple[str, str]] = [
        ("product", product),
        ("include_fields", "id"),
        ("limit", "1"),
        ("offset", str(max(0, offset))),
        ("order", "bug_id"),
        ("sort", "ASC"),
    ]
    for status in statuses:
        params.append(("bug_status", status))
    status_code, payload = request_json(client, f"{base_url}/bug", params=params)
    if status_code >= 400 or not isinstance(payload, dict):
        return []
    bugs = payload.get("bugs")
    if not isinstance(bugs, list):
        return []
    return [bug for bug in bugs if isinstance(bug, dict)]


def find_last_visible_for_product(
    client: httpx.Client,
    base_url: str,
    *,
    product: str,
    statuses: list[str],
) -> dict[str, Any]:
    first_page = bug_page_for_product(client, base_url, product=product, offset=0, statuses=statuses)
    if not first_page:
        return {
            "product": product,
            "status": "empty",
            "last_issue_id": None,
            "last_visible_offset": None,
            "visible_issue_count": 0,
        }

    low = 0
    high = 1
    while True:
        page = bug_page_for_product(client, base_url, product=product, offset=high, statuses=statuses)
        if not page:
            break
        low = high
        high *= 2
        if high > 5_000_000:
            break

    while low + 1 < high:
        mid = (low + high) // 2
        page = bug_page_for_product(client, base_url, product=product, offset=mid, statuses=statuses)
        if page:
            low = mid
        else:
            high = mid

    last_page = bug_page_for_product(client, base_url, product=product, offset=low, statuses=statuses)
    last_issue_id = None
    if last_page and isinstance(last_page[0], dict):
        last_issue_id = last_page[0].get("id")

    return {
        "product": product,
        "status": "ok",
        "last_issue_id": last_issue_id,
        "last_visible_offset": low,
        "visible_issue_count": low + 1,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Find whole-instance Bugzilla bounds by probing all products.")
    parser.add_argument("--base-url", required=True, help="Bugzilla REST base URL, e.g. https://bugzilla.mozilla.org/rest")
    parser.add_argument("--instance-name", required=True, help="Label used in output")
    parser.add_argument("--output-path", default=None, help="Optional JSON output path")
    parser.add_argument("--max-products", type=int, default=None, help="Optional limit for debugging")
    return parser.parse_args()


def main() -> None:
    load_env_file(".env")
    args = parse_args()

    with httpx.Client(timeout=60.0, headers={"User-Agent": "GBTD-InstanceBounds/0.1"}) as client:
        statuses = list_statuses(client, args.base_url.rstrip("/"))
        product_ids = list_product_ids(client, args.base_url.rstrip("/"))
        product_names = resolve_product_names(client, args.base_url.rstrip("/"), product_ids)

        if args.max_products is not None:
            product_names = product_names[: args.max_products]

        rows: list[dict[str, Any]] = []
        for idx, product in enumerate(product_names, start=1):
            row = find_last_visible_for_product(
                client,
                args.base_url.rstrip("/"),
                product=product,
                statuses=statuses,
            )
            rows.append(row)
            print(json.dumps({"index": idx, **row}, ensure_ascii=False))

    ok_rows = [row for row in rows if row["status"] == "ok" and row["last_issue_id"] is not None]
    total_visible_issues = sum(int(row["visible_issue_count"]) for row in ok_rows)
    max_last_issue_id = max((int(row["last_issue_id"]) for row in ok_rows), default=None)

    summary = {
        "instance": args.instance_name,
        "base_url": args.base_url.rstrip("/"),
        "product_count": len(product_names),
        "products_with_visible_issues": len(ok_rows),
        "whole_instance_max_visible_bug_id": max_last_issue_id,
        "total_visible_issues_across_products": total_visible_issues,
        "products": rows,
    }

    print("\nSUMMARY")
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if args.output_path:
        output_path = Path(args.output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
