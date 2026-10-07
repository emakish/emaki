#!/bin/bash
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
# Shell status: shell-status.sh [jq-filter] [target options]
HERE=$(dirname "$(readlink -f "$0")")
filter=${1:-.}
[ "$#" -eq 0 ] || shift
set -o pipefail
python3 "$HERE/glass-target.py" status "$@" | jq -c "$filter"
