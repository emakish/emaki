#!/usr/bin/env bash
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
# Self-contained host-queue job; all mutable evidence stays under VMDIR.
set -Eeuo pipefail
exec python3 tests/vm/iso-encrypt-check.py "$@"
