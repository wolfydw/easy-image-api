#!/usr/bin/env bash

set -euo pipefail

INSTALLER_URL="https://raw.githubusercontent.com/wolfydw/easy-image-api/main/scripts/install_skill.py"
PYTHON_COMMAND=""
TEMP_FILE=""

cleanup() {
  if [[ -n "$TEMP_FILE" && -f "$TEMP_FILE" ]]; then
    rm -f -- "$TEMP_FILE"
  fi
}

trap cleanup EXIT HUP INT TERM

for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 &&
    "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
    PYTHON_COMMAND="$candidate"
    break
  fi
done

if [[ -z "$PYTHON_COMMAND" ]]; then
  printf '%s\n' "Error: Python 3.9 or newer is required." >&2
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  printf '%s\n' "Error: curl is required to download the installer." >&2
  exit 1
fi

umask 077
TEMP_FILE="$(mktemp "${TMPDIR:-/tmp}/easy-image-api-installer.XXXXXX")"

curl \
  --fail \
  --silent \
  --show-error \
  --location \
  --proto '=https' \
  --tlsv1.2 \
  --output "$TEMP_FILE" \
  "$INSTALLER_URL"

if (: </dev/tty) 2>/dev/null; then
  "$PYTHON_COMMAND" "$TEMP_FILE" </dev/tty
else
  "$PYTHON_COMMAND" "$TEMP_FILE"
fi
