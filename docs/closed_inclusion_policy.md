# 고정된 실운영 기준 API 설계서 (closed/resolved 우선 수집 정책)

본 문서는 “전 단계에서 fixed-only를 강제하지 않고 closed/resolved superset를 우선 보존”하는 목적을 실증적으로 뒷받침합니다.

## 1) 적용 우선순위 (1순위 → 3순위)

1. 1순위: API가 제공하는 공식 closed/resolved 필터를 사용
   - 예: `state=closed`, `status=closed`, `state=closed` 필터.
   - 장점:
     - API 네이티브 조회공간을 그대로 제한해 누락/재탐색 비용이 낮고,
     - count/페이지 탐색이 `closed`만 타깃팅되므로 샘플 편향이 작음.
   - 실패조건:
     - 필터 파라미터 미지원/미정의 시 2순위로 전환.

2. 2순위: API 상태 분리 미지원일 때 closed 보조 판정
   - 조합 근거:
     - `closed_at` 존재
     - `state` 또는 `state_raw`(open/closed/reopened 등)
     - `resolution`/`resolution_raw`(resolved/fixed/wontfix/duplicate 등)
     - `close_reason_raw`
   - 판정은 tracker-agnostic이지만 family별 키 규칙은 `IssueAdapter`에서 보강할 수 있음.
   - 보수성 원칙:
     - 닫힘 판정이 불가능한 경우 `is_closed=False`가 아니라 `needs_review=True`로 남김.

3. 3순위: 애매/불명확 상태
   - 위의 결합 규칙으로 closed/closed-like 판단이 안 되면
     - `is_closed=false`
     - `needs_review=true`
     - raw 상태/해결/close reason은 그대로 보존
   - 수집은 `needs_review` 플래그를 가진 레코드도 저장하되, subset 뷰에서 기본 제외.

## 2) fixed-only 미적용 근거

- fixed-only는 tracker별 관행 차이(`WONTFIX`, `INVALID`, `WONTFIX`, `BY DESIGN` 등)로 인해
  연구용 고정점(fixed-only) 정의가 family별로 비표준화되어 오분류 위험이 큼.
- 재현 가능한 확장 실험을 위해:
  - 1차 raw warehouse는 closed/resolved superset를 저장,
  - fixed-like는 추후 derived view/쿼리 단계에서 실험 정의별로 재산출.
- EMSE 관점:
  - 초기 기준 정의가 명시적·객관적으로 재현 가능해야 하며,
  - 원천 필드를 보존해야 추적/반증/재분석이 가능.

## 3) 감사 항목(필수 로그)

- `issues.is_closed`: 1순위/2순위 판단 결과 (boolean)
- `issues.needs_review`: 모호한 경우 true
- `issues.state_raw`, `issues.resolution_raw`, `issues.close_reason_raw`: 판단 근거 보존
- `collection_jobs`, `count_snapshots`, `capability_probes`:
  - closed/resolved 필터 사용 가능성 및 실패 사유를 추적.

## 4) family별 고정 해석 예시

- GitHub: `state=closed` + `state_reason` 존재 시 보조 판정
- GitLab: `state=closed` + issue state 정규화
- Bugzilla: `is_open`/`status`/`resolution` 계열 + 클라이언트 fallback
- Jira/Launchpad/Redmine/YouTrack: API 문서 기준으로 state/resolution 계열 정규화 후 판단
- 기타 special family: API가 닫힘 상태를 분리 제공하지 않으면 `needs_review=true` 유지 후 후속 수작업/재처리 후보화

## 5) “1~100샘플” 실거래 정책

- smoke 단계에서는 entry당 1~100 건으로 고정
- closed 전용 필터(1순위) 또는 heuristic fallback(2/3순위) 결과를 모두 수집하고
  `needs_review_rate`를 함께 리포팅한 뒤 다음 단계로 진행.
