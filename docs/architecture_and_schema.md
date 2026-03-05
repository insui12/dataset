# GBTD 시스템 설계

## 1) 시스템 아키텍처 설명

GBTD는 수집 인프라를 4개 레이어로 분리한다.

1. Registry Layer
   - tracker 계보를 표준화: family, instance, project/product/repo, component
   - 수집 정책/가시성/범위(tier, collection_mode, dataset_role 등) 저장
   - 매니페스트 버전(Manifest Version)으로 변경 이력 관리

2. Discovery & Capability Probe Layer
   - 인스턴스/프로젝트가 공식 API로 접근 가능한지와 지원 프로토콜 판별
   - Pagination 방식, count 가능성, auth 요구 여부, blocked 정책을 기록

3. Collector Layer
   - family 별 adapter 기반 플러그인 구조
   - official API만 사용, API 호출 로그와 raw payload 저장

4. Storage & Orchestration Layer
   - PostgreSQL에 raw payload 저장(raw_api_payloads, raw_api_pages)
   - canonical table(issues, comments, events, labels, ...)로 수집 정합성 보존
   - job 큐 + lease 재할당(3대 동시 실행 가정)

## 2) source taxonomy 설계

`registry_entries`는 최소 5속성을 포함해 관리한다.

- tier: `core / extended / special / legacy / excluded`
- collection_mode: `instance_exhaustive / manifest_exhaustive / conditional`
- dataset_role: `software_product / library_runtime / os_kernel / infra_tool / desktop_app / community_process / graveyard_legacy / security_restricted`
- protocol: `REST / GraphQL / JSON-RPC / XML-RPC / SOAP`
- visibility: `public / auth_required / restricted / blocked`
- status: `active / legacy / unknown`

`Tracker family -> instance -> entry(project/repo) -> component` 계층을 고정한다.

## 3) registry tiering rationale

- `core`: 재사용성과 실험 표준성을 담보하는 주요 공개 OSS 추적기.
- `extended`: 공개 범위가 제한적이거나 도메인별 가치가 높아 보완 수집이 필요한 경우.
- `special`: 기존 범용 API 패턴이 덜 표준/특수 프로토콜 필요.
- `legacy`: 과거에 가치가 있으나 현재 접근성/품질이 불안정한 트래커.
- `excluded`: 현재 API-first phase에서 제외.

## 4) platform family별 collector strategy

- **Bugzilla**: REST 우선 → JSON-RPC 대체 → XML-RPC 대체
  - 차단 시 block/unsupported로 명시하고 skip.
  - 가능한 instance/product 자동 발견 우선.

- **GitHub / GitLab / Jira 계열**
  - mega-host 전체 크롤링 금지.
  - manifest_exhaustive로 curated repo/project 범위 수집.
  - closed/resolved는 query 필터로 수집, 이후 fixed-only는 파생 뷰로 처리.

- **Launchpad / Redmine / YouTrack**
  - REST 공식 엔드포인트 기반.
  - 인증/요금제/레이트 제한을 manifest에 반영.

- **Google Issue Tracker / Debian BTS / Phabricator**
  - special family로 별도 전략 유지.
  - 프로토콜 지원/비지원은 blocked/unsupported으로 명시.

## 5) PostgreSQL schema 설계

다음 그룹을 최소 포함한다.

A. Registry / taxonomy
- tracker_families
- tracker_instances
- registry_entries
- registry_components
- collection_policies

B. Collection control
- capability_probes
- count_snapshots
- collection_jobs
- job_leases
- sync_watermarks

C. Raw ingestion
- raw_api_payloads
- raw_api_pages
- collection_errors
- rate_limit_events

D. Canonical data
- issues
- issue_comments
- issue_events
- issue_attachments
- issue_labels
- issue_links
- issue_assignees
- issue_custom_fields

E. Audit / reproducibility
- ingestion_runs
- schema_versions
- manifest_versions

