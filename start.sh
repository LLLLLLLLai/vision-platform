#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python_bin="$project_root/.venv/bin/python"

if [[ ! -x "$python_bin" ]]; then
  echo "Python virtual environment not found: $python_bin" >&2
  exit 1
fi

"$python_bin" "$project_root/scripts/init_db.py"
exec "$python_bin" "$project_root/scripts/run.py"
