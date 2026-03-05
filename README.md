# GBTD: Global Bug Tracker Dataset Infrastructure (raw warehouse v0)

## 핵심 목표
- 전처리 이전의 **원시 수집 warehouse** 구축
- official API만 사용
- closed/resolved superset 보존
- raw payload(JSONB/원문) + canonical normalization 이원 보존
- 장기 재사용 가능한 registry + manifest + distributed collector

## 산출물 체크리스트
1. 시스템 아키텍처 설계
2. source taxonomy / tiering
3. family 전략
4. PostgreSQL schema
5. Alembic migration
6. repository 구조
7. collector implementation
8. sample manifest
9. 분산 실행 설계
10. 테스트
11. SQL 샘플 쿼리
12. 운영 가이드

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

# 3) 수집 준비 잡 시딩 (예: github.com)

gbtd seed-jobs --family github --instance github.com

# 4) 워커 실행

gbtd run-worker
```

## 패키지 구조

- `src/gbtd_infra/models.py`: PostgreSQL ORM 스키마
- `src/gbtd_infra/manifests.py`: manifest versioning + registry sync
- `src/gbtd_infra/adapter_registry.py`: family slug -> adapter 매핑
- `src/gbtd_infra/adapters/*`: family adapter들 (API-first, TODO placeholders 포함)
- `src/gbtd_infra/scheduler/lease.py`: `SELECT ... FOR UPDATE SKIP LOCKED`
- `src/gbtd_infra/orchestrator.py`: job claim / dispatch / status update
- `src/gbtd_infra/clients/http.py`: polite HTTP + retry/backoff + token bucket
- `src/gbtd_infra/cli.py`: 운영 entrypoint
- `migrations/versions/202603050001_initial.py`: Alembic migration

## 운영 정책

- anti-bot 우회, HTML 스크래핑, 비공식 파싱 금지
- bounded instance는 instance_exhaustive, mega-host는 manifest_exhaustive
- 재수집은 manifest 변경 또는 backfill 기준으로 수행
- raw payload는 삭제하지 않으며 `raw_api_payloads`에 보존

## 한계(TODO)

- adapter endpoint 상세 구현은 단계 2에서 채울 예정(placeholder)
- 실 API 통합 테스트는 fixture 기반으로 확장 예정
- fixed-only, text-only, prompt/학습 파이프라인은 후속 단계

## 관련 문서

- `docs/architecture_and_schema.md`
- `docs/operations.md`
- `docs/sample_sql_queries.sql`
- `docs/limitations_and_extensions.md`
