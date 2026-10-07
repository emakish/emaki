#!/bin/bash
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
# Capture the host display using the same evidence checks as the ISO runner.
set -Eeuo pipefail
HERE=$(dirname -- "$(readlink -f -- "$0")")
exec python3 "$HERE/glass-target.py" shot "$@"
