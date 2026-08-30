#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python_bin="${OCR_PYTHON:-$project_root/ocr_service/.venv/bin/python}"

if [[ ! -x "$python_bin" ]]; then
  echo "OCR Python environment not found: $python_bin" >&2
  exit 1
fi

exec "$python_bin" "$project_root/scripts/run_ocr.py"
