#!/bin/bash
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
# Start the Emaki test VM without a window (F0); sockets use a private /tmp directory.
# ssh:     ssh -p 2222 -i "$EMAKI_VM_DIR/id_vm" arch@127.0.0.1
# screenshot: EMAKI_VM_DIR="$EMAKI_VM_DIR" tests/vm/shot.sh shot.png
set -e
VM=${EMAKI_VM_DIR:-$HOME/VMs/emaki-vm}
# EMAKI_VM_OUTPUTS=2 exposes a second virtio output for lock hotplug acceptance.
outputs=${EMAKI_VM_OUTPUTS:-1}
case "$outputs" in 1|2) ;; *) echo "EMAKI_VM_OUTPUTS must be 1 or 2" >&2; exit 2 ;; esac
socket_runtime=$(python3 "$(dirname -- "$(readlink -f -- "$0")")/socket_runtime.py" prepare "$VM")
exec qemu-system-x86_64 -enable-kvm -cpu host -smp 8 -m 12G \
    -drive file="$VM/base.qcow2",if=virtio,format=qcow2,discard=unmap \
    -nic user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:2222-:22 \
    -device virtio-vga-gl,max_outputs="$outputs" -display egl-headless,rendernode=/dev/dri/renderD128 \
    -monitor unix:"$socket_runtime/mon.sock",server,nowait \
    -vnc "unix:$socket_runtime/vnc.sock" \
    -serial file:"$VM/serial.log" \
    -pidfile "$VM/qemu.pid"
