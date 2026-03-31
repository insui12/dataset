#!/bin/bash
# =============================================================================
# lab_collector.sh — 실습실 PC용 수집 + 자동 전송 + 자동 종료
#
# 수집 대상: Apache JIRA (660개) + C팀 Mozilla Bugzilla (56개) = 716 엔트리
# 41대 PC에 라운드 로빈으로 배분, 각 PC는 약 17~18개 엔트리 담당
#
# 사용법:
#   bash scripts/lab_collector.sh --machine 3
#
# 필수 인자:
#   --machine N    이 PC의 번호 (1~41)
#
# 옵션:
#   --total N        전체 PC 대수 (기본: 41)
#
# 옵션:
#   --server         서버 주소 (기본: selab@aise.hknu.ac.kr)
#   --port           SSH 포트 (기본: 51713)
#   --sync-interval  동기화 간격 분 (기본: 30)
#   --shutdown-at    자동 종료 시각 HH:MM KST (기본: 09:00)
#   --no-shutdown    자동 종료 안 함
#   --page-size      페이지 크기 (기본: 100)
#   --dest-dir       서버 목적지 (기본: /home/selab/dataset/artifacts/json_downloads_round_robin_D)
#
# 재개: 중단 후 같은 --machine --total로 다시 실행하면 자동 이어서 수집
# =============================================================================

set -euo pipefail

# ---- 기본값 ----
MACHINE_ID=""
TOTAL_MACHINES=41
SERVER="selab@aise.hknu.ac.kr"
PORT=51713
SYNC_INTERVAL=30
SHUTDOWN_AT="09:00"
NO_SHUTDOWN=false
PAGE_SIZE=100
DEST_DIR="/home/selab/dataset/artifacts/json_downloads_round_robin_D"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
VENV_PYTHON="$PROJECT_ROOT/.venv/bin/python"

# ---- 인자 파싱 ----
while [[ $# -gt 0 ]]; do
  case $1 in
    --machine)       MACHINE_ID="$2"; shift 2;;
    --total)         TOTAL_MACHINES="$2"; shift 2;;
    --server)        SERVER="$2"; shift 2;;
    --port)          PORT="$2"; shift 2;;
    --sync-interval) SYNC_INTERVAL="$2"; shift 2;;
    --shutdown-at)   SHUTDOWN_AT="$2"; shift 2;;
    --no-shutdown)   NO_SHUTDOWN=true; shift;;
    --page-size)     PAGE_SIZE="$2"; shift 2;;
    --dest-dir)      DEST_DIR="$2"; shift 2;;
    *) echo "[ERROR] 알 수 없는 인자: $1"; exit 1;;
  esac
done

if [[ -z "$MACHINE_ID" ]]; then
  echo "사용법: bash scripts/lab_collector.sh --machine N"
  echo "  예: bash scripts/lab_collector.sh --machine 3"
  exit 1
fi

# 범위 검증
if [[ "$MACHINE_ID" -lt 1 || "$MACHINE_ID" -gt "$TOTAL_MACHINES" ]]; then
  echo "[ERROR] --machine은 1~${TOTAL_MACHINES} 사이여야 합니다 (입력: $MACHINE_ID)"
  exit 1
fi

# ---- 타임존 설정 ----
export TZ="Asia/Seoul"

# ---- Python 선택 ----
if [[ -x "$VENV_PYTHON" ]]; then
  PY="$VENV_PYTHON"
else
  PY=python3
fi

# ---- 출력/로그 디렉토리 ----
OUTPUT_DIR="$PROJECT_ROOT/artifacts/json_downloads_round_robin_D"
LOG_DIR="$PROJECT_ROOT/artifacts/logs"
mkdir -p "$OUTPUT_DIR" "$LOG_DIR"
LOG_FILE="$LOG_DIR/machine_${MACHINE_ID}.log"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S KST')] [M${MACHINE_ID}] $*" | tee -a "$LOG_FILE"
}

log "=== 실습실 수집기 시작 ==="
log "머신: ${MACHINE_ID}/${TOTAL_MACHINES}, 서버: ${SERVER}:${PORT}"

# ---- 서버에서 state 복원 (재개 지원) ----
log "서버에서 진행상태 동기화..."
rsync -az --timeout=30 \
  -e "ssh -p ${PORT} -o ConnectTimeout=10 -o StrictHostKeyChecking=no" \
  "${SERVER}:${DEST_DIR}/_state/" "$OUTPUT_DIR/_state/" 2>>"$LOG_FILE" \
  && log "state 동기화 완료 (이전 진행상태 복원)" \
  || log "state 동기화 실패 (처음 실행이거나 서버 연결 불가 → 처음부터 수집)"

# ---- 프로젝트 배분 계산 ----
ENTRIES_FILE=$(mktemp)
cd "$PROJECT_ROOT"

"$PY" -c "
import json, yaml

machine_id = int('${MACHINE_ID}')
total = int('${TOTAL_MACHINES}')

# --- 1. Apache JIRA 프로젝트 (660개, issue_count 내림차순) ---
projects = json.load(open('artifacts/apache_jira_projects.json'))
projects = [p for p in projects if p['issue_count'] > 0]
projects.sort(key=lambda x: -x['issue_count'])

my_jira = []
jira_issues = 0
for i, p in enumerate(projects):
    if (i % total) + 1 == machine_id:
        my_jira.append(p['key'].lower())
        jira_issues += p['issue_count']

# 충돌 이름 보정
rename = {'incubator': 'jira_incubator', 'testing': 'jira_testing', 'tools': 'jira_tools'}
my_jira = [rename.get(p, p) for p in my_jira]

