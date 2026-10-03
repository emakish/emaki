#!/usr/bin/env bash
set -Eeuo pipefail
# shellcheck source=tests/vm/iso-common.sh
source "$(dirname -- "$(readlink -f -- "$0")")/iso-common.sh"
iso_parse "$@"
((${#ISO_ARGS[@]} == 0)) || iso_die 'Usage: iso-wait-ssh.sh [--dir DIR] [--ssh-port PORT] [--user USER]'
iso_ssh_args
start=$SECONDS
for ((attempt=0; attempt<60; attempt++)); do
    if "${ISO_SSH[@]}" true 2>"$ISO_VM/wait-ssh.log"; then
        printf 'OK: SSH %s after %ss\n' "$ISO_USER" "$((SECONDS-start))"
        exit 0
    fi
    sleep 3
done
cat "$ISO_VM/wait-ssh.log" >&2
tail -n 5 "$ISO_VM/serial.log" >&2 || true
iso_die "SSH did not become ready for $ISO_USER"