## 6) Alembic migration 또는 DDL

- Alembic: `migrations/versions/202603050001_initial.py`
- 스키마는 `src/gbtd_infra/models.py`의 `Base.metadata` 기반으로 초기 생성.
- 수집 정합성 이슈가 생길 때 revision 단위로 변경 기록.

## 7) Python repository structure

- `src/gbtd_infra/config.py`: env + config file 조합
- `src/gbtd_infra/db.py`: 엔진/세션
- `src/gbtd_infra/models.py`: SQLAlchemy 모델
- `src/gbtd_infra/manifests.py`: manifest 로더/동기화
- `src/gbtd_infra/clients/http.py`: polite API client
- `src/gbtd_infra/adapters/*`: family adapter
- `src/gbtd_infra/adapter_registry.py`: family slug -> adapter 연결
- `src/gbtd_infra/scheduler/lease.py`: DB lease 기반 큐
- `src/gbtd_infra/orchestrator.py`: job 처리
- `src/gbtd_infra/cli.py`: operator CLI

## 7.1) closed/resolved 적용 규칙 (공식 API 우선)

- 1순위: 공식 API가 closed/resolved 필터를 제공하면 이를 우선 사용
- 2순위: 필터가 불가하면 `state/resolution/close_reason/closed_at`로 닫힘 판정
- 3순위: 애매한 레코드는 `is_closed=false`, `needs_review=true` 보존 후 파생 집합에서 제외
- fixed-only: 현재 단계에서 기본 필터로 사용하지 않음(파생 뷰로 분리 생성)

자세한 정책 문서: [`docs/closed_inclusion_policy.md`](docs/closed_inclusion_policy.md)

## 8) collector implementation

- 추상 클래스 `TrackerAdapter`에서 family별 Probe/Discover 인터페이스를 제공.
- `discover`는 현재 manifest 우선 정책을 기본으로 두되, bounded instance에서 가능한 경우 adapter 구현 기반 자동 탐색을 병행한다.
- `run`은 job_type별 처리기(ability probe, count, list/comments/attachments) 기반.

## 9) sample manifests

- `manifests/sample.manifest.yaml` 제공
- 핵심: bounded/unbounded 구분 (`is_bounded` + collection_mode)

## 10) distributed execution design

- shared PostgreSQL + lease table
- 동일 코드 worker 3개가 병렬 실행
- `FOR UPDATE SKIP LOCKED`로 동일 job 동시 처리 방지
- `lease_expires_at` 만료 시 타 워커가 reclaim

## 11) tests

- `tests/test_manifest_loader.py`: manifest 버전/엔트리 파싱
- `tests/test_rate_limit_bucket.py`: token bucket 동작
- `tests/test_count_modes.py`: count mode enum 보존
- `tests/test_infer_closed_state.py`: closed/needs_review 판정 규칙 단위 테스트
- `tests/test_adapters_list_issue_fixtures.py`: fixture 기반 GitHub/GitLab issue list 파싱 테스트

## 12) sample SQL queries

`docs/sample_sql_queries.sql` 참조.

## 13) 운영 README

- 환경 설정: `DATABASE_URL`, 토큰 값
- 1차 실행: migrate -> manifest bootstrap -> seed_jobs -> worker
- 장애 recovery: reclaim-timeout jobs + status 확인
- 감사: capability_probes, count_snapshots, collection_errors

## 14) 한계와 추후 확장 포인트

- 수집 파서 본문 처리 및 이벤트 정합성은 v0 범위를 벗어나기 때문에 현재는 적재 최소값만 보존하고, 전용 후속 모듈에서 확장한다.
- family 일부(특수 family)는 endpoint 실제 파라미터/인증 방식 미반영.
- 추후: fake-fixture based 어댑터 단위 테스트 및 실 API 통합 테스트 추가.

---

## 왜 “전 세계”를 family coverage + versioned registry exhaustiveness로 재정의했나

