#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$ROOT_DIR/.venv"
RUNTIME_DIR="$ROOT_DIR/.runtime"
BACKEND_LOG_DIR="$RUNTIME_DIR/logs/server"
DATABASE_FILE="$ROOT_DIR/server/cro_scheduler.db"
HOST="${DEEPSCHED_HOST:-0.0.0.0}"
PORT="${DEEPSCHED_PORT:-5889}"

if [[ ! -x "$VENV_DIR/bin/uvicorn" ]]; then
  echo "错误：未找到本机 Python 虚拟环境，请先运行 ./setup-linux.sh" >&2
  exit 1
fi
if ! command -v corepack >/dev/null || [[ ! -d "$ROOT_DIR/web/node_modules" ]]; then
  echo "错误：未找到本机前端依赖，请先运行 ./setup-linux.sh" >&2
  exit 1
fi
if ss -ltn | awk '{print $4}' | grep -Eq "(^|:)$PORT$"; then
  echo "错误：端口 $PORT 已被占用。请先停止 ./start-linux.sh 或其他占用该端口的服务。" >&2
  exit 1
fi

mkdir -p "$BACKEND_LOG_DIR"
echo "正在构建正式前端..."
(cd "$ROOT_DIR/web" && corepack pnpm run build)

# 正式模式使用本机 SQLite，不依赖 Docker 或 MySQL。外部环境变量仍可覆盖默认值。
export ENVIRONMENT="production"
export DATABASE_URL="${DATABASE_URL:-sqlite:///$DATABASE_FILE}"
export AUTO_CREATE_SCHEMA="${AUTO_CREATE_SCHEMA:-true}"
export CORS_ORIGINS="${CORS_ORIGINS:-https://deepsched.sduzbbri.online,http://127.0.0.1:$PORT}"

echo "正式模式：http://127.0.0.1:$PORT"
echo "数据库：$DATABASE_FILE"
echo "日志：$BACKEND_LOG_DIR/uvicorn.out.log"
echo "按 Ctrl+C 停止服务。"

cd "$ROOT_DIR/server"
exec "$VENV_DIR/bin/uvicorn" app.production:app --host "$HOST" --port "$PORT" \
  > >(tee -a "$BACKEND_LOG_DIR/uvicorn.out.log") \
  2> >(tee -a "$BACKEND_LOG_DIR/uvicorn.err.log" >&2)
