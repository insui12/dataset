#!/bin/bash
# =============================================================================
# lab_status.sh — 서버에서 41대 실습실 PC 수집 현황 확인
#
# 사용법 (서버에서 실행):
#   bash scripts/lab_status.sh                    # 전체 요약
#   bash scripts/lab_status.sh --machine 3        # 3번 머신 상세
#   bash scripts/lab_status.sh --all              # 전체 머신별 상세
#
# 옵션:
#   --total N       전체 PC 대수 (기본: 41)
#   --machine N     특정 머신 상세 보기
#   --all           모든 머신 상세 보기
# =============================================================================

set -euo pipefail

TOTAL_MACHINES=41
TARGET_MACHINE=""
SHOW_ALL=false
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
OUTPUT_DIR="$PROJECT_ROOT/artifacts/json_downloads_round_robin_D"
LOG_DIR="$PROJECT_ROOT/artifacts/logs"
STATE_DIR="$OUTPUT_DIR/_state"

while [[ $# -gt 0 ]]; do
  case $1 in
    --total)   TOTAL_MACHINES="$2"; shift 2;;
    --machine) TARGET_MACHINE="$2"; shift 2;;
    --all)     SHOW_ALL=true; shift;;
    *) echo "[ERROR] 알 수 없는 인자: $1"; exit 1;;
  esac
done

# Python 선택
VENV_PYTHON="$PROJECT_ROOT/.venv/bin/python"
if [[ -x "$VENV_PYTHON" ]]; then
  PY="$VENV_PYTHON"
else
  PY=python3
fi

cd "$PROJECT_ROOT"

"$PY" -c "
import json, yaml, os, sys
from pathlib import Path
from datetime import datetime

total = int('${TOTAL_MACHINES}')
target_machine = '${TARGET_MACHINE}'
show_all = '${SHOW_ALL}' == 'true'
output_dir = Path('${OUTPUT_DIR}')
log_dir = Path('${LOG_DIR}')
state_dir = output_dir / '_state'

# ---- 프로젝트 배분 계산 (lab_collector.sh와 동일 로직) ----
projects = json.load(open('artifacts/apache_jira_projects.json'))
projects = [p for p in projects if p['issue_count'] > 0]
projects.sort(key=lambda x: -x['issue_count'])

rename = {'incubator': 'jira_incubator', 'testing': 'jira_testing', 'tools': 'jira_tools'}

team_data = yaml.safe_load(open('manifests/team_assignments.yaml'))
c_entries = team_data['teams']['C']

manifest = yaml.safe_load(open('manifests/sample.manifest.yaml'))
mozilla_names = set()
for fam in manifest['families']:
    if fam['slug'] == 'bugzilla':
        for inst in fam['instances']:
            if inst['name'] == 'mozilla':
                for e in inst['entries']:
                    mozilla_names.add(e['name'])

c_mozilla = [e for e in c_entries if e in mozilla_names]

# 각 머신별 엔트리 계산
machine_entries: dict[int, list[dict]] = {}
for m in range(1, total + 1):
    entries = []
    for i, p in enumerate(projects):
        if (i % total) + 1 == m:
            name = rename.get(p['key'].lower(), p['key'].lower())
            entries.append({
                'name': name,
                'type': 'jira',
                'est_issues': p['issue_count'],
                'state_file': f'jira__apache__{name}__all.json',
            })
    for i, e in enumerate(c_mozilla):
        if (i % total) + 1 == m:
            entries.append({
                'name': e,
                'type': 'mozilla',
                'est_issues': 0,
                'state_file': f'bugzilla__mozilla__{e}__all.json',
            })
    machine_entries[m] = entries

# ---- state 파일 읽기 ----
def read_state(state_file: str) -> dict:
    path = state_dir / state_file
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}
    return {}

