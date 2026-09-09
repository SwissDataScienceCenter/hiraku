#!/bin/sh
set -eu

task_id=
task_file=
experiment_id=
experiment_file=
entrypoint=

while [ "$#" -gt 0 ]; do
  case "$1" in
    --commit|--task-id|--task-file|--experiment-id|--experiment-file|--entrypoint)
      option=$1
      [ "$#" -ge 2 ] || { echo "launch.sh: $option requires a value" >&2; exit 2; }
      value=$2
      case "$option" in
        --commit) : ;;
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
      echo "launch.sh: unknown option: $1" >&2
      exit 2
      ;;
  esac
done

[ "$#" -gt 0 ] || { echo "launch.sh: missing command after --" >&2; exit 2; }

unset HIRAKU_TASK_ID HIRAKU_TASK_FILE HIRAKU_EXPERIMENT_ID
unset HIRAKU_EXPERIMENT_FILE HIRAKU_COMMIT HIRAKU_ENTRYPOINT
[ -n "$task_id" ] && export HIRAKU_TASK_ID=$task_id
[ -n "$task_file" ] && export HIRAKU_TASK_FILE=$task_file
[ -n "$experiment_id" ] && export HIRAKU_EXPERIMENT_ID=$experiment_id
[ -n "$experiment_file" ] && export HIRAKU_EXPERIMENT_FILE=$experiment_file
[ -n "$entrypoint" ] && export HIRAKU_ENTRYPOINT=$entrypoint

exec "$@"
