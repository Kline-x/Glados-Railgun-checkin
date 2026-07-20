#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

if [[ -z "${GLADOS_COOKIES:-}" ]]; then
  echo "ERROR: GLADOS_COOKIES 未设置。请复制 .env.example 为 .env 并填入 Cookie。" >&2
  exit 1
fi

PYTHON="${PYTHON:-python3}"
exec "$PYTHON" "$ROOT/checkin.py"
