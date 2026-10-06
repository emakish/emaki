#!/usr/bin/env bash
# Queue entry: all writes and VM lifetime stay under this session's VMDIR.
set -Eeuo pipefail
: "${VMDIR:?Run through the VM queue with VMDIR set}"
exec python3 tests/vm/rollback-check.py "$@"
