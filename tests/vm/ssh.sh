#!/bin/bash
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
# Run a command in the test VM; --fixture JSON selects the release account.
HERE=$(dirname -- "$(readlink -f -- "$0")")
exec python3 "$HERE/suite_target.py" "$@"
