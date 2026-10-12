#!/bin/sh
set -eu

while [ "$#" -gt 0 ]; do
  case "$1" in
    --commit)
      option=$1
      [ "$#" -ge 2 ] || { echo "launch.sh: $option requires a value" >&2; exit 2; }
      shift 2
      ;;
    --)
      shift
      break
      ;;
    *)
      echo "launch.sh: unknown option: $1" >&2
      exit 2
      ;;
  esac
done

[ "$#" -gt 0 ] || { echo "launch.sh: missing command after --" >&2; exit 2; }

export HIRAKU_COMMIT=
export HIRAKU_GIT_STATE=unavailable

# `git status` of the code: Hiraku's own state (HIRAKU_STATE_PATHS, one path per line,
# relative to the project root: the configuration, the state directory, runs.csv) is
# left out, so a new record or run row does not make the launch "dirty". Those files may
# be committed or not; either way they are not what the job runs.
tree_status() {
  set -f
  set -- --porcelain --untracked-files=normal -- .
  old_ifs=$IFS
  IFS='
'
  for state_path in ${HIRAKU_STATE_PATHS-}; do
    set -- "$@" ":(exclude,literal)$state_path"
  done
  IFS=$old_ifs
  git status "$@" 2>/dev/null
}

if command -v git >/dev/null 2>&1 &&
  head=$(git rev-parse --verify 'HEAD^{commit}' 2>/dev/null) &&
  status=$(tree_status); then
  export HIRAKU_COMMIT=$head
  if [ -n "$status" ]; then
    export HIRAKU_GIT_STATE=dirty
  else
    export HIRAKU_GIT_STATE=clean
  fi
fi

exec "$@"
