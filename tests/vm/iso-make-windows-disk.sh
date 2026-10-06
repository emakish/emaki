#!/usr/bin/env bash
# Host-side helper. Never mounts a disk or needs host root. Guest writes are
# restricted to an empty emaki-target disk inside the already running test ISO.
set -Eeuo pipefail
umask 077
# shellcheck source=tests/vm/iso-common.sh
source "$(dirname -- "$(readlink -f -- "$0")")/iso-common.sh"
iso_parse "$@"
((${#ISO_ARGS[@]} == 1)) || iso_die 'Usage: iso-make-windows-disk.sh DISK.qcow2 [--dir DIR] [--ssh-port PORT]'
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
assert f'file={sys.argv[2]},if=none,id=target,format=qcow2' in argv, 'qcow2 does not match the running target'
assert 'virtio-blk-pci,drive=target,serial=emaki-target' in argv, 'target serial differs'
PY
[[ ! -e $ISO_VM/windows-before.json ]] || iso_die 'baseline already exists; use a fresh VM directory and empty disk'
ISO_USER=live
iso_ssh_args
"${ISO_SSH[@]}" 'cat > /tmp/windows-efi.cfg' <<'EOF'
serial --unit=0 --speed=115200
terminal_output console serial
echo EMAKI_SYNTHETIC_WINDOWS_EFI_OK
sleep 3600
EOF
"${ISO_SSH[@]}" "grub-mkstandalone -O x86_64-efi --locales='' --fonts='' -o /tmp/windows-fixture.efi boot/grub/grub.cfg=/tmp/windows-efi.cfg"
"${ISO_SSH[@]}" 'umask 077; cat > /tmp/emaki-windows-fixture.py' <"$ISO_HERE/fixtures/windows-disk.py"
"${ISO_SSH[@]}" 'sudo -n python3 /tmp/emaki-windows-fixture.py prepare' >"$ISO_VM/windows-before.json.tmp"
mv -- "$ISO_VM/windows-before.json.tmp" "$ISO_VM/windows-before.json"
printf 'Prepared synthetic Windows disk. Preservation baseline: %s/windows-before.json\n' "$ISO_VM"
