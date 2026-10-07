# Shared helpers for hiraku-powers scripts. Sourced, not executed.
set -euo pipefail
# Scripts called by other scripts see this and never archive a workspace.
export HP_INTERNAL=1
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

LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SECTIONS="$LIB_DIR/_sections.awk"
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
workspace_base() {
  local root
  root=$(project_root) || { echo "cannot find the Hiraku project root" >&2; exit 1; }
  printf '%s\n' "$root/.hiraku-powers"
}
workspace_dir() {
  require_record "$1"
  local base
  base=$(workspace_base) || exit 1
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

# One writer at a time per workspace. The lock records its PID so a lock left by
# a killed process is detected and replaced. Child scripts inherit HP_LOCKED.
LOCK_DIR=""
lock() {
  [ -z "${HP_LOCKED:-}" ] || return 0
  local busy="another hiraku-powers operation is running (remove $1/.lock if stale)" pid
  if ! mkdir "$1/.lock" 2>/dev/null; then
    pid=$(cat "$1/.lock/pid" 2>/dev/null || true)
    if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
      rm -rf "$1/.lock"
      mkdir "$1/.lock" 2>/dev/null || { echo "$busy" >&2; exit 6; }
    else
      echo "$busy" >&2; exit 6
    fi
  fi
  printf '%s\n' "$$" > "$1/.lock/pid"
  LOCK_DIR=$1
  trap 'rm -rf "$LOCK_DIR/.lock"' EXIT
  export HP_LOCKED=1
}

# One ledger line per entry: newlines are flattened so text can never start a
# line that looks like a marker.
ledger_append() {
  local text
  # Invalid UTF-8 would make grep treat the whole ledger as binary and drop the
  # line, so it is stripped (iconv) or reduced to ASCII (fallback).
  text=$(printf '%s' "$2" | tr '\r\n' '  ' | utf8_clean)
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$text" >> "$1/ledger.md"
}
utf8_clean() {
  if command -v iconv >/dev/null 2>&1; then
    iconv -c -f UTF-8 -t UTF-8 2>/dev/null
  else
    LC_ALL=C tr -cd '\11\40-\176'
  fi
}
LEDGER_LINE='^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]{8}Z '

current_stage() { bash "$LIB_DIR/state" "$1" | sed -n 's/^stage: //p'; }

# Exit 7 unless the record is in one of the given stages ($1 = record ID). The
# message carries state's note (e.g. "plan changed after approval") so the cause
# is visible to the agent that was refused.
require_stage() {
  local id=$1 out got note s
  shift
  out=$(bash "$LIB_DIR/state" "$id")
  got=$(printf '%s\n' "$out" | sed -n 's/^stage: //p')
  for s in "$@"; do [ "$got" = "$s" ] && return 0; done
  note=$(printf '%s\n' "$out" | sed -n 's/^note: //p')
  echo "record $id is in stage '$got'; this step needs: $*${note:+ ($note)}" >&2
  exit 7
}

# Hash of a plan that ignores ticks and the ### Deviations log, so approval pins
# the plan content but not the progress recorded while implementing it.
plan_sha() { awk -v mode=planbody -f "$SECTIONS" | trim | sha_of; }

# Hash of the proposal's Hypothesis and Success criterion lines.
proposal_sha() {
  { grep -E '^- (Hypothesis|Success criterion):' || true; } \
    | sed 's/[[:space:]]*$//' | sha_of
}

# Value of a "- Label: value" line in a part read from stdin (first match).
plan_field() { sed -n "s/^- $1:[[:space:]]*\(.*[^[:space:]]\)[[:space:]]*\$/\1/p" | head -n1; }
