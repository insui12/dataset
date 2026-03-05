#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

MANIFEST="${1:-manifests/sample.manifest.yaml}"
SAMPLE_SIZE="${2:-20}"
MAX_PAGES="${3:-5}"
PAGE_SIZE="${4:-50}"
OUT_DIR="${5:-artifacts/preview_csv}"

cd "${REPO_ROOT}"

source .venv/bin/activate

mkdir -p "${OUT_DIR}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_DIR="${OUT_DIR}/${TS}"
mkdir -p "${RUN_DIR}"

echo "Running preview-collect-csv for all entries in: ${MANIFEST}"
echo "Output directory: ${RUN_DIR}"
echo "sample-size=${SAMPLE_SIZE}, max-pages=${MAX_PAGES}, page-size=${PAGE_SIZE}"

PYTHONPATH=src gbtd preview-collect-csv \
  "${MANIFEST}" \
  --sample-size "${SAMPLE_SIZE}" \
  --max-pages "${MAX_PAGES}" \
  --page-size "${PAGE_SIZE}" \
  --output-dir "${RUN_DIR}"

echo "Generated files:"
ls -1 "${RUN_DIR}"/preview_*_all_all_*.csv
