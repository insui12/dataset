# 한계와 추후 확장 포인트

## 현재 알려진 한계 (explicit TODO)

- 여러 family adapter는 현재 스켈레톤 상태이며, endpoint 경로/필드 매핑은 2차 단계에서 구현.
- GitHub/GitLab/Jira의 실제 discover/search 파라미터는 placeholder로 남아 있음.
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
