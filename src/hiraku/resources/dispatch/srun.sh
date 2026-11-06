#!/bin/sh
# Run the command as a Slurm job step and wait for it: interactive work, short
# tests, debugging on a compute node.
#
#   hiraku run --dispatch srun -- uv run python train.py --epochs 1
#   hiraku run --dispatch srun -- bash        # a shell on the compute node
#
# Outside an allocation, srun requests one, named after the record; Slurm's
# input variables set it up, for example SLURM_ACCOUNT, SLURM_PARTITION,
# SLURM_TIMELIMIT and SLURM_GPUS_PER_NODE. Other options go in
# HIRAKU_SRUN_ARGS, separated by spaces. Inside an allocation (salloc, an
# interactive job), the command runs there as one task that shares the job's
# resources. A terminal gets a pseudo-terminal (--pty).
set -eu

while [ "$#" -gt 0 ]; do
  case "$1" in
    --)
      shift
      break
      ;;
    *)
      echo "srun.sh: unknown option: $1" >&2
      exit 2
      ;;
  esac
done

[ "$#" -gt 0 ] || { echo "srun.sh: missing command after --" >&2; exit 2; }
command -v srun >/dev/null 2>&1 || {
  echo "srun.sh: srun not found; run hiraku on a Slurm login node" >&2
  exit 127
}

dispatch_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# launch.sh runs on the compute node and records the commit that runs there.
if [ -x "$dispatch_dir/launch.sh" ]; then
  set -- "$dispatch_dir/launch.sh" -- "$@"
fi
if [ -n "${HIRAKU_SRUN_ARGS:-}" ]; then
  # They end up after the defaults added below, so they win. Split on spaces,
  # without glob expansion.
  set -f
  set -- $HIRAKU_SRUN_ARGS "$@"
  set +f
fi
if [ -t 0 ] && [ -t 1 ]; then
  set -- --pty "$@"
fi
if [ -n "${SLURM_JOB_ID:-}" ]; then
  set -- --overlap --ntasks=1 "$@"
elif [ -z "${SLURM_JOB_NAME:-}" ]; then
  set -- --job-name="${HIRAKU_RECORD_ID:-hiraku}" "$@"
fi

exec srun "$@"
