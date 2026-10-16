#!/usr/bin/env bash
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
# Start in the foreground; use setsid/backgrounding as documented in docs/iso.md.
set -Eeuo pipefail
# shellcheck source=tests/vm/iso-common.sh
source "$(dirname -- "$(readlink -f -- "$0")")/iso-common.sh"
iso_parse "$@"
iso='' disk='' disk2='' outputs=1 no_cd=0 usb=0 offline=0 memory=6G
set -- "${ISO_ARGS[@]}"
while (($#)); do
    case $1 in
        --iso|--disk|--disk2|--outputs|--memory)
            (($# >= 2)) || iso_die "missing value for $1"
            case $1 in --iso) iso=$2 ;; --disk) disk=$2 ;; --disk2) disk2=$2 ;; --outputs) outputs=$2 ;; --memory) memory=$2 ;; esac
            shift 2 ;;
        --no-cd) no_cd=1; shift ;;
        --usb) usb=1; shift ;;
        --offline) offline=1; shift ;;
        --help) echo 'Usage: run-iso.sh --iso FILE [--usb] [--offline] [--disk FILE] [--disk2 FILE] [--no-cd] [--outputs 1|2] [--memory 6G] [--ssh-port 2223] [--dir DIR]'; exit 0 ;;
        *) iso_die "unknown option: $1" ;;
    esac
done
# The lock comes first: iso-stop.sh and iso-owner.py know this directory's launcher by it from
# here on, and QEMU inherits it through exec.
mkdir -p -- "$ISO_VM"
exec 9>"$ISO_VM/run.lock"
flock -n 9 || iso_die 'this ISO VM is already running'
[[ $outputs == 1 || $outputs == 2 ]] || iso_die '--outputs must be 1 or 2'
[[ $memory =~ ^[1-9][0-9]*G$ ]] || iso_die '--memory must be a positive whole number of GiB (for example 4G)'
((usb == 0 || no_cd == 0)) || iso_die '--usb requires a live ISO; incompatible with --no-cd'
if ((no_cd == 0)); then
    [[ -n $iso && -r $iso ]] || iso_die '--iso must name a readable ISO (or use --no-cd)'
    iso=$(realpath -- "$iso")
fi
# EMAKI_VM_SECBOOT=2011|2023|both|none: the Secure Boot firmware (SMM, secure flash) with the
# variable store of that db variant, written by secboot-vars.sh into EMAKI_VM_SECBOOT_VARS.
secboot=${EMAKI_VM_SECBOOT:-}
machine=q35 secure_flash=()
if [[ -n $secboot ]]; then
    [[ $secboot =~ ^(2011|2023|both|none)$ ]] || iso_die 'EMAKI_VM_SECBOOT must be 2011, 2023, both or none'
    machine=q35,smm=on secure_flash=(-global 'driver=cfi.pflash01,property=secure,value=on')
fi
disk=$(realpath -m -- "${disk:-$ISO_VM/target.qcow2}")
# --disk2: a second, existing disk after the target (serial emaki-disk2), e.g. a Windows fixture
# made as the target of another run, next to a disk Emaki erases.
disk2_args=()
if [[ -n $disk2 ]]; then
    [[ -f $disk2 ]] || iso_die '--disk2 must name an existing disk image'
    disk2=$(realpath -- "$disk2")
    disk2_args=(-drive "file=$disk2,if=none,id=disk2,format=qcow2,discard=unmap"
                -device 'virtio-blk-pci,drive=disk2,serial=emaki-disk2')
