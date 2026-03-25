"""Generate team_assignments.yaml and update sample.manifest.yaml
with all discovered Mozilla/Eclipse products.

Reads: artifacts/bugzilla_all_products.json (from discover_bugzilla_products.py)
Updates: manifests/sample.manifest.yaml (add new entries)
Updates: manifests/team_assignments.yaml (assign new products to teams)

Usage:
    python scripts/generate_team_assignments.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml


# Fixed team assignments:
# - Non-Mozilla/Eclipse entries follow README_team_collection.md
# - Mozilla/Eclipse "anchor" products follow user-specified split
EXISTING_ASSIGNMENTS: dict[str, list[str]] = {
    "A": [
        # README (non-Mozilla/Eclipse)
        "gitlab-org/gitlab", "gitlab-org/gitlab-runner",
        "gitlab-org/gitaly", "gitlab-org/omnibus-gitlab",
        "kernel", "apache/airflow",
        # Mozilla/Eclipse anchors (user-specified)
        "eclipse_platform", "z_archived",
        "thunderbird", "firefox_os_graveyard",
        "mailnews_core", "core_graveyard",
    ],
    "B": [
        # README (non-Mozilla/Eclipse)
        "microsoft/vscode", "llvm/llvm-project",
        "nodejs/node", "moby/moby",
        "freebsd",
        # Mozilla/Eclipse anchors (user-specified)
        "firefox", "seamonkey",
        "toolkit", "devtools",
        "testing", "firefox_for_android_graveyard",
    ],
    "C": [
        # README (non-Mozilla/Eclipse)
        "rust-lang/rust", "python/cpython",
        "kubernetes/kubernetes",
        "gcc", "libreoffice",
        # Mozilla/Eclipse anchors (user-specified)
        "core", "cdt",
        "calendar", "firefox_for_android",
        "invalid_bugs", "equinox",
    ],
}

# All mozilla/eclipse entries already assigned above
EXISTING_BUGZILLA_ENTRIES: dict[str, set[str]] = {
    "mozilla": {
        "firefox", "thunderbird", "core", "toolkit", "devtools",
        "seamonkey", "testing", "firefox_for_android_graveyard",
        "firefox_os_graveyard", "mailnews_core", "core_graveyard",
        "calendar", "firefox_for_android", "invalid_bugs",
    },
    "eclipse": {"eclipse_platform", "z_archived", "cdt", "equinox"},
}


def product_to_entry_name(product_name: str) -> str:
    name = product_name.lower()
    name = re.sub(r"[^a-z0-9]+", "_", name)
    name = re.sub(r"_+", "_", name)
    return name.strip("_")


def load_discovered_products(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [p for p in data if p.get("probe_status") == "ok" and p.get("approx_issues", 0) > 0]


def assign_new_products_to_teams(
    new_products: list[dict[str, Any]],
    existing_assignments: dict[str, list[str]],
    existing_bugzilla_issues: dict[str, int],
) -> dict[str, list[str]]:
    """Assign new products to teams using weight-based load balancing.

    Start with existing team loads (from already-assigned bugzilla products),
    then assign new products largest-first to the team with the lowest load.
    """
    # Calculate existing bugzilla load per team
    team_load = {t: 0 for t in ("A", "B", "C")}
    for team, entries in existing_assignments.items():
        for entry in entries:
            team_load[team] += existing_bugzilla_issues.get(entry, 0)

    # Sort new products by issue count descending for better balancing
    sorted_products = sorted(new_products, key=lambda p: -p.get("approx_issues", 0))

    # Assign each new product to the least-loaded team
    team_new: dict[str, list[str]] = {"A": [], "B": [], "C": []}
    for product in sorted_products:
        entry_name = product["entry_name"]
        approx = product.get("approx_issues", 0)
        # Find team with lowest load
        target = min(("A", "B", "C"), key=lambda t: (team_load[t], len(team_new[t]), t))
        team_new[target].append(entry_name)
        team_load[target] += approx

    print(f"\n[BALANCE] Final team loads (mozilla/eclipse only):")
    for t in ("A", "B", "C"):
        total_entries = len(existing_assignments[t]) + len(team_new[t])
        print(f"  Team {t}: {team_load[t]:,} issues, {total_entries} entries ({len(team_new[t])} new)")

    return team_new


def update_manifest(manifest_path: Path, new_products: list[dict[str, Any]]) -> int:
    """Add new product entries to the manifest YAML."""
    text = manifest_path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)

    added = 0
    for family in data.get("families", []):
        if family.get("slug") != "bugzilla":
            continue
        for instance in family.get("instances", []):
            inst_name = instance.get("name")
            if inst_name not in ("mozilla", "eclipse"):
                continue

            existing_names = {e.get("name") for e in instance.get("entries", [])}
            instance_products = [p for p in new_products if p["instance"] == inst_name]

            for product in sorted(instance_products, key=lambda p: p["entry_name"]):
                if product["entry_name"] not in existing_names:
                    instance.setdefault("entries", []).append({
                        "name": product["entry_name"],
                        "kind": "product",
                        "tracker_id": product["product"],
                    })
                    added += 1

    # Write back with clean formatting
    manifest_path.write_text(
        yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False, width=120),
        encoding="utf-8",
    )
    return added


def write_team_assignments(
    config_path: Path,
    existing: dict[str, list[str]],
    new_assignments: dict[str, list[str]],
) -> None:
    """Write the complete team_assignments.yaml."""
    teams: dict[str, list[str]] = {}
    for team in ("A", "B", "C"):
        teams[team] = existing[team] + sorted(new_assignments.get(team, []))

    output = {
        "version": 1,
        "teams": teams,
    }
    config_path.write_text(
        yaml.dump(output, default_flow_style=False, allow_unicode=True, sort_keys=False, width=120),
        encoding="utf-8",
    )


def main() -> None:
    products_path = Path("artifacts/bugzilla_all_products.json")
    manifest_path = Path("manifests/sample.manifest.yaml")
    config_path = Path("manifests/team_assignments.yaml")

    if not products_path.exists():
        raise SystemExit(
            f"[ERROR] {products_path} not found. Run discover_bugzilla_products.py first."
        )

    # Load discovered products
    all_products = load_discovered_products(products_path)
    print(f"[INFO] Loaded {len(all_products)} products with issues > 0")

    # Separate new vs existing
    existing_entry_names: set[str] = set()
    for entries in EXISTING_BUGZILLA_ENTRIES.values():
        existing_entry_names.update(entries)
    for entries in EXISTING_ASSIGNMENTS.values():
        existing_entry_names.update(entries)

    new_products = [p for p in all_products if p["entry_name"] not in existing_entry_names]
    print(f"[INFO] {len(new_products)} new products to add (excluding {len(all_products) - len(new_products)} already assigned)")

    # Build existing bugzilla issue counts for load balancing
    existing_bugzilla_issues: dict[str, int] = {}
    for p in all_products:
        existing_bugzilla_issues[p["entry_name"]] = p.get("approx_issues", 0)

    # Assign new products to teams
    new_assignments = assign_new_products_to_teams(
        new_products, EXISTING_ASSIGNMENTS, existing_bugzilla_issues,
    )

    # Update manifest with new entries
    added = update_manifest(manifest_path, new_products)
    print(f"\n[MANIFEST] Added {added} new entries to {manifest_path}")

    # Write team assignments
    write_team_assignments(config_path, EXISTING_ASSIGNMENTS, new_assignments)
    print(f"[TEAM] Wrote team assignments to {config_path}")

    # Summary
    total_issues = sum(p.get("approx_issues", 0) for p in all_products)
    print(f"\n[SUMMARY]")
    print(f"  Total products: {len(all_products)}")
    print(f"  Total issues: {total_issues:,}")
    for team in ("A", "B", "C"):
        team_entries = EXISTING_ASSIGNMENTS[team] + new_assignments.get(team, [])
        team_issues = sum(
            existing_bugzilla_issues.get(e, 0) for e in team_entries
            if e in existing_bugzilla_issues
        )
        print(f"  Team {team}: {len(team_entries)} entries, ~{team_issues:,} mozilla/eclipse issues")


if __name__ == "__main__":
    main()
