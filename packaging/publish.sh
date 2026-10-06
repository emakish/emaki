#!/usr/bin/env bash
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
# Publish the [emaki] repository to pkgs.emaki.sh (docs/mirror.md). `publish.sh --help` lists
# the subcommands; every one of them takes --dry-run.
exec python3 "$(dirname -- "$(readlink -f -- "$0")")/mirror/publish.py" "$@"
