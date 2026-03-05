# 운영 가이드

## 1. 초기 셋업

1. PostgreSQL 생성
2. `.env` 작성
3. `pip install -e .`
4. `gbtd init-database`
5. `gbtd bootstrap-manifest manifests/sample.manifest.yaml`
6. `gbtd seed_jobs --family github --instance github.com`
7. `gbtd run-worker`

## 2. 3대 동시 실행 권장

- 노드 A: `GBTD_RUNNER_ID=node-a gbtd run-worker`
- 노드 B: `GBTD_RUNNER_ID=node-b gbtd run-worker`
- 노드 C: `GBTD_RUNNER_ID=node-c gbtd run-worker`

모든 노드가 동일한 `DATABASE_URL`를 공유해야 한다.

## 3. 장애 복구

- 타임아웃(job lease 만료) 복구: `gbtd reclaim-jobs`
- 장애 시 수집 실패는 `collection_errors`와 `collection_jobs.last_error`에 저장.
- anti-bot 이벤트: `rate_limit_events`와 `collection_errors`로 추적.

## 4. manifest 버전 관리

- `manifests/*.yaml` 변경 시 `manifest.version` 증가
- 기존 버전과 diff를 저장하고 새 버전으로 `bootstrap-manifest` 재실행
- 레지스트리 변경은 `manifest_versions`로 추적

## 5. static sharding fallback(중앙 DB 미사용 시)

중앙 PostgreSQL이 불가능한 경우:

- 파일 기반 partition key(`family_slug`, `instance_id`)를 기준으로 고정 샤드 분리
- 각 샤드(예: A/B/C)에 동일 코드 배포 후 `--family` 단위로 실행
- 단, manifest와 count snapshot은 병합 불가능성이 크므로 중앙 DB 재결합 단계가 필수
- 최종 목표가 장기 재사용 인프라인 만큼 이 옵션은 fallback로만 사용

## 6. 보안/정책 제약 준수

- 인증 필요 소스는 `visibility=auth_required` 기록
- 승인되지 않은 API 호출은 수행하지 않음
- 공개가 아닌 데이터는 registry와 job에서 차단 여부를 표시
