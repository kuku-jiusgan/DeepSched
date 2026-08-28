#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$ROOT_DIR/.venv"

if [[ -n "${DEEPSCHED_PROXY:-}" ]]; then
  export HTTP_PROXY="$DEEPSCHED_PROXY"
  export HTTPS_PROXY="$DEEPSCHED_PROXY"
  export http_proxy="$DEEPSCHED_PROXY"
  export https_proxy="$DEEPSCHED_PROXY"
fi

command -v python3 >/dev/null || { echo "错误：未找到 python3" >&2; exit 1; }
command -v corepack >/dev/null || { echo "错误：未找到 corepack（Node.js 需为 20+）" >&2; exit 1; }

python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install -r "$ROOT_DIR/server/requirements.txt"
(cd "$ROOT_DIR/web" && corepack pnpm install --frozen-lockfile)

echo "Linux 开发依赖安装完成。"
echo "运行 ./start.sh 启动前后端。"
