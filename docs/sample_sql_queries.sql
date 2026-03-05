-- 1) family/instance/entry별 closed issue count
SELECT
  f.slug AS family_slug,
  i.canonical_name AS instance_name,
  e.name AS entry_name,
  COUNT(*) FILTER (WHERE i2.is_closed) AS closed_count,
  COUNT(*) AS total
FROM issues i2
JOIN tracker_families f ON f.id = i2.source_family_id
JOIN tracker_instances i ON i.id = i2.tracker_instance_id
JOIN registry_entries e ON e.id = i2.registry_entry_id
GROUP BY f.slug, i.canonical_name, e.name
ORDER BY total DESC;

-- 2) raw payload size by family
SELECT
  family_id,
  COUNT(*) AS req_cnt,
  SUM(LENGTH(COALESCE(response_body_raw, ''))) / 1024.0 / 1024 AS raw_mb
FROM raw_api_payloads
GROUP BY family_id
ORDER BY raw_mb DESC;

-- 3) blocked registry entries
SELECT
  f.slug, i.canonical_name, r.name, p.block_reason, p.auth_required, p.count_supported
FROM capability_probes p
JOIN tracker_families f ON f.id = p.family_id
LEFT JOIN tracker_instances i ON i.id = p.instance_id
LEFT JOIN registry_entries r ON r.id = p.registry_entry_id
WHERE p.block_reason IN ('blocked', 'unsupported', 'auth_required')
ORDER BY p.created_at DESC;

-- 4) count snapshot comparison
SELECT
  r.name,
  c.registry_entry_id,
  c.query_signature,
  c.count_mode,
  c.count_method,
  c.count_value,
  c.comparator,
  c.created_at AS counted_at
FROM count_snapshots c
JOIN registry_entries r ON r.id = c.registry_entry_id
ORDER BY c.registry_entry_id, c.counted_at DESC;

-- 5) job 상태 대시보드
SELECT
  status,
  COUNT(*) AS jobs,
  COUNT(*) FILTER (WHERE last_error IS NOT NULL) AS with_error
FROM collection_jobs
GROUP BY status
ORDER BY status;
