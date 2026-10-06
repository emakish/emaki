#!/usr/bin/env bash
# Read the shipped boot tools in a disposable live guest.
set -Eeuo pipefail
export EMAKI_ISO_VM_DIR="${VMDIR:?}/capabilities-${1:?run name}"
export EMAKI_ISO_SSH_PORT=2231
mkdir -p "$EMAKI_ISO_VM_DIR"
trap 'tests/vm/iso-stop.sh --ssh-port 2231' EXIT
tests/vm/run-iso.sh --ssh-port 2231 --iso "$HOME/VMs/iso/test-0.1.1/emaki-0.1.1-x86_64.iso" >"$EMAKI_ISO_VM_DIR/qemu.log" 2>&1 &
tests/vm/iso-wait-ssh.sh --ssh-port 2231
tests/vm/iso-ssh.sh --ssh-port 2231 'pacman -Q grub cryptsetup mkinitcpio btrfs-progs; ls /usr/lib/grub/x86_64-efi/{luks2,argon2,pbkdf2,cryptodisk}.mod; cat /usr/lib/initcpio/hooks/{encrypt,resume}; cat /usr/lib/initcpio/install/encrypt; cat /usr/lib/systemd/zram-generator.conf.d/* 2>/dev/null; ls /usr/lib/emaki-installer; cat /usr/lib/python3.14/site-packages/archinstall/lib/disk/filesystem.py' >"$EMAKI_ISO_VM_DIR/capabilities.txt"
cat "$EMAKI_ISO_VM_DIR/capabilities.txt"
