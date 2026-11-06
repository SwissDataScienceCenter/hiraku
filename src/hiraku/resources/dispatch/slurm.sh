#!/bin/sh
# Submit the command as a Slurm batch job and return once Slurm accepts it.
#
#   hiraku run --dispatch slurm -- uv run python train.py --epochs 20
#
# The job runs slurm.sbatch (next to this file): edit its #SBATCH lines for the
# project's defaults. Slurm's input variables override them per submission, for
# example SBATCH_ACCOUNT, SBATCH_PARTITION, SBATCH_TIMELIMIT and
# SBATCH_GPUS_PER_NODE. Options without a variable, such as --nodes, go in
# HIRAKU_SBATCH_ARGS, separated by spaces. With SBATCH_WAIT set, sbatch waits
# for the job and the exit code is the job's. The log goes to logs/slurm/
# unless SBATCH_OUTPUT is set.
set -eu

while [ "$#" -gt 0 ]; do
  case "$1" in
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
[ -n "${HIRAKU_ROOT:-}" ] || {
  echo "slurm.sh: HIRAKU_ROOT is not set; run it with hiraku run --dispatch slurm" >&2
  exit 2
}
command -v sbatch >/dev/null 2>&1 || {
  echo "slurm.sh: sbatch not found; run hiraku on a Slurm login node" >&2
  exit 127
}

dispatch_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
script=$dispatch_dir/slurm.sbatch
[ -f "$script" ] || { echo "slurm.sh: job script not found: $script" >&2; exit 2; }

# Variables of an enclosing allocation (salloc, an interactive job) would leak
# into the new job through the default --export=ALL and change how srun
# behaves there. SLURM_CONF may locate the cluster configuration: keep it.
if [ -n "${SLURM_JOB_ID:-}" ]; then
  for name in $(env | sed -n 's/^\(SLURM_[A-Za-z0-9_]*\)=.*/\1/p'); do
    [ "$name" = SLURM_CONF ] || unset "$name"
  done
fi

# Slurm runs a copy of the job script; this tells it where launch.sh is.
export HIRAKU_DISPATCH_DIR="$dispatch_dir"

set -- "$script" "$@"
if [ -n "${HIRAKU_SBATCH_ARGS:-}" ]; then
  # They end up after the defaults added below, so they win. Split on spaces,
  # without glob expansion.
  set -f
  set -- $HIRAKU_SBATCH_ARGS "$@"
  set +f
fi
log_dir=$HIRAKU_ROOT/logs/slurm
if [ -z "${SBATCH_OUTPUT:-}" ]; then
  # Slurm does not create the directory and silently drops the log otherwise.
  # Untracked logs would mark every later run's git state as dirty.
  mkdir -p "$log_dir"
  [ -e "$log_dir/.gitignore" ] || printf '*\n' > "$log_dir/.gitignore"
  set -- --output="$log_dir/%x-%j.out" "$@"
fi
if [ -z "${SBATCH_JOB_NAME:-}" ]; then
  set -- --job-name="${HIRAKU_RECORD_ID:-hiraku}" "$@"
fi

if [ -n "${SBATCH_WAIT:-}" ]; then
  exec sbatch --chdir="$HIRAKU_ROOT" "$@"
fi

submitted=$(sbatch --parsable --chdir="$HIRAKU_ROOT" "$@")
job_id=${submitted%%;*}
echo "slurm_job_id: $job_id"
if [ -z "${SBATCH_OUTPUT:-}" ]; then
  echo "slurm_log: $log_dir/${SBATCH_JOB_NAME:-${HIRAKU_RECORD_ID:-hiraku}}-$job_id.out"
fi
