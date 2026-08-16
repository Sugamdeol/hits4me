#!/usr/bin/env bash
# =============================================================================
# Convenience wrapper around run_native.py (the Docker-free runner).
#
#   * loads .env if present (so the same file works with and without Docker)
#   * picks a usable python3
#   * forwards every argument to run_native.py
#
# Usage:
#   cp .env.example .env && nano .env      # set ACCESS_KEY
#   ./run_native.sh --system-session
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ------------------------------------------------------------------ .env
ENV_FILE="${ENV_FILE:-$SCRIPT_DIR/.env}"
if [ -f "$ENV_FILE" ]; then
  echo "[run] loading $ENV_FILE"
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

# ---------------------------------------------------------------- python
PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
  for candidate in python3 python3.12 python3.11 python3.10 python3.9 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      PYTHON="$candidate"
      break
    fi
  done
fi

if [ -z "$PYTHON" ]; then
  echo "ERROR: python3 not found. Install it, e.g.:" >&2
  echo "  sudo apt-get install -y python3   # Debian/Ubuntu" >&2
  echo "  sudo yum install -y python3       # RHEL/Rocky/Alma" >&2
  exit 1
fi

# run_native.py is stdlib-only, but it needs a reasonably modern Python.
if ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 6) else 1)'; then
  echo "ERROR: Python 3.6+ is required ($($PYTHON --version 2>&1))." >&2
  exit 1
fi

exec "$PYTHON" "$SCRIPT_DIR/run_native.py" "$@"
