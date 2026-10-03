#!/usr/bin/env bash
set -Eeuo pipefail
# shellcheck source=tests/vm/iso-common.sh
source "$(dirname -- "$(readlink -f -- "$0")")/iso-common.sh"
iso_parse "$@"
((${#ISO_ARGS[@]} == 0)) || iso_die 'Usage: iso-stop.sh [--dir DIR]'
[[ -e $ISO_VM/qemu.pid ]] || { echo 'OK: no VM pidfile'; exit 0; }
pid=$(<"$ISO_VM/qemu.pid")
[[ $pid =~ ^[1-9][0-9]{0,9}$ ]] || iso_die 'invalid pidfile; VM untouched'
if [[ -e /proc/$pid ]]; then
    [[ $(readlink -- "/proc/$pid/exe") == */qemu-system-x86_64 ]] || iso_die 'PID is not QEMU; untouched'
    python3 "$ISO_HERE/iso-monitor.py" --dir "$ISO_VM" quit >"$ISO_VM/stop-vm.log" 2>&1 || iso_die 'monitor quit failed; see stop-vm.log'
    if timeout 180 tail --pid="$pid" -f /dev/null >>"$ISO_VM/stop-vm.log" 2>&1; then :
    else iso_die 'QEMU did not exit within 180s; disk untouched'; fi
fi
rm -f -- "$ISO_VM/qemu.pid" "$ISO_VM/mon.sock"
echo 'OK: QEMU stopped'
