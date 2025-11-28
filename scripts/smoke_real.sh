#!/usr/bin/env bash
set -euo pipefail

# Simple wrapper to run the real smoke command from repo root.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ "${ALLOW_REAL:-}" != "1" ]]; then
  echo "ALLOW_REAL=1 is required for real smoke; skipping." >&2
  exit 0
fi

cd "$ROOT_DIR"
poetry run python -m app smoke "$@"
