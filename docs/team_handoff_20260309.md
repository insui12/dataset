# GBTD 인수인계 작업 지시서

## 1. 목적

이 문서는 현재 `dataset` 프로젝트의 작업 상태를 팀원에게 인수인계하기 위한 실무 문서다.

현재 우선순위는 아래 3가지다.

1. 각 데이터셋 family별 DB 저장 규칙 확정
2. 로컬에서 API로 수집한 데이터를 JupyterLab 서버 PostgreSQL에 바로 저장하는 운영 방식 확정
3. IP block을 피하면서 로컬 PC 3대로 분산 수집하고 중앙 JupyterLab 서버 DB에 적재하는 방식 확정

이 문서는 "지금까지 한 것", "아직 안 된 것", "바로 해야 할 것"을 분리해서 정리한다.


## 2. 현재 저장소 상태

현재 로컬 저장소에는 범용 GBTD 인프라 코드가 들어 있다.

- 공통 스키마/모델: [models.py](C:/Users/user/Desktop/datasets/dataset/src/gbtd_infra/models.py)
- 공통 CLI: [cli.py](C:/Users/user/Desktop/datasets/dataset/src/gbtd_infra/cli.py)
- Bugzilla family adapter: [bugzilla.py](C:/Users/user/Desktop/datasets/dataset/src/gbtd_infra/adapters/bugzilla.py)
- manifest 샘플: [sample.manifest.yaml](C:/Users/user/Desktop/datasets/dataset/manifests/sample.manifest.yaml)
- 운영 문서: [operations.md](C:/Users/user/Desktop/datasets/dataset/docs/operations.md)

현재 저장소에 없는 것:

- Eclipse 전용 ad hoc 수집 스크립트
- `find_last_eclipse_bug_id.py`
- `ingest_one_eclipse_bug.py`
- `ingest_eclipse_full.py`
- `find_last_issue_per_manifest.py`

중요:

- 위 스크립트들은 대화 중 서버에서 임시로 작성/실행된 것으로 보이며, 현재 로컬 저장소에는 없다.
- 즉 팀원이 로컬 저장소만 clone 하면 Eclipse 전용 임시 스크립트는 자동으로 따라오지 않는다.
- 서버에서 만든 임시 스크립트는 별도 커밋 또는 별도 백업이 필요하다.


## 3. 지금까지 확인된 사실

### 3.1 GBTD 기본 구조

현재 프로젝트는 family별 adapter 구조다.

- Bugzilla, GitHub, GitLab, Jira, Launchpad, Redmine, YouTrack, Google Issue Tracker, Debian BTS, Phabricator adapter가 분리되어 있다.
- 기본 방향은 "공통 canonical schema + raw payload 보존"이다.
- 같은 family라도 instance별 차이가 있으므로 raw JSON 보존이 필수다.

### 3.2 sample manifest 범위

현재 manifest는 full registry가 아니라 샘플이다.

- Mozilla는 `firefox`만 등록
- Eclipse는 `eclipse_platform`만 등록
- GitHub/GitLab/Jira 등도 일부 샘플 entry만 등록

즉 `gbtd` 기본 수집을 manifest 기준으로 돌리면 Eclipse 전체가 아니라 `eclipse_platform`만 수집된다.

### 3.3 Bugzilla 공통성

Mozilla와 Eclipse는 둘 다 Bugzilla family라서 핵심 필드는 대체로 공통이다.

Bugzilla adapter가 현재 공통으로 기대하는 대표 필드:

- `id`
- `summary`
- `description`
- `status`
- `resolution`
- `priority`
- `creator`
- `assigned_to`
- `whiteboard`
- `creation_time`
- `last_change_time`
- `cf_last_closed`

하지만 instance별 차이도 있다.

- `cf_*` custom field는 인스턴스마다 다를 수 있음
- 일부 detail field는 없는 인스턴스가 있을 수 있음
- private/security 제한 이슈는 응답 형태가 다를 수 있음

결론:

- Eclipse용 7개 테이블과 Mozilla용 7개 테이블을 별도로 복제하는 것보다
- `bugzilla_*` 공통 7개 테이블에 `instance_name` 또는 `instance_id`를 두는 구조가 더 낫다.

