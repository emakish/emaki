#!/usr/bin/env bash
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
set -Eeuo pipefail
exec python3 "$(dirname -- "$(readlink -f -- "$0")")/iso-shot.py" "$@"
