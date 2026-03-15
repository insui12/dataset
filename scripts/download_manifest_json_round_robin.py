from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from gbtd_infra.adapter_registry import adapter_for_family
from gbtd_infra.clients.http import PoliteHttpClient
from gbtd_infra.config import AppConfig
from gbtd_infra.manifests import ManifestLoader

from download_manifest_json import (
    build_runtime_models,
    download_single_page,
    load_env_file,
    load_state,
    now_utc,
    state_path,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download manifest entries in round-robin order, one page per entry per cycle."
    )
    parser.add_argument("--manifest-path", default="manifests/sample.manifest.yaml")
    parser.add_argument("--output-dir", default="artifacts/json_downloads_round_robin")
    parser.add_argument("--mode", choices=["closed", "all"], default="all")
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--families", default=None, help="comma-separated family slugs")
    parser.add_argument("--entries", default=None, help="comma-separated entry names")
    parser.add_argument("--max-cycles", type=int, default=None, help="stop after this many round-robin cycles")
    parser.add_argument("--pause-seconds", type=float, default=0.0, help="sleep between entries")
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
        cycle = 0
        while True:
            cycle += 1
            progressed = False
            completed_count = 0

            print(f"[CYCLE] index={cycle} started_at_utc={now_utc().isoformat()}")

            for candidate in candidates:
                s_path = state_path(output_root, candidate, args.mode)
                state = load_state(s_path)
                if state.get("completed"):
                    completed_count += 1
                    print(f"[SKIP] {candidate.family_slug}:{candidate.entry_name} already completed")
                    continue

                adapter_cls = adapter_for_family(candidate.family_slug)
                if adapter_cls is None:
                    print(f"[SKIP] {candidate.family_slug}:{candidate.entry_name} unsupported_family")
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
                    f"[ENTRY] cycle={cycle} family={candidate.family_slug} entry={candidate.entry_name} "
                    f"saved={page_result['saved_this_page']} total={page_result['issues_saved']} "
                    f"next_cursor={page_result['next_cursor']!r} completed={page_result['completed']}"
                )

                if args.pause_seconds > 0:
                    await asyncio.sleep(args.pause_seconds)

            print(
                f"[CYCLE_DONE] index={cycle} completed_entries={completed_count}/{len(candidates)} "
                f"progressed={progressed}"
            )

            if completed_count >= len(candidates):
                print("[DONE] all selected entries completed")
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
