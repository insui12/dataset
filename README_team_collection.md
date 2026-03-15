# Team Collection README

## 목적
- `sample.manifest.yaml` 기준으로 이슈를 로컬 JSON으로 수집한다.
- `mozilla`, `eclipse`는 이번 분산 수집 범위에서 제외한다.
- 각 팀원은 자기에게 할당된 entry만 실행한다.
- 기본 운영 방식은 `round-robin`이다.
  - entry 1개를 오래 연속 호출하지 않고
  - 각 entry에서 1페이지씩만 받고 다음 entry로 넘어간다.

## 현재 저장 방식
- 저장 루트 예시:
  - `artifacts/json_downloads_round_robin_A`
  - `artifacts/json_downloads_round_robin_B`
  - `artifacts/json_downloads_round_robin_C`

- Bugzilla는 full report 저장:
  - `BASE/<bug_id>.json`
  - `DESC/<bug_id>.json`
  - `CMT/<bug_id>_<comment_id>.json`
  - `HIST/<bug_id>_<seq>.json`
  - `ATTACH/<bug_id>_<attachment_id>.json`
  - `ATTACH_DATA/<bug_id>_<attachment_id>.json`

- GitHub / GitLab은 현재 `BASE` 중심 저장이다.
  - issue body 포함 raw payload는 저장되지만
  - comments / events / attachments 세분화는 아직 안 붙였다.

## 실행 환경
- 권장: WSL Ubuntu
- Python: `3.11`
- conda env 이름 예시: `gbtd_raw`

## 최초 1회 설치
```bash
cd /mnt/c/Users/user/Desktop/datasets/dataset

conda create -n gbtd_raw python=3.11 -y
conda activate gbtd_raw

python -m pip install -U pip
pip install -e .
```

## 실행 전 공통
```bash
cd /mnt/c/Users/user/Desktop/datasets/dataset
conda activate gbtd_raw
```

## A / B / C 할당

### A
- `gitlab-org/gitlab`
- `gitlab-org/gitlab-runner`
- `gitlab-org/gitaly`
- `gitlab-org/omnibus-gitlab`
- `kernel`
- `apache/airflow`

### B
- `microsoft/vscode`
- `llvm/llvm-project`
- `nodejs/node`
- `moby/moby`
- `freebsd`

### C
- `rust-lang/rust`
- `python/cpython`
- `kubernetes/kubernetes`
- `gcc`
- `libreoffice`

## 권장 실행 방식
- `scripts/download_manifest_json_round_robin.py`
- entry마다 1페이지씩만 받고 다음 entry로 넘어간다.
- 완료된 entry는 자동으로 skip한다.
- `_state` 파일 기준으로 중단 후 재실행이 가능하다.

## 테스트 실행
먼저 한 사이클만 돌려서 경로와 로그를 확인한다.

### A 테스트
```bash
PYTHONPATH=src python scripts/download_manifest_json_round_robin.py \
  --mode all \
  --page-size 20 \
  --entries gitlab-org/gitlab,gitlab-org/gitlab-runner,gitlab-org/gitaly,gitlab-org/omnibus-gitlab,kernel,apache/airflow \
  --pause-seconds 1 \
  --max-cycles 1 \
  --output-dir artifacts/json_downloads_round_robin_A_test
```

### B 테스트
```bash
PYTHONPATH=src python scripts/download_manifest_json_round_robin.py \
  --mode all \
  --page-size 20 \
  --entries microsoft/vscode,llvm/llvm-project,nodejs/node,moby/moby,freebsd \
  --pause-seconds 1 \
  --max-cycles 1 \
  --output-dir artifacts/json_downloads_round_robin_B_test
```

### C 테스트
```bash
PYTHONPATH=src python scripts/download_manifest_json_round_robin.py \
  --mode all \
  --page-size 20 \
  --entries rust-lang/rust,python/cpython,kubernetes/kubernetes,gcc,libreoffice \
  --pause-seconds 1 \
  --max-cycles 1 \
  --output-dir artifacts/json_downloads_round_robin_C_test
```

