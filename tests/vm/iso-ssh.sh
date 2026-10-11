#!/usr/bin/env bash
set -Eeuo pipefail
# shellcheck source=tests/vm/iso-common.sh
source "$(dirname -- "$(readlink -f -- "$0")")/iso-common.sh"
iso_parse "$@"
iso_ssh_args
# Talks only to the QEMU this directory started (iso_ssh in iso-common.sh).
iso_prove_vm >/dev/null
exec "${ISO_SSH_UNCHECKED[@]}" "${ISO_ARGS[@]}"
