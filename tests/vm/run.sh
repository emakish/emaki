#!/bin/bash
# Start the Emaki test VM without a window (F0). Only changes files in $EMAKI_VM_DIR on the host.
# ssh:     ssh -p 2222 -i "$EMAKI_VM_DIR/id_vm" arch@127.0.0.1
# screenshot:  echo "screendump shot.png -f png" | socat - UNIX-CONNECT:"$EMAKI_VM_DIR/mon.sock"
VM=${EMAKI_VM_DIR:-$HOME/VMs/emaki-vm}
# EMAKI_VM_OUTPUTS=2 exposes a second virtio output for lock hotplug acceptance.
outputs=${EMAKI_VM_OUTPUTS:-1}
case "$outputs" in 1|2) ;; *) echo "EMAKI_VM_OUTPUTS must be 1 or 2" >&2; exit 2 ;; esac
exec qemu-system-x86_64 -enable-kvm -cpu host -smp 8 -m 12G \
    -drive file="$VM/base.qcow2",if=virtio,format=qcow2 \
    -nic user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:2222-:22 \
    -device virtio-vga-gl,max_outputs="$outputs" -display egl-headless,rendernode=/dev/dri/renderD128 \
    -monitor unix:"$VM/mon.sock",server,nowait \
    -serial file:"$VM/serial.log" \
    -pidfile "$VM/qemu.pid"
