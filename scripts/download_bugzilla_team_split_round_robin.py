"""DEPRECATED: Use download_manifest_json_round_robin.py --team A/B/C instead.

This script is kept for backward compatibility with existing collection runs.
New collections should use the unified round-robin script with --team flag.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import re
import warnings

warnings.warn(
    "download_bugzilla_team_split_round_robin.py is deprecated. "
    "Use download_manifest_json_round_robin.py --team A/B/C instead.",
    DeprecationWarning,
    stacklevel=2,
)

from gbtd_infra.adapter_registry import adapter_for_family
from gbtd_infra.clients.http import PoliteHttpClient
from gbtd_infra.config import AppConfig
from gbtd_infra.manifests import ManifestCandidate, ManifestLoader
from gbtd_infra.models import (
    CollectionMode,
    DatasetRole,
    ProtocolType,
    RegistryEntryKind,
    RegistryStatus,
    TrackerTier,
    Visibility,
)

from download_manifest_json import (
    build_runtime_models,
    download_single_page,
    load_env_file,
    load_state,
    now_utc,
    state_path,
)


BUGZILLA_INSTANCES: dict[str, dict[str, str]] = {
    "mozilla": {
        "base_url": "https://bugzilla.mozilla.org",
        "api_base_url": "https://bugzilla.mozilla.org/rest",
    },
    "eclipse": {
        "base_url": "https://bugs.eclipse.org/bugs",
        "api_base_url": "https://bugs.eclipse.org/bugs/rest",
    },
}
TEAM_NAMES = ("A", "B", "C")
VISIBLE_OFFSET_RE = re.compile(r"last_visible_offset=(\d+)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download Mozilla/Eclipse Bugzilla products for a selected team split in round-robin order."
    )
    parser.add_argument("--team", choices=TEAM_NAMES, required=True)
    parser.add_argument(
        "--split-path",
        default="artifacts/mozilla_eclipse_team_split.json",
        help="Path to the team split JSON generated from Mozilla/Eclipse instance bounds.",
    )
    parser.add_argument(
        "--manifest-path",
        default="manifests/sample.manifest.yaml",
        help="Manifest YAML used to auto-generate the split file when it is missing.",
    )
    parser.add_argument(
        "--weights-path",
        default="artifacts/last_issue_per_manifest.json",
        help="Optional JSON file with last-visible issue metadata used to balance auto-generated teams.",
    )
    parser.add_argument(
        "--no-auto-generate-split",
        action="store_true",
        help="Fail when the split file is missing instead of generating it from the manifest.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory. Defaults to artifacts/json_downloads_round_robin_<TEAM>.",
    )
    parser.add_argument("--mode", choices=["closed", "all"], default="all")
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--max-cycles", type=int, default=None)
    parser.add_argument("--pause-seconds", type=float, default=0.0)
    parser.add_argument(
        "--instances",
        default=None,
        help="Optional comma-separated instance filter, e.g. mozilla,eclipse",
    )
    return parser.parse_args()


def load_weight_lookup(weights_path: Path) -> dict[tuple[str, str], int]:
    if not weights_path.exists():
        return {}

    try:
        payload = json.loads(weights_path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}

    if not isinstance(payload, list):
        return {}

    weight_lookup: dict[tuple[str, str], int] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        if str(item.get("family")) != "bugzilla":
            continue
        instance_name = str(item.get("instance") or "").strip()
        entry_name = str(item.get("entry") or "").strip()
        if not instance_name or not entry_name:
            continue

        note = str(item.get("note") or "")
        matched = VISIBLE_OFFSET_RE.search(note)
        if not matched:
            continue

        weight_lookup[(instance_name, entry_name)] = int(matched.group(1)) + 1

    return weight_lookup


def build_split_payload(
    manifest_path: Path,
    weights_path: Path,
) -> list[dict[str, object]]:
    _, candidates = ManifestLoader(manifest_path).load()
    bugzilla_candidates = [
        candidate
        for candidate in candidates
        if candidate.family_slug == "bugzilla" and candidate.instance_name in BUGZILLA_INSTANCES
    ]
    if not bugzilla_candidates:
        raise ValueError(
            f"no Mozilla/Eclipse Bugzilla entries found in manifest: {manifest_path}"
        )

    weight_lookup = load_weight_lookup(weights_path)
    team_state = {
        team_name: {"team": team_name, "approx_visible_issues": 0, "products": []}
        for team_name in TEAM_NAMES
    }

    ordered_candidates = sorted(
        bugzilla_candidates,
        key=lambda candidate: (
            -weight_lookup.get((candidate.instance_name, candidate.entry_name), 1),
            candidate.instance_name,
            candidate.entry_name,
        ),
    )
    for candidate in ordered_candidates:
        team_name = min(
            TEAM_NAMES,
            key=lambda name: (
                int(team_state[name]["approx_visible_issues"]),
                len(team_state[name]["products"]),
                name,
            ),
        )
        weight = weight_lookup.get((candidate.instance_name, candidate.entry_name), 1)
        team_state[team_name]["products"].append(
            {
                "instance": candidate.instance_name,
                "product": candidate.entry_name,
                "approx_visible_issues": weight,
            }
        )
        team_state[team_name]["approx_visible_issues"] = int(
            team_state[team_name]["approx_visible_issues"]
        ) + weight

    return [team_state[team_name] for team_name in TEAM_NAMES]


def ensure_split_file(split_path: Path, manifest_path: Path, weights_path: Path) -> None:
    payload = build_split_payload(manifest_path, weights_path)
    split_path.parent.mkdir(parents=True, exist_ok=True)
    split_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[INIT] generated split file at {split_path}")
    for team_block in payload:
        products = ",".join(
            f"{item['instance']}:{item['product']}" for item in team_block.get("products", [])
        )
        print(
            f"[INIT] team={team_block['team']} "
            f"approx_visible_issues={team_block['approx_visible_issues']} "
            f"products={products or '-'}"
        )


def load_team_candidates(split_path: Path, team: str, instance_filter: set[str] | None) -> list[ManifestCandidate]:
    payload = json.loads(split_path.read_text(encoding="utf-8-sig"))
    team_block = next((item for item in payload if str(item.get("team")) == team), None)
    if team_block is None:
        raise ValueError(f"team not found in split file: {team}")

    candidates: list[ManifestCandidate] = []
    for product in team_block.get("products", []):
        instance_name = str(product["instance"])
        if instance_filter and instance_name not in instance_filter:
            continue
        instance_cfg = BUGZILLA_INSTANCES.get(instance_name)
        if instance_cfg is None:
            raise ValueError(f"unsupported bugzilla instance in split file: {instance_name}")

        product_name = str(product["product"])
        candidates.append(
            ManifestCandidate(
                family_slug="bugzilla",
                family_name="Bugzilla",
                instance_name=instance_name,
                instance_base_url=instance_cfg["base_url"],
                instance_api_base_url=instance_cfg["api_base_url"],
                entry_name=product_name,
                entry_kind=RegistryEntryKind.product,
                entry_tracker_id=product_name,
                tracker_api_key=None,
                tracker_url=None,
                api_url=None,
                tier=TrackerTier.core,
                collection_mode=CollectionMode.instance_exhaustive,
                dataset_role=DatasetRole.software_product,
                protocol=ProtocolType.REST,
                visibility=Visibility.public,
                status=RegistryStatus.active,
                is_bounded=True,
                parent_key=None,
            )
        )
    return candidates


async def async_main() -> None:
    load_env_file(".env")
    args = parse_args()
    cfg = AppConfig()

    split_path = Path(args.split_path)
    if not split_path.exists():
        if args.no_auto_generate_split:
            raise FileNotFoundError(f"team split file not found: {split_path}")
        ensure_split_file(
            split_path=split_path,
            manifest_path=Path(args.manifest_path),
            weights_path=Path(args.weights_path),
        )

    instance_filter = None
    if args.instances:
        instance_filter = {item.strip() for item in args.instances.split(",") if item.strip()}

    candidates = load_team_candidates(split_path, args.team, instance_filter)
    if not candidates:
        print(f"[STOP] no candidates selected for team={args.team}")
        return

    output_dir = args.output_dir or f"artifacts/json_downloads_round_robin_{args.team}"
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    client = PoliteHttpClient(cfg)
    try:
        cycle = 0
        while True:
            cycle += 1
            progressed = False
            completed_count = 0

            print(f"[CYCLE] index={cycle} team={args.team} started_at_utc={now_utc().isoformat()}")

            for candidate in candidates:
                s_path = state_path(output_root, candidate, args.mode)
                state = load_state(s_path)
                if state.get("completed"):
                    completed_count += 1
                    print(
                        f"[SKIP] {candidate.instance_name}:{candidate.entry_name} already completed"
                    )
                    continue

                adapter_cls = adapter_for_family(candidate.family_slug)
                if adapter_cls is None:
                    print(f"[SKIP] {candidate.instance_name}:{candidate.entry_name} unsupported_family")
                    continue

                adapter = adapter_cls(session_factory=None, client=client, config=cfg)
                _, _, entry = build_runtime_models(candidate)

                page_result = await download_single_page(
                    candidate=candidate,
                    entry=entry,
                    adapter=adapter,
                    client=client,
                    output_root=output_root,
                    mode=args.mode,
                    page_size=args.page_size,
                )
                progressed = progressed or page_result["attempted"]
                if page_result["completed"]:
                    completed_count += 1

                print(
                    f"[ENTRY] cycle={cycle} instance={candidate.instance_name} product={candidate.entry_name} "
                    f"saved={page_result['saved_this_page']} total={page_result['issues_saved']} "
                    f"next_cursor={page_result['next_cursor']!r} completed={page_result['completed']}"
                )

                if args.pause_seconds > 0:
                    await asyncio.sleep(args.pause_seconds)

            print(
                f"[CYCLE_DONE] index={cycle} team={args.team} "
                f"completed_entries={completed_count}/{len(candidates)} progressed={progressed}"
            )

            if completed_count >= len(candidates):
                print(f"[DONE] team={args.team} all selected products completed")
                return

            if args.max_cycles is not None and cycle >= args.max_cycles:
                print(f"[STOP] reached max_cycles={args.max_cycles}")
                return

            if not progressed:
                print("[STOP] no progress in cycle")
                return
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(async_main())
