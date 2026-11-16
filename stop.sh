#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$project_root/scripts/python_environment.sh"
select_platform_python
cd "$project_root"
echo "Using $environment_name ($python_bin)"
exec "$python_bin" "$project_root/scripts/manage_services.py" stop "$@"
