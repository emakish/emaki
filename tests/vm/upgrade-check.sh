#!/usr/bin/env bash
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
# Queue entry for the upgrade acceptance (tests/vm/upgrade-check.py --help): all writes and the
# VM's lifetime stay under this job's VMDIR.
set -Eeuo pipefail
: "${VMDIR:?Run through the VM queue with VMDIR set}"
exec python3 "$(dirname -- "$(readlink -f -- "$0")")/upgrade-check.py" --vm-dir "$VMDIR" "$@"
