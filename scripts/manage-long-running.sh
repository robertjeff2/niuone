#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ACTION="${1:-install}"
LOCAL_DATA_DIR="${NIUONE_LOCAL_DATA_DIR:-$ROOT/.local-data}"
ENV_FILE="${DASHBOARD_ENV_FILE:-$LOCAL_DATA_DIR/dashboard.env}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  source "$ENV_FILE"
  set +a
fi

DASHBOARD_HOME="${DASHBOARD_HOME:-$LOCAL_DATA_DIR/runtime}"
PYTHON_BIN="${PYTHON_BIN:-$LOCAL_DATA_DIR/.venv/bin/python}"
LOG_DIR="${DASHBOARD_LOG_DIR:-$DASHBOARD_HOME/logs}"
PLATFORM="$(uname -s)"

LABELS=(
  "ai.niuone.dashboard"
  "ai.niuone.cron-scheduler"
  "ai.niuone.x-watchlist"
)
PROGRAMS=(
  "$ROOT/run-dashboard.sh"
  "$ROOT/run-niuone-cron-scheduler.sh"
  "$ROOT/run-x-watchlist-daemon.sh"
)
LINUX_UNITS=(
  "niuone-dashboard.service"
  "niuone-cron-scheduler.service"
  "niuone-x-watchlist.service"
)

usage() {
  cat <<'EOF'
Manage NiuOne long-running services on macOS or Linux.

Usage:
  ./scripts/manage-long-running.sh [install|status|restart|uninstall]
EOF
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "$1 is required for long-running mode on $PLATFORM." >&2
    exit 1
  fi
}

generate_macos_plist() {
  local plist_path="$1"
  local label="$2"
  local program="$3"
  "$PYTHON_BIN" - "$plist_path" "$label" "$program" "$ROOT" "$LOCAL_DATA_DIR" "$ENV_FILE" "$LOG_DIR" <<'PY'
import plistlib
import sys
from pathlib import Path

plist_path, label, program, root, data_dir, env_file, log_dir = sys.argv[1:]
log_path = Path(log_dir)
payload = {
    "Label": label,
    "ProgramArguments": [program],
    "WorkingDirectory": root,
    "EnvironmentVariables": {
        "NIUONE_LOCAL_DATA_DIR": data_dir,
        "DASHBOARD_ENV_FILE": env_file,
        "PYTHONDONTWRITEBYTECODE": "1",
    },
    "RunAtLoad": True,
    "KeepAlive": True,
    "ProcessType": "Background",
    "ThrottleInterval": 5,
    "StandardOutPath": str(log_path / f"{label}.stdout.log"),
    "StandardErrorPath": str(log_path / f"{label}.stderr.log"),
}
Path(plist_path).write_bytes(plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=False))
PY
}

install_macos() {
  require_command launchctl
  if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "Python virtual environment is missing: $PYTHON_BIN" >&2
    echo "Run ./run.sh once without --skip-install, then retry." >&2
    exit 1
  fi

  local agent_dir="$HOME/Library/LaunchAgents"
  local domain="gui/$(id -u)"
  mkdir -p "$agent_dir" "$LOG_DIR"

  for index in "${!LABELS[@]}"; do
    local label="${LABELS[$index]}"
    local plist="$agent_dir/$label.plist"
    launchctl bootout "$domain/$label" >/dev/null 2>&1 || true
    generate_macos_plist "$plist" "$label" "${PROGRAMS[$index]}"
    launchctl bootstrap "$domain" "$plist"
    launchctl enable "$domain/$label" >/dev/null 2>&1 || true
    launchctl kickstart -k "$domain/$label"
  done

  echo "NiuOne LaunchAgents installed and started."
  echo "  status:    ./scripts/manage-long-running.sh status"
  echo "  uninstall: ./scripts/manage-long-running.sh uninstall"
}

status_macos() {
  require_command launchctl
  local domain="gui/$(id -u)"
  local failed=0
  for label in "${LABELS[@]}"; do
    if ! launchctl print "$domain/$label"; then
      failed=1
    fi
  done
  return "$failed"
}

restart_macos() {
  require_command launchctl
  local domain="gui/$(id -u)"
  for label in "${LABELS[@]}"; do
    launchctl kickstart -k "$domain/$label"
  done
}

uninstall_macos() {
  require_command launchctl
  local agent_dir="$HOME/Library/LaunchAgents"
  local domain="gui/$(id -u)"
  for label in "${LABELS[@]}"; do
    launchctl bootout "$domain/$label" >/dev/null 2>&1 || true
    rm -f "$agent_dir/$label.plist"
  done
  echo "NiuOne LaunchAgents removed. Local data was kept at $LOCAL_DATA_DIR."
}

