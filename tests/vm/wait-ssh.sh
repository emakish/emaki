#!/bin/bash
# Wait until key-based login to the VM is available (up to ~3 minutes).
# Do not use ssh-keyscan: it disconnects before login, and OpenSSH ≥ 9.8 (PerSourcePenalties)
# bans the host address for minutes after repeated disconnects — one run waited 7.5 minutes.
VM=${EMAKI_VM_DIR:-$HOME/VMs/emaki-vm}
for i in $(seq 1 60); do
    if ssh -p 2222 -i "$VM/id_vm" -o UserKnownHostsFile="$VM/known_hosts" -o BatchMode=yes \
        -o ConnectTimeout=3 arch@127.0.0.1 true 2>/dev/null; then
        echo "ssh up after ~$((i * 3))s"
        exit 0
    fi
    sleep 3
done
echo "ssh not up"
tail -5 "$VM/serial.log"
exit 1
