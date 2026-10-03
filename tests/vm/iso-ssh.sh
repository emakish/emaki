#!/usr/bin/env bash
set -Eeuo pipefail
# shellcheck source=tests/vm/iso-common.sh
source "$(dirname -- "$(readlink -f -- "$0")")/iso-common.sh"
iso_parse "$@"
iso_ssh_args
exec "${ISO_SSH[@]}" "${ISO_ARGS[@]}"
