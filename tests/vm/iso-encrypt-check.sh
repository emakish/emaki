#!/usr/bin/env bash
# Self-contained host-queue job; all mutable evidence stays under VMDIR.
set -Eeuo pipefail
exec python3 tests/vm/iso-encrypt-check.py "$@"
