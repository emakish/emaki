#!/usr/bin/env bash
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
# Queue entry: all writes and VM lifetime stay under this session's VMDIR.
set -Eeuo pipefail
: "${VMDIR:?Run through the VM queue with VMDIR set}"
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec python3 "$here/rollback-check.py" "$@"
