#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$ROOT/app"
LOCAL_DATA_DIR="${NIUONE_LOCAL_DATA_DIR:-$ROOT/.local-data}"
RUNTIME="${DASHBOARD_HOME:-$LOCAL_DATA_DIR/runtime}"
ENV_FILE="${DASHBOARD_ENV_FILE:-$LOCAL_DATA_DIR/dashboard.env}"
BACKUP="$LOCAL_DATA_DIR/backups/deploy-$(date +%Y%m%d-%H%M%S)"

cd "$ROOT"

echo "== Validate NiuOne before deploy =="
./scripts/validate.sh

echo "== Backup current app/config to $BACKUP =="
mkdir -p "$BACKUP"
rsync -a "$APP/" "$BACKUP/app/"
cp -p "$ROOT/dashboard.env" "$BACKUP/dashboard.env" 2>/dev/null || true
cp -p "$ENV_FILE" "$BACKUP/dashboard.env" 2>/dev/null || true
cp -p "$ROOT/run-dashboard.sh" "$BACKUP/run-dashboard.sh" 2>/dev/null || true

echo "== Ensure runtime directories =="
mkdir -p "$RUNTIME/cron/output" "$RUNTIME/logs"

echo "== Restart live dashboard on 8787 =="
PID=$(pgrep -f 'niuone_dashboard.py --host 127.0.0.1 --port 8787' | head -1 || true)
if [[ -n "$PID" ]]; then
  kill -HUP "$PID" || true
  sleep 2
fi

echo "== Smoke check =="
curl -s -o /dev/null -w 'dashboard / HTTP:%{http_code} TTFB:%{time_starttransfer} TOTAL:%{time_total}\n' http://127.0.0.1:8787/ || true

echo "Deployed. Backup: $BACKUP"
