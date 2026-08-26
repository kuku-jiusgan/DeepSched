#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${DEEPSCHED_ENV_FILE:-$ROOT_DIR/server/.env}"
BACKUP_DIR="${DEEPSCHED_BACKUP_DIR:-$ROOT_DIR/.runtime/backups/mysql}"
RETENTION_DAYS="${DEEPSCHED_BACKUP_RETENTION_DAYS:-30}"
[[ -r "$ENV_FILE" ]] || { echo "错误：无法读取数据库配置 $ENV_FILE" >&2; exit 1; }
command -v mysqldump >/dev/null || { echo "错误：未找到 mysqldump" >&2; exit 1; }
database_url=$(sed -n 's/^DATABASE_URL=//p' "$ENV_FILE" | tail -n 1 | tr -d '\r')
[[ "$database_url" == mysql+pymysql://* ]] || { echo "错误：DATABASE_URL 必须是 mysql+pymysql:// 格式" >&2; exit 1; }
database_url="${database_url#mysql+pymysql://}"
credentials="${database_url%%/*}"
database_name="${database_url#*/}"
user_password="${credentials%@*}"
host_port="${credentials##*@}"
db_user="${user_password%%:*}"
db_password="${user_password#*:}"
db_host="${host_port%%:*}"
db_port="${host_port##*:}"
db_port="${db_port%%/*}"
database_name="${database_name%%\?*}"
[[ -n "$db_user" && -n "$db_password" && -n "$db_host" && -n "$database_name" ]] || { echo "错误：DATABASE_URL 缺少数据库连接字段" >&2; exit 1; }
mkdir -p "$BACKUP_DIR"
umask 077
credentials_file=$(mktemp)
lock_file="$BACKUP_DIR/.backup.lock"
cleanup() { rm -f "$credentials_file"; }
trap cleanup EXIT
(
  flock -n 9 || { echo "已有备份任务运行，跳过本次执行"; exit 0; }
  printf '[client]\nuser=%s\npassword=%s\nhost=%s\nport=%s\n' "$db_user" "$db_password" "$db_host" "$db_port" > "$credentials_file"
  timestamp=$(date '+%Y%m%d-%H%M%S')
  target="$BACKUP_DIR/${database_name}-${timestamp}.sql.gz"
  temporary="${target}.tmp"
  mysqldump --defaults-extra-file="$credentials_file" --single-transaction --routines --events --triggers "$database_name" | gzip -c > "$temporary"
  mv "$temporary" "$target"
  find "$BACKUP_DIR" -maxdepth 1 -type f -name '*.sql.gz' -mtime +"$RETENTION_DAYS" -delete
  echo "MySQL 备份完成：$target"
) 9>"$lock_file"
