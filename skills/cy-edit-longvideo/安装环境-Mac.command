#!/usr/bin/env bash
set -euo pipefail
lve_dir="$(cd "$(dirname "$0")" && pwd)"
exec bash "$lve_dir/scripts/bootstrap.sh" "$@"
