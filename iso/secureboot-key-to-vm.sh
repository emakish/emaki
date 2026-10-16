#!/usr/bin/env bash
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
# Lend the Emaki Secure Boot release key to the build VM for one release image build, then take it
# back (docs/signing.md, "The Secure Boot release key"). Runs on the publishing machine, which is
# also the VM host (the build VM's QEMU runs here).
#
#   send:   checks that the key here is encrypted and its certificate is the one pinned in
#           iso/secureboot-release.crt; that this machine has no swap that is not encrypted (it holds
#           the unlocked key, and the guest's memory is this machine's memory), that core dumps are
#           off for the processes that hold the key (this script, openssl, and the VM's QEMU given by
#           --vm-pid), that the VM keeps DIR on a RAM file system and has no swap. Then OpenSSL asks
#           for the passphrase once, on this terminal, and the unlocked key goes straight through ssh
#           into DIR (root only, 0700/0600). The VM proves the key belongs to the certificate. Build
#           with `iso/build.sh --secure-boot-key DIR/emaki-release`.
#   remove: deletes DIR in the VM and says "Removed" only after the VM confirmed it is gone (run it
#           after the release build, also after a failed one; turning the VM off also clears it).
#   status: present, absent, or unknown (the VM could not be asked; exit status 2).
#
# Usage: iso/secureboot-key-to-vm.sh send|remove|status [--key PREFIX] [--remote-dir DIR]
#            [--release-cert FILE] [--vm-pid PID] -- SSH COMMAND...
#   PREFIX defaults to ${XDG_CONFIG_HOME:-~/.config}/emaki-secureboot/emaki-release (as made by
#   scripts/new-secureboot-release-key.sh), DIR to /run/emaki-secureboot; --vm-pid (the build VM's
#   QEMU process here) is required by send; the SSH command runs one remote shell command as the
#   VM's user, who has sudo.
set -Eeuo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
# shellcheck source=SCRIPTDIR/secureboot-host.sh
. "$HERE/secureboot-host.sh"
secret_no_trace
action=${1:-}
(($#)) && shift
prefix=${XDG_CONFIG_HOME:-$HOME/.config}/emaki-secureboot/emaki-release
remote=/run/emaki-secureboot release_cert=$HERE/secureboot-release.crt vm_pid=''
while (($#)); do
    case $1 in
        --key|--remote-dir|--release-cert|--vm-pid)
            (($# >= 2)) || fail "missing value for $1"
            case $1 in --key) prefix=$2 ;; --remote-dir) remote=$2 ;; --release-cert) release_cert=$2 ;;
                --vm-pid) vm_pid=$2 ;; esac
            shift 2 ;;
        --) shift; break ;;
        *) fail "unknown option: $1" ;;
    esac
done
[[ $action == send || $action == remove || $action == status ]] || fail 'usage: secureboot-key-to-vm.sh send|remove|status [options] -- SSH COMMAND...'
(($#)) || fail 'give the ssh command after --'
ssh_command=("$@")
# DIR is put into remote shell commands: only plain absolute paths, never / itself.
[[ $remote =~ ^/[A-Za-z0-9._/-]+$ && $remote != */ && $remote != *..* && $(dirname -- "$remote") != / ]] \
    || fail "unsafe remote directory: $remote"
# A remote command never reads this terminal (keys typed early stay for the passphrase prompt);
# vm_in hands it data.
vm() { "${ssh_command[@]}" "$1" </dev/null; }
vm_in() { "${ssh_command[@]}" "$1"; }
# present / absent from the VM itself; anything else (ssh or sudo failed, odd answer) is unknown.
remote_state() {
    local answer
    answer=$(vm "sudo sh -c 'if [ -e \"$remote\" ]; then echo present; else echo absent; fi'" 2>/dev/null) || answer=''
    case $answer in present|absent) echo "$answer" ;; *) echo unknown ;; esac
}
# Remove DIR and prove it is gone; returns non-zero when that cannot be confirmed.
remove_remote() {
    vm "sudo rm -rf -- '$remote'" >/dev/null 2>&1 || true
    [[ $(remote_state) == absent ]]
}
if [[ $action == status ]]; then
    case $(remote_state) in
        present) echo "present: $remote in the build VM" ;;
        absent) echo "absent: $remote" ;;
        *) echo "unknown: the build VM could not be asked about $remote" >&2; exit 2 ;;
    esac
    exit 0
fi
if [[ $action == remove ]]; then
    remove_remote || fail "$remote could not be confirmed gone from the build VM (state: $(remote_state)); check it there"
    echo "Removed: $remote is gone from the build VM."
    echo "Keep this machine's swap off until the build VM is powered off and its QEMU process is gone: its memory may still hold copies of the key."
    exit 0
fi

