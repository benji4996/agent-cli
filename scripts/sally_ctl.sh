#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

BOT_NAME="Sally"
DEFAULT_CONFIG_REL="configs/runtime/paradex_sol_avellaneda_profit_tuned_maker_only.yaml"
DEFAULT_DURATION_S=10800
DEFAULT_KILL_AFTER_S=120
LOCK_DIR="$ROOT_DIR/.runtime"
LOCK_FILE="$LOCK_DIR/sally.lock"
STATE_FILE="$LOCK_DIR/sally.state"
LOG_DIR="$ROOT_DIR/logs"
mkdir -p "$LOCK_DIR" "$LOG_DIR"

CONFIG_REL="$DEFAULT_CONFIG_REL"
DURATION_S="$DEFAULT_DURATION_S"
KILL_AFTER_S="$DEFAULT_KILL_AFTER_S"
REPLACE_EXISTING=0
JSON=0
NOHUP_MODE=0

usage() {
  cat <<'EOF'
Usage:
  bash scripts/sally_ctl.sh start [--duration SEC] [--config PATH] [--replace] [--nohup]
  bash scripts/sally_ctl.sh stop [--config PATH]
  bash scripts/sally_ctl.sh restart [--duration SEC] [--config PATH] [--nohup]
  bash scripts/sally_ctl.sh status [--config PATH] [--json]

Notes:
- Reads Paradex credentials from the current environment only; it never persists keys.
- start refuses to launch if a matching Sally instance is already running.
- restart/--replace clean up any matching existing Sally processes first.
- --nohup backgrounds the launched timeout wrapper inside this script and records its PID.
EOF
}

quote_json() {
  python3 - <<'PY' "$1"
import json, sys
print(json.dumps(sys.argv[1]))
PY
}

require_env() {
  local missing=0
  for name in PARADEX_PRIVATE_KEY PARADEX_L1_ADDRESS; do
    if [[ -z "${!name:-}" ]]; then
      echo "ERROR: missing required env var $name" >&2
      missing=1
    fi
  done
  if [[ -z "${PARADEX_ADDRESS:-${PARADEX_L2_ADDRESS:-}}" ]]; then
    echo "ERROR: missing PARADEX_ADDRESS or PARADEX_L2_ADDRESS" >&2
    missing=1
  fi
  if [[ ! -x "$ROOT_DIR/.venv/bin/hl" ]]; then
    echo "ERROR: expected executable not found: $ROOT_DIR/.venv/bin/hl" >&2
    missing=1
  fi
  if [[ ! -f "$ROOT_DIR/$CONFIG_REL" && ! -f "$CONFIG_REL" ]]; then
    echo "ERROR: config not found: $CONFIG_REL" >&2
    missing=1
  fi
  if (( missing )); then
    exit 1
  fi
}

resolve_config_abs() {
  python3 - <<'PY' "$ROOT_DIR" "$CONFIG_REL"
import os, sys
root, config = sys.argv[1:3]
path = config if os.path.isabs(config) else os.path.join(root, config)
print(os.path.realpath(path))
PY
}

normalize_config_rel() {
  python3 - <<'PY' "$ROOT_DIR" "$CONFIG_REL"
import os, sys
root, config = sys.argv[1:3]
path = config if os.path.isabs(config) else os.path.join(root, config)
print(os.path.relpath(os.path.realpath(path), os.path.realpath(root)))
PY
}

CONFIG_ABS=""
CONFIG_REL_NORM=""

refresh_config_paths() {
  CONFIG_ABS="$(resolve_config_abs)"
  CONFIG_REL_NORM="$(normalize_config_rel)"
}

list_python_pids() {
  refresh_config_paths
  python3 - <<'PY' "$ROOT_DIR" "$CONFIG_ABS" "$CONFIG_REL_NORM"
import os, subprocess, sys
root, config_abs, config_rel = sys.argv[1:4]
ps = subprocess.run(["ps", "-eo", "pid=,ppid=,args="], capture_output=True, text=True, check=True)
for raw in ps.stdout.splitlines():
    line = raw.strip()
    if not line:
        continue
    try:
        pid_s, ppid_s, args = line.split(None, 2)
    except ValueError:
        continue
    if "hl run avellaneda_mm" not in args:
        continue
    if ".venv/bin/python" not in args and "python .venv/bin/hl" not in args and "/bin/python" not in args:
        continue
    if config_abs not in args and config_rel not in args:
        continue
    cwd = ""
    try:
        cwd = os.readlink(f"/proc/{pid_s}/cwd")
    except OSError:
        pass
    if os.path.realpath(cwd) != os.path.realpath(root):
        continue
    print(f"{pid_s}\t{ppid_s}\t{args}")
PY
}

