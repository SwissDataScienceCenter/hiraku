#!/bin/sh
set -eu

commit_ref=
task_id=
task_file=
experiment_id=
experiment_file=
entrypoint=

while [ "$#" -gt 0 ]; do
  case "$1" in
    --commit|--task-id|--task-file|--experiment-id|--experiment-file|--entrypoint)
      option=$1
      [ "$#" -ge 2 ] || { echo "worktree.sh: $option requires a value" >&2; exit 2; }
      value=$2
      case "$option" in
        --commit) commit_ref=$value ;;
        --task-id) task_id=$value ;;
        --task-file) task_file=$value ;;
        --experiment-id) experiment_id=$value ;;
        --experiment-file) experiment_file=$value ;;
        --entrypoint) entrypoint=$value ;;
      esac
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
working_directory=$worktree
[ -z "$project_relative" ] || working_directory=$worktree/$project_relative
[ -d "$working_directory" ] ||
  { echo "worktree.sh: project directory is absent at commit $commit" >&2; exit 1; }

unset HIRAKU_TASK_ID HIRAKU_TASK_FILE HIRAKU_EXPERIMENT_ID
unset HIRAKU_EXPERIMENT_FILE HIRAKU_COMMIT HIRAKU_ENTRYPOINT
[ -n "$task_id" ] && export HIRAKU_TASK_ID=$task_id
[ -n "$task_file" ] && export HIRAKU_TASK_FILE=$task_file
[ -n "$experiment_id" ] && export HIRAKU_EXPERIMENT_ID=$experiment_id
[ -n "$experiment_file" ] && export HIRAKU_EXPERIMENT_FILE=$experiment_file
[ -n "$entrypoint" ] && export HIRAKU_ENTRYPOINT=$entrypoint
export HIRAKU_COMMIT=$commit

cd "$working_directory"
"$@"
