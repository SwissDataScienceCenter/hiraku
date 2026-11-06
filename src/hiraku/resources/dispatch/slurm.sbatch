#!/bin/bash
# Batch job script of the slurm dispatcher (slurm.sh). Edit the #SBATCH lines
# for this project; SBATCH_* variables at submission override them.
#
# The job runs the command once, through launch.sh, which records the commit
# that actually runs. For several tasks or nodes, start them in the command,
# for example: hiraku run --dispatch slurm -- srun python train.py
#
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --time=01:00:00
#
# Examples (remove one # to enable):
##SBATCH --account=ACCOUNT
##SBATCH --partition=PARTITION
##SBATCH --gpus-per-node=1
set -euo pipefail

cd "${HIRAKU_ROOT:?HIRAKU_ROOT is not set; submit with hiraku run --dispatch slurm}"

echo "slurm.sbatch: job ${SLURM_JOB_ID:-?} on ${SLURM_JOB_NODELIST:-?}," \
  "record ${HIRAKU_RECORD_ID:-?}"

launcher=${HIRAKU_DISPATCH_DIR:-$HIRAKU_ROOT/hiraku/dispatch}/launch.sh
if [ -x "$launcher" ]; then
  exec "$launcher" -- "$@"
fi
exec "$@"
