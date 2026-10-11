#!/usr/bin/env bash
set -Eeuo pipefail
# shellcheck source=tests/vm/iso-common.sh
source "$(dirname -- "$(readlink -f -- "$0")")/iso-common.sh"
iso_parse "$@"
((${#ISO_ARGS[@]} == 0)) || iso_die 'Usage: iso-wait-ssh.sh [--dir DIR] [--ssh-port PORT] [--user USER]'
iso_ssh_args
start=$SECONDS
iso_prove_vm 120
for ((attempt=0; attempt<60; attempt++)); do
    # iso_ssh proves ownership again before each attempt: the port may change hands while the
    # guest boots, and a retry must never reach the VM that took it over.
    if iso_ssh true 2>"$ISO_VM/wait-ssh.log"; then
        iso_prove_vm >/dev/null  # and it did not change hands during this contact
        printf 'OK: SSH %s after %ss\n' "$ISO_USER" "$((SECONDS-start))"
        exit 0
    fi
    sleep 3
done
cat "$ISO_VM/wait-ssh.log" >&2
tail -n 5 "$ISO_VM/serial.log" >&2 || true
iso_die "SSH did not become ready for $ISO_USER"
