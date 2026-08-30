#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE="development"
VENV_DIR="$ROOT_DIR/.venv"
# 端口约定：公网 Nginx（nginx.conf）转发到本机 5889；Vite 将 /api 代理到后端 8000。
# 启动服务请统一使用本脚本，必要时通过 DEEPSCHED_* 环境变量覆盖。
BACKEND_HOST="${DEEPSCHED_BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${DEEPSCHED_BACKEND_PORT:-8000}"
FRONTEND_HOST="${DEEPSCHED_FRONTEND_HOST:-0.0.0.0}"
FRONTEND_PORT="${DEEPSCHED_FRONTEND_PORT:-5889}"
RUNTIME_DIR="$ROOT_DIR/.runtime"
BACKEND_LOG_DIR="$RUNTIME_DIR/logs/server"
FRONTEND_LOG_DIR="$RUNTIME_DIR/logs/web"

usage() {
  echo "用法：./start.sh [--production|--help]"
  echo "  默认           启动开发模式（前端 5889，后端 8000，支持热更新）"
  echo "  --production   构建前端并在 5889 端口启动正式模式"
}

case "${1:-}" in
  "") ;;
  --production) MODE="production" ;;
  --help|-h)
    usage
    exit 0
    ;;
  *)
    echo "错误：不支持的参数 ${1}" >&2
    usage >&2
    exit 2
    ;;
esac

if [[ ! -x "$VENV_DIR/bin/uvicorn" || ! -x "$ROOT_DIR/web/node_modules/.bin/vite" ]]; then
  echo "错误：项目依赖尚未安装，请先运行 ./setup-linux.sh" >&2
  exit 1
fi

if [[ ! -f "$ROOT_DIR/server/.env" ]]; then
  echo "错误：缺少 server/.env，请根据 .env.example 配置数据库连接" >&2
  exit 1
fi

mkdir -p "$BACKEND_LOG_DIR" "$FRONTEND_LOG_DIR"

# 后端一律不走代理。它对外只调企业微信这类国内接口，走代理反而让出口 IP 变成
# 代理节点的地址，企业微信按"企业可信IP"白名单校验时会返回 60020
# （not allow to access from your ip），而且节点一换 IP 就变，白名单加不过来。

if [[ "$MODE" == "production" ]]; then
  HOST="${DEEPSCHED_HOST:-0.0.0.0}"
  PORT="${DEEPSCHED_PRODUCTION_PORT:-5889}"
  if ss -ltn | awk '{print $4}' | grep -Eq "(^|:)$PORT$"; then
    echo "错误：端口 $PORT 已被占用，项目可能已经启动" >&2
    exit 1
  fi
  echo "正在构建正式前端..."
  (cd "$ROOT_DIR/web" && corepack pnpm run build)
  export ENVIRONMENT="production"
  export AUTO_CREATE_SCHEMA="${AUTO_CREATE_SCHEMA:-true}"
  export CORS_ORIGINS="${CORS_ORIGINS:-https://deepsched.sduzbbri.online,http://127.0.0.1:$PORT}"
  echo "正式模式：http://127.0.0.1:$PORT"
  cd "$ROOT_DIR/server"
  exec env -u http_proxy -u https_proxy -u all_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
    "$VENV_DIR/bin/uvicorn" app.production:app --host "$HOST" --port "$PORT" \
    > >(tee -a "$BACKEND_LOG_DIR/uvicorn.out.log") \
    2> >(tee -a "$BACKEND_LOG_DIR/uvicorn.err.log" >&2)
fi

for port in "$BACKEND_PORT" "$FRONTEND_PORT"; do
  if ss -ltn | awk '{print $4}' | grep -Eq "(^|:)$port$"; then
    echo "错误：开发端口 $port 已被占用，项目可能已经启动" >&2
    exit 1
  fi
done

cleanup() {
  trap - EXIT INT TERM
  [[ -n "${BACKEND_PID:-}" ]] && kill "$BACKEND_PID" 2>/dev/null || true
  [[ -n "${FRONTEND_PID:-}" ]] && kill "$FRONTEND_PID" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

(
  cd "$ROOT_DIR/server"
  exec env -u http_proxy -u https_proxy -u all_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
    "$VENV_DIR/bin/uvicorn" app.main:app --reload --host "$BACKEND_HOST" --port "$BACKEND_PORT"
) > >(tee -a "$BACKEND_LOG_DIR/uvicorn.out.log") \
  2> >(tee -a "$BACKEND_LOG_DIR/uvicorn.err.log" >&2) &
BACKEND_PID=$!

(
  cd "$ROOT_DIR/web"
  exec corepack pnpm run dev --host "$FRONTEND_HOST" --port "$FRONTEND_PORT"
) > >(tee -a "$FRONTEND_LOG_DIR/vite.out.log") \
  2> >(tee -a "$FRONTEND_LOG_DIR/vite.err.log" >&2) &
FRONTEND_PID=$!

echo "前端：http://127.0.0.1:$FRONTEND_PORT（公网代理端口）"
echo "后端文档：http://$BACKEND_HOST:$BACKEND_PORT/docs"
echo "按 Ctrl+C 停止全部服务。"

wait -n "$BACKEND_PID" "$FRONTEND_PID"
