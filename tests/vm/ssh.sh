#!/bin/bash
# Run a command in the test VM: tests/vm/ssh.sh '<command>'
VM=${EMAKI_VM_DIR:-$HOME/VMs/emaki-vm}
exec ssh -p 2222 -i "$VM/id_vm" -o UserKnownHostsFile="$VM/known_hosts" -o BatchMode=yes \
    -o ServerAliveInterval=15 arch@127.0.0.1 "$@"
