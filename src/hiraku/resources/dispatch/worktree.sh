#!/bin/sh
set -eu

commit_ref=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --commit)
      option=$1
      [ "$#" -ge 2 ] || { echo "worktree.sh: $option requires a value" >&2; exit 2; }
      commit_ref=$2
      shift 2
      ;;
    --)
      shift
      break
      ;;
    *)
      echo "worktree.sh: unknown option: $1" >&2
      exit 2
      ;;
  esac
done

[ -n "$commit_ref" ] || { echo "worktree.sh: --commit is required" >&2; exit 2; }
[ "$#" -gt 0 ] || { echo "worktree.sh: missing command after --" >&2; exit 2; }
command -v git >/dev/null 2>&1 ||
  { echo "worktree.sh: Git executable was not found" >&2; exit 1; }

project_root=$(pwd -P)
repository_root=$(git rev-parse --show-toplevel 2>/dev/null) ||
  { echo "worktree.sh: current directory is not inside a Git repository" >&2; exit 1; }
commit=$(git rev-parse --verify "$commit_ref^{commit}" 2>/dev/null) ||
  { echo "worktree.sh: cannot resolve commit $commit_ref" >&2; exit 1; }
case "$project_root/" in
  "$repository_root/"*) project_relative=${project_root#"$repository_root"} ;;
  *) echo "worktree.sh: project is outside the Git repository" >&2; exit 1 ;;
esac
project_relative=${project_relative#/}

temporary_parent=$(mktemp -d "${TMPDIR:-/tmp}/hiraku-worktree.XXXXXX")
worktree=$temporary_parent/checkout
added=false
cleanup() {
  status=$?
  trap - 0 HUP INT TERM
  if [ "$added" = true ]; then
    git -C "$repository_root" worktree remove --force "$worktree" >/dev/null 2>&1 || true
    git -C "$repository_root" worktree prune >/dev/null 2>&1 || true
  fi
  rm -rf -- "$temporary_parent"
  exit "$status"
}
trap cleanup 0
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

git -C "$repository_root" worktree add --detach "$worktree" "$commit" >/dev/null
added=true
echo "worktree.sh: created temporary worktree at $worktree ($commit)" >&2
working_directory=$worktree
[ -z "$project_relative" ] || working_directory=$worktree/$project_relative
[ -d "$working_directory" ] ||
  { echo "worktree.sh: project directory is absent at commit $commit" >&2; exit 1; }
echo "worktree.sh: running from $working_directory" >&2

export HIRAKU_COMMIT=$commit
export HIRAKU_GIT_STATE=clean

cd "$working_directory"
"$@"
