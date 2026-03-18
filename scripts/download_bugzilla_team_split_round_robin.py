from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from gbtd_infra.adapter_registry import adapter_for_family
from gbtd_infra.clients.http import PoliteHttpClient
from gbtd_infra.config import AppConfig
from gbtd_infra.manifests import ManifestCandidate
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download Mozilla/Eclipse Bugzilla products for a selected team split in round-robin order."
    )
    parser.add_argument("--team", choices=["A", "B", "C"], required=True)
    parser.add_argument(
        "--split-path",
        default="artifacts/mozilla_eclipse_team_split.json",
        help="Path to the team split JSON generated from Mozilla/Eclipse instance bounds.",
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
        raise FileNotFoundError(f"team split file not found: {split_path}")

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
