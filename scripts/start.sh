#!/usr/bin/env bash

# Start the backend and serve frontend/dist; --dev runs the API only.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
backend="$root/backend"

if [[ ! -d "$backend/.venv" ]]; then
  echo "backend/.venv missing. Run: cd backend && uv venv --python 3.12 .venv && uv sync --extra dev" >&2
  exit 1
fi

if [[ "${1:-}" != "--dev" && ! -f "$root/frontend/dist/index.html" ]]; then
  echo "Building frontend with Bun..."
  (cd "$root/frontend" && bun run build)
fi

cd "$backend"
python="$backend/.venv/bin/python"
[[ -x "$python" ]] || python="$backend/.venv/Scripts/python.exe"
exec "$python" -m app
