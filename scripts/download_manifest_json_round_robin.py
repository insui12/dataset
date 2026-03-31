from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import yaml

from gbtd_infra.adapter_registry import adapter_for_family
from gbtd_infra.clients.http import PoliteHttpClient
from gbtd_infra.config import AppConfig
from gbtd_infra.manifests import ManifestCandidate, ManifestLoader

from download_manifest_json import (
    build_runtime_models,
    download_single_page,
    load_env_file,
    load_state,
    now_utc,
    state_path,
)


TEAM_CONFIG_DEFAULT = "manifests/team_assignments.yaml"


def load_team_entries(
    config_path: Path,
    team: str,
    manifest_candidates: list[ManifestCandidate],
) -> list[ManifestCandidate]:
    """Load team config, validate, and return filtered candidates."""
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    teams = raw.get("teams") or {}
    if team not in teams:
        raise SystemExit(f"[ERROR] team '{team}' not found in {config_path}")

    team_entries: list[str] = teams[team]
    if not team_entries:
        raise SystemExit(f"[ERROR] team '{team}' has no entries in {config_path}")

    # Validate: no entry appears in multiple teams
    all_assigned: dict[str, str] = {}
    for t_name, t_entries in teams.items():
        for entry_name in (t_entries or []):
            if entry_name in all_assigned:
                raise SystemExit(
                    f"[ERROR] entry '{entry_name}' is assigned to both "
                    f"team {all_assigned[entry_name]} and team {t_name}"
                )
            all_assigned[entry_name] = t_name

    # Build lookup from manifest
    candidate_map: dict[str, ManifestCandidate] = {}
    for c in manifest_candidates:
        candidate_map[c.entry_name] = c

    # Filter and validate
    filtered: list[ManifestCandidate] = []
    missing: list[str] = []
    for entry_name in team_entries:
        if entry_name in candidate_map:
            filtered.append(candidate_map[entry_name])
        else:
            missing.append(entry_name)

    if missing:
        raise SystemExit(
            f"[ERROR] entries not found in manifest: {', '.join(missing)}\n"
            f"Check that these entry names exist in the manifest YAML."
        )

    return filtered


def check_team_lock(output_root: Path, team: str) -> None:
    """Prevent different teams from writing to the same output directory."""
    lock_path = output_root / "_team_lock.json"
    if lock_path.exists():
        try:
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
            existing_team = lock.get("team")
            if existing_team and existing_team != team:
                raise SystemExit(
                    f"[ERROR] output directory {output_root} belongs to team {existing_team}, "
                    f"but you specified --team {team}. Use a different output directory "
                    f"or remove {lock_path} if you are sure."
                )
        except json.JSONDecodeError:
            pass
    else:
        output_root.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(
            json.dumps({"team": team, "created_at_utc": now_utc().isoformat()}, indent=2),
            encoding="utf-8",
        )
        print(f"[INIT] created team lock for team={team} at {lock_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download manifest entries in round-robin order, one page per entry per cycle."
    )
    parser.add_argument("--manifest-path", default="manifests/sample.manifest.yaml")
    parser.add_argument("--output-dir", default=None, help="Output directory. Auto-set when --team is used.")
    parser.add_argument("--mode", choices=["closed", "all"], default="all")
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--families", default=None, help="comma-separated family slugs")
    parser.add_argument("--max-cycles", type=int, default=None, help="stop after this many round-robin cycles")
    parser.add_argument("--pause-seconds", type=float, default=0.0, help="sleep between entry batches")
    parser.add_argument("--concurrent-entries", type=int, default=4, help="number of entries to process in parallel per cycle")

    # Team vs entries: mutually exclusive
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--team", choices=["A", "B", "C", "D"], default=None, help="Team name. Loads entries from team config.")
    group.add_argument("--entries", default=None, help="comma-separated entry names (alternative to --team)")

    parser.add_argument("--team-config", default=TEAM_CONFIG_DEFAULT, help="Team assignments YAML path.")

    return parser.parse_args()


async def async_main() -> None:
    load_env_file(".env")
    args = parse_args()
    cfg = AppConfig()

    _, candidates = ManifestLoader(Path(args.manifest_path)).load()

    # Filter by family if specified
    if args.families:
        family_set = {x.strip() for x in args.families.split(",") if x.strip()}
        candidates = [c for c in candidates if c.family_slug in family_set]

    # Filter by team or entries
    if args.team:
        candidates = load_team_entries(Path(args.team_config), args.team, candidates)
        print(f"[TEAM] team={args.team} entries={len(candidates)}")
        for c in candidates:
            print(f"  {c.family_slug}:{c.entry_name}")
    elif args.entries:
        entry_set = {x.strip() for x in args.entries.split(",") if x.strip()}
        candidates = [c for c in candidates if c.entry_name in entry_set]

    if not candidates:
        print("[STOP] no candidates selected")
        return

    # Resolve output directory
    if args.output_dir:
        output_root = Path(args.output_dir)
    elif args.team:
        output_root = Path(f"artifacts/json_downloads_round_robin_{args.team}")
    else:
        output_root = Path("artifacts/json_downloads_round_robin")

    output_root.mkdir(parents=True, exist_ok=True)

    # Team lock safety
    if args.team:
        check_team_lock(output_root, args.team)

    client = PoliteHttpClient(cfg)

    # Concurrency for parallel entry processing within a cycle
    concurrent_entries = args.concurrent_entries

    try:
        cycle = 0
        while True:
            cycle += 1
            progressed = False
            completed_count = 0

            print(f"[CYCLE] index={cycle} started_at_utc={now_utc().isoformat()}")

            # Separate completed vs active candidates
            active_candidates = []
            for candidate in candidates:
                s_path = state_path(output_root, candidate, args.mode)
                state = load_state(s_path)
                if state.get("completed"):
                    completed_count += 1
                    print(f"[SKIP] {candidate.family_slug}:{candidate.entry_name} already completed")
                else:
                    active_candidates.append(candidate)

            # Process active candidates in parallel batches
            for batch_start in range(0, len(active_candidates), concurrent_entries):
                batch = active_candidates[batch_start:batch_start + concurrent_entries]

                async def _process_entry(cand):
                    adapter_cls = adapter_for_family(cand.family_slug)
                    if adapter_cls is None:
                        print(f"[SKIP] {cand.family_slug}:{cand.entry_name} unsupported_family")
                        return None
                    adapter = adapter_cls(session_factory=None, client=client, config=cfg)
                    _, _, ent = build_runtime_models(cand)
                    try:
                        return await download_single_page(
                            candidate=cand, entry=ent, adapter=adapter,
                            client=client, output_root=output_root,
                            mode=args.mode, page_size=args.page_size,
                        )
                    except Exception as exc:
                        print(f"[ERROR] {cand.family_slug}:{cand.entry_name} {type(exc).__name__}: {exc}")
                        return {"attempted": True, "saved_this_page": 0,
                                "issues_saved": 0, "next_cursor": None,
                                "completed": False, "error": str(exc)}

                results = await asyncio.gather(*[_process_entry(c) for c in batch])

                for candidate, page_result in zip(batch, results):
                    if page_result is None:
                        continue
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