find_log_for_pid() {
  local pid="$1"
  python3 - <<'PY' "$pid"
import os, sys
pid = sys.argv[1]
for fd in (1, 2):
    try:
        target = os.readlink(f"/proc/{pid}/fd/{fd}")
    except OSError:
        continue
    if "/logs/sally_" in target and target.endswith(".log"):
        print(target)
        raise SystemExit(0)
print("")
PY
}

record_state() {
  local wrapper_pid="$1"
  local log_file="$2"
  cat > "$STATE_FILE" <<EOF
wrapper_pid=$wrapper_pid
log_file=$log_file
config_rel=$CONFIG_REL_NORM
config_abs=$CONFIG_ABS
started_at=$(date -Iseconds)
EOF
}

remove_state_if_stale() {
  if [[ -f "$STATE_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$STATE_FILE" || true
    if [[ -n "${wrapper_pid:-}" ]] && kill -0 "$wrapper_pid" 2>/dev/null; then
      return 0
    fi
    rm -f "$STATE_FILE"
  fi
  return 1
}

stop_matching_processes() {
  mapfile -t matches < <(list_python_pids)
  if (( ${#matches[@]} == 0 )); then
    return 0
  fi

  local pid
  for row in "${matches[@]}"; do
    pid="${row%%$'\t'*}"
    kill -TERM "$pid" 2>/dev/null || true
  done

  local waited=0
  while (( waited < KILL_AFTER_S )); do
    sleep 1
    waited=$((waited + 1))
    mapfile -t matches < <(list_python_pids)
    if (( ${#matches[@]} == 0 )); then
      break
    fi
  done

  if (( ${#matches[@]} > 0 )); then
    for row in "${matches[@]}"; do
      pid="${row%%$'\t'*}"
      kill -KILL "$pid" 2>/dev/null || true
    done
  fi

  rm -f "$STATE_FILE"
}

cmd_status() {
  refresh_config_paths
  mapfile -t matches < <(list_python_pids)
  local count="${#matches[@]}"
  local latest_log=""
  local latest_log_mtime=0
  local line pid ppid args log current_mtime

  for line in "${matches[@]}"; do
    pid="${line%%$'\t'*}"
    local rest="${line#*$'\t'}"
    ppid="${rest%%$'\t'*}"
    args="${rest#*$'\t'}"
    log="$(find_log_for_pid "$pid")"
    if [[ -n "$log" && -f "$log" ]]; then
      current_mtime=$(stat -c %Y "$log" 2>/dev/null || echo 0)
      if (( current_mtime >= latest_log_mtime )); then
        latest_log_mtime=$current_mtime
        latest_log="$log"
      fi
    fi
    if (( ! JSON )); then
      echo "$pid|$ppid|$log|$args"
    fi
  done

  if (( JSON )); then
    python3 - <<'PY' "$count" "$latest_log" "$CONFIG_REL_NORM" "$CONFIG_ABS" "$ROOT_DIR"
import json, os, subprocess, sys
count, latest_log, config_rel, config_abs, root = sys.argv[1:6]
count_i = int(count)
ps = subprocess.run(["ps", "-eo", "pid=,ppid=,args="], capture_output=True, text=True, check=True)
instances = []
for raw in ps.stdout.splitlines():
    line = raw.strip()
    if not line:
        continue
    try:
        pid_s, ppid_s, args = line.split(None, 2)
    except ValueError:
        continue
    if "hl run avellaneda_mm" not in args:
        continue
    if config_abs not in args and config_rel not in args:
        continue
    try:
        cwd = os.readlink(f"/proc/{pid_s}/cwd")
    except OSError:
        continue
    if os.path.realpath(cwd) != os.path.realpath(root):
        continue
    if ".venv/bin/python" not in args and "python .venv/bin/hl" not in args and "/bin/python" not in args:
        continue
    log = ""
    for fd in (1, 2):
        try:
            target = os.readlink(f"/proc/{pid_s}/fd/{fd}")
        except OSError:
            continue
        if "/logs/sally_" in target and target.endswith(".log"):
            log = target
            break
    instances.append({"pid": int(pid_s), "ppid": int(ppid_s), "log": log, "args": args})
print(json.dumps({
    "bot": "Sally",
    "running": count_i > 0,
    "instance_count": count_i,
    "config_rel": config_rel,
    "config_abs": config_abs,
    "latest_log": latest_log,
    "instances": instances,
}, indent=2))
PY
  fi
}

cmd_start() {
  require_env
  refresh_config_paths
  exec 9>"$LOCK_FILE"
  flock -n 9 || { echo "ERROR: another Sally control action is already in progress" >&2; exit 1; }

  mapfile -t matches < <(list_python_pids)
  if (( ${#matches[@]} > 0 )); then
    if (( ! REPLACE_EXISTING )); then
      echo "ERROR: refusing to start duplicate Sally; matching instance(s) already running:" >&2
      printf '%s
' "${matches[@]}" >&2
      exit 2
    fi
    stop_matching_processes
  fi

  local ts log_file wrapper_pid
  ts="$(date +%Y%m%d_%H%M%S)"
  log_file="$LOG_DIR/sally_${ts}.log"

  local launch_cmd=(timeout --signal=TERM --kill-after="${KILL_AFTER_S}s" "${DURATION_S}s" "$ROOT_DIR/.venv/bin/hl" run avellaneda_mm --config "$CONFIG_REL_NORM" --fresh)

  if (( NOHUP_MODE )); then
    nohup env \
      PARADEX_PRIVATE_KEY="${PARADEX_PRIVATE_KEY}" \
      PARADEX_ADDRESS="${PARADEX_ADDRESS:-${PARADEX_L2_ADDRESS:-}}" \
      PARADEX_L2_ADDRESS="${PARADEX_L2_ADDRESS:-${PARADEX_ADDRESS:-}}" \
      PARADEX_L1_ADDRESS="${PARADEX_L1_ADDRESS}" \
      "${launch_cmd[@]}" >> "$log_file" 2>&1 &
    wrapper_pid=$!
    record_state "$wrapper_pid" "$log_file"
    exec 9>&-
    echo "started $BOT_NAME"
    echo "wrapper_pid=$wrapper_pid"
    echo "log=$log_file"
    return 0
  fi

  echo "log=$log_file"
  exec 9>&-
  exec env \
    PARADEX_PRIVATE_KEY="${PARADEX_PRIVATE_KEY}" \
    PARADEX_ADDRESS="${PARADEX_ADDRESS:-${PARADEX_L2_ADDRESS:-}}" \
    PARADEX_L2_ADDRESS="${PARADEX_L2_ADDRESS:-${PARADEX_ADDRESS:-}}" \
    PARADEX_L1_ADDRESS="${PARADEX_L1_ADDRESS}" \
    "${launch_cmd[@]}" >> "$log_file" 2>&1
}

cmd_stop() {
  exec 9>"$LOCK_FILE"
  flock -n 9 || { echo "ERROR: another Sally control action is already in progress" >&2; exit 1; }
  stop_matching_processes
  echo "stopped matching $BOT_NAME instances"
}

ACTION="${1:-}"
if [[ -z "$ACTION" ]]; then
  usage
  exit 1
fi
shift || true

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)
      CONFIG_REL="$2"
      shift 2
      ;;
    --duration)
      DURATION_S="$2"
      shift 2
      ;;
    --kill-after)
      KILL_AFTER_S="$2"
      shift 2
      ;;
    --replace)
      REPLACE_EXISTING=1
      shift
      ;;
    --json)
      JSON=1
      shift
      ;;
    --nohup)
      NOHUP_MODE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

case "$ACTION" in
  start)
    cmd_start
    ;;
  restart)
    REPLACE_EXISTING=1
    cmd_start
    ;;
  stop)
    cmd_stop
    ;;
  status)
    cmd_status
    ;;
  *)
    echo "ERROR: unknown action: $ACTION" >&2
    usage >&2
    exit 1
    ;;
esac