# --- 2. C팀 Mozilla Bugzilla 엔트리 (56개) ---
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

my_mozilla = []
for i, e in enumerate(c_mozilla):
    if (i % total) + 1 == machine_id:
        my_mozilla.append(e)

# --- 3. 합산 ---
my_all = my_jira + my_mozilla

with open('${ENTRIES_FILE}', 'w') as f:
    f.write(','.join(my_all))

print(f'JIRA 배정: {len(my_jira)}개, 예상 이슈: {jira_issues:,}')
print(f'Mozilla 배정: {len(my_mozilla)}개 (C팀 {len(c_mozilla)}개 중)')
print(f'합계: {len(my_all)}개 엔트리')
for p in my_all[:5]:
    print(f'  {p}')
if len(my_all) > 5:
    print(f'  ... 외 {len(my_all)-5}개')
" 2>&1 | tee -a "$LOG_FILE"

ENTRIES=$(cat "$ENTRIES_FILE")
rm -f "$ENTRIES_FILE"

if [[ -z "$ENTRIES" ]]; then
  log "[ERROR] 배정된 프로젝트가 없습니다"
  exit 1
fi

# ---- 자동 종료 스케줄링 ----
if [[ "$NO_SHUTDOWN" == "false" ]]; then
  now_epoch=$(date +%s)
  target_today=$(date -d "today ${SHUTDOWN_AT}" +%s 2>/dev/null || date -d "${SHUTDOWN_AT}" +%s)

  if [[ $target_today -le $now_epoch ]]; then
    target_epoch=$(( target_today + 86400 ))
  else
    target_epoch=$target_today
  fi

  remaining=$(( target_epoch - now_epoch ))
  remaining_hours=$(( remaining / 3600 ))
  remaining_mins=$(( (remaining % 3600) / 60 ))

  log "자동 종료: ${SHUTDOWN_AT} KST (${remaining_hours}시간 ${remaining_mins}분 후)"

  (
    sleep "$remaining"
    log "=== 자동 종료 시작 ==="
    log "종료 전 최종 동기화..."
    rsync -az -e "ssh -p ${PORT} -o ConnectTimeout=10 -o StrictHostKeyChecking=no" \
      "$OUTPUT_DIR/" "${SERVER}:${DEST_DIR}/" 2>>"$LOG_FILE" || true
    # 로그도 전송
    rsync -az -e "ssh -p ${PORT} -o ConnectTimeout=10 -o StrictHostKeyChecking=no" \
      "$LOG_DIR/machine_${MACHINE_ID}.log" "${SERVER}:${DEST_DIR}/../logs/" 2>>"$LOG_FILE" || true
    log "최종 동기화 완료"
    sudo shutdown -h now
  ) &
  SHUTDOWN_PID=$!
  log "종료 타이머 PID: $SHUTDOWN_PID"
fi

# ---- 주기적 동기화 (백그라운드) ----
(
  while true; do
    sleep $(( SYNC_INTERVAL * 60 ))
    log "[SYNC] rsync 시작..."
    rsync -az --timeout=60 \
      -e "ssh -p ${PORT} -o ConnectTimeout=10 -o StrictHostKeyChecking=no" \
      "$OUTPUT_DIR/" "${SERVER}:${DEST_DIR}/" 2>>"$LOG_FILE" \
      && log "[SYNC] 완료" \
      || log "[SYNC] 실패 (다음 주기에 재시도)"
    # 로그도 전송
    rsync -az --timeout=60 \
      -e "ssh -p ${PORT} -o ConnectTimeout=10 -o StrictHostKeyChecking=no" \
      "$LOG_DIR/machine_${MACHINE_ID}.log" "${SERVER}:${DEST_DIR}/../logs/" 2>>"$LOG_FILE" || true
  done
) &
SYNC_PID=$!
log "동기화 프로세스 PID: $SYNC_PID (${SYNC_INTERVAL}분 간격)"

# ---- 수집 시작 ----
log "수집 시작: page_size=${PAGE_SIZE}, entries=${ENTRIES:0:80}..."

cd "$PROJECT_ROOT"
PYTHONPATH=src "$PY" scripts/download_manifest_json_round_robin.py \
  --entries "$ENTRIES" \
  --mode all \
  --page-size "$PAGE_SIZE" \
  --pause-seconds 0.3 \
  --concurrent-entries 2 \
  --output-dir "$OUTPUT_DIR" \
  2>&1 | tee -a "$LOG_FILE"

# ---- 수집 완료 후 최종 동기화 ----
log "수집 완료. 최종 동기화..."
rsync -az -e "ssh -p ${PORT} -o ConnectTimeout=10 -o StrictHostKeyChecking=no" \
  "$OUTPUT_DIR/" "${SERVER}:${DEST_DIR}/" 2>>"$LOG_FILE" \
  && log "최종 동기화 완료" \
  || log "최종 동기화 실패"

rsync -az -e "ssh -p ${PORT} -o ConnectTimeout=10 -o StrictHostKeyChecking=no" \
  "$LOG_DIR/machine_${MACHINE_ID}.log" "${SERVER}:${DEST_DIR}/../logs/" 2>>"$LOG_FILE" || true

# 백그라운드 프로세스 정리
kill $SYNC_PID 2>/dev/null || true
if [[ "$NO_SHUTDOWN" == "false" ]]; then
  kill $SHUTDOWN_PID 2>/dev/null || true
fi

log "=== 모든 작업 완료 ==="
