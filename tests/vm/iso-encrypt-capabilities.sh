#!/usr/bin/env bash
# Read the shipped boot tools in a disposable live guest.
set -Eeuo pipefail
[[ $# == 1 && $1 =~ ^[a-zA-Z0-9][a-zA-Z0-9-]*$ ]] || {
    echo 'Usage: EMAKI_CHECK_ISO=/path/to/candidate.iso VMDIR=/path/to/runs iso-encrypt-capabilities.sh RUN-NAME' >&2
    exit 2
}
[[ -n ${EMAKI_CHECK_ISO:-} && -f $EMAKI_CHECK_ISO && -r $EMAKI_CHECK_ISO ]] || {
    echo 'EMAKI_CHECK_ISO must name a readable candidate ISO image file' >&2
    exit 2
}
iso=$(readlink -f -- "$EMAKI_CHECK_ISO")
export EMAKI_ISO_VM_DIR="${VMDIR:?}/capabilities-$1"
export EMAKI_ISO_SSH_PORT=2231
umask 077
mkdir -p -- "$(dirname -- "$EMAKI_ISO_VM_DIR")"
mkdir -- "$EMAKI_ISO_VM_DIR"
{ printf 'ISO: %s\n' "$iso"; sha256sum -- "$iso"; } >"$EMAKI_ISO_VM_DIR/iso.txt"
trap 'tests/vm/iso-stop.sh --ssh-port 2231' EXIT
tests/vm/run-iso.sh --ssh-port 2231 --iso "$iso" >"$EMAKI_ISO_VM_DIR/qemu.log" 2>&1 &
tests/vm/iso-wait-ssh.sh --ssh-port 2231
tests/vm/iso-ssh.sh --ssh-port 2231 'pacman -Q grub cryptsetup mkinitcpio btrfs-progs; ls /usr/lib/grub/x86_64-efi/{luks2,argon2,pbkdf2,cryptodisk}.mod; cat /usr/lib/initcpio/hooks/{encrypt,resume}; cat /usr/lib/initcpio/install/encrypt; cat /usr/lib/systemd/zram-generator.conf.d/* 2>/dev/null; ls /usr/lib/emaki-installer; cat /usr/lib/python3.14/site-packages/archinstall/lib/disk/filesystem.py' >"$EMAKI_ISO_VM_DIR/capabilities.txt"
cat "$EMAKI_ISO_VM_DIR/capabilities.txt"