### 3.4 Eclipse 실험 결과

서버에서 Eclipse Bugzilla에 대해 아래가 검증되었다.

- `bug_id=1`, `10`, `100`, `10000`은 정상 적재
- `bug_id=1000`은 `404`로 skip 기록
- `bug/comments/history/attachments`는 수집 성공
- 일부 bug는 `attachment_data`까지 추가 수집 가능

즉 "개별 bug 1건을 공식 REST API로 가져와 DB에 저장"하는 경로는 검증되었다.


## 4. 현재 DB 설계에 대한 권장안

### 4.1 지금 당장 권장하는 저장 전략

family마다 완전히 다른 테이블 집합을 만드는 방식보다 아래 2층 구조를 권장한다.

1. 범용 warehouse layer
2. family-specific staging layer

범용 warehouse layer:

- 현재 [models.py](C:/Users/user/Desktop/datasets/dataset/src/gbtd_infra/models.py) 기반 유지
- `issues`, `issue_comments`, `issue_events`, `issue_attachments`, `raw_api_payloads` 등을 중심으로 유지

family-specific staging layer:

- `bugzilla_base`
- `bugzilla_desc`
- `bugzilla_hist`
- `bugzilla_cmt`
- `bugzilla_attach`
- `bugzilla_raw`
- `bugzilla_skiplog`

이때 각 테이블에 최소 아래 구분 컬럼 추가:

- `instance_name`
- `instance_base_url`
- `entry_name`
- `entry_tracker_id`

이유:

- Eclipse와 Mozilla를 같은 구조로 적재 가능
- 쿼리와 유지보수가 쉬움
- 테이블 복제 수가 폭증하지 않음
- raw payload를 통해 custom field를 계속 확장 가능

### 4.2 피해야 하는 설계

피해야 할 방식:

- `eclipse_base`, `mozilla_base`, `gnome_base` 식으로 instance마다 7개 테이블 복제

문제:

- schema drift 관리가 어려움
- 컬럼 추가 때 모든 테이블을 동시에 수정해야 함
- cross-instance 비교 쿼리가 불편함
- 팀원이 유지보수하기 어려움


## 5. 로컬 -> JupyterLab 서버 DB 저장 방식

### 5.1 가능한 방식

가능한 방식은 2가지다.

1. 로컬 코드가 JupyterLab 서버 PostgreSQL에 직접 접속
2. SSH 터널을 열고 로컬에서 `127.0.0.1:<local_port>`로 접속

권장:

- 보안상 SSH 터널 권장

예시:

```powershell
ssh -N -L 15432:127.0.0.1:5432 <jupyter_user>@<jupyter_host>
```

로컬 `.env` 예시:

```dotenv
GBTD_DATABASE_URL=postgresql+psycopg://gbtd:<PASSWORD>@127.0.0.1:15432/gbtd
```

장점:

- 로컬에서 API 호출
- 적재는 중앙 DB로 바로 반영
- 서버에 별도 수집 코드 배포 없이 실험 가능

주의:

- 장시간 작업은 로컬 PC 절전/재부팅/네트워크 이슈에 취약
- 생산 배치는 서버에서 직접 실행하는 편이 더 안정적


## 6. 3대 로컬 PC 분산 수집 운영안

### 6.1 목표

- API rate limit과 block risk를 낮추면서
- 3대 로컬 PC가 동시에 수집하고
- 중앙 JupyterLab 서버 PostgreSQL에 저장

### 6.2 권장 운영 원칙

- 동시성은 낮게 유지
- 한 PC가 같은 host에 과도하게 붙지 않음
- 같은 entry를 여러 PC가 중복 수집하지 않음
- 분산 기준은 family 또는 instance 또는 product 단위로 고정

### 6.3 추천 분할 방식

권장 분할:

- PC-A: Bugzilla 계열 일부 instance
- PC-B: GitHub/GitLab
- PC-C: Jira/YouTrack/Redmine/Launchpad

Bugzilla만 따로 분할할 경우:

- PC-A: mozilla, eclipse, gnome
- PC-B: gcc, libreoffice, freebsd
- PC-C: kernel, chromium, 기타

핵심은 "host 단위로 분산"이다.

