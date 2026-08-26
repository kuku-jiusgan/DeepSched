#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
command -v systemctl >/dev/null || { echo "错误：当前系统未提供 systemctl" >&2; exit 1; }
install -m 0750 "$ROOT_DIR/scripts/mysql-backup.sh" /usr/local/bin/deepsched-mysql-backup
install -m 0644 "$ROOT_DIR/systemd/deepsched-mysql-backup.service" /etc/systemd/system/deepsched-mysql-backup.service
install -m 0644 "$ROOT_DIR/systemd/deepsched-mysql-backup.timer" /etc/systemd/system/deepsched-mysql-backup.timer
systemctl daemon-reload
systemctl enable --now deepsched-mysql-backup.timer
systemctl list-timers deepsched-mysql-backup.timer