# ---- 상세 출력 함수 ----
def print_machine_detail(m: int):
    entries = machine_entries[m]
    completed = 0
    in_progress = 0
    not_started = 0
    total_saved = 0

    print(f'  머신 {m:2d} | 엔트리 {len(entries)}개')
    print(f'  {\"─\"*60}')
    for ent in entries:
        state = read_state(ent['state_file'])
        saved = state.get('issues_saved', 0)
        is_completed = state.get('completed', False)
        cursor = state.get('cursor') or state.get('next_cursor')
        total_saved += saved

        if is_completed:
            completed += 1
            status = 'DONE'
        elif saved > 0 or cursor:
            in_progress += 1
            status = 'RUN '
        else:
            not_started += 1
            status = '    '

        est = ent['est_issues']
        pct = f'{saved/est*100:.0f}%' if est > 0 and saved > 0 else ''
        print(f'  [{status}] {ent[\"type\"]:7s} {ent[\"name\"]:30s} {saved:>8,} {pct:>5s}')

    print(f'  {\"─\"*60}')
    print(f'  완료={completed} 진행={in_progress} 대기={not_started} 수집이슈={total_saved:,}')

    # 로그 파일 마지막 활동
    log_file = log_dir / f'machine_{m}.log'
    if log_file.exists():
        try:
            lines = log_file.read_text().strip().split('\\n')
            last_line = lines[-1] if lines else ''
            print(f'  최근 로그: {last_line[:80]}')
        except Exception:
            pass
    print()

# ---- 전체 요약 ----
def print_summary():
    print('=' * 70)
    print(f' 실습실 수집 현황 (총 {total}대)')
    print('=' * 70)
    print()

    # 디스크 사용량
    if output_dir.exists():
        total_size = sum(f.stat().st_size for f in output_dir.rglob('*') if f.is_file())
        total_files = sum(1 for f in output_dir.rglob('*.json') if '_state' not in str(f))
        print(f' 데이터 위치: {output_dir}')
        print(f' 총 파일 수: {total_files:,}개')
        print(f' 총 용량:    {total_size / 1024**3:.2f} GB')
    print()

    print(f' {\"머신\":>4s}  {\"엔트리\":>6s}  {\"완료\":>4s}  {\"진행\":>4s}  {\"대기\":>4s}  {\"수집이슈\":>10s}  {\"마지막활동\":>12s}')
    print(f' {\"─\"*64}')

    grand_completed = 0
    grand_progress = 0
    grand_waiting = 0
    grand_saved = 0
    grand_entries = 0

    for m in range(1, total + 1):
        entries = machine_entries[m]
        completed = 0
        in_progress = 0
        not_started = 0
        total_saved = 0

        for ent in entries:
            state = read_state(ent['state_file'])
            saved = state.get('issues_saved', 0)
            is_completed = state.get('completed', False)
            cursor = state.get('cursor') or state.get('next_cursor')
            total_saved += saved
            if is_completed:
                completed += 1
            elif saved > 0 or cursor:
                in_progress += 1
            else:
                not_started += 1

        grand_completed += completed
        grand_progress += in_progress
        grand_waiting += not_started
        grand_saved += total_saved
        grand_entries += len(entries)

        # 마지막 활동 시각
        last_activity = ''
        log_file = log_dir / f'machine_{m}.log'
        if log_file.exists():
            try:
                mtime = os.path.getmtime(log_file)
                last_activity = datetime.fromtimestamp(mtime).strftime('%m-%d %H:%M')
            except Exception:
                pass

        bar = ''
        if len(entries) > 0:
            done_ratio = completed / len(entries)
            filled = int(done_ratio * 10)
            bar = '█' * filled + '░' * (10 - filled)

        print(f' {m:4d}  {len(entries):6d}  {completed:4d}  {in_progress:4d}  {not_started:4d}  {total_saved:>10,}  {last_activity:>12s}  {bar}')

    print(f' {\"─\"*64}')
    print(f' {\"합계\":>4s}  {grand_entries:6d}  {grand_completed:4d}  {grand_progress:4d}  {grand_waiting:4d}  {grand_saved:>10,}')
    print()

    if grand_entries > 0:
        pct = grand_completed / grand_entries * 100
        print(f' 전체 진행률: {grand_completed}/{grand_entries} 엔트리 완료 ({pct:.1f}%)')
        print(f' 수집된 이슈: {grand_saved:,}개')
    print()

# ---- 실행 ----
if target_machine:
    m = int(target_machine)
    if m < 1 or m > total:
        print(f'[ERROR] --machine은 1~{total} 사이여야 합니다')
        sys.exit(1)
    print()
    print_machine_detail(m)
elif show_all:
    print_summary()
    print()
    for m in range(1, total + 1):
        print_machine_detail(m)
else:
    print_summary()
"
