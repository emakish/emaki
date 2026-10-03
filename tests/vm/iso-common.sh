#!/usr/bin/env bash
# Shared by the ISO-only harness. This file does not start or stop a VM.
# Values are consumed by the scripts sourcing this file.
# shellcheck disable=SC2034
set -Eeuo pipefail
ISO_HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ISO_VM=${EMAKI_ISO_VM_DIR:-$HOME/VMs/iso-vm}
ISO_PORT=${EMAKI_ISO_SSH_PORT:-}
ISO_USER=live
ISO_KEY=${EMAKI_ISO_SSH_KEY:-$HOME/VMs/emaki-vm/id_vm}
ISO_ARGS=()
iso_die() { printf 'BAD: %s\n' "$*" >&2; exit 1; }
iso_parse() {
    while (($#)); do
        case $1 in
            --dir|--ssh-port|--user|--key)
                (($# >= 2)) || iso_die "missing value for $1"
                case $1 in
                    --dir) ISO_VM=$2 ;;
                    --ssh-port) ISO_PORT=$2 ;;
                    --user) ISO_USER=$2 ;;
                    --key) ISO_KEY=$2 ;;
                esac
                shift 2 ;;
            --) shift; ISO_ARGS+=("$@"); break ;;
            *) ISO_ARGS+=("$1"); shift ;;
        esac
    done
    ISO_VM=$(realpath -m -- "$ISO_VM")
    if [[ -z $ISO_PORT && -f $ISO_VM/ssh-port ]]; then ISO_PORT=$(<"$ISO_VM/ssh-port"); fi
    ISO_PORT=${ISO_PORT:-2223}
    if [[ ! $ISO_PORT =~ ^[0-9]{1,5}$ ]] || ((10#$ISO_PORT < 1 || 10#$ISO_PORT > 65535)); then
        iso_die 'invalid SSH port'
    fi
    [[ $ISO_USER =~ ^[a-z_][a-z0-9_-]*$ ]] || iso_die 'invalid SSH user'
}
iso_ssh_args() {
    [[ -r $ISO_KEY ]] || iso_die "SSH key unavailable: $ISO_KEY"
    mkdir -p -- "$ISO_VM"
    local generation=initial
    [[ ! -f $ISO_VM/ssh-host-generation ]] || generation=$(<"$ISO_VM/ssh-host-generation")
    [[ $generation =~ ^[a-z0-9-]+$ ]] || iso_die 'invalid SSH host generation'
    ISO_SSH=(ssh -p "$ISO_PORT" -i "$ISO_KEY" -o BatchMode=yes -o IdentitiesOnly=yes
        -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$ISO_VM/known_hosts-$ISO_USER-$generation"
        -o ConnectTimeout=3 -o ServerAliveInterval=15 "$ISO_USER@127.0.0.1")
}
