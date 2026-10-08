# Shared helpers for the hiraku-powers scripts. Sourced, not executed.
set -euo pipefail

command -v jq >/dev/null 2>&1 || { echo "missing dependency: jq" >&2; exit 2; }

# `hiraku` from PATH, else the project environment's copy via uv.
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
LEDGER_LINE='^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]{8}Z '

require_record() {
  case "$1" in */*|"") echo "invalid record ID: '$1'" >&2; exit 2 ;; esac
  hk record show --raw --json -- "$1" >/dev/null \
    || { echo "record '$1' not found" >&2; exit 2; }
}

# Workspace path for a record; created on demand. It is git-ignored through a
# self-ignoring .gitignore in .hiraku-powers/.
workspace_path() {
  local root
  root=$(hk status --json | jq -er '.project_root') \
    || { echo "cannot find the Hiraku project root" >&2; exit 1; }
  printf '%s\n' "$root/.hiraku-powers/records/$1"
}
workspace_dir() {
  local dir
  require_record "$1"
  dir=$(workspace_path "$1")
  mkdir -p "$dir"
  printf '*\n' > "$dir/../../.gitignore"
  [ -e "$dir/ledger.md" ] || printf '# hiraku-powers ledger: %s\n' "$1" > "$dir/ledger.md"
  printf '%s\n' "$dir"
}

# Stored (not inherited) record values.
raw_json() { hk record show --raw --json -- "$1"; }
body_of() { raw_json "$1" | jq -r '.markdown'; }
field_of() { raw_json "$1" | jq -r --arg k "$2" '.values[$k] // empty'; }

sha_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum; else shasum -a 256; fi \
    | cut -d' ' -f1
}

# Drop trailing blank lines.
trim() { awk '{ l[NR] = $0 } NF { last = NR } END { for (i = 1; i <= last; i++) print l[i] }'; }

plan_of() { awk -v mode=plan -f "$SECTIONS"; }
# Hash of a plan without ticks and the ### Deviations log, so an approval pins
# the plan but not the progress recorded while implementing it.
plan_sha() { awk -v mode=planhash -f "$SECTIONS" | trim | sha_of; }
# Hash of the stored hypothesis and success criterion, from raw record JSON.
proposal_sha() { jq -r '[.values.hypothesis, .values.success_criterion] | @json' | sha_of; }

# "- Label: value" from a plan read on stdin.
plan_field() { sed -n "s/^- $1:[[:space:]]*\(.*[^[:space:]]\)[[:space:]]*\$/\1/p"; }

# One timestamped line per entry; newlines are flattened so a note can never
# start a line that reads as a marker.
ledger_append() {
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(printf '%s' "$2" | tr '\r\n' '  ')" \
    >> "$1/ledger.md"
}

# Exit 7 unless the record is in one of the given stages; the message carries
# state's note so the refused agent sees why.
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

set_status() { hk record update --set "status=$2" -- "$1" >/dev/null; }
