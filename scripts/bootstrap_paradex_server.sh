#!/usr/bin/env bash
set -euo pipefail

# One-shot bootstrap for running the Nunchi/agent-cli Paradex bot on a new server.
#
# What it does:
# - clones or updates the repo
# - checks out a branch/commit if provided
# - creates a local .venv
# - installs the project
# - installs pytest optionally
# - writes a runtime config if provided inline or copies one from a local path
# - optionally installs Hermes skills from a tarball
# - writes a local env file for Paradex credentials
#
# Usage examples:
#
# 1) Minimal:
# REPO_URL=git@github.com:YOURORG/agent-cli.git \
# PARADEX_PRIVATE_KEY=0x... \
# PARADEX_L2_ADDRESS=0x... \
# PARADEX_L1_ADDRESS=0x... \
# bash bootstrap_paradex_server.sh
#
# 2) Pin a branch and copy a runtime config already on the server:
# REPO_URL=git@github.com:YOURORG/agent-cli.git \
# REPO_REF=paradex-live-validation \
# CONFIG_SOURCE=/tmp/paradex_sol_avellaneda_profit_tuned_maker_only.yaml \
# PARADEX_PRIVATE_KEY=0x... \
# PARADEX_L2_ADDRESS=0x... \
# PARADEX_L1_ADDRESS=0x... \
# bash bootstrap_paradex_server.sh
#
# 3) Also install Hermes skills from a tarball created on another machine:
# REPO_URL=git@github.com:YOURORG/agent-cli.git \
# SKILLS_TARBALL=/tmp/hermes-skills-paradex.tar.gz \
# PARADEX_PRIVATE_KEY=0x... \
# PARADEX_L2_ADDRESS=0x... \
# PARADEX_L1_ADDRESS=0x... \
# bash bootstrap_paradex_server.sh

REPO_URL="${REPO_URL:-}"
REPO_REF="${REPO_REF:-}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/agent-cli}"
CONFIG_NAME="${CONFIG_NAME:-paradex_sol_avellaneda_profit_tuned_maker_only.yaml}"
CONFIG_SOURCE="${CONFIG_SOURCE:-}"
CONFIG_INLINE_B64="${CONFIG_INLINE_B64:-}"
SKILLS_TARBALL="${SKILLS_TARBALL:-}"
RUN_SMOKE_TEST="${RUN_SMOKE_TEST:-0}"
INSTALL_PYTEST="${INSTALL_PYTEST:-1}"

PARADEX_PRIVATE_KEY="${PARADEX_PRIVATE_KEY:-}"
PARADEX_ADDRESS="${PARADEX_ADDRESS:-${PARADEX_L2_ADDRESS:-}}"
PARADEX_L2_ADDRESS="${PARADEX_L2_ADDRESS:-${PARADEX_ADDRESS:-}}"
PARADEX_L1_ADDRESS="${PARADEX_L1_ADDRESS:-}"
PARADEX_TESTNET="${PARADEX_TESTNET:-false}"

