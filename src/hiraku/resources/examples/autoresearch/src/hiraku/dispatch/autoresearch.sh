#!/usr/bin/env bash
set -uo pipefail

if [[ $(uname -s) != Linux ]]; then
  echo "autoresearch.sh: this dispatcher requires Linux" >&2
  exit 1
fi
for dependency in timeout flock; do
  if ! command -v "$dependency" >/dev/null 2>&1; then
    echo "autoresearch.sh: required Linux utility is unavailable: $dependency" >&2
    exit 1
  fi
done
if ! timeout --signal=TERM --kill-after=1s 1s true >/dev/null 2>&1; then
  echo "autoresearch.sh: required Linux utility is unavailable: compatible timeout command" >&2
  exit 1
fi
if [[ ! -r /proc/sys/kernel/random/uuid ]]; then
  echo "autoresearch.sh: required Linux UUID source is unavailable: /proc/sys/kernel/random/uuid" >&2
  exit 1
fi

usage() {
  echo "usage: autoresearch.sh [--commit REF] -- COMMAND..." >&2
  exit 2
}

requested_commit=
while (($# > 0)); do
  case "$1" in
    --commit)
      (($# >= 2)) || usage
      requested_commit=$2
      shift 2
      ;;
    --)
      shift
      break
      ;;
    *)
      usage
      ;;
  esac
done
(($# > 0)) || usage

for name in HIRAKU_TRACKING_ROOT HIRAKU_RECORD_ID; do
  if [[ -z ${!name:-} ]]; then
    echo "autoresearch.sh: missing required environment variable: $name" >&2
    exit 2
  fi
done

timeout_seconds=${HIRAKU_AUTORESEARCH_TIMEOUT_SECONDS:-600}
if [[ ! $timeout_seconds =~ ^[0-9]+([.][0-9]+)?$ ]] ||
  ! awk -v value="$timeout_seconds" 'BEGIN { exit !(value > 0) }'; then
  echo "autoresearch.sh: timeout must be a positive number" >&2
  exit 2
fi

commit=
git_state=unavailable
if command -v git >/dev/null 2>&1 &&
  commit=$(git rev-parse --verify 'HEAD^{commit}' 2>/dev/null) &&
  worktree_status=$(git status --porcelain --untracked-files=normal 2>/dev/null); then
  if [[ -n $worktree_status ]]; then
    git_state=dirty
  else
    git_state=clean
  fi
fi
if [[ -n $requested_commit && -n $commit && $requested_commit != "$commit" ]]; then
  echo "autoresearch.sh: requested commit $requested_commit; running actual commit $commit" >&2
fi

capture_file=$(mktemp "${TMPDIR:-/tmp}/hiraku-autoresearch.XXXXXX") || exit 1
cleanup() {
  rm -f -- "$capture_file"
}
trap cleanup EXIT

started=$(date -u +'%Y-%m-%dT%H:%M:%SZ')
started_seconds=$(date +%s.%N)
timeout --signal=TERM --kill-after=10s "$timeout_seconds" "$@" 2>&1 |
  tee "$capture_file"
pipeline_status=("${PIPESTATUS[@]}")
duration=$(LC_ALL=C awk -v start="$started_seconds" -v end="$(date +%s.%N)" \
  'BEGIN { printf "%.3f", end - start }')
command_status=${pipeline_status[0]}
tee_status=${pipeline_status[1]}

metric() {
  local key=$1
  local value
  value=$(awk -F: -v key="$key" '
    $1 == key {
      value = $2
      sub(/^[[:space:]]+/, "", value)
      sub(/[[:space:]]+$/, "", value)
      result = value
    }
    END { print result }
  ' "$capture_file")
  if [[ $value =~ ^[0-9]+([.][0-9]+)?$ ]]; then
    printf '%s' "$value"
  fi
}

val_bpb=$(metric val_bpb)
training_seconds=$(metric training_seconds)
peak_vram_mb=$(metric peak_vram_mb)
mfu_percent=$(metric mfu_percent)

final_status=$command_status
if ((tee_status != 0)); then
  status=crashed
  final_status=$tee_status
elif ((command_status == 124)); then
  status=timeout
elif ((command_status == 0)) && [[ -n $val_bpb ]]; then
  status=finished
else
  status=crashed
  if ((final_status == 0)); then
    final_status=1
  fi
fi

read -r run_uuid </proc/sys/kernel/random/uuid
run_id=${run_uuid//-/}
csv_path=$HIRAKU_TRACKING_ROOT/runs.csv
header='id,record_id,commit,git_state,timestamp,duration,status,metric.val_bpb,metric.training_seconds,metric.peak_vram_mb,metric.mfu_percent'

exec 9>>"$csv_path" || exit 1
flock 9 || exit 1
if [[ ! -s $csv_path ]]; then
  printf '%s\n' "$header" >&9
else
  IFS= read -r existing_header <"$csv_path"
  existing_header=${existing_header%$'\r'}
  if [[ $existing_header != "$header" ]]; then
    echo "autoresearch.sh: incompatible CSV header in $csv_path" >&2
    flock -u 9
    exec 9>&-
    exit 1
  fi
fi
printf '%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n' \
  "$run_id" "$HIRAKU_RECORD_ID" "$commit" "$git_state" \
  "$started" "$duration" "$status" "$val_bpb" \
  "$training_seconds" "$peak_vram_mb" "$mfu_percent" >&9
flock -u 9
exec 9>&-

printf 'tracking_run_id: %s\n' "$run_id"
exit "$final_status"
