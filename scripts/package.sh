#!/usr/bin/env bash

# Build sdist and wheel, temporarily staging LICENSE; --skip-frontend reuses frontend/dist.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
backend="$root/backend"
license_src="$root/LICENSE"
license_dst="$backend/LICENSE"
skip_frontend=0

for arg in "$@"; do
  case "$arg" in
    --skip-frontend) skip_frontend=1 ;;
    -h | --help)
      sed -n '2,16p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "unknown option: $arg" >&2
      exit 2
      ;;
  esac
done

[[ -f "$license_src" ]] || {
  echo "LICENSE not found at $license_src" >&2
  exit 1
}

# A leftover from an interrupted run is ours to replace; anything else is not.
if [[ -e "$license_dst" ]]; then
  if cmp -s "$license_src" "$license_dst"; then
    rm -f "$license_dst"
  else
    echo "backend/LICENSE exists and differs from the repository LICENSE." >&2
    echo "Move it away first; this script only manages its own temporary copy." >&2
    exit 1
  fi
fi

cleanup() { rm -f "$license_dst"; }
trap cleanup EXIT
trap 'exit 130' INT TERM

cp "$license_src" "$license_dst"

if ((skip_frontend)); then
  echo "==> Reusing frontend/dist"
  [[ -f "$root/frontend/dist/index.html" ]] || {
    echo "frontend/dist/index.html is missing; run without --skip-frontend" >&2
    exit 1
  }
else
  echo "==> Building frontend"
  (cd "$root/frontend" && bun run build)
fi

echo "==> Packaging backend"
(cd "$backend" && uv build)

echo "==> Verifying artifacts"
python="$(command -v python3 || true)"
[[ -n "$python" ]] || python="$backend/.venv/bin/python"
"$python" - "$root/dist" "$backend/dist" <<'PY'
"""Fail the build when an artifact is missing the licence or the SPA."""

import glob
import sys
import tarfile
import zipfile
from pathlib import Path

problems: list[str] = []
found: list[Path] = []

for raw in sys.argv[1:]:
    directory = Path(raw)
    if not directory.is_dir():
        continue
    found.extend(directory.glob("*.whl"))
    found.extend(directory.glob("*.tar.gz"))

if not found:
    print("no artifacts found; did `uv build` write somewhere else?", file=sys.stderr)
    raise SystemExit(1)

for path in sorted(found, key=lambda item: item.stat().st_mtime):
    size = path.stat().st_size / 1024 / 1024
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            meta = next(n for n in names if n.endswith(".dist-info/METADATA"))
            metadata = archive.read(meta).decode("utf-8", "replace")
    else:
        with tarfile.open(path) as archive:
            names = archive.getnames()
            meta = next(n for n in names if n.endswith("/PKG-INFO"))
            handle = archive.extractfile(meta)
            metadata = handle.read().decode("utf-8", "replace") if handle else ""

    if not any(Path(name).name == "LICENSE" for name in names):
        problems.append(f"{path.name}: LICENSE is not packaged")
    if "License-File: LICENSE" not in metadata:
        problems.append(f"{path.name}: METADATA has no License-File")
    # Fail if node_modules would inflate the package.
    stray = [name for name in names if "node_modules" in name]
    if stray:
        problems.append(f"{path.name}: {len(stray)} node_modules entries packaged")
    if path.suffix == ".whl":
        frontend = [name for name in names if name.startswith("app/_frontend/")]
        if not any(name.endswith("index.html") for name in frontend):
            problems.append(f"{path.name}: bundled frontend is missing")
        print(f"    {path.name}: {size:.1f} MiB, {len(frontend)} frontend files")
    else:
        print(f"    {path.name}: {size:.1f} MiB")

if problems:
    for problem in problems:
        print(f"ERROR: {problem}", file=sys.stderr)
    raise SystemExit(1)
print("artifacts ok: licence packaged, no node_modules, frontend bundled")
PY
