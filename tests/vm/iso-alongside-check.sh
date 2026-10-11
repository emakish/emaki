#!/usr/bin/env bash
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
# Self-contained KVM queue job; all evidence is confined to VMDIR.
# Exit 0 = PASS, 77 = NOT APPLICABLE (the ISO's installer does not offer
# install alongside Windows), anything else = FAIL.
# Runs both a foreign-fallback fixture and a fresh no-fallback fixture.
# The latter must create a removable EFI loader; both retain separate evidence.
# Requires EMAKI_CHECK_ISO, EMAKI_ALONGSIDE_BASE_DISK (empty standalone qcow2),
# The base must contain only zero guest bytes; its clone grows to 96 GiB if below 80 GiB.
# Requires reflink support and VMDIR strictly inside ~/VMs. EMAKI_ISO_SSH_PORT defaults to 2241.
set -Eeuo pipefail
exec python3 tests/vm/iso-alongside-check.py "$@"
