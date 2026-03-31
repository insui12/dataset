#!/bin/bash
# =============================================================================
# lab_setup.sh — 실습실 PC 초기 세팅 (1회만 실행)
#
# 사용법:
#   bash lab_setup.sh
#
# 하는 일:
#   1. conda/pip 환경 확인
#   2. SSH 키 생성 및 서버에 등록
#   3. 연결 테스트
#   4. 코드 다운로드 (git clone)
# =============================================================================

set -euo pipefail

SERVER="selab@aise.hknu.ac.kr"
PORT=51712
REPO_URL="https://github.com/insui12/dataset.git"

echo "=== 실습실 PC 초기 세팅 ==="
echo ""

# 1. SSH 키 확인/생성
echo "[1/4] SSH 키 확인..."
if [[ ! -f ~/.ssh/id_ed25519 ]]; then
  echo "  SSH 키 생성 중..."
  ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N "" -q
  echo "  생성 완료: ~/.ssh/id_ed25519"
else
  echo "  이미 존재: ~/.ssh/id_ed25519"
fi

# 2. 서버에 키 등록
echo ""
echo "[2/4] 서버에 SSH 키 등록..."
echo "  서버 비밀번호를 입력하세요:"
ssh-copy-id -p "$PORT" -o StrictHostKeyChecking=no "$SERVER" 2>&1 || {
  echo "  [WARN] ssh-copy-id 실패. 수동 등록이 필요할 수 있습니다."
}

# 3. 연결 테스트
echo ""
echo "[3/4] SSH 연결 테스트..."
if timeout 10 ssh -p "$PORT" -o StrictHostKeyChecking=no "$SERVER" echo "SSH_OK" 2>/dev/null; then
  echo "  SSH 연결 성공!"
else
  echo "  [ERROR] SSH 연결 실패. 네트워크/방화벽 확인 필요."
  exit 1
fi

# rsync 테스트
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

# 4. 코드 다운로드
echo ""
echo "[4/4] 코드 다운로드..."
if [[ -d ~/dataset ]]; then
  echo "  ~/dataset 이미 존재. git pull..."
  cd ~/dataset && git pull 2>&1 || echo "  pull 실패 (무시하고 진행)"
else
  echo "  git clone..."
  git clone "$REPO_URL" ~/dataset 2>&1 || {
    echo "  [ERROR] git clone 실패. 수동으로 코드를 복사하세요."
    exit 1
  }
fi

# 5. Python 환경 확인
echo ""
echo "[확인] Python 환경..."
cd ~/dataset
if python3 -c "import httpx, yaml, pydantic" 2>/dev/null; then
  echo "  필수 패키지 OK"
else
  echo "  패키지 설치 필요. 실행:"
  echo "    pip install httpx pyyaml pydantic pydantic-settings tenacity"
fi

echo ""
echo "============================================"
echo " 세팅 완료!"
echo ""
echo " 수집 시작 명령어:"
echo "   cd ~/dataset"
echo "   bash scripts/lab_collector.sh --machine N --total 25"
echo ""
echo " N = 이 PC의 번호 (1~25)"
echo "============================================"
