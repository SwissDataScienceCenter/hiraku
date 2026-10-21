#!/bin/sh
# Submit an entrypoint as a Slurm batch job that runs on an exact commit.
#
# Site settings (account, partition, time, GPUs, container environment, ...) go
# in hiraku/dispatch/slurm.header as ordinary `#SBATCH` lines, the same lines you
# would put at the top of a hand-written .sbatch file. Extra sbatch options can
# also be passed per call in HIRAKU_SBATCH_ARGS.
#
# The job runs in a Git worktree of the requested commit (HEAD when none is
# given), created under the repository's Git directory: that is on the same
# shared filesystem as the repository, so compute nodes can see it, and it is
# outside the working tree, so it never makes a later run look dirty. The job
# removes the worktree when it ends. Only committed files are in the worktree:
# data, checkpoints and other ignored files must be reached by absolute path,
# and results must be written under $HIRAKU_ROOT (the project), not relative
# to the current directory.
#
# sbatch returns as soon as the job is queued, so this dispatcher returns before
# the experiment has run; the job log is under <git dir>/hiraku-worktrees/logs/.
# Set HIRAKU_SLURM_WAIT=1 to block until the job ends. The job id is printed on
# stdout.
set -eu

commit_ref=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --commit)
      [ "$#" -ge 2 ] || { echo "slurm.sh: --commit requires a value" >&2; exit 2; }
      commit_ref=$2
      shift 2
      ;;
    --)
      shift
      break
      ;;
    *)
      echo "slurm.sh: unknown option: $1" >&2
      exit 2
      ;;
  esac
done

[ "$#" -gt 0 ] || { echo "slurm.sh: missing command after --" >&2; exit 2; }
command -v git >/dev/null 2>&1 ||
  { echo "slurm.sh: Git executable was not found" >&2; exit 1; }
command -v sbatch >/dev/null 2>&1 ||
  { echo "slurm.sh: sbatch was not found on PATH (run this on a Slurm login node)" >&2; exit 1; }

project_root=$(pwd -P)
repository_root=$(git rev-parse --show-toplevel 2>/dev/null) ||
  { echo "slurm.sh: current directory is not inside a Git repository" >&2; exit 1; }
commit=$(git rev-parse --verify "${commit_ref:-HEAD}^{commit}" 2>/dev/null) ||
  { echo "slurm.sh: cannot resolve commit ${commit_ref:-HEAD}" >&2; exit 1; }
if [ -z "$commit_ref" ] &&
  [ -n "$(git status --porcelain --untracked-files=no 2>/dev/null)" ]; then
  echo "slurm.sh: no --commit given; the job runs HEAD ($commit) without your uncommitted changes" >&2
fi
case "$project_root/" in
  "$repository_root/"*) project_relative=${project_root#"$repository_root"} ;;
  *) echo "slurm.sh: project is outside the Git repository" >&2; exit 1 ;;
esac
project_relative=${project_relative#/}

git_dir=$(git -C "$repository_root" rev-parse --git-common-dir)
case "$git_dir" in
  /*) ;;
  *) git_dir=$repository_root/$git_dir ;;
esac
base=${HIRAKU_WORKTREE_ROOT:-$git_dir/hiraku-worktrees}
header=$project_root/hiraku/dispatch/slurm.header
[ -n "${HIRAKU_ROOT:-}" ] && header=$HIRAKU_ROOT/hiraku/dispatch/slurm.header
mkdir -p "$base/logs"
short=$(printf '%s' "$commit" | cut -c1-12)
job_dir=$(mktemp -d "$base/${HIRAKU_RECORD_ID:-run}-$short.XXXXXX")
worktree=$job_dir/checkout

# Single-quote one argument for the generated job script.
quote() {
  printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}

remove_worktree() {
  git -C "$repository_root" worktree remove --force "$worktree" >/dev/null 2>&1 || true
  git -C "$repository_root" worktree prune >/dev/null 2>&1 || true
  rm -rf -- "$job_dir"
}

git -C "$repository_root" worktree add --detach "$worktree" "$commit" >/dev/null ||
  { rm -rf -- "$job_dir"; echo "slurm.sh: could not create a worktree for $commit" >&2; exit 1; }
working_directory=$worktree
[ -z "$project_relative" ] || working_directory=$worktree/$project_relative
[ -d "$working_directory" ] ||
  { remove_worktree; echo "slurm.sh: project directory is absent at commit $commit" >&2; exit 1; }

job=$job_dir/job.sh
{
  echo '#!/bin/bash'
  if [ -f "$header" ]; then grep '^#SBATCH' "$header" || true; fi
  echo 'set -u'
  printf 'export HIRAKU_COMMIT=%s\n' "$(quote "$commit")"
  echo 'export HIRAKU_GIT_STATE=clean'
  for name in HIRAKU_RECORD_ID HIRAKU_RECORD_FILE HIRAKU_ROOT HIRAKU_ENTRYPOINT; do
    eval "value=\${$name-}"
    printf 'export %s=%s\n' "$name" "$(quote "$value")"
  done
  # Git may be missing inside a job container: fall back to deleting the files;
  # `git worktree prune` on the login node then drops the stale entry.
  printf 'cleanup() { git -C %s worktree remove --force %s >/dev/null 2>&1 || rm -rf -- %s; git -C %s worktree prune >/dev/null 2>&1 || true; rm -rf -- %s; }\n' \
    "$(quote "$repository_root")" "$(quote "$worktree")" "$(quote "$worktree")" \
    "$(quote "$repository_root")" "$(quote "$job_dir")"
  echo 'trap cleanup EXIT'
  printf 'cd %s || exit 1\n' "$(quote "$working_directory")"
  printf 'echo "slurm.sh: job ${SLURM_JOB_ID-} running %s in $PWD" >&2\n' "$commit"
  for argument in "$@"; do printf '%s ' "$(quote "$argument")"; done
  echo
} > "$job"

has_option() {
  [ -f "$header" ] && grep -Eq "^#SBATCH[[:space:]]+($1)([=[:space:]]|\$)" "$header"
}

set -- --parsable --chdir="$working_directory"
has_option '--job-name|-J' || set -- "$@" --job-name="hiraku-${HIRAKU_RECORD_ID:-run}"
has_option '--output|-o' || set -- "$@" --output="$base/logs/%x-%j.out"
waiting=false
if [ "${HIRAKU_SLURM_WAIT:-}" = 1 ]; then
  waiting=true
  set -- "$@" --wait
fi
# Word splitting of HIRAKU_SBATCH_ARGS is intended: one option per word.
# shellcheck disable=SC2086
submitted=$(sbatch "$@" ${HIRAKU_SBATCH_ARGS:-} "$job") || {
  status=$?
  [ "$waiting" = true ] || remove_worktree
  echo "slurm.sh: sbatch failed (status $status)" >&2
  exit "$status"
}
job_id=${submitted%%;*}
job_id=$(printf '%s' "$job_id" | tail -n 1)
echo "slurm.sh: submitted job $job_id for commit $commit" >&2
has_option '--output|-o' || echo "slurm.sh: job log under $base/logs/ (<job name>-$job_id.out)" >&2

printf '%s\n' "$job_id"
