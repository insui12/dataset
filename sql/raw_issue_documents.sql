CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS raw_issue_documents (
    id BIGSERIAL PRIMARY KEY,
    source_family TEXT NOT NULL,
    tracker_instance TEXT NOT NULL,
    project_name TEXT NOT NULL,
    product_name TEXT,
    component_name TEXT,
    year INT NOT NULL,
    month INT NOT NULL,
    doc_type TEXT NOT NULL,
    bug_id TEXT NOT NULL,
    bug_key TEXT,
    item_id TEXT,
    storage_path TEXT NOT NULL,
    api_url TEXT,
    source_url TEXT,
    payload_sha256 TEXT NOT NULL,
    raw_payload JSONB NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    http_status INT,
    is_private BOOLEAN NOT NULL DEFAULT FALSE,
    note TEXT,
    CONSTRAINT ck_raw_issue_documents_year CHECK (year >= 1990 AND year <= 2100),
    CONSTRAINT ck_raw_issue_documents_month CHECK (month >= 1 AND month <= 12),
    CONSTRAINT ck_raw_issue_documents_doc_type CHECK (
        doc_type IN (
            'BASE',
            'DESC',
            'HIST',
            'CMT',
            'ATTACH',
            'ATTACH_DATA',
            'RAW_PAGE',
            'RAW_ITEM'
        )
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_raw_issue_documents_logical
ON raw_issue_documents (
    project_name,
    doc_type,
    bug_id,
    COALESCE(item_id, '')
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_raw_issue_documents_content
ON raw_issue_documents (
    source_family,
    tracker_instance,
    project_name,
    doc_type,
    bug_id,
    COALESCE(item_id, ''),
    payload_sha256
);

CREATE INDEX IF NOT EXISTS ix_raw_issue_documents_project_bug
ON raw_issue_documents (project_name, bug_id);

CREATE INDEX IF NOT EXISTS ix_raw_issue_documents_family_instance
ON raw_issue_documents (source_family, tracker_instance);

CREATE INDEX IF NOT EXISTS ix_raw_issue_documents_doc_type
ON raw_issue_documents (doc_type);

CREATE INDEX IF NOT EXISTS ix_raw_issue_documents_year_month
ON raw_issue_documents (year, month);

CREATE INDEX IF NOT EXISTS ix_raw_issue_documents_fetched_at
ON raw_issue_documents (fetched_at DESC);

CREATE INDEX IF NOT EXISTS ix_raw_issue_documents_payload_gin
ON raw_issue_documents
USING GIN (raw_payload);
