# 운영 가이드

## 1. 초기 셋업

1. PostgreSQL 생성
2. `.env` 작성
3. `pip install -e .`
4. `gbtd init-database`
5. `gbtd bootstrap-manifest manifests/sample.manifest.yaml`
6. `gbtd seed-sample --sample-size 20` (또는 대상 집합 지정)
7. `gbtd smoke-collect --sample-size 20 --iterations 1000`
8. 운영 worker 실행: `gbtd run-worker`

권장: `seed-jobs`로 집합 기반 수집 job을 만들고, `run-worker`로 실제 수집을 수행.

## 2. 표준 명령

- **Manifest 반영**
  - `gbtd bootstrap-manifest <manifest_path>`
- **Probe/Count/List 잡 시딩**
  - `gbtd seed-jobs --family github --instance github.com --job-mode all --sample-size 50`
- **파일럿 샘플 시딩**
  - `gbtd seed-sample --sample-size 50`
- **파일럿 바로 실행**
  - `gbtd smoke-collect --sample-size 50 --iterations 1000`
- **워커 실행**
  - `gbtd run-worker --iterations 1000` (0은 영구실행)
- **삽입 전 API dry-run(호출 CSV 저장)**
  - `gbtd preview-collect-csv manifests/sample.manifest.yaml --family github --instance github.com --sample-size 20 --max-pages 2 --page-size 50 --output-dir artifacts/preview_csv`
  - 출력 파일:
    - `preview_raw_responses_<family>_<instance>_<timestamp>.csv`
    - `preview_issues_<family>_<instance>_<timestamp>.csv`
  - 목적: 수집 결과를 DB에 적재하지 않고 API 응답 파싱/closed 판정 상태를 먼저 검증
- **재시도 가능한 락 유실 복구**
  - `gbtd reclaim-jobs`

## 2-1. smoke 테스트 가이드 (1~100 건/entry)

- Family별 최소 동작 확인(권장):

```bash
# 샘플 manifest 전체의 지원 family만 1~100 건 수준으로 빠르게 검증
gbtd smoke-collect --sample-size 50 --iterations 2000
```

- 현재 1차 단계에서 리스트 수집이 구현된 family만 대상:

```bash
gbtd smoke-collect --family github --manifest-path manifests/sample.manifest.yaml --sample-size 50 --iterations 2000
gbtd smoke-collect --family gitlab --manifest-path manifests/sample.manifest.yaml --sample-size 50 --iterations 2000
```

- fixture 기반 단위 테스트(실행):

```bash
PYTHONPATH=src pytest -q tests/test_infer_closed_state.py tests/test_adapters_list_issue_fixtures.py tests/test_manifest_loader.py
```

## 3. 3대 동시 실행

- 노드 A: `GBTD_RUNNER_ID=node-a gbtd run-worker`
- 노드 B: `GBTD_RUNNER_ID=node-b gbtd run-worker`
- 노드 C: `GBTD_RUNNER_ID=node-c gbtd run-worker`

모든 노드가 동일한 `GBTD_DATABASE_URL`(또는 `DATABASE_URL`)를 공유해야 합니다.

## 4. 장애 복구

- 타임아웃(job lease 만료) 복구: `gbtd reclaim-jobs`
- 장애 시 수집 실패는 `collection_errors`와 `collection_jobs.last_error`에 저장
- anti-bot 이벤트: `rate_limit_events`와 `collection_errors`로 추적

## 5. 샘플 수집 기준 권장값

- 최소 검증: per entry `1~100`건
- 기본 추천: `sample-size 20` 또는 `50`
- 실제 전수 수집 전, 각 family별 적어도 1~2개 entry에서 closed 수집/수집 실패율/`needs_review` 비율을 점검

## 6. 성능/정합성

- 병렬도는 `GBTD_CONCURRENCY`
- per-host RPS는 `GBTD_PER_HOST_RPS`, 버스트는 `GBTD_RATE_BUCKET_BURST`
- 재수집 시 동일 manifest를 재시딩하고 잡을 재생성해서 증분/정합성 확인

## 7. manifest fallback 정책

중앙 PostgreSQL 사용이 안 되는 경우(강제 fallback):

- family 단위로 코드 배포 후 샤드 분리 실행 가능하나, 현재는 중앙 DB를 권장 아키텍처로 문서화
- 추후 `run-worker`에서 동일 manifest 스냅샷을 기준으로만 병합이 가능해야 함
