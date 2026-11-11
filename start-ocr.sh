#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -n "${OCR_PYTHON:-}" ]]; then
  python_bin="$OCR_PYTHON"
elif [[ -x "$project_root/ocr_service/.venv/bin/python" ]]; then
  python_bin="$project_root/ocr_service/.venv/bin/python"
elif [[ -n "${CONDA_PREFIX:-}" && -x "$CONDA_PREFIX/bin/python" ]]; then
  python_bin="$CONDA_PREFIX/bin/python"
else
  echo "Set OCR_PYTHON or activate an OCR conda environment with ocr_service/requirements.txt installed." >&2
  exit 1
fi

if [[ ! -x "$python_bin" ]]; then
  echo "OCR Python environment not found: $python_bin" >&2
  exit 1
fi

exec "$python_bin" "$project_root/scripts/run_ocr.py"