systemd_escape() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//%/%%}"
  printf '%s' "$value"
}

generate_linux_unit() {
  local unit_path="$1"
  local description="$2"
  local program="$3"
  local root_value data_value env_value program_value
  root_value="$(systemd_escape "$ROOT")"
  data_value="$(systemd_escape "$LOCAL_DATA_DIR")"
  env_value="$(systemd_escape "$ENV_FILE")"
  program_value="$(systemd_escape "$program")"

  {
    printf '[Unit]\n'
    printf 'Description=%s\n' "$description"
    printf 'Wants=network-online.target\n'
    printf 'After=network-online.target\n\n'
    printf '[Service]\n'
    printf 'Type=simple\n'
    printf 'WorkingDirectory="%s"\n' "$root_value"
    printf 'Environment="NIUONE_LOCAL_DATA_DIR=%s"\n' "$data_value"
    printf 'Environment="DASHBOARD_ENV_FILE=%s"\n' "$env_value"
    printf 'Environment="PYTHONDONTWRITEBYTECODE=1"\n'
    printf 'ExecStart="%s"\n' "$program_value"
    printf 'Restart=on-failure\n'
    printf 'RestartSec=5\n\n'
    printf '[Install]\n'
    printf 'WantedBy=default.target\n'
  } > "$unit_path"
}

install_linux() {
  require_command systemctl
  if [[ "${EUID:-$(id -u)}" == "0" ]]; then
    echo "Do not install NiuOne as root. Use a dedicated non-root user." >&2
    exit 1
  fi
  if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "Python virtual environment is missing: $PYTHON_BIN" >&2
    echo "Run ./run.sh once without --skip-install, then retry." >&2
    exit 1
  fi
  if ! systemctl --user show-environment >/dev/null 2>&1; then
    echo "A user systemd session is not available." >&2
    echo "Log in with a systemd user session or install the units manually." >&2
    exit 1
  fi

  local unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
  mkdir -p "$unit_dir" "$LOG_DIR"
  generate_linux_unit "$unit_dir/${LINUX_UNITS[0]}" "NiuOne Dashboard" "${PROGRAMS[0]}"
  generate_linux_unit "$unit_dir/${LINUX_UNITS[1]}" "NiuOne Cron Scheduler" "${PROGRAMS[1]}"
  generate_linux_unit "$unit_dir/${LINUX_UNITS[2]}" "NiuOne X Watchlist Daemon" "${PROGRAMS[2]}"

  systemctl --user daemon-reload
  systemctl --user enable "${LINUX_UNITS[@]}"
  systemctl --user restart "${LINUX_UNITS[@]}"

  if command -v loginctl >/dev/null 2>&1; then
    local linger
    linger="$(loginctl show-user "${USER:-$(id -un)}" -p Linger --value 2>/dev/null || true)"
    if [[ "$linger" != "yes" ]]; then
      if loginctl enable-linger "${USER:-$(id -un)}" >/dev/null 2>&1; then
        echo "Enabled systemd linger so services can run while the user is logged out."
      else
        echo "Warning: systemd linger is disabled." >&2
        echo "Run 'loginctl enable-linger ${USER:-$(id -un)}' with the required authorization for boot-time service." >&2
      fi
    fi
  fi

  echo "NiuOne systemd user services installed and started."
  echo "  status:    ./scripts/manage-long-running.sh status"
  echo "  uninstall: ./scripts/manage-long-running.sh uninstall"
}

status_linux() {
  require_command systemctl
  systemctl --user status "${LINUX_UNITS[@]}" --no-pager
}

restart_linux() {
  require_command systemctl
  systemctl --user restart "${LINUX_UNITS[@]}"
}

uninstall_linux() {
  require_command systemctl
  local unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
  systemctl --user disable --now "${LINUX_UNITS[@]}" >/dev/null 2>&1 || true
  for unit in "${LINUX_UNITS[@]}"; do
    rm -f "$unit_dir/$unit"
  done
  systemctl --user daemon-reload
  systemctl --user reset-failed >/dev/null 2>&1 || true
  echo "NiuOne systemd user services removed. Local data was kept at $LOCAL_DATA_DIR."
}

case "$ACTION" in
  install|status|restart|uninstall)
    ;;
  -h|--help|help)
    usage
    exit 0
    ;;
  *)
    echo "Unknown action: $ACTION" >&2
    usage >&2
    exit 2
    ;;
esac

case "$PLATFORM" in
  Darwin)
    "${ACTION}_macos"
    ;;
  Linux)
    "${ACTION}_linux"
    ;;
  *)
    echo "Unsupported platform for this script: $PLATFORM" >&2
    echo "Use run.bat --service on Windows." >&2
    exit 1
    ;;
esac