log() {
  printf '\n[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

write_env_file() {
  local env_file="$INSTALL_DIR/.env.paradex"
  umask 077
  {
    printf 'PARADEX_PRIVATE_KEY=%q\n' "$PARADEX_PRIVATE_KEY"
    printf 'PARADEX_ADDRESS=%q\n' "$PARADEX_ADDRESS"
    printf 'PARADEX_L2_ADDRESS=%q\n' "$PARADEX_L2_ADDRESS"
    printf 'PARADEX_L1_ADDRESS=%q\n' "$PARADEX_L1_ADDRESS"
    printf 'PARADEX_TESTNET=%q\n' "$PARADEX_TESTNET"
  } > "$env_file"
  chmod 600 "$env_file"
  log "Wrote Paradex env file to $env_file"
}

install_skills() {
  [[ -n "$SKILLS_TARBALL" ]] || return 0
  [[ -f "$SKILLS_TARBALL" ]] || die "SKILLS_TARBALL does not exist: $SKILLS_TARBALL"
  SKILLS_TARBALL_PATH="$SKILLS_TARBALL" python3 - <<'PY'
import os
import posixpath
import sys
import tarfile

path = os.environ["SKILLS_TARBALL_PATH"]
with tarfile.open(path, "r:gz") as tf:
    for member in tf.getmembers():
        name = member.name
        norm = posixpath.normpath(name)
        if not name or name.startswith("/"):
            raise SystemExit(f"unsafe tar member path: {name}")
        if norm in {".", ""}:
            continue
        if norm.startswith("../") or "/../" in norm or norm == "..":
            raise SystemExit(f"unsafe parent-path tar member: {name}")
        if member.issym() or member.islnk():
            raise SystemExit(f"tarball links are not allowed: {name}")
PY
  mkdir -p "$HOME/.hermes/skills"
  tar -xzf "$SKILLS_TARBALL" -C "$HOME/.hermes/skills"
  log "Installed Hermes skills from $SKILLS_TARBALL"
}

install_config() {
  local target_dir="$INSTALL_DIR/configs/runtime"
  [[ "$CONFIG_NAME" != */* ]] || die "CONFIG_NAME must be a filename under configs/runtime, not a path"
  [[ "$CONFIG_NAME" != *..* ]] || die "CONFIG_NAME must not contain parent-path segments"
  local target_path="$target_dir/$CONFIG_NAME"
  mkdir -p "$target_dir"

  if [[ -n "$CONFIG_SOURCE" ]]; then
    [[ -f "$CONFIG_SOURCE" ]] || die "CONFIG_SOURCE does not exist: $CONFIG_SOURCE"
    cp "$CONFIG_SOURCE" "$target_path"
    log "Copied runtime config to $target_path"
    return 0
  fi

  if [[ -n "$CONFIG_INLINE_B64" ]]; then
    printf '%s' "$CONFIG_INLINE_B64" | base64 -d > "$target_path"
    log "Wrote inline runtime config to $target_path"
    return 0
  fi

  if [[ -f "$target_path" ]]; then
    log "Runtime config already exists at $target_path"
    return 0
  fi

  die "No runtime config provided. Set CONFIG_SOURCE or CONFIG_INLINE_B64, or ensure $target_path already exists."
}

run_smoke_test() {
  [[ "$RUN_SMOKE_TEST" == "1" ]] || return 0
  local venv_python="$INSTALL_DIR/.venv/bin/python"
  local env_file="$INSTALL_DIR/.env.paradex"
  [[ -f "$INSTALL_DIR/scripts/paradex_smoke_test.py" ]] || {
    log "No paradex smoke test script found; skipping smoke test"
    return 0
  }
  log "Running Paradex smoke test"
  set +u
  source "$env_file"
  set -u
  "$venv_python" "$INSTALL_DIR/scripts/paradex_smoke_test.py"
}

main() {
  need_cmd git
  need_cmd python3
  need_cmd base64
  need_cmd tar

  [[ -n "$REPO_URL" ]] || die "Set REPO_URL to the Nunchi/agent-cli repository URL"
  [[ -n "$PARADEX_PRIVATE_KEY" ]] || die "Set PARADEX_PRIVATE_KEY"
  [[ -n "$PARADEX_L2_ADDRESS" ]] || die "Set PARADEX_L2_ADDRESS or PARADEX_ADDRESS"
  [[ -n "$PARADEX_L1_ADDRESS" ]] || die "Set PARADEX_L1_ADDRESS"

  mkdir -p "$(dirname "$INSTALL_DIR")"

  if [[ -d "$INSTALL_DIR/.git" ]]; then
    log "Updating existing repo at $INSTALL_DIR"
    git -C "$INSTALL_DIR" fetch --all --tags
    if [[ -z "$REPO_REF" ]]; then
      git -C "$INSTALL_DIR" pull --ff-only || true
    fi
  else
    log "Cloning repo into $INSTALL_DIR"
    git clone "$REPO_URL" "$INSTALL_DIR"
  fi

  if [[ -n "$REPO_REF" ]]; then
    log "Checking out $REPO_REF"
    git -C "$INSTALL_DIR" checkout "$REPO_REF"
    git -C "$INSTALL_DIR" pull --ff-only || true
  fi

  log "Creating virtual environment"
  python3 -m venv "$INSTALL_DIR/.venv"

  log "Upgrading pip"
  "$INSTALL_DIR/.venv/bin/python" -m pip install -U pip

  log "Installing project"
  "$INSTALL_DIR/.venv/bin/python" -m pip install -e "$INSTALL_DIR"

  if [[ "$INSTALL_PYTEST" == "1" ]]; then
    log "Installing pytest"
    "$INSTALL_DIR/.venv/bin/python" -m pip install pytest
  fi

  install_config
  install_skills
  write_env_file
  run_smoke_test

  cat <<EOF

Bootstrap complete.

Next steps:
1. cd $INSTALL_DIR
2. source .venv/bin/activate
3. source .env.paradex
4. Run the bot:
   $INSTALL_DIR/.venv/bin/hl run avellaneda_mm --config $INSTALL_DIR/configs/runtime/$CONFIG_NAME --fresh

Optional Hermes skills installed under:
- $HOME/.hermes/skills

Secrets were written to:
- $INSTALL_DIR/.env.paradex

Protect that file carefully.
EOF
}

main "$@"