- 같은 시점에 같은 host를 여러 PC가 동시에 세게 두드리지 않도록 조정

### 6.4 block 방지 규칙

- host별 concurrency `1`
- aggressive retry 금지
- `429` 시 `Retry-After` 준수
- 요청 간 고정 sleep + jitter 사용
- batch 크기 작게 시작
- 처음에는 `sample-size` 또는 `max-bugs`로 검증 후 확대

교수님 요구와 맞는 방향:

- 멀티쓰레드/멀티프로세스 공격적 실행은 피함
- 순차 또는 저동시성 실행
- 중앙 DB 적재만 공유


## 7. 실제 해야 할 일

### 7.1 최우선

1. Eclipse ad hoc 스크립트와 DDL을 로컬 저장소에 정식 반영
2. `eclipse_*`가 아니라 `bugzilla_*` 공통 스키마로 재정리 여부 결정
3. 로컬 -> 서버 DB 연결 방식을 SSH 터널 또는 직접 접속 중 하나로 고정
4. 3대 PC 분산 규칙 문서화

### 7.2 바로 다음

1. Mozilla Bugzilla 1건 적재 테스트
2. Eclipse와 Mozilla payload 차이 비교
3. 공통 컬럼과 raw-only 필드 분리 표 작성
4. `bugzilla_base` 계열 공통 DDL 확정

### 7.3 그다음

1. Bugzilla bounded instance 전수 수집 스크립트 작성
2. `resume state` 테이블 도입
3. skip/retry 정책 정리
4. product/component auto-discovery 정식 구현


## 8. 팀원 작업 지시

### 작업 A: 저장소 정리

- 서버에서 만든 임시 스크립트를 로컬 저장소로 가져와 커밋 가능한 상태로 정리
- ad hoc 스크립트와 범용 `gbtd_infra` 코드를 혼동하지 않게 `scripts/experimental/` 또는 `scripts/bugzilla/` 하위로 분리

### 작업 B: DB 스키마 정리

- Eclipse 전용 7개 테이블을 유지할지, `bugzilla_*` 공통 7개 테이블로 통합할지 결정
- 권장안은 공통 7개 테이블 + `instance_name` 컬럼
- 공통 컬럼 외 나머지는 `extra_json` 또는 `raw_payload`에 저장

### 작업 C: Mozilla 검증

- Mozilla Bugzilla에서 bug 1건 수집
- Eclipse와 응답 필드 비교
- 공통 필드/instance-specific 필드 차이 문서화

### 작업 D: 원격 DB 연결 운영화

- 로컬에서 JupyterLab DB 접속 테스트
- SSH 터널 문서화
- `.env` 예시를 팀 공용 형식으로 정리

### 작업 E: 분산 운영

- PC 3대 역할 분담표 작성
- host별 요청 속도 제한값 합의
- 중복 수집 방지 규칙 합의


## 9. 지금 당장 팀원에게 꼭 전달할 것

- 현재 로컬 저장소에는 Eclipse ad hoc 스크립트가 없다.
- 서버에서 실행된 임시 스크립트는 저장소에 반영되지 않았을 수 있다.
- `sample.manifest.yaml`은 full registry가 아니라 샘플이다.
- `gbtd` 기본 수집을 돌리면 Eclipse 전체가 아니라 `eclipse_platform`만 대상이다.
- Bugzilla 계열은 공통 핵심 필드가 있으므로 공통 schema 재사용이 가능하다.
- 하지만 custom field 차이 때문에 raw payload 보존은 필수다.
- 3대 PC 분산 시 성능보다 안정성과 block 회피가 우선이다.


## 10. 권장 결론

현재 단계에서 가장 합리적인 다음 결정은 아래다.

1. Eclipse-only 임시 실험을 종료하고 Bugzilla 공통 staging schema로 정리
2. Mozilla/Eclipse 두 instance에서 같은 적재 코드가 도는지 먼저 검증
3. 로컬 3대 -> 중앙 JupyterLab PostgreSQL 구조를 SSH 터널 또는 직접 접속으로 고정
4. Bugzilla bounded instance부터 저속 전수 수집 시작

이 4가지를 먼저 확정해야 나머지 GitHub/GitLab/Jira family 확장 때 구조가 흔들리지 않는다.
