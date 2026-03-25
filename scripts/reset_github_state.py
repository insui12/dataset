"""Reset GitHub state files that prematurely completed at ~1000 issues.

After fixing the GitHub adapter's since-based pagination, the existing state files
need to be reset so collection can continue from the beginning.

Usage:
    python scripts/reset_github_state.py --output-dir artifacts/json_downloads_round_robin_B [--dry-run]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset prematurely completed GitHub state files.")
    parser.add_argument("--output-dir", required=True, help="Output directory containing _state/")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be changed without modifying files")
    parser.add_argument("--max-issues", type=int, default=1100,
                        help="Only reset state files where issues_saved <= this value (default: 1100)")
    args = parser.parse_args()

    state_dir = Path(args.output_dir) / "_state"
    if not state_dir.exists():
        print(f"[ERROR] State directory not found: {state_dir}")
        return

    reset_count = 0
    for state_file in sorted(state_dir.glob("github__*.json")):
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[WARN] Could not read {state_file.name}: {exc}")
            continue

        if not state.get("completed"):
            print(f"[SKIP] {state_file.name}: not completed, no reset needed")
            continue

        issues_saved = state.get("issues_saved", 0)
        if issues_saved > args.max_issues:
            print(f"[SKIP] {state_file.name}: {issues_saved} issues (above threshold, likely genuinely complete)")
            continue

        print(f"[RESET] {state_file.name}: was completed={state.get('completed')} "
              f"issues_saved={issues_saved} cursor={state.get('cursor')!r}")

        if not args.dry_run:
            state["completed"] = False
            state["cursor"] = None
            state_file.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"  -> Set completed=false, cursor=null")

        reset_count += 1

    action = "Would reset" if args.dry_run else "Reset"
    print(f"\n[DONE] {action} {reset_count} state files")


if __name__ == "__main__":
    main()
