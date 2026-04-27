#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="$ROOT_DIR/.runtime"
GNUPGHOME_DIR="$RUNTIME_DIR/gnupg"
SECRETS_DIR="$RUNTIME_DIR/secrets"
RECIPIENT="nunchi-sally-paradex-secrets@local"
SECRET_FILE="$SECRETS_DIR/paradex.env.gpg"
mkdir -p "$RUNTIME_DIR" "$SECRETS_DIR" "$GNUPGHOME_DIR"
chmod 700 "$RUNTIME_DIR" "$SECRETS_DIR" "$GNUPGHOME_DIR"

export GNUPGHOME="$GNUPGHOME_DIR"

usage() {
  cat <<'EOF'
Usage:
  bash scripts/paradex_secret_store.sh init
  bash scripts/paradex_secret_store.sh save-from-env
  bash scripts/paradex_secret_store.sh save-from-pid PID
  bash scripts/paradex_secret_store.sh export-env
  bash scripts/paradex_secret_store.sh status
  bash scripts/paradex_secret_store.sh restart-sally-week

Notes:
- Stores Paradex runtime credentials encrypted with a local GPG key under .runtime/.
- Does not print secret values.
- export-env prints shell export commands; use: eval "$(bash scripts/paradex_secret_store.sh export-env)"
EOF
}

ensure_key() {
  if gpg --batch --list-secret-keys "$RECIPIENT" >/dev/null 2>&1; then
    return 0
  fi
  cat > "$RUNTIME_DIR/gpg-batch.conf" <<EOF
Key-Type: EDDSA
Key-Curve: ed25519
Key-Usage: sign
Subkey-Type: ECDH
Subkey-Curve: cv25519
Subkey-Usage: encrypt
Name-Real: Nunchi Sally Paradex Secrets
Name-Email: $RECIPIENT
Expire-Date: 0
%no-protection
%commit
EOF
  chmod 600 "$RUNTIME_DIR/gpg-batch.conf"
  gpg --batch --generate-key "$RUNTIME_DIR/gpg-batch.conf" >/dev/null
  rm -f "$RUNTIME_DIR/gpg-batch.conf"
}

require_secret_env() {
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
  (( missing == 0 )) || exit 1
}

save_from_env() {
  ensure_key
  require_secret_env
  local tmp
  tmp="$(mktemp "$RUNTIME_DIR/paradex.env.XXXXXX")"
  chmod 600 "$tmp"
  {
    printf 'PARADEX_L1_ADDRESS=%q\n' "$PARADEX_L1_ADDRESS"
    printf 'PARADEX_ADDRESS=%q\n' "${PARADEX_ADDRESS:-${PARADEX_L2_ADDRESS:-}}"
    printf 'PARADEX_L2_ADDRESS=%q\n' "${PARADEX_L2_ADDRESS:-${PARADEX_ADDRESS:-}}"
    printf 'PARADEX_PRIVATE_KEY=%q\n' "$PARADEX_PRIVATE_KEY"
  } > "$tmp"
  gpg --batch --yes --trust-model always --encrypt --recipient "$RECIPIENT" --output "$SECRET_FILE" "$tmp" >/dev/null
  shred -u "$tmp" 2>/dev/null || rm -f "$tmp"
  chmod 600 "$SECRET_FILE"
  echo "saved encrypted Paradex secrets to $SECRET_FILE"
}

save_from_pid() {
  local pid="${1:-}"
  [[ -n "$pid" && -r "/proc/$pid/environ" ]] || { echo "ERROR: cannot read /proc/$pid/environ" >&2; exit 1; }
  ensure_key
  python3 - <<'PY' "$pid" > "$RUNTIME_DIR/pid_env_exports.tmp"
import os, shlex, sys
pid = sys.argv[1]
raw = open(f"/proc/{pid}/environ", "rb").read().split(b"\0")
env = {}
for item in raw:
    if b"=" in item:
        k, v = item.split(b"=", 1)
        env[k.decode("utf-8", "replace")] = v.decode("utf-8", "replace")
required = ["PARADEX_PRIVATE_KEY", "PARADEX_L1_ADDRESS"]
missing = [k for k in required if not env.get(k)]
if not (env.get("PARADEX_ADDRESS") or env.get("PARADEX_L2_ADDRESS")):
    missing.append("PARADEX_ADDRESS or PARADEX_L2_ADDRESS")
if missing:
    raise SystemExit("missing in pid env: " + ", ".join(missing))
for key in ["PARADEX_L1_ADDRESS", "PARADEX_ADDRESS", "PARADEX_L2_ADDRESS", "PARADEX_PRIVATE_KEY"]:
    val = env.get(key)
    if key == "PARADEX_ADDRESS" and not val:
        val = env.get("PARADEX_L2_ADDRESS", "")
    if key == "PARADEX_L2_ADDRESS" and not val:
        val = env.get("PARADEX_ADDRESS", "")
    print(f"{key}={shlex.quote(val)}")
PY
  chmod 600 "$RUNTIME_DIR/pid_env_exports.tmp"
  gpg --batch --yes --trust-model always --encrypt --recipient "$RECIPIENT" --output "$SECRET_FILE" "$RUNTIME_DIR/pid_env_exports.tmp" >/dev/null
  shred -u "$RUNTIME_DIR/pid_env_exports.tmp" 2>/dev/null || rm -f "$RUNTIME_DIR/pid_env_exports.tmp"
  chmod 600 "$SECRET_FILE"
  echo "saved encrypted Paradex secrets from pid $pid to $SECRET_FILE"
}

export_env() {
  [[ -f "$SECRET_FILE" ]] || { echo "ERROR: encrypted secret file not found: $SECRET_FILE" >&2; exit 1; }
  gpg --batch --quiet --decrypt "$SECRET_FILE" | while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    case "$line" in
      PARADEX_L1_ADDRESS=*|PARADEX_ADDRESS=*|PARADEX_L2_ADDRESS=*|PARADEX_PRIVATE_KEY=*)
        printf 'export %s\n' "$line"
        ;;
    esac
  done
}

status() {
  ensure_key
  if [[ -f "$SECRET_FILE" ]]; then
    echo "secret_file=$SECRET_FILE"
    echo "secret_file_present=true"
    echo "secret_file_permissions=$(stat -c %a "$SECRET_FILE" 2>/dev/null || true)"
  else
    echo "secret_file=$SECRET_FILE"
    echo "secret_file_present=false"
  fi
  echo "gnupg_home=$GNUPGHOME_DIR"
  echo "recipient=$RECIPIENT"
}

restart_sally_week() {
  eval "$(export_env)"
  cd "$ROOT_DIR"
  bash scripts/sally_ctl.sh restart --duration 604800 --nohup
}

ACTION="${1:-}"
case "$ACTION" in
  init)
    ensure_key
    status
    ;;
  save-from-env)
    save_from_env
    ;;
  save-from-pid)
    shift || true
    save_from_pid "${1:-}"
    ;;
  export-env)
    export_env
    ;;
  status)
    status
    ;;
  restart-sally-week)
    restart_sally_week
    ;;
  -h|--help|help|"")
    usage
    ;;
  *)
    echo "ERROR: unknown action: $ACTION" >&2
    usage >&2
    exit 1
    ;;
esac
