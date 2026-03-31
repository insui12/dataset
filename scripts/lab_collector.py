"""실습실 PC 수집기 (Windows/Linux 호환).

사용법:
    python scripts/lab_collector.py --machine 3
    python scripts/lab_collector.py --machine 3 --no-shutdown
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

SERVER = "selab@aise.hknu.ac.kr"
PORT = 51712
SYNC_INTERVAL = 30  # minutes
SHUTDOWN_AT = "09:00"
DEST_DIR = "/home/selab/dataset/artifacts/json_downloads_round_robin_D"
REMOTE_LOG_DIR = "/home/selab/dataset/artifacts/logs"


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def log(machine_id: int, msg: str, log_file: str | None = None):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [M{machine_id}] {msg}"
    print(line, flush=True)
    if log_file:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")


# ---- 동기화 (rsync 없이 scp 기반) ----

def pull_state(local_dir: str, remote_dir: str, server: str, port: int):
    """서버에서 _state/ 가져오기 (재개용)."""
    state_dir = os.path.join(local_dir, "_state")
    os.makedirs(state_dir, exist_ok=True)
    try:
        subprocess.run(
            ["scp", "-r", "-P", str(port),
             "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
             f"{server}:{remote_dir}/_state", local_dir],
            capture_output=True, timeout=60,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass


def sync_to_server(local_dir: str, remote_dir: str, server: str, port: int,
                   machine_id: int, log_file: str | None) -> int:
    """새 파일만 서버로 전송."""
    marker = os.path.join(local_dir, "_last_sync")
    last_sync = 0.0
    if os.path.exists(marker):
        try:
            last_sync = float(Path(marker).read_text().strip())
        except (ValueError, OSError):
            pass

    # _state/ 는 항상 전체 동기화 (작고, 재개에 필수)
    state_dir = os.path.join(local_dir, "_state")
    if os.path.isdir(state_dir):
        try:
            subprocess.run(
                ["ssh", "-p", str(port), "-o", "StrictHostKeyChecking=no",
                 "-o", "ConnectTimeout=10", server, f"mkdir -p {remote_dir}"],
                capture_output=True, timeout=15,
            )
            subprocess.run(
                ["scp", "-r", "-P", str(port), "-o", "StrictHostKeyChecking=no",
                 state_dir, f"{server}:{remote_dir}/"],
                capture_output=True, timeout=120,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    # 새 데이터 파일 찾기
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
        log(machine_id, "[SYNC] 새 파일 없음", log_file)
        Path(marker).write_text(str(time.time()))
        return 0

    log(machine_id, f"[SYNC] {len(new_files)}개 파일 전송 중...", log_file)

    # 디렉토리별 그룹화
    by_dir: dict[str, list[str]] = {}
    for path in new_files:
        rel = os.path.relpath(path, local_dir).replace("\\", "/")
        subdir = os.path.dirname(rel)
        by_dir.setdefault(subdir, []).append(path)

    # 원격 디렉토리 생성
    dirs = [f"{remote_dir}/{d}" for d in by_dir if d]
    if dirs:
        mkdir_cmd = "mkdir -p " + " ".join(f'"{d}"' for d in dirs)
        try:
            subprocess.run(
                ["ssh", "-p", str(port), "-o", "StrictHostKeyChecking=no", server, mkdir_cmd],
                capture_output=True, timeout=30,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    # scp 배치 전송 (디렉토리별, 50개씩)
    for subdir, files in by_dir.items():
        target = f"{server}:{remote_dir}/{subdir}/" if subdir else f"{server}:{remote_dir}/"
        for i in range(0, len(files), 50):
            batch = files[i:i + 50]
            try:
                subprocess.run(
                    ["scp", "-P", str(port), "-o", "StrictHostKeyChecking=no"] + batch + [target],
                    capture_output=True, timeout=300,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass

    Path(marker).write_text(str(time.time()))
    log(machine_id, f"[SYNC] 완료 ({len(new_files)}개)", log_file)
    return len(new_files)


def sync_log(log_file: str, server: str, port: int):
    """로그 파일 서버 전송."""
    try:
        subprocess.run(
            ["ssh", "-p", str(port), "-o", "StrictHostKeyChecking=no", server,
             f"mkdir -p {REMOTE_LOG_DIR}"],
            capture_output=True, timeout=10,
        )
        subprocess.run(
            ["scp", "-P", str(port), "-o", "StrictHostKeyChecking=no",
             log_file, f"{server}:{REMOTE_LOG_DIR}/"],
            capture_output=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass


# ---- 엔트리 배분 ----

def calculate_entries(root: Path, machine_id: int, total: int):
    """라운드 로빈으로 이 머신의 엔트리 계산."""
    import yaml

    # JIRA
    with open(root / "artifacts" / "apache_jira_projects.json", encoding="utf-8") as f:
        projects = json.load(f)
    projects = sorted([p for p in projects if p["issue_count"] > 0],
                      key=lambda x: -x["issue_count"])

    rename = {"incubator": "jira_incubator", "testing": "jira_testing", "tools": "jira_tools"}
    my_jira, jira_issues = [], 0
    for i, p in enumerate(projects):
        if (i % total) + 1 == machine_id:
            name = rename.get(p["key"].lower(), p["key"].lower())
            my_jira.append(name)
            jira_issues += p["issue_count"]

    # C팀 Mozilla
    with open(root / "manifests" / "team_assignments.yaml", encoding="utf-8") as f:
        c_entries = yaml.safe_load(f)["teams"]["C"]
    with open(root / "manifests" / "sample.manifest.yaml", encoding="utf-8") as f:
        manifest = yaml.safe_load(f)

    mozilla_names = set()
    for fam in manifest["families"]:
        if fam["slug"] == "bugzilla":
            for inst in fam["instances"]:
                if inst["name"] == "mozilla":
                    mozilla_names.update(e["name"] for e in inst["entries"])

    c_mozilla = [e for e in c_entries if e in mozilla_names]
    my_mozilla = [e for i, e in enumerate(c_mozilla) if (i % total) + 1 == machine_id]

    return my_jira, my_mozilla, jira_issues, len(c_mozilla)


# ---- 백그라운드 스레드 ----

def periodic_sync_loop(local_dir, remote_dir, server, port, machine_id, log_file,
                       interval_min, stop_event):
    while not stop_event.wait(interval_min * 60):
        sync_to_server(local_dir, remote_dir, server, port, machine_id, log_file)
        sync_log(log_file, server, port)


def auto_shutdown_timer(shutdown_at, local_dir, remote_dir, server, port,
                        machine_id, log_file, stop_event):
    now = datetime.now()
    h, m = map(int, shutdown_at.split(":"))
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)

    remaining = (target - now).total_seconds()
    hrs, mins = int(remaining // 3600), int((remaining % 3600) // 60)
    log(machine_id, f"자동 종료: {shutdown_at} KST ({hrs}시간 {mins}분 후)", log_file)

    if stop_event.wait(remaining):
        return  # 수집이 먼저 끝남

    log(machine_id, "=== 자동 종료 시작 ===", log_file)
    log(machine_id, "종료 전 최종 동기화...", log_file)
    sync_to_server(local_dir, remote_dir, server, port, machine_id, log_file)
    sync_log(log_file, server, port)
    log(machine_id, "최종 동기화 완료", log_file)

    if sys.platform == "win32":
        os.system('shutdown /s /t 30 /c "실습실 수집 자동 종료 (30초 후)"')
    else:
        os.system("sudo shutdown -h now")


# ---- 메인 ----

def main() -> int:
    parser = argparse.ArgumentParser(description="실습실 PC 수집기")
    parser.add_argument("--machine", type=int, required=True, help="이 PC의 번호 (1~41)")
    parser.add_argument("--total", type=int, default=41)
    parser.add_argument("--server", default=SERVER)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--sync-interval", type=int, default=SYNC_INTERVAL, help="동기화 간격(분)")
    parser.add_argument("--shutdown-at", default=SHUTDOWN_AT, help="자동 종료 HH:MM KST")
    parser.add_argument("--no-shutdown", action="store_true")
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--dest-dir", default=DEST_DIR)
    args = parser.parse_args()

    if not 1 <= args.machine <= args.total:
        print(f"[ERROR] --machine은 1~{args.total} 사이여야 합니다")
        return 1

    root = project_root()
    output_dir = str(root / "artifacts" / "json_downloads_round_robin_D")
    log_dir = root / "artifacts" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = str(log_dir / f"machine_{args.machine}.log")
    os.makedirs(output_dir, exist_ok=True)

    mid = args.machine
    log(mid, "=== 실습실 수집기 시작 ===", log_file)
    log(mid, f"머신: {mid}/{args.total}, 서버: {args.server}:{args.port}", log_file)

    # 서버에서 state 복원
    log(mid, "서버에서 진행상태 동기화...", log_file)
    pull_state(output_dir, args.dest_dir, args.server, args.port)
    log(mid, "state 동기화 완료", log_file)

    # 엔트리 배분
    my_jira, my_mozilla, jira_issues, total_moz = calculate_entries(
        root, mid, args.total,
    )
    my_all = my_jira + my_mozilla
    if not my_all:
        log(mid, "[ERROR] 배정된 프로젝트가 없습니다", log_file)
        return 1

    log(mid, f"JIRA: {len(my_jira)}개 ({jira_issues:,} 이슈), Mozilla: {len(my_mozilla)}개", log_file)
    for p in my_all[:5]:
        log(mid, f"  {p}", log_file)
    if len(my_all) > 5:
        log(mid, f"  ... 외 {len(my_all) - 5}개", log_file)

    # 백그라운드 스레드
    stop = threading.Event()

    if not args.no_shutdown:
        threading.Thread(
            target=auto_shutdown_timer,
            args=(args.shutdown_at, output_dir, args.dest_dir, args.server, args.port,
                  mid, log_file, stop),
            daemon=True,
        ).start()

    threading.Thread(
        target=periodic_sync_loop,
        args=(output_dir, args.dest_dir, args.server, args.port, mid, log_file,
              args.sync_interval, stop),
        daemon=True,
    ).start()

    # 수집 실행
    log(mid, f"수집 시작: page_size={args.page_size}, entries={len(my_all)}개", log_file)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src")

    try:
        subprocess.run(
            [sys.executable, str(root / "scripts" / "download_manifest_json_round_robin.py"),
             "--entries", ",".join(my_all),
             "--mode", "all",
             "--page-size", str(args.page_size),
             "--pause-seconds", "0.3",
             "--concurrent-entries", "2",
             "--output-dir", output_dir],
            env=env, cwd=str(root),
        )
    except KeyboardInterrupt:
        log(mid, "사용자에 의해 중단됨", log_file)

    stop.set()

    # 최종 동기화
    log(mid, "수집 완료. 최종 동기화...", log_file)
    sync_to_server(output_dir, args.dest_dir, args.server, args.port, mid, log_file)
    sync_log(log_file, args.server, args.port)
    log(mid, "=== 모든 작업 완료 ===", log_file)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
