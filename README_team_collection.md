# Team Collection Guide

## 목적
- `sample.manifest.yaml` 기준으로 이슈를 로컬 JSON으로 수집한다.
- 3명(A/B/C)이 같은 코드를 공유하고, `--team A/B/C`만 다르게 줘서 각자 자기 몫만 수집한다.
- round-robin 방식: entry마다 1페이지씩만 받고 다음 entry로 넘어간다.

## 팀 배정
`manifests/team_assignments.yaml`에 정의됨.

### 기본 배정 (GitHub/GitLab/기타 Bugzilla)
| Team | Entry | Family |
|------|-------|--------|
| A | gitlab-org/gitlab, gitlab-org/gitlab-runner, gitlab-org/gitaly, gitlab-org/omnibus-gitlab | GitLab |
| A | kernel | Bugzilla |
| A | apache/airflow | GitHub |
| B | microsoft/vscode, llvm/llvm-project, nodejs/node, moby/moby | GitHub |
| B | freebsd | Bugzilla |
| C | rust-lang/rust, python/cpython, kubernetes/kubernetes | GitHub |
| C | gcc, libreoffice | Bugzilla |

### Mozilla/Eclipse Bugzilla 배정 (대표 항목)
| Team | Entry | Est. Issues |
|------|-------|------------:|
| A | core (mozilla) | 556,210 |
| B | firefox (mozilla) | 228,891 |
| B | devtools (mozilla) | 46,117 |
| C | eclipse_platform | 122,516 |
| C | thunderbird (mozilla) | 75,955 |
| C | toolkit (mozilla) | 65,523 |

> 위 6개 외에도 ~406개의 Mozilla/Eclipse product가 팀별로 자동 배정됨.
> 전체 목록은 `manifests/team_assignments.yaml` 참조.

## 저장 방식
- 저장 루트: `artifacts/json_downloads_round_robin_{A,B,C}/`
- Bugzilla full report: `BASE/`, `DESC/`, `CMT/`, `HIST/`, `ATTACH/`, `ATTACH_DATA/`
- GitHub / GitLab: `BASE/` 중심 (raw payload 포함)

## 실행 환경
- 권장: WSL Ubuntu
- Python: `3.11`
- conda env: `gbtd_raw`

## 최초 1회 설치
```bash
cd /home/selab/dataset
conda create -n gbtd_raw python=3.11 -y
conda activate gbtd_raw
pip install -e .
```

## 실행 명령어

### 테스트 (1 사이클)
```bash
cd /home/selab/dataset
conda activate gbtd_raw

# Team A 테스트
PYTHONPATH=src python scripts/download_manifest_json_round_robin.py \
  --team A --mode all --page-size 20 --pause-seconds 1 --max-cycles 1

# Team B 테스트
PYTHONPATH=src python scripts/download_manifest_json_round_robin.py \
  --team B --mode all --page-size 20 --pause-seconds 1 --max-cycles 1

# Team C 테스트
PYTHONPATH=src python scripts/download_manifest_json_round_robin.py \
  --team C --mode all --page-size 20 --pause-seconds 1 --max-cycles 1
```

### 실제 실행
`--max-cycles 1`만 빼고 실행한다.
```bash
# Team A
PYTHONPATH=src python scripts/download_manifest_json_round_robin.py \
  --team A --mode all --page-size 100 --pause-seconds 1

# Team B
PYTHONPATH=src python scripts/download_manifest_json_round_robin.py \
  --team B --mode all --page-size 100 --pause-seconds 1

# Team C
PYTHONPATH=src python scripts/download_manifest_json_round_robin.py \
  --team C --mode all --page-size 100 --pause-seconds 1
```

## 중단 / 재시작
- 중간에 `Ctrl+C`로 멈춰도 된다.
- 같은 명령을 다시 실행하면 `_state` 파일 기준으로 이어서 진행한다.
- 상태 파일 위치: `artifacts/json_downloads_round_robin_{A,B,C}/_state/`
- **output 디렉토리를 바꾸면 새 수집으로 시작**된다. 이어받으려면 같은 output dir를 써야 한다.

## 안전장치
- **Team lock file**: 각 output 디렉토리에 `_team_lock.json`이 생성됨.
  다른 팀으로 같은 디렉토리에 접근하면 에러 발생.
- 완료된 entry는 state에 `completed=true`가 저장되어 자동 skip.

## 로그 해석
- `[TEAM]` — 팀 배정 정보 및 entry 목록 출력
- `[CYCLE]` — round-robin 한 바퀴 시작
- `[ENTRY]` — 해당 entry에서 1페이지 처리 (`saved=`, `total=`, `completed=`)
- `[SKIP]` — 완료된 entry skip
- `[CYCLE_DONE]` — 한 바퀴 종료 (`completed_entries=X/Y`)
- `[DONE]` — 모든 entry 완료

## 빠른 점검 명령
```bash
# 저장된 프로젝트 폴더 확인
ls artifacts/json_downloads_round_robin_A/

# 상태 파일 확인
ls artifacts/json_downloads_round_robin_A/_state/

# 특정 상태 파일 내용 확인
cat artifacts/json_downloads_round_robin_A/_state/bugzilla__mozilla__core__all.json

# 디스크 사용량
du -sh artifacts/json_downloads_round_robin_*/
```

## 팀 배정 수정
1. `manifests/team_assignments.yaml` 편집
2. 새로운 entry는 먼저 `manifests/sample.manifest.yaml`에 추가
3. 같은 entry가 두 팀에 겹치면 스크립트가 에러를 냄

## 관련 스크립트
| 스크립트 | 용도 |
|---------|------|
| `download_manifest_json_round_robin.py` | **메인 수집 스크립트** (--team 사용) |
| `download_manifest_json.py` | 일반 순차 다운로드 (하위 함수 제공) |
| `discover_bugzilla_products.py` | Mozilla/Eclipse 전체 product 발견 |
| `generate_team_assignments.py` | 발견된 product를 manifest/team에 배정 |
| `reset_github_state.py` | GitHub state 리셋 유틸리티 |
| `find_last_bugzilla_ids_manifest.py` | entry별 마지막 issue 번호 확인 |
| `download_bugzilla_team_split_round_robin.py` | **(deprecated)** 구 Mozilla/Eclipse 전용 |

## 주의사항
- Bugzilla `bug_id`는 인스턴스 전역 번호다 (product 내부 번호가 아님).
- `--mode all`을 사용해야 열린/닫힌 이슈를 모두 받는다.
- GitHub/GitLab은 현재 `BASE` 위주 저장이다 (comments/events 분해 미구현).
- Mozilla/Eclipse 대형 product는 수집에 수일 소요될 수 있다.
