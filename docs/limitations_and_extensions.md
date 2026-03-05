# 한계와 추후 확장 포인트

## 현재 알려진 한계 (explicitly tracked)

- 일부 family는 manifest 우선 수집 정책으로 시작하고, bounded instance에서만 adapter 기반 자동 탐색이 활성화되어 있다.
- 일부 family는 discover/search 세부 파라미터가 API 정책상 변동되어, 운영 단계에서 보수적으로 유지되고 있다.
- `raw_api_pages`의 페이지 링크 추적은 현재 최소 저장량만.
- attachment body는 binary 저장하지 않고 metadata만 저장.

## 추후 확장 포인트

- future preprocessing tables
  - issue_raw_text_norm
  - issue_language_profile
  - issue_preprocess_artifacts

- fixed-like derived views
  - resolved_fixed_only
  - bug_resolved_only

- text-only / richer-evidence separation
  - pure_text, with_evidence, with_attachments

- training export
  - csv/jsonl exporter with manifest filters
  - train/dev/test split by source family/version

- API integration hardening
  - adapter 단위 테스트 + 재시도/backoff 지표 시뮬레이션
  - pagination cursor corruption 검사
