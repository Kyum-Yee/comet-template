#!/bin/sh
# comet-template installer (macOS · Linux). Windows: `python comet.py setup`.
# Usage: ./install.sh [comet setup options]   e.g. ./install.sh --yes   ./install.sh --link
set -e
DIR=$(cd "$(dirname "$0")" && pwd)

if command -v uv >/dev/null 2>&1; then
  exec uv run --quiet --script "$DIR/comet.py" setup "$@"
fi

PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    PY=$c; break
  fi
done
if [ -z "$PY" ]; then
  echo "✗ Python 3.10 이상이 필요합니다. 가장 쉬운 길: uv 설치(https://docs.astral.sh/uv/) 후 다시 실행" >&2
  exit 1
fi
if ! "$PY" -c 'import PIL' 2>/dev/null; then
  echo "△ uv가 없어 Pillow(미리보기·소재·썸네일)를 pip로 설치합니다: $PY -m pip install --user 'pillow>=10'"
  "$PY" -m pip install --user --quiet 'pillow>=10' || echo "△ 설치 실패. 나중에 직접 설치하거나 uv를 쓰세요" >&2
fi
exec "$PY" "$DIR/comet.py" setup "$@"
