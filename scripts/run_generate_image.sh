#!/usr/bin/env sh

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
VERSION_CHECK='import sys; raise SystemExit(0 if sys.version_info >= (3, 7) else 1)'

for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 \
    && "$candidate" -c "$VERSION_CHECK" >/dev/null 2>&1; then
    exec "$candidate" "$SCRIPT_DIR/generate_image.py" "$@"
  fi
done

printf '%s\n' "错误：未找到可用的 Python 3.7+ 环境。请先安装 Python 3，再重试。" >&2
exit 1