전 세계의 무한 GitHub/GitLab 전체를 직접 크롤링하면 완전한 재현성이 무의미하고, API 비용과 법적/정책 리스크가 큽니다. 따라서 "가족 단위 커버리지 + 버전 관리 가능한 매니페스트"가 재현성의 기준이 됩니다. 즉, 같은 manifest 버전으로 같은 소스 범위를 반복 수집하면 결과가 비교 가능해집니다.

## 왜 bounded vs mega-host를 분리했나

bounded instance는 등록된 인스턴스의 모든 프로젝트를 탐색해도 수집 범위가 유한하고, 버전관리된 제외 조건이 명확합니다. 반면 GitHub.com 같은 mega-host는 무한에 가깝고 신규 프로젝트 유입이 계속되므로 manifest로 범위를 명시해야 실험 재현성이 생깁니다.

## 각 source family official API/protocol 사용 전략

- Bugzilla: REST를 기본으로 하고 장애 시 JSON-RPC/XML-RPC fallback.
- GitHub: REST/GraphQL, manifest_exhaustive + official pagination.
- GitLab/Jira/Launchpad/Redmine/YouTrack: REST 기준.
- Google/Phabricator/Debian: 각 family의 공식 프로토콜만 사용, unavailable이면 blocked 기록.

## Bugzilla blocked instance fallback

Probe 순서: REST -> JSON-RPC -> XML-RPC.
차단(403/429/네트워크) 시 block 상태로 기록하고 skip. 임의 우회 코드 없음.

## project suitability criteria

매니페스트 적합성은 다음을 기반.
- 공개성(public 또는 authenticated 공개)
- 소프트웨어 artifact 중심성(라이브러리/커널/도구/앱)
- tracker 실제 운영 여부(issue/bug 생성 활동)
- 데모/demo, archive/support-only 여부
- privacy/security 제약

## count exactness 모델

count_snapshots의 count_mode 4분류:
- exact: API count endpoint 존재
- approximate: endpoint 반환 approximate total
- enumerated: 페이지를 전부 순회해 집계
- offset_probe: 제한적 탐색으로 상한선 추정

저장 시 count_signature + count_method + mode + count_value를 남겨 재검증 가능.

## 3대 머신 동시 실행 failure recovery

- 각 워커가 `claim_job()`로 임시 lease 획득.
- 완료 시 `complete_job`, 실패 시 `fail_job`.
- lease timeout 발생 시 reclaim 후 재스케줄.
- crashed worker 로그는 ingestion_runs + collection_errors로 재구성 가능.

## raw preservation과 canonical normalization 분리 이유

- raw payload 보존은 추후 스키마 변경과 재파싱/교정에 필수.
- canonical은 실험용 조회를 위한 최소 공통 컬럼 집합.
- 연구 확장(텍스트 전처리, 라벨 재정의, fixed-only)에 대비해 유연성 확보.

## 왜 fixed-only/preprocessing을 뒤로 미루는지

현재 단계는 raw warehouse 기반의 재현성 보장이 우선이며,
closed/resolved 전체를 저장해야 파생 subset(fixed-only, bug-only, 텍스트-only) 계산의 기준점이 됩니다.

## Index 및 재현성 보조 인덱스 전략(권고)

- `issues`: GiN trigram/full-text index on (`title`, `body_plaintext`)
- `issues`: B-tree on (`tracker_instance_id`, `is_closed`, `updated_at`)
- `issues`: B-tree on (`tracker_issue_id`, `tracker_instance_id`) (unique)
- `raw_api_payloads`: B-tree on `(family_id, fetched_at)` 및 `(request_headers_hash)`
- `collection_jobs`: B-tree on `(status, next_run_at, priority)`
- `count_snapshots`: B-tree on `(registry_entry_id, counted_at)`
- JSONB GIN on `collection_errors.detail`, `issue_custom_fields.field_value_raw`, `raw_api_payloads.response_body_json`