## 실제 실행
테스트가 정상인지 확인한 뒤 `--max-cycles 1`만 빼고 실행한다.

### A 실행
```bash
PYTHONPATH=src python scripts/download_manifest_json_round_robin.py \
  --mode all \
  --page-size 100 \
  --entries gitlab-org/gitlab,gitlab-org/gitlab-runner,gitlab-org/gitaly,gitlab-org/omnibus-gitlab,kernel,apache/airflow \
  --pause-seconds 1 \
  --output-dir artifacts/json_downloads_round_robin_A
```

### B 실행
```bash
PYTHONPATH=src python scripts/download_manifest_json_round_robin.py \
  --mode all \
  --page-size 100 \
  --entries microsoft/vscode,llvm/llvm-project,nodejs/node,moby/moby,freebsd \
  --pause-seconds 1 \
  --output-dir artifacts/json_downloads_round_robin_B
```

### C 실행
```bash
PYTHONPATH=src python scripts/download_manifest_json_round_robin.py \
  --mode all \
  --page-size 100 \
  --entries rust-lang/rust,python/cpython,kubernetes/kubernetes,gcc,libreoffice \
  --pause-seconds 1 \
  --output-dir artifacts/json_downloads_round_robin_C
```

## 로그 해석
- `[CYCLE]`
  - round-robin 한 바퀴 시작
- `[ENTRY]`
  - 해당 entry에서 이번 cycle에 1페이지 처리
  - `saved=...`
  - `total=...`
  - `next_cursor=...`
  - `completed=True/False`
- `[CYCLE_DONE]`
  - 한 바퀴 종료
- `[DONE] all selected entries completed`
  - 선택된 entry 전부 완료

## 중단 / 재시작
- 중간에 `Ctrl+C`로 멈춰도 된다.
- 같은 명령을 다시 실행하면 `_state` 파일 기준으로 이어서 진행한다.
- 상태 파일 위치:
  - `artifacts/json_downloads_round_robin_A/_state`
  - `artifacts/json_downloads_round_robin_B/_state`
  - `artifacts/json_downloads_round_robin_C/_state`

## 완료된 프로젝트 처리
- 어떤 entry가 완료되면 state에 `completed=true`가 저장된다.
- 다음 cycle부터는 자동으로 skip된다.
- 다른 미완료 entry만 계속 반복한다.

## 빠른 점검 명령

### 저장된 프로젝트 폴더 확인
```bash
find artifacts/json_downloads_round_robin_A -maxdepth 2 -type d
```

### 상태 파일 확인
```bash
find artifacts/json_downloads_round_robin_A/_state -type f -maxdepth 1
```

### Bugzilla full report 구조 확인
```bash
find artifacts/json_downloads_round_robin_B/BUGZILLA_FREEBSD_FREEBSD/2026/03 -maxdepth 1 -type d
```

### 본문 확인
```bash
cat artifacts/json_downloads_round_robin_B/BUGZILLA_FREEBSD_FREEBSD/2026/03/DESC/1.json
```

## 주의
- Bugzilla `bug_id`는 product 내부 번호가 아니라 인스턴스 전역 번호다.
  - 따라서 `kernel`의 첫 bug가 `213863`처럼 커도 이상이 아니다.
- `--mode all`을 사용해야 열린/닫힌 이슈를 모두 받는다.
- output 디렉토리를 바꾸면 새 수집으로 시작한다.
- 기존 state를 이어받고 싶으면 같은 output 디렉토리를 써야 한다.

## 현재 한계
- GitHub / GitLab은 아직 `BASE` 위주 저장이다.
- Bugzilla만 full report 분해 저장이 붙어 있다.
- `mozilla`, `eclipse`는 이번 팀 분산 수집 범위에서 제외한다.

## 관련 스크립트
- `scripts/download_manifest_json.py`
  - 일반 다운로드
- `scripts/download_manifest_json_round_robin.py`
  - entry 1페이지씩 교차 수집
- `scripts/find_last_issue_per_manifest.py`
  - manifest entry별 마지막 issue 번호/키 확인