fi
# QEMU drive options use commas as separators; reject ambiguous filenames.
[[ $disk != *,* && $disk2 != *,* && $ISO_VM != *,* && $iso != *,* ]] || iso_die 'QEMU paths must not contain commas'
for command in qemu-system-x86_64 qemu-img; do command -v "$command" >/dev/null || iso_die "missing $command"; done
# Firmware and the render node can live elsewhere (another distribution, a fixture). QEMU opens
# the KVM device itself; EMAKI_ISO_KVM_DEVICE only moves this check, for the fake-QEMU tests.
firmware=${EMAKI_ISO_OVMF_DIR:-/usr/share/edk2/x64}
kvm=${EMAKI_ISO_KVM_DEVICE:-/dev/kvm}
render=${EMAKI_ISO_RENDER_NODE:-/dev/dri/renderD128}
code=$firmware/OVMF_CODE.4m.fd
vars=$firmware/OVMF_VARS.4m.fd
if [[ -n $secboot ]]; then
    code=$firmware/OVMF_CODE.secboot.4m.fd
    vars=${EMAKI_VM_SECBOOT_VARS:-$ISO_HERE/../../.cache/secboot-vars}/OVMF_VARS.secboot-$secboot.4m.fd
    [[ -r $vars ]] || iso_die "missing $vars (write it with tests/vm/secboot-vars.sh)"
fi
[[ $code != *,* && $vars != *,* && $render != *,* ]] || iso_die 'QEMU paths must not contain commas'
[[ -r $code && -r $vars && -r $kvm && -w $kvm ]] || iso_die 'OVMF or KVM unavailable'
[[ -r $render && -w $render ]] || iso_die "render node $render unavailable"
mkdir -p -- "$(dirname -- "$disk")"
if [[ -e $ISO_VM/qemu.pid ]]; then
    pid=$(<"$ISO_VM/qemu.pid")
    [[ $pid =~ ^[1-9][0-9]{0,9}$ ]] || iso_die 'invalid existing pidfile'
    [[ ! -e /proc/$pid ]] || iso_die 'PID is still alive; use iso-stop.sh first'
    rm -- "$ISO_VM/qemu.pid"
fi
# A QEMU of another directory can still hold this port; its guest would answer every SSH step.
python3 "$ISO_HERE/iso-owner.py" port-free "$ISO_PORT" \
    || iso_die "127.0.0.1:$ISO_PORT is already in use (another VM?); stop it first or choose --ssh-port"
if ((no_cd)) && [[ ! -f $disk ]]; then iso_die '--no-cd requires an existing installed disk'; fi
[[ -f $disk ]] || qemu-img create -f qcow2 "$disk" 40G
# The directory's variable store keeps what the guest wrote (boot entries, an enrolled MOK) and
# stays with the firmware it was made for: a plain store is never started as a Secure Boot one.
firmware_kind=${secboot:+secboot-$secboot}
firmware_kind=${firmware_kind:-plain}
if [[ -f $ISO_VM/OVMF_VARS.4m.fd ]]; then
    made_for=plain
    [[ ! -f $ISO_VM/firmware ]] || made_for=$(<"$ISO_VM/firmware")
    [[ $made_for == "$firmware_kind" ]] \
        || iso_die "$ISO_VM/OVMF_VARS.4m.fd belongs to $made_for firmware, not $firmware_kind"
else
    cp -- "$vars" "$ISO_VM/OVMF_VARS.4m.fd"
    printf '%s\n' "$firmware_kind" >"$ISO_VM/firmware"
fi
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
exec qemu-system-x86_64 -machine "$machine" -enable-kvm -cpu host -smp 6 -m "$memory" "${secure_flash[@]}" \
    -drive "if=pflash,format=raw,readonly=on,file=$code" \
    -drive "if=pflash,format=raw,file=$ISO_VM/OVMF_VARS.4m.fd" \
    -drive "file=$disk,if=none,id=target,format=qcow2,discard=unmap" \
    -device virtio-blk-pci,drive=target,serial=emaki-target "${disk2_args[@]}" "${cd_args[@]}" \
    -nic "$network" \
    -device "virtio-vga-gl,max_outputs=$outputs" -display "egl-headless,rendernode=$render" \
    -monitor "unix:$socket_runtime/mon.sock,server=on,wait=off" \
    -vnc "unix:$socket_runtime/vnc.sock" \
    -serial "file:$ISO_VM/serial.log" -pidfile "$ISO_VM/qemu.pid"
