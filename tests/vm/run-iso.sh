#!/usr/bin/env bash
# Start in the foreground; use setsid/backgrounding as documented in docs/iso.md.
set -Eeuo pipefail
# shellcheck source=tests/vm/iso-common.sh
source "$(dirname -- "$(readlink -f -- "$0")")/iso-common.sh"
iso_parse "$@"
iso='' disk='' outputs=1 no_cd=0 usb=0 offline=0 memory=6G
set -- "${ISO_ARGS[@]}"
while (($#)); do
    case $1 in
        --iso|--disk|--outputs|--memory)
            (($# >= 2)) || iso_die "missing value for $1"
            case $1 in --iso) iso=$2 ;; --disk) disk=$2 ;; --outputs) outputs=$2 ;; --memory) memory=$2 ;; esac
            shift 2 ;;
        --no-cd) no_cd=1; shift ;;
        --usb) usb=1; shift ;;
        --offline) offline=1; shift ;;
        --help) echo 'Usage: run-iso.sh --iso FILE [--usb] [--offline] [--disk FILE] [--no-cd] [--outputs 1|2] [--memory 6G] [--ssh-port 2223] [--dir DIR]'; exit 0 ;;
        *) iso_die "unknown option: $1" ;;
    esac
done
[[ $outputs == 1 || $outputs == 2 ]] || iso_die '--outputs must be 1 or 2'
[[ $memory =~ ^[1-9][0-9]*G$ ]] || iso_die '--memory must be a positive whole number of GiB (for example 4G)'
((usb == 0 || no_cd == 0)) || iso_die '--usb requires a live ISO; incompatible with --no-cd'
if ((no_cd == 0)); then
    [[ -n $iso && -r $iso ]] || iso_die '--iso must name a readable ISO (or use --no-cd)'
    iso=$(realpath -- "$iso")
fi
disk=$(realpath -m -- "${disk:-$ISO_VM/target.qcow2}")
# QEMU drive options use commas as separators; reject ambiguous filenames.
[[ $disk != *,* && $ISO_VM != *,* && $iso != *,* ]] || iso_die 'QEMU paths must not contain commas'
for command in qemu-system-x86_64 qemu-img; do command -v "$command" >/dev/null || iso_die "missing $command"; done
code=/usr/share/edk2/x64/OVMF_CODE.4m.fd
vars=/usr/share/edk2/x64/OVMF_VARS.4m.fd
[[ -r $code && -r $vars && -r /dev/kvm && -w /dev/kvm ]] || iso_die 'OVMF or KVM unavailable'
[[ -r /dev/dri/renderD128 && -w /dev/dri/renderD128 ]] || iso_die 'renderD128 unavailable'
mkdir -p -- "$ISO_VM" "$(dirname -- "$disk")"
exec 9>"$ISO_VM/run.lock"
flock -n 9 || iso_die 'this ISO VM is already running'
if [[ -e $ISO_VM/qemu.pid ]]; then
    pid=$(<"$ISO_VM/qemu.pid")
    [[ $pid =~ ^[1-9][0-9]{0,9}$ ]] || iso_die 'invalid existing pidfile'
    [[ ! -e /proc/$pid ]] || iso_die 'PID is still alive; use iso-stop.sh first'
    rm -- "$ISO_VM/qemu.pid"
fi
if ((no_cd)) && [[ ! -f $disk ]]; then iso_die '--no-cd requires an existing installed disk'; fi
[[ -f $disk ]] || qemu-img create -f qcow2 "$disk" 40G
[[ -f $ISO_VM/OVMF_VARS.4m.fd ]] || cp -- "$vars" "$ISO_VM/OVMF_VARS.4m.fd"
printf '%s\n' "$ISO_PORT" >"$ISO_VM/ssh-port"
socket_runtime=$(python3 "$ISO_HERE/socket_runtime.py" prepare "$ISO_VM")
cd_args=(-boot order=c)
if ((no_cd == 0)); then
    # The live root generates fresh SSH host keys on each boot. Keep trust for
    # this installation cycle, including subsequent no-CD boots of its target.
    date +%s-%N >"$ISO_VM/ssh-host-generation"
    cd_args=(-cdrom "$iso" -boot order=dc)
    if ((usb)); then
        cd_args=(-device qemu-xhci -drive "if=none,id=liveiso,format=raw,readonly=on,file=$iso"
                 -device 'usb-storage,drive=liveiso,bootindex=1')
    fi
fi
network="user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:$ISO_PORT-:22"
((offline == 0)) || network+=",restrict=on"
# discard=unmap passes the guest's discards to the image, as a disk would: without it,
# mkfs.btrfs's whole-device discard left no superblock on a formatted partition (VM check d3).
exec qemu-system-x86_64 -machine q35 -enable-kvm -cpu host -smp 6 -m "$memory" \
    -drive "if=pflash,format=raw,readonly=on,file=$code" \
    -drive "if=pflash,format=raw,file=$ISO_VM/OVMF_VARS.4m.fd" \
    -drive "file=$disk,if=none,id=target,format=qcow2,discard=unmap" \
    -device virtio-blk-pci,drive=target,serial=emaki-target "${cd_args[@]}" \
    -nic "$network" \
    -device "virtio-vga-gl,max_outputs=$outputs" -display egl-headless,rendernode=/dev/dri/renderD128 \
    -monitor "unix:$socket_runtime/mon.sock,server=on,wait=off" \
    -vnc "unix:$socket_runtime/vnc.sock" \
    -serial "file:$ISO_VM/serial.log" -pidfile "$ISO_VM/qemu.pid"
