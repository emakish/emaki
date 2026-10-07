#!/usr/bin/env bash
# Self-contained KVM queue job; all evidence is confined to VMDIR.
# Exit 0 = PASS, 77 = NOT APPLICABLE (the ISO's installer does not offer
# install alongside Windows, as in this release), anything else = FAIL.
set -Eeuo pipefail
exec python3 tests/vm/iso-alongside-check.py "$@"
