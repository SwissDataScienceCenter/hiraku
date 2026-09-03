#!/bin/sh
set -eu
if [ "${1:-}" = "--" ]; then
  shift
fi
exec "$@"