command -v openssl >/dev/null || fail 'missing dependency: openssl'
# This machine: no unencrypted swap, a sleep block held, no core dumps of this process or openssl,
# all before the first read of the key file (also when it is then refused).
host_memory_check
[[ -r $prefix.key && -r $prefix.crt ]] || fail "the release key needs $prefix.key and $prefix.crt"
head -n 1 -- "$prefix.key" | grep -qx -- '-----BEGIN ENCRYPTED PRIVATE KEY-----' \
    || fail "$prefix.key is not stored encrypted; the release key is kept under its passphrase (scripts/new-secureboot-release-key.sh)"
[[ $(stat -L -c %a -- "$prefix.key") == [0-7]00 ]] || fail "$prefix.key must be readable by its owner only"
[[ -f $release_cert ]] || fail "no release certificate is pinned in $release_cert"
der_sha256() { openssl x509 -in "$1" -outform DER | sha256sum | cut -d' ' -f1; }
[[ $(der_sha256 "$prefix.crt") == "$(der_sha256 "$release_cert")" ]] \
    || fail "$prefix.crt is not the release certificate pinned in $release_cert"
{ : </dev/tty; } 2>/dev/null || fail 'run this in a terminal: the passphrase is typed into it'
# The VM's QEMU on this machine holds the guest's memory, the unlocked key included: no core dump of it.
[[ $vm_pid =~ ^[1-9][0-9]{0,9}$ && -r $EMAKI_PROC/$vm_pid/limits && -r $EMAKI_PROC/$vm_pid/cmdline ]] \
    || fail "give --vm-pid PID: the build VM's QEMU process on this machine (its memory will hold the key)"
qemu_cmdline=$(tr '\0' ' ' <"$EMAKI_PROC/$vm_pid/cmdline")
[[ $qemu_cmdline == *qemu-system* ]] || fail "process $vm_pid is not a QEMU virtual machine"
qemu_core=$(awk '/^Max core file size/ { print $5 }' "$EMAKI_PROC/$vm_pid/limits")
[[ $qemu_core == 0 || $qemu_cmdline == *dump-guest-core=off* ]] \
    || fail "the build VM (QEMU $vm_pid) could write its memory, the key included, to a core dump; first run: prlimit --pid $vm_pid --core=0:0"
# The VM side: DIR on a RAM file system, no swap at all, nothing left over. Each inspection must succeed.
vm "[ \"\$(findmnt -no FSTYPE --target '$(dirname -- "$remote")')\" = tmpfs ]" \
    || fail "$(dirname -- "$remote") in the build VM is not a RAM file system (tmpfs), or it could not be checked"
vm_swaps=$(vm 'cat /proc/swaps') || fail 'cannot inspect the swap of the build VM'
swaps_check "$vm_swaps" 'the build VM'
vm_core=$(vm 'cat /proc/sys/kernel/core_pattern') || fail 'cannot read the core dump setting of the build VM'
core_handler_check "$vm_core" 'the build VM'
case $(remote_state) in
    absent) ;;
    present) fail "$remote already exists in the build VM; run: secureboot-key-to-vm.sh remove -- ..." ;;
    *) fail "the build VM could not be asked about $remote" ;;
esac
# Leaves the VM only through remove_remote; says so plainly when that cannot be confirmed.
abandon() {
    trap - ERR INT TERM
    if remove_remote; then
        fail "$1; nothing was left in the build VM"
    fi
    fail "$1; and $remote could NOT be confirmed gone from the build VM: run secureboot-key-to-vm.sh remove -- ... and check it there"
}
# Every VM process that holds the key (the writer, the check's openssl) runs with a zero core size.
vm_no_core='ulimit -S -c 0 && ulimit -H -c 0'
trap 'abandon "an unexpected error stopped the transfer"' ERR
trap 'abandon "interrupted"' INT TERM
echo 'Type the passphrase of the Emaki Secure Boot release key.'
if ! openssl pkey -in "$prefix.key" \
    | vm_in "sudo install -d -m 0700 '$remote' && sudo sh -c '$vm_no_core && umask 077 && cat > \"$remote/emaki-release.key\"'"; then
    abandon 'the key was not unlocked (wrong passphrase?)'
fi
vm_in "sudo sh -c 'umask 022 && cat > \"$remote/emaki-release.crt\"'" <"$prefix.crt" || abandon 'the certificate was not copied'
expected=$(openssl x509 -in "$prefix.crt" -noout -pubkey | sha256sum | cut -d' ' -f1)
got=$(vm "sudo sh -c '$vm_no_core && openssl pkey -in \"$remote/emaki-release.key\" -pubout' | sha256sum | cut -d' ' -f1") || got=''
[[ $got == "$expected" ]] || abandon 'the key in the build VM does not belong to the certificate'
mode=$(vm "sudo stat -c %a '$remote/emaki-release.key'") || mode=''
[[ $mode == 600 ]] || abandon "the key in the build VM has mode ${mode:-unknown}"
trap - ERR INT TERM
echo "The release key is in the build VM's memory: $remote/emaki-release.{key,crt}."
echo "Build with: iso/build.sh ... --secure-boot-key $remote/emaki-release"
echo 'Afterwards, also after a failed build: secureboot-key-to-vm.sh remove -- <the same ssh command>'
