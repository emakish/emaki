#!/usr/bin/env bash
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
# Host-side helper. Never mounts a disk or needs host root. Guest writes are
# restricted to an empty emaki-target disk inside the already running test ISO.
set -Eeuo pipefail
umask 077
# shellcheck source=tests/vm/iso-common.sh
source "$(dirname -- "$(readlink -f -- "$0")")/iso-common.sh"
iso_parse "$@"
# --loader EFI: the ESP's bootmgfw.efi is that file instead of the diagnostic GRUB image, e.g. the
# Microsoft-signed Windows Boot Manager (efi/boot/bootx64.efi of a Windows installation medium),
# which firmware with Secure Boot on starts where it refuses the unsigned diagnostic image.
# --esp-mib 100|260|512: the Windows ESP's size (Windows uses 100 MiB, 260 MiB on 4Kn disks).
usage='Usage: iso-make-windows-disk.sh DISK.qcow2 [--loader EFI] [--esp-mib 100|260|512] [--dir DIR] [--ssh-port PORT]'
loader='' esp_mib=100
options=("${ISO_ARGS[@]:1}")
ISO_ARGS=("${ISO_ARGS[@]:0:1}")
while ((${#options[@]})); do
    ((${#options[@]} >= 2)) || iso_die "$usage"
    case ${options[0]} in
        --loader)
            loader=$(realpath -e -- "${options[1]}") || iso_die "unreadable loader: ${options[1]}"
            [[ $(head -c 2 -- "$loader") == MZ ]] || iso_die 'the loader is not an EFI (PE) image' ;;
        --esp-mib)
            [[ ${options[1]} =~ ^(100|260|512)$ ]] || iso_die '--esp-mib must be 100, 260 or 512'
            esp_mib=${options[1]} ;;
        *) iso_die "$usage" ;;
    esac
    options=("${options[@]:2}")
done
((${#ISO_ARGS[@]} == 1)) || iso_die "$usage"
disk=$(realpath -m -- "${ISO_ARGS[0]}")
[[ $disk == *.qcow2 && $disk != *,* ]] || iso_die 'expected a qcow2 path without commas'
if [[ ! -e $disk ]]; then
    qemu-img create -f qcow2 "$disk" 96G
    printf 'Created %s. Boot the test ISO with run-iso.sh --disk pointing to this file, then rerun this command.\n' "$disk"
    exit 0
fi
[[ -f $disk && ! -L $disk ]] || iso_die 'expected an ordinary qcow2 file'
[[ -f $ISO_VM/qemu.pid ]] || iso_die 'start the test ISO with this disk first'
# Prove the named file is the target attached to this harness instance.
python3 - "$ISO_VM/qemu.pid" "$disk" <<'PY'
from pathlib import Path
import sys
pid = int(Path(sys.argv[1]).read_text())
argv = Path(f'/proc/{pid}/cmdline').read_bytes().decode().split('\0')
drive = f'file={sys.argv[2]},if=none,id=target,format=qcow2'
assert any(arg == drive or arg.startswith(drive + ',') for arg in argv), 'qcow2 does not match the running target'
assert 'virtio-blk-pci,drive=target,serial=emaki-target' in argv, 'target serial differs'
PY
[[ ! -e $ISO_VM/windows-before.json ]] || iso_die 'baseline already exists; use a fresh VM directory and empty disk'
ISO_USER=live
iso_ssh_args
iso_ssh 'cat > /tmp/windows-efi.cfg' <<'EOF'
serial --unit=0 --speed=115200
terminal_output console serial
echo EMAKI_SYNTHETIC_WINDOWS_EFI_OK
sleep 3600
EOF
if [[ -n $loader ]]; then
    iso_ssh 'umask 077; cat > /tmp/windows-fixture.efi' <"$loader"
else
    iso_ssh "grub-mkstandalone -O x86_64-efi --locales='' --fonts='' -o /tmp/windows-fixture.efi boot/grub/grub.cfg=/tmp/windows-efi.cfg"
fi
iso_ssh 'umask 077; cat > /tmp/emaki-windows-fixture.py' <"$ISO_HERE/fixtures/windows-disk.py"
iso_ssh "sudo -n python3 /tmp/emaki-windows-fixture.py prepare --esp-mib $esp_mib" >"$ISO_VM/windows-before.json.tmp"
mv -- "$ISO_VM/windows-before.json.tmp" "$ISO_VM/windows-before.json"
printf 'Prepared synthetic Windows disk. Preservation baseline: %s/windows-before.json\n' "$ISO_VM"
