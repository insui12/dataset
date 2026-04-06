"""팀별 수집기 (A/B/C팀용, 원격 PC에서 서버로 자동 전송).

사용법:
    # 규민 PC (A팀)
    python scripts/team_collector.py --team A

    # 혜린 PC (C팀)
    python scripts/team_collector.py --team C

    # 서버에서 직접 (전송 없이)
    python scripts/team_collector.py --team A --no-sync
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
for _p in [str(_root / "src"), str(_root / "scripts")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

SERVER = "selab@aise.hknu.ac.kr"
PORT = 51713
SYNC_INTERVAL = 30  # minutes


def log(team: str, msg: str, log_file: str | None = None):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [Team {team}] {msg}"
    print(line, flush=True)
    if log_file:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")


# ---- 동기화 ----

def pull_state(local_dir: str, remote_dir: str, server: str, port: int):
    """서버에서 _state/ 가져오기."""
    state_dir = os.path.join(local_dir, "_state")
    os.makedirs(state_dir, exist_ok=True)
    try:
        subprocess.run(
            ["scp", "-r", "-P", str(port),
             "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes",
             "-o", "ConnectTimeout=10",
             f"{server}:{remote_dir}/_state", local_dir],
            capture_output=True, timeout=60,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass


def sync_to_server(local_dir: str, remote_dir: str, server: str, port: int,
                   team: str, log_file: str | None) -> int:
    """새 파일만 서버로 전송."""
    marker = os.path.join(local_dir, "_last_sync")
    last_sync = 0.0
    if os.path.exists(marker):
        try:
            last_sync = float(Path(marker).read_text().strip())
        except (ValueError, OSError):
            pass

    # _state/ 항상 전체 동기화
    state_dir = os.path.join(local_dir, "_state")
    if os.path.isdir(state_dir):
        try:
            subprocess.run(
                ["ssh", "-p", str(port), "-o", "StrictHostKeyChecking=no",
                 "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                 server, f"mkdir -p {remote_dir}"],
                capture_output=True, timeout=15,
            )
            subprocess.run(
                ["scp", "-r", "-P", str(port), "-o", "StrictHostKeyChecking=no",
                 "-o", "BatchMode=yes",
                 state_dir, f"{server}:{remote_dir}/"],
                capture_output=True, timeout=120,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    # 새 데이터 파일
    skip = {"_last_sync", "_sync_files.txt"}
    new_files: list[str] = []
    for root, _, files in os.walk(local_dir):
        if "_state" in root:
            continue
        for name in files:
            if name in skip:
                continue
            path = os.path.join(root, name)
            try:
                if os.path.getmtime(path) > last_sync:
                    new_files.append(path)
            except OSError:
                continue

    if not new_files:
        log(team, "[SYNC] 새 파일 없음", log_file)
        Path(marker).write_text(str(time.time()))
        return 0

    log(team, f"[SYNC] {len(new_files)}개 파일 전송 중...", log_file)

    by_dir: dict[str, list[str]] = {}
    for path in new_files:
        rel = os.path.relpath(path, local_dir).replace("\\", "/")
        subdir = os.path.dirname(rel)
        by_dir.setdefault(subdir, []).append(path)

    dirs = [f"{remote_dir}/{d}" for d in by_dir if d]
    if dirs:
        mkdir_cmd = "mkdir -p " + " ".join(f'"{d}"' for d in dirs)
        try:
            subprocess.run(
                ["ssh", "-p", str(port), "-o", "StrictHostKeyChecking=no",
                 "-o", "BatchMode=yes", server, mkdir_cmd],
                capture_output=True, timeout=30,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    batch_size = 20 if sys.platform == "win32" else 50
    for subdir, files in by_dir.items():
        target = f"{server}:{remote_dir}/{subdir}/" if subdir else f"{server}:{remote_dir}/"
        for i in range(0, len(files), batch_size):
            batch = files[i:i + batch_size]
            try:
                subprocess.run(
                    ["scp", "-P", str(port), "-o", "StrictHostKeyChecking=no",
                     "-o", "BatchMode=yes"] + batch + [target],
                    capture_output=True, timeout=300,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass

    Path(marker).write_text(str(time.time()))
    log(team, f"[SYNC] 완료 ({len(new_files)}개)", log_file)
    return len(new_files)


def sync_log(log_file: str, server: str, port: int):
    """로그 파일 서버 전송."""
    remote_log_dir = "/home/selab/dataset/artifacts/logs"
    try:
        subprocess.run(
            ["ssh", "-p", str(port), "-o", "StrictHostKeyChecking=no",
             "-o", "BatchMode=yes", server, f"mkdir -p {remote_log_dir}"],
            capture_output=True, timeout=10,
        )
        subprocess.run(
            ["scp", "-P", str(port), "-o", "StrictHostKeyChecking=no",
             "-o", "BatchMode=yes",
             log_file, f"{server}:{remote_log_dir}/"],
            capture_output=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass


def periodic_sync_loop(local_dir, remote_dir, server, port, team, log_file,
                       interval_min, stop_event):
    while not stop_event.wait(interval_min * 60):
        sync_to_server(local_dir, remote_dir, server, port, team, log_file)
        sync_log(log_file, server, port)


# ---- 메인 ----

def main() -> int:
    parser = argparse.ArgumentParser(description="팀별 수집기")
    parser.add_argument("--team", required=True, choices=["A", "B", "C"],
                        help="팀 (A/B/C)")
    parser.add_argument("--server", default=SERVER)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--sync-interval", type=int, default=SYNC_INTERVAL,
                        help="동기화 간격(분, 기본 30)")
    parser.add_argument("--no-sync", action="store_true",
                        help="서버 동기화 비활성화 (서버에서 직접 실행 시)")
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--pause-seconds", type=float, default=1.0)
    parser.add_argument("--concurrent-entries", type=int, default=2)
    args = parser.parse_args()

    team = args.team
    root = _root
    output_dir = str(root / "artifacts" / f"json_downloads_round_robin_{team}")
    remote_dir = f"/home/selab/dataset/artifacts/json_downloads_round_robin_{team}"

    log_dir = root / "artifacts" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = str(log_dir / f"team_{team}.log")
    os.makedirs(output_dir, exist_ok=True)

    log(team, "=== 팀 수집기 시작 ===", log_file)
    log(team, f"서버: {args.server}:{args.port}", log_file)

    # team_assignments.yaml에서 엔트리 목록 로드
    import yaml
    ta_path = root / "manifests" / "team_assignments.yaml"
    with open(ta_path, encoding="utf-8") as f:
        entries = yaml.safe_load(f)["teams"][team]

    log(team, f"배정 엔트리: {len(entries)}개", log_file)
    for e in entries[:5]:
        log(team, f"  {e}", log_file)
    if len(entries) > 5:
        log(team, f"  ... 외 {len(entries) - 5}개", log_file)

    # 서버에서 state 복원 (sync 모드일 때)
    if not args.no_sync:
        log(team, "서버에서 진행상태 동기화...", log_file)
        pull_state(output_dir, remote_dir, args.server, args.port)
        log(team, "state 동기화 완료", log_file)

    # 백그라운드 sync 스레드
    stop = threading.Event()

    if not args.no_sync:
        threading.Thread(
            target=periodic_sync_loop,
            args=(output_dir, remote_dir, args.server, args.port,
                  team, log_file, args.sync_interval, stop),
            daemon=True,
        ).start()
        log(team, f"자동 동기화 활성화: {args.sync_interval}분 간격", log_file)

    # 수집 실행
    log(team, f"수집 시작: page_size={args.page_size}, entries={len(entries)}개", log_file)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src")

    try:
        subprocess.run(
            [sys.executable, str(root / "scripts" / "download_manifest_json_round_robin.py"),
             "--entries", ",".join(entries),
             "--mode", "all",
             "--page-size", str(args.page_size),
             "--pause-seconds", str(args.pause_seconds),
             "--concurrent-entries", str(args.concurrent_entries),
             "--output-dir", output_dir],
            env=env, cwd=str(root),
        )
    except KeyboardInterrupt:
        log(team, "사용자에 의해 중단됨", log_file)

    stop.set()

    # 최종 동기화
    if not args.no_sync:
        log(team, "수집 완료. 최종 동기화...", log_file)
        sync_to_server(output_dir, remote_dir, args.server, args.port, team, log_file)
        sync_log(log_file, args.server, args.port)

    log(team, "=== 모든 작업 완료 ===", log_file)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
