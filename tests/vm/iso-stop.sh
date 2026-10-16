#!/usr/bin/env bash
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
set -Eeuo pipefail
# shellcheck source=tests/vm/iso-common.sh
source "$(dirname -- "$(readlink -f -- "$0")")/iso-common.sh"
iso_parse "$@"
stop_command=system_powerdown
if ((${#ISO_ARGS[@]} == 1)) && [[ ${ISO_ARGS[0]} == --force ]]; then
    stop_command=quit
elif ((${#ISO_ARGS[@]} != 0)); then
    iso_die 'Usage: iso-stop.sh [--dir DIR] [--force]'
fi
# The launcher locks run.lock before its first step and QEMU inherits it, so a stop at any moment
# of a start finds it, waits for its QEMU and stops that. Only a process holding this directory's
# run.lock open is touched (iso-owner.py).
set +e
pid=$(python3 "$ISO_HERE/iso-owner.py" find --dir "$ISO_VM" --wait 60)
found=$?
set -e
case $found in
    0) ;;
    1) rm -f -- "$ISO_VM/qemu.pid" "$ISO_VM/qemu.launch"
       python3 "$ISO_HERE/socket_runtime.py" cleanup "$ISO_VM" || echo "NOTE: the socket runtime of $ISO_VM was left in place" >&2
       echo 'OK: no VM of this directory is running'; exit 0 ;;
    *) iso_die 'run-iso.sh is still preparing after 60s; VM untouched' ;;
esac
# An exited QEMU that its parent has not reaped yet (a harness that started run-iso.sh as its
# child) is a zombie: still in /proc, but gone.
alive() {
    local stat
    stat=$(<"/proc/$pid/stat") 2>/dev/null || return 1
    stat=${stat##*) }
    [[ ${stat%% *} != Z ]]
}
: >"$ISO_VM/stop-vm.log"
requested=0
for ((attempt=0; attempt<20; attempt++)); do
    if python3 "$ISO_HERE/iso-monitor.py" --dir "$ISO_VM" "$stop_command" >>"$ISO_VM/stop-vm.log" 2>&1; then requested=1; break; fi
    alive || break
    sleep 0.5
done
if ((requested == 0)) && alive; then
    [[ $stop_command == quit ]] || iso_die 'monitor shutdown request failed; VM left running; see stop-vm.log'
    # The monitor of a QEMU that just started may not exist yet. SIGTERM makes QEMU exit as the
    # monitor's quit does: at once, without a guest shutdown.
    echo "monitor unavailable; sending SIGTERM to QEMU $pid" >>"$ISO_VM/stop-vm.log"
    kill -TERM "$pid" 2>>"$ISO_VM/stop-vm.log" || ! alive || iso_die 'could not signal QEMU; see stop-vm.log'
fi
for ((tick=0; tick<1800; tick++)); do alive || break; sleep 0.1; done
! alive || iso_die 'QEMU did not exit within 180s; VM left running. Use --force only for an intentional abrupt stop.'
# Stopped means nothing of this directory runs any more, its launcher included.
set +e
python3 "$ISO_HERE/iso-owner.py" find --dir "$ISO_VM" --wait 60 >/dev/null 2>&1
found=$?
set -e
((found == 1)) || iso_die "QEMU $pid exited, but a process of this directory still holds run.lock; see iso-owner.py find"
python3 "$ISO_HERE/socket_runtime.py" cleanup "$ISO_VM" || echo "NOTE: the socket runtime of $ISO_VM was left in place" >&2
rm -f -- "$ISO_VM/qemu.pid" "$ISO_VM/qemu.launch" "$ISO_VM/mon.sock"
echo 'OK: QEMU stopped'
