#!/bin/bash

# PSI/J's single_launch.sh leaves redirection paths unquoted. Keep this equivalent
# launcher until upstream supports paths with spaces (including its library path).
source "$(dirname "$0")/launcher_lib.sh"
pre_launch
set +e
"$@" <"$_PSI_J_STDIN" >"$_PSI_J_STDOUT" 2>"$_PSI_J_STDERR"
command_exit_code=$?
set -e
post_launch
echo "_PSI_J_LAUNCHER_DONE"
exit "$command_exit_code"
