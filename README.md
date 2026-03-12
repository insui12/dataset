# GBTD: Global Bug Tracker Dataset Infrastructure (raw warehouse v0)

## 핵심 목표
- 전처리 이전의 **원시 수집 warehouse** 구축
- official API만 사용
- closed/resolved superset 보존
- raw payload(JSONB/원문) + canonical normalization 이원 보존
- 장기 재사용 가능한 registry + manifest + distributed collector

## 빠른 시작

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env

# 1) DB 스키마

gbtd init-database

# 2) 레지스트리 시딩

gbtd bootstrap-manifest manifests/sample.manifest.yaml

# 3) 샘플 파일럿 수집(job 시딩 + 즉시 1회 실행)
# 가족/인스턴스 전체를 20개씩 우선 수집

gbtd seed-sample --sample-size 20 --page-size 50

gbtd smoke-collect --manifest-path manifests/sample.manifest.yaml --sample-size 20 --iterations 500

# 4) DB 적재 전 API 호출 dry-run (호출 결과를 CSV로 저장)
gbtd preview-collect-csv manifests/sample.manifest.yaml \
  --family github \
  --instance github.com \
  --sample-size 20 \
  --max-pages 2 \
  --page-size 50 \
  --output-dir artifacts/preview_csv
```

`preview-collect-csv`는 `init-database`, DB 연결 없이도 동작하며 호출 응답만 CSV로 저장합니다.

## 패키지 구조

- `src/gbtd_infra/models.py`: PostgreSQL ORM 스키마
- `src/gbtd_infra/manifests.py`: manifest versioning + registry sync
- `src/gbtd_infra/adapter_registry.py`: family slug -> adapter 매핑
- `src/gbtd_infra/adapters/*`: family adapter들 (API-first 계약 구현)
- `src/gbtd_infra/scheduler/lease.py`: `SELECT ... FOR UPDATE SKIP LOCKED`
- `src/gbtd_infra/orchestrator.py`: job claim / dispatch / status update
- `src/gbtd_infra/clients/http.py`: polite HTTP + retry/backoff
- `src/gbtd_infra/cli.py`: 운영 entrypoint
- `migrations/versions/202603050001_initial.py`: Alembic migration

## 오늘 바로 쓰는 실무 명령

```bash
# 수집 인프라(공식 API만) 부팅
gbtd bootstrap-manifest manifests/sample.manifest.yaml
gbtd seed-jobs --family github --instance github.com --job-mode all --sample-size 50
gbtd run-worker --iterations 200

# 범위 지정 worker 실행 예시
gbtd run-worker --family github --instance github.com --entry torvalds/linux --max-jobs 8

# 1~100 샘플 규칙으로 권장 전수 탐색
# (현재 단계는 구현 family 위주. 특이 family는 필요 시 순차 확장)
gbtd seed-sample --sample-size 50
gbtd smoke-collect --family github --instance github.com --max-entries 20 --sample-size 20 --iterations 500 --max-jobs 200

gbtd smoke-collect --sample-size 50 --iterations 1000
```

## Smoke 테스트 가이드 (1~100 건/entry)

```bash
# 1) 설치/환경
python -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env

# 2) 초기 준비
gbtd init-database
gbtd bootstrap-manifest manifests/sample.manifest.yaml

# 3) 구현 family부터 단계적 검증
gbtd smoke-collect --family github --sample-size 20 --iterations 500
gbtd smoke-collect --family gitlab --sample-size 20 --iterations 500

# 4) 특수/미구현 family는 운영 안정성 검증 후 단계적으로 확장
gbtd smoke-collect --sample-size 20 --iterations 2000
```

```bash
# fixture 기반 단위 테스트
PYTHONPATH=src pytest -q tests/test_infer_closed_state.py tests/test_adapters_list_issue_fixtures.py
```

## 실시간 진행 로그 해석

- `run-worker` / `smoke-collect`:
  - `job START/DONE/FAIL` : 단위 job 상태
  - `cycle end: ...` : 배치 단위 처리 요약 (claimed/ok/failed/elapsed_ms)
  - `page_fetch_start` / `page=... issue_count=... inserted=...` : 페이지 처리 진행
- `preview-collect-csv`:
  - 대상 entry별 `preview target`
  - 페이지별 수집된 응답/이슈 건수
  - 종료 시 전체 페이지/이슈/스킵 수치

## 파일럿 CSV Dry-Run

```bash
# manifest 기반으로 호출 결과를 DB insert 없이 CSV로 저장
gbtd preview-collect-csv manifests/sample.manifest.yaml \
  --family github \
  --instance github.com \
  --sample-size 20 \
  --max-pages 2 \
  --page-size 50 \
  --output-dir artifacts/preview_csv
```

`manifests/sample.manifest.yaml`은 라이브 preview/smoke 실행용 green manifest이고, 전체 family coverage 설계는 `manifests/family_matrix.yaml`에서 유지합니다.

생성 파일:
- `artifacts/preview_csv/preview_raw_responses_<family>_<instance>_<timestamp>.csv` : page/요청 호출 레코드
- `artifacts/preview_csv/preview_issues_<family>_<instance>_<timestamp>.csv` : 파싱된 issue rows + closed 판정(`is_closed`, `needs_review`)

## 운영 정책

- anti-bot 우회, HTML 스크래핑, 비공식 파싱 금지
- bounded instance는 `instance_exhaustive`, mega-host는 `manifest_exhaustive`
- `closed` 상태 filter를 최우선으로 쓰고, filter 미지원인 경우 상태 조합 기반으로 판정
- fixed-only, text-only, prompt/학습 파이프라인은 후속 단계
- closed 정책 상세: [`docs/closed_inclusion_policy.md`](docs/closed_inclusion_policy.md)

## 실험 문서

- `docs/architecture_and_schema.md`
- `docs/operations.md`
- `docs/sample_sql_queries.sql`
- `docs/limitations_and_extensions.md`
- `docs/closed_inclusion_policy.md`
