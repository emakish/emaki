#!/bin/sh
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
set -eu
if ! command -v unshare >/dev/null 2>&1 ||
   ! unshare --map-auto --map-root-user true 2>/dev/null; then
    echo 'SKIP: user namespaces with subordinate ID mappings are unavailable'
    exit 77
fi
exec unshare --map-auto --map-root-user "$1"
