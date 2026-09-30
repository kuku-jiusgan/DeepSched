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
PID_FILE="$RUNTIME_DIR/deepsched.pid"

usage() {
  echo "用法：./start.sh [--production|--stop|--help]"
  echo "  默认           在后台启动开发模式（前端 5889，后端 8000，支持热更新）"
  echo "  --production   构建前端并在后台启动 5889 端口的正式模式"
  echo "  --stop         停止由本脚本启动的后台服务"
}

is_process_running() {
  local pid="$1"
  [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null
}

read_service_pid() {
  [[ -f "$PID_FILE" ]] || return 1
  local pid expected_start_time current_start_time
  read -r pid expected_start_time <"$PID_FILE"
  is_process_running "$pid" || return 1
  [[ "$expected_start_time" =~ ^[0-9]+$ ]] || return 1
  current_start_time="$(awk '{print $22}' "/proc/$pid/stat" 2>/dev/null)"
  [[ "$current_start_time" == "$expected_start_time" ]] || return 1
  echo "$pid"
}

stop_services() {
  local pid
  if ! pid="$(read_service_pid)"; then
    echo "错误：未找到正在运行的 DeepSched 后台服务" >&2
    return 1
  fi

  kill -TERM -- "-$pid"
  for _ in {1..50}; do
    if ! is_process_running "$pid"; then
      rm -f "$PID_FILE"
      echo "DeepSched 服务已停止。"
      return 0
    fi
    sleep 0.1
  done

  echo "错误：服务未在 5 秒内停止，请检查进程 $pid" >&2
  return 1
}

ensure_port_available() {
  local port="$1"
  if ss -ltn | awk '{print $4}' | grep -Eq "(^|:)$port$"; then
    echo "错误：端口 $port 已被占用，项目可能已经启动" >&2
    return 1
  fi
}

cleanup_development_services() {
  trap - EXIT INT TERM
  [[ -n "${BACKEND_PID:-}" ]] && kill "$BACKEND_PID" 2>/dev/null || true
  [[ -n "${FRONTEND_PID:-}" ]] && kill "$FRONTEND_PID" 2>/dev/null || true
  wait 2>/dev/null || true
}

run_development() {
  trap cleanup_development_services EXIT INT TERM

  (
    cd "$ROOT_DIR/server"
    # 显式排除脚本和测试，避免数据修复期间触发服务重启。
    exec env -u http_proxy -u https_proxy -u all_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
      "$VENV_DIR/bin/uvicorn" app.main:app --reload --reload-dir "$ROOT_DIR/server/app" \
      --reload-exclude "$ROOT_DIR/server/scripts" --reload-exclude "$ROOT_DIR/server/tests" \
      --host "$BACKEND_HOST" --port "$BACKEND_PORT"
  ) >>"$BACKEND_LOG_DIR/uvicorn.out.log" 2>>"$BACKEND_LOG_DIR/uvicorn.err.log" &
  BACKEND_PID=$!

  (
    cd "$ROOT_DIR/web"
    exec corepack pnpm run dev --host "$FRONTEND_HOST" --port "$FRONTEND_PORT"
  ) >>"$FRONTEND_LOG_DIR/vite.out.log" 2>>"$FRONTEND_LOG_DIR/vite.err.log" &
  FRONTEND_PID=$!

  wait -n "$BACKEND_PID" "$FRONTEND_PID"
}

run_production() {
  local host="${DEEPSCHED_HOST:-0.0.0.0}"
  local port="${DEEPSCHED_PRODUCTION_PORT:-5889}"
  export ENVIRONMENT="production"
  export CORS_ORIGINS="${CORS_ORIGINS:-https://deepsched.sduzbbri.online,http://127.0.0.1:$port}"
  cd "$ROOT_DIR/server"
  exec env -u http_proxy -u https_proxy -u all_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
    "$VENV_DIR/bin/uvicorn" app.production:app --host "$host" --port "$port" \
    >>"$BACKEND_LOG_DIR/uvicorn.out.log" 2>>"$BACKEND_LOG_DIR/uvicorn.err.log"
}

launch_detached() {
  local internal_mode="$1"
  local pid start_time

  nohup setsid "$ROOT_DIR/start.sh" "$internal_mode" \
    >>"$RUNTIME_DIR/start.out.log" 2>>"$RUNTIME_DIR/start.err.log" </dev/null &
  pid=$!
  start_time="$(awk '{print $22}' "/proc/$pid/stat")"
  echo "$pid $start_time" >"$PID_FILE"

  sleep 1
  if ! is_process_running "$pid"; then
    rm -f "$PID_FILE"
    echo "错误：DeepSched 启动失败，请查看 $RUNTIME_DIR/start.err.log" >&2
    return 1
  fi

  echo "DeepSched 已在后台启动（PID $pid）。"
  echo "停止服务：./start.sh --stop"
  echo "运行日志：$RUNTIME_DIR/logs/"
}

case "${1:-}" in
  --_run-development)
    run_development
    exit $?
    ;;
  --_run-production)
    run_production
    exit $?
    ;;
  "") ;;
  --production) MODE="production" ;;
  --stop)
    stop_services
    exit $?
    ;;
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

if running_pid="$(read_service_pid)"; then
  echo "错误：DeepSched 已在后台运行（PID $running_pid）" >&2
  exit 1
fi

mkdir -p "$BACKEND_LOG_DIR" "$FRONTEND_LOG_DIR"

# 后端一律不走代理。它对外只调企业微信这类国内接口，走代理反而让出口 IP 变成
# 代理节点的地址，企业微信按"企业可信IP"白名单校验时会返回 60020。
if [[ "$MODE" == "production" ]]; then
  PRODUCTION_PORT="${DEEPSCHED_PRODUCTION_PORT:-5889}"
  ensure_port_available "$PRODUCTION_PORT"
  echo "正在构建正式前端..."
  (cd "$ROOT_DIR/web" && corepack pnpm run build)
  launch_detached --_run-production
  echo "正式模式：http://127.0.0.1:$PRODUCTION_PORT"
  exit 0
fi

ensure_port_available "$BACKEND_PORT"
ensure_port_available "$FRONTEND_PORT"
launch_detached --_run-development
echo "前端：http://127.0.0.1:$FRONTEND_PORT（公网代理端口）"
echo "后端文档：http://$BACKEND_HOST:$BACKEND_PORT/docs"
