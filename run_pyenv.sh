#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYENV_VERSION_NAME="${PYENV_VERSION_NAME:-3.12.9}"
VENV_PYTHON="$ROOT_DIR/.venv/bin/python"

if ! command -v pyenv >/dev/null 2>&1; then
  echo "pyenv is required but was not found in PATH." >&2
  exit 1
fi

if ! pyenv versions --bare | grep -Fxq "$PYENV_VERSION_NAME"; then
  echo "pyenv Python $PYENV_VERSION_NAME is not installed." >&2
  exit 1
fi

if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "Missing virtualenv interpreter: $VENV_PYTHON" >&2
  echo "Create it with:" >&2
  echo "  PYENV_VERSION=$PYENV_VERSION_NAME pyenv exec python -m venv .venv" >&2
  echo "  .venv/bin/python -m pip install -e ." >&2
  exit 1
fi

cd "$ROOT_DIR"
export PYENV_VERSION="$PYENV_VERSION_NAME"
exec "$VENV_PYTHON" -m pdftranslator.main "$@"
