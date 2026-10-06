#!/usr/bin/env bash
# Run a host-side VM job inside a jail: the whole filesystem is read-only except the given
# directories, /tmp is private, /dev has only KVM and the render node, secrets are hidden,
# no-new-privileges blocks sudo, and a private PID namespace means the job can only see (and
# kill) its own processes; everything it started dies with it.
# Network stays shared so ssh reaches QEMU's forwarded ports.
# Usage: jail.sh <writable-dir>... -- <command> [args...]
set -Eeuo pipefail
binds=()
while (($#)) && [[ $1 != -- ]]; do
    mkdir -p -- "$1"
    binds+=(--bind "$1" "$1")
    shift
done
(($#)) || { echo 'Usage: jail.sh <writable-dir>... -- <command> [args...]' >&2; exit 2; }
shift
hidden=()
for secret in "$HOME/.ssh" "$HOME/.gnupg" "$HOME/.config/emaki-signing"; do
    [[ ! -d $secret ]] || hidden+=(--tmpfs "$secret")
done
exec bwrap --ro-bind / / --dev /dev --dev-bind /dev/kvm /dev/kvm --dev-bind /dev/dri /dev/dri \
    --proc /proc --tmpfs /tmp --tmpfs /run/user "${hidden[@]}" \
    --unshare-pid --die-with-parent "${binds[@]}" -- "$@"
