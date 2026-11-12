#!/bin/bash

# DetachedLauncher starts a new session; disconnect streams and save the outcome.
# The first seven arguments belong to PSI/J; ours precede the command argv.
result_path="$8"
exec </dev/null >/dev/null 2>&1

record_result() {
    exit_code=$?
    trap - EXIT
    temporary="${result_path}.$$.tmp"
    finished_at=$(date +%s) || exit 125
    if ! printf '{"exit_code":%d,"finished_at":%s}\n' \
        "$exit_code" "$finished_at" >"$temporary" ||
        ! mv -f -- "$temporary" "$result_path"; then
        rm -f -- "$temporary"
        echo "Could not record execution result" >&2
        exit 125
    fi
    exit "$exit_code"
}
trap record_result EXIT

source "$(dirname "$0")/launcher_lib.sh"
shift

pre_launch
set +e
"$@" <"$_PSI_J_STDIN" >"$_PSI_J_STDOUT" 2>"$_PSI_J_STDERR"
command_exit_code=$?
set -e
post_launch
echo "_PSI_J_LAUNCHER_DONE"
exit "$command_exit_code"
