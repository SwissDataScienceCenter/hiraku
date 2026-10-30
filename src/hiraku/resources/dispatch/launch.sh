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

if [ -z "${HIRAKU_SOURCE_DIGEST:-}" ]; then
  export HIRAKU_COMMIT=
  export HIRAKU_GIT_STATE=unavailable
fi

if [ -z "${HIRAKU_SOURCE_DIGEST:-}" ] && command -v git >/dev/null 2>&1 &&
  head=$(git rev-parse --verify 'HEAD^{commit}' 2>/dev/null) &&
  status=$(git status --porcelain --untracked-files=normal 2>/dev/null); then
  export HIRAKU_COMMIT=$head
  if [ -n "$status" ]; then
    export HIRAKU_GIT_STATE=dirty
  else
    export HIRAKU_GIT_STATE=clean
  fi
fi

exec "$@"
