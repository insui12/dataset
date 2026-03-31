#!/bin/bash
# =============================================================================
# lab_setup.sh — 실습실 PC 원스탭 설정
#
# 사용법 (git clone 후 실행):
#   git clone https://github.com/insui12/dataset.git ~/dataset
#   cd ~/dataset && bash scripts/lab_setup.sh
#
# 하는 일:
#   1. Python 버전 확인 (>=3.11)
#   2. venv 생성 + 의존성 설치
#   3. SSH 키 생성 + 서버 등록
#   4. SSH/rsync 연결 테스트
# =============================================================================

set -euo pipefail

SERVER="selab@aise.hknu.ac.kr"
PORT=51713
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="$PROJECT_ROOT/.venv"

echo "=== 실습실 PC 초기 세팅 ==="
echo ""

# ---- 1. Python 버전 확인 ----
echo "[1/4] Python 확인..."

# python3 또는 python 찾기
PYTHON=""
for cmd in python3 python; do
  if command -v "$cmd" &>/dev/null; then
    PYTHON="$cmd"
    break
  fi
done

if [[ -z "$PYTHON" ]]; then
  echo "  [ERROR] Python을 찾을 수 없습니다. Python 3.11 이상을 설치하세요."
  exit 1
fi

# 버전 확인
PY_VERSION=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$("$PYTHON" -c "import sys; print(sys.version_info.major)")
PY_MINOR=$("$PYTHON" -c "import sys; print(sys.version_info.minor)")

if [[ "$PY_MAJOR" -lt 3 ]] || { [[ "$PY_MAJOR" -eq 3 ]] && [[ "$PY_MINOR" -lt 11 ]]; }; then
  echo "  [ERROR] Python $PY_VERSION → 3.11 이상 필요"
  exit 1
fi
echo "  Python $PY_VERSION OK ($PYTHON)"

# ---- 2. venv + 의존성 설치 ----
echo ""
echo "[2/4] 가상환경 + 패키지 설치..."

if [[ -d "$VENV_DIR" ]]; then
  echo "  .venv 이미 존재, 패키지 업데이트..."
else
  echo "  .venv 생성 중..."
  "$PYTHON" -m venv "$VENV_DIR"
fi

# venv 내 pip 업그레이드 + 패키지 설치
"$VENV_DIR/bin/pip" install --upgrade pip --quiet 2>&1 | tail -1
"$VENV_DIR/bin/pip" install --quiet \
  "httpx>=0.27" \
  "tenacity>=9.0" \
  "pydantic>=2.7" \
  "pydantic-settings>=2.3" \
  "PyYAML>=6.0" \
  "sqlalchemy>=2.0" \
  2>&1

# 설치 확인
if "$VENV_DIR/bin/python" -c "import httpx, yaml, pydantic, tenacity, sqlalchemy" 2>/dev/null; then
  echo "  패키지 설치 완료!"
else
  echo "  [ERROR] 패키지 설치 실패."
  exit 1
fi

# ---- 3. SSH 키 생성 + 서버 등록 ----
echo ""
echo "[3/4] SSH 설정..."

mkdir -p ~/.ssh
chmod 700 ~/.ssh

if [[ ! -f ~/.ssh/id_ed25519 ]]; then
  echo "  SSH 키 생성 중..."
  ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N "" -q
  echo "  생성 완료: ~/.ssh/id_ed25519"
else
  echo "  SSH 키 이미 존재"
fi

echo "  서버에 SSH 키 등록 (비밀번호 입력 필요)..."
ssh-copy-id -p "$PORT" -o StrictHostKeyChecking=no "$SERVER" 2>&1 || {
  echo "  [WARN] ssh-copy-id 실패. 수동 등록이 필요할 수 있습니다."
}

# ---- 4. 연결 테스트 ----
echo ""
echo "[4/4] 연결 테스트..."

if timeout 10 ssh -p "$PORT" -o StrictHostKeyChecking=no "$SERVER" echo "SSH_OK" 2>/dev/null; then
  echo "  SSH 연결 성공!"
else
  echo "  [ERROR] SSH 연결 실패. 네트워크/방화벽 확인 필요."
  exit 1
fi

echo "  rsync 테스트..."
mkdir -p /tmp/_rsync_test && echo "test" > /tmp/_rsync_test/test.txt
if timeout 10 rsync -az -e "ssh -p $PORT" /tmp/_rsync_test/ "$SERVER:/tmp/_rsync_test_$$/" 2>/dev/null; then
  echo "  rsync 전송 성공!"
  ssh -p "$PORT" "$SERVER" "rm -rf /tmp/_rsync_test_$$" 2>/dev/null || true
else
  echo "  [ERROR] rsync 실패."
  exit 1
fi
rm -rf /tmp/_rsync_test

# ---- 완료 ----
echo ""
echo "============================================"
echo " 세팅 완료!"
echo ""
echo " 수집 시작:"
echo "   cd ~/dataset"
echo "   bash scripts/lab_collector.sh --machine N --total 25"
echo ""
echo " N = 이 PC의 번호 (1~25)"
echo "============================================"
