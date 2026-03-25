"""Discover all products from Mozilla and Eclipse Bugzilla instances,
measure each product's approximate issue count via binary-search offset probe,
and save the results to artifacts/bugzilla_all_products.json.

Re-uses ApiClient, extract_statuses, fetch_bug_page, and
find_last_bugzilla_visible_id from find_last_bugzilla_ids_manifest.py.

Usage:
    python scripts/discover_bugzilla_products.py [--output artifacts/bugzilla_all_products.json]
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

from find_last_bugzilla_ids_manifest import (  # noqa: E402
    ApiClient,
    find_last_bugzilla_visible_id,
    load_env_file,
)


INSTANCES: dict[str, str] = {
    "mozilla": "https://bugzilla.mozilla.org/rest",
    "eclipse": "https://bugs.eclipse.org/bugs/rest",
}

VISIBLE_OFFSET_RE = re.compile(r"last_visible_offset=(\d+)")


def discover_products(api: ApiClient, base_url: str) -> list[str]:
    """List all product names from a Bugzilla instance."""
    st, payload = api.get_json(
        f"{base_url}/product",
        params={"type": "accessible", "include_fields": "name,id"},
    )
    if st >= 400 or not isinstance(payload, dict):
        print(f"[WARN] product listing failed for {base_url}: HTTP {st}")
        return []

    products = payload.get("products")
    if not isinstance(products, list):
        print(f"[WARN] no products array in response from {base_url}")
        return []

    names: list[str] = []
    for item in products:
        if isinstance(item, dict) and item.get("name"):
            names.append(str(item["name"]))
    return sorted(names)


def product_to_entry_name(product_name: str) -> str:
    """Convert a Bugzilla product name to a manifest entry_name.

    Rules: lowercase, replace spaces/special chars with underscore,
    collapse multiple underscores, strip leading/trailing underscores.
    """
    name = product_name.lower()
    name = re.sub(r"[^a-z0-9]+", "_", name)
    name = re.sub(r"_+", "_", name)
    return name.strip("_")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Discover all Bugzilla products from Mozilla and Eclipse and probe issue counts."
    )
    parser.add_argument(
        "--output",
        default="artifacts/bugzilla_all_products.json",
        help="Output JSON file path.",
    )
    parser.add_argument(
        "--instances",
        default=None,
        help="Comma-separated instance filter, e.g. mozilla,eclipse. Default: all.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="If output file exists, skip products already probed.",
    )
    return parser.parse_args()


def main() -> None:
    load_env_file(".env")
    args = parse_args()
    out_path = Path(args.output)

    instance_filter = None
    if args.instances:
        instance_filter = {x.strip() for x in args.instances.split(",") if x.strip()}

    # Load existing results for resume
    existing: dict[tuple[str, str], dict[str, Any]] = {}
    if args.resume and out_path.exists():
        try:
            for item in json.loads(out_path.read_text(encoding="utf-8")):
                key = (item.get("instance", ""), item.get("product", ""))
                existing[key] = item
            print(f"[RESUME] loaded {len(existing)} existing results from {out_path}")
        except Exception as exc:
            print(f"[WARN] could not load existing results: {exc}")

    api = ApiClient(timeout=30.0)
    results: list[dict[str, Any]] = []

    try:
        for instance_name, base_url in sorted(INSTANCES.items()):
            if instance_filter and instance_name not in instance_filter:
                continue

            print(f"\n[DISCOVER] {instance_name} ({base_url})")
            products = discover_products(api, base_url)
            print(f"[DISCOVER] found {len(products)} products for {instance_name}")

            for idx, product_name in enumerate(products, 1):
                entry_name = product_to_entry_name(product_name)
                key = (instance_name, product_name)

                # Resume: skip already probed
                if key in existing:
                    results.append(existing[key])
                    approx = existing[key].get("approx_issues", "?")
                    print(f"  [{idx}/{len(products)}] {product_name} -> (resumed, ~{approx} issues)")
                    continue

                print(f"  [{idx}/{len(products)}] {product_name} -> probing...", end=" ", flush=True)

                probe = find_last_bugzilla_visible_id(api, base=base_url, product=product_name)
                approx_issues = 0
                if probe.get("status") == "ok":
                    m = VISIBLE_OFFSET_RE.search(probe.get("note", ""))
                    if m:
                        approx_issues = int(m.group(1)) + 1

                row: dict[str, Any] = {
                    "instance": instance_name,
                    "product": product_name,
                    "entry_name": entry_name,
                    "approx_issues": approx_issues,
                    "probe_status": probe.get("status", "unknown"),
                    "last_issue_id": probe.get("last_issue_id"),
                    "note": probe.get("note", ""),
                }
                results.append(row)
                print(f"~{approx_issues} issues (status={probe.get('status')})")

                # Incremental save every 10 products
                if idx % 10 == 0:
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    out_path.write_text(
                        json.dumps(results, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )

                # Rate limit courtesy
                time.sleep(0.5)

    finally:
        api.close()

    # Final save
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Summary
    total_issues = sum(r.get("approx_issues", 0) for r in results)
    mozilla_count = sum(1 for r in results if r["instance"] == "mozilla")
    eclipse_count = sum(1 for r in results if r["instance"] == "eclipse")
    mozilla_issues = sum(r.get("approx_issues", 0) for r in results if r["instance"] == "mozilla")
    eclipse_issues = sum(r.get("approx_issues", 0) for r in results if r["instance"] == "eclipse")

    print(f"\n[DONE] Saved {len(results)} products to {out_path}")
    print(f"  Mozilla: {mozilla_count} products, ~{mozilla_issues:,} issues")
    print(f"  Eclipse: {eclipse_count} products, ~{eclipse_issues:,} issues")
    print(f"  Total:   {len(results)} products, ~{total_issues:,} issues")


if __name__ == "__main__":
    main()
