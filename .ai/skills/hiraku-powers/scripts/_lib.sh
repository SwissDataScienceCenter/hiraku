# Shared helpers for hiraku-powers scripts. Sourced, not executed.
set -euo pipefail
# Make `x=$(failing_function)` honour errexit inside the substitution (bash >= 4.4).
shopt -s inherit_errexit 2>/dev/null || true

command -v jq >/dev/null 2>&1 || { echo "missing dependency: jq" >&2; exit 2; }

# Use `hiraku` from PATH, else the project environment's copy via uv.
hk() {
  if command -v hiraku >/dev/null 2>&1; then
    hiraku "$@"
  elif command -v uv >/dev/null 2>&1; then
    uv run --frozen hiraku "$@"
  else
    echo "missing dependency: hiraku (not on PATH, and uv not found)" >&2
    return 2
  fi
}

SECTIONS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_sections.awk"
PARTS="head summary plan rest"

# The Hiraku project root, from Hiraku itself (works without Git).
project_root() { hk status --json | jq -er '.project_root'; }

# Fail before touching the filesystem unless the record exists. Options come
# before `--` so IDs that start with "-" are not read as flags.
require_record() {
  case "$1" in */*|"") echo "invalid record ID: '$1'" >&2; exit 2 ;; esac
  hk record show --raw --json -- "$1" >/dev/null \
    || { echo "record '$1' not found" >&2; exit 2; }
}

# Scratch workspace for one record, git-ignored by a self-ignoring .gitignore.
# Workspaces live under records/ so no ID can collide with the .gitignore.
workspace_dir() {
  require_record "$1"
  local root base
  root=$(project_root) || { echo "cannot find the Hiraku project root" >&2; exit 1; }
  base="$root/.hiraku-powers"
  mkdir -p "$base/records/$1" || exit 1
  printf '*\n' > "$base/.gitignore"
  printf '%s\n' "$base/records/$1"
}

body_of() { hk record show --raw --json -- "$1" | jq -r '.markdown'; }

sha_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum; else shasum -a 256; fi \
    | cut -d' ' -f1
}

# Drop trailing blank lines so pull/push round trips do not accumulate them.
trim() { awk '{ lines[NR] = $0 } NF { last = NR } END { for (i = 1; i <= last; i++) print lines[i] }'; }

part_name() { case "$1" in head) echo head ;; summary) echo Summary ;; plan) echo Plan ;; rest) echo rest ;; esac; }

# Split a body read from stdin into the four part files in directory $1.
split_text() {
  local part body
  body=$(cat)
  for part in $PARTS; do
    printf '%s\n' "$body" \
      | awk -v mode=split -v want="$(part_name "$part")" -f "$SECTIONS" \
      | trim > "$1/$part.md"
  done
}
split_body() { body_of "$1" | split_text "$2"; }

snapshot() { # copy the four parts to .synced/ as the last-synced baseline
  mkdir -p "$1/.synced"
  local part
  for part in $PARTS; do cp "$1/$part.md" "$1/.synced/$part.md"; done
}

unsynced_edits() { # succeeds if workspace parts differ from the baseline
  local part
  for part in $PARTS; do
    cmp -s "$1/$part.md" "$1/.synced/$part.md" 2>/dev/null || return 0
  done
  return 1
}

# One writer at a time per workspace. Child scripts inherit HP_LOCKED.
LOCK_DIR=""
lock() {
  [ -z "${HP_LOCKED:-}" ] || return 0
  mkdir "$1/.lock" 2>/dev/null \
    || { echo "another hiraku-powers operation is running (remove $1/.lock if stale)" >&2; exit 6; }
  LOCK_DIR=$1
  trap 'rmdir "$LOCK_DIR/.lock" 2>/dev/null || true' EXIT
  export HP_LOCKED=1
}

ledger_append() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$2" >> "$1/ledger.md"; }

# Hash of a plan that ignores ticks and the ### Deviations log, so approval pins
# the plan content but not the progress recorded while implementing it.
plan_sha() {
  awk '/^### Deviations/ { exit } { print }' \
    | sed -E 's/^([[:space:]]*)- \[[xX]\]/\1- [ ]/' | trim | sha_of
}
