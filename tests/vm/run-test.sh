#!/bin/bash
# F0: test Emaki on a clean VM with one command.
#   tests/vm/run-test.sh [ref] [--greeter-only]
#   --greeter-only requires an explicit ref; it leaves the guest at the login screen.
# Restore the disk to the “clean” snapshot, install Emaki from the committed repo state
# (not the working directory), reboot the guest, and take screenshots. Output: $VM/runs/<date>/.
set -Eeuo pipefail
# ERR is inherited by functions and command substitutions. Diagnostics go to
# stderr and, once available, the run summary; they never contaminate captured
# command output. Disable the trap inside itself so a full disk cannot recurse.
on_error() {
    local status=$1 line=$2 command=$3 message
    trap - ERR
    set +e
    message="ERROR: line $line, rc=$status: $command"
    printf '[%s] %s\n' "$(date +%T)" "$message" >&2
    if [ -n "${OUT:-}" ] && [ -d "$OUT" ]; then
        printf '[%s] %s\n' "$(date +%T)" "$message" >> "$OUT/summary.txt"
    fi
    exit "$status"
}
trap 'on_error "$?" "$LINENO" "$BASH_COMMAND"' ERR
usage_error() {
    echo "$1" >&2
    echo "Usage: tests/vm/run-test.sh [ref] [--greeter-only]" >&2
    echo "--greeter-only requires an explicit committed ref before the option." >&2
    exit 2
}
# Validate all arguments and the revision before mkdir, stopping QEMU, reverting
# its disk or uploading anything. In particular a bare option is never a ref.
[ "$#" -le 2 ] || usage_error "Too many arguments"
REF=${1:-HEAD}
GREETER_ONLY=${2:-}
case "$REF" in ""|-*) usage_error "Missing or invalid revision: $REF" ;; esac
case "$GREETER_ONLY" in ""|--greeter-only) ;; *) usage_error "Unknown option: $GREETER_ONLY" ;; esac
HERE=$(dirname "$(readlink -f "$0")")
REPO=$(git -C "$HERE" rev-parse --show-toplevel)
git -C "$REPO" rev-parse --verify "$REF^{commit}" >/dev/null 2>&1 || usage_error "Unknown committed revision: $REF"
VM=${EMAKI_VM_DIR:-$HOME/VMs/emaki-vm}
OUT=$VM/runs/$(date +%F-%H%M%S)
mkdir -p "$OUT"
log() { echo "[$(date +%T)] $*" | tee -a "$OUT/summary.txt"; }
fail() {
    local status=$1 line=${BASH_LINENO[0]}
    shift
    log "ERROR: line $line, rc=$status: $*" >&2
    exit "$status"
}

summarize() {
    local pattern=$1 source=$2 matches status
    if matches=$(grep "$pattern" "$source"); then
        printf '%s\n' "$matches" | tee -a "$OUT/summary.txt"
    else
        status=$?
        if [ "$status" -eq 1 ]; then
            # This grep only selects summary lines. The original full log and
            # the checked command status remain authoritative.
            log "WARNING: summary grep found no matches in $source (rc=1); retaining the full log"
        else
            fail "$status" "summary grep failed for $source"
        fi
    fi
}

stop_vm() {
    local pid status
    if [ ! -e "$VM/qemu.pid" ]; then
        log "No VM PID file; nothing to stop"
        return 0
    fi
    if pid=$(cat "$VM/qemu.pid"); then
        :
    else
        fail "$?" "cannot read VM PID file $VM/qemu.pid"
    fi
    if [[ ! "$pid" =~ ^[1-9][0-9]*$ ]] || [ "${#pid}" -gt 10 ]; then
        fail 2 "invalid PID in $VM/qemu.pid; VM untouched"
    fi
    if [ -e "/proc/$pid" ]; then
        log "Requesting graceful QEMU quit for PID $pid; waiting up to 180 seconds"
        if printf 'quit\n' | socat - UNIX-CONNECT:"$VM/mon.sock" >>"$OUT/stop-vm.log" 2>&1; then
            :
        else
            fail "$?" "QEMU monitor quit request failed; see $OUT/stop-vm.log"
        fi
        if timeout 180 tail --pid="$pid" -f /dev/null >>"$OUT/stop-vm.log" 2>&1; then
            log "QEMU PID $pid exited"
        else
            status=$?
            if [ "$status" -eq 124 ]; then
                fail "$status" "QEMU PID $pid did not exit within 180 seconds (timeout rc=124); disk untouched; see $OUT/stop-vm.log"
            fi
            fail "$status" "waiting for QEMU PID $pid failed; disk untouched; see $OUT/stop-vm.log"
        fi
    else
        log "VM PID $pid is no longer running; removing its stale PID file"
    fi
    rm -f "$VM/qemu.pid" || fail "$?" "cannot remove stopped VM PID file"
}

start_vm() {
    setsid "$HERE/run.sh" >>"$OUT/qemu.log" 2>&1 </dev/null &
    if "$HERE/wait-ssh.sh" >>"$OUT/summary.txt" 2>&1; then
        :
    else
        fail "$?" "VM did not become reachable; see $OUT/qemu.log and $OUT/summary.txt"
    fi
}

shot() {
    local result
    if result=$("$HERE/shot.sh" "$OUT/$1.png"); then
        log "$result"
    else
        fail "$?" "screenshot $1 failed"
    fi
}

REF_LABEL=$(git -C "$REPO" rev-parse --short "$REF")
REF_SUBJECT=$(git -C "$REPO" log -1 --format=%s "$REF")
log "ref: $REF_LABEL ($REF_SUBJECT)"
stop_vm
if qemu-img snapshot -a clean "$VM/base.qcow2"; then
    :
else
    fail "$?" "could not restore the clean VM snapshot"
fi
start_vm

# The bundle carries the branch but no HEAD, so fetch and checkout the desired branch in the guest
# instead of cloning (clone without HEAD leaves an empty directory).
if git -C "$REPO" bundle create "$OUT/emaki.bundle" "$REF" >"$OUT/bundle.log" 2>&1; then
    :
else
    fail "$?" "could not create the committed-revision bundle; see $OUT/bundle.log"
fi
HEADREF=$(git -C "$REPO" bundle list-heads "$OUT/emaki.bundle" | awk 'NR==1 {print $2}')
[ -n "$HEADREF" ] || fail 1 "committed-revision bundle has no head"
"$HERE/ssh.sh" 'cat > emaki.bundle' < "$OUT/emaki.bundle"
"$HERE/ssh.sh" "git init -q -b main emaki && cd emaki && git fetch -q ../emaki.bundle '$HEADREF' \
    && git checkout -q FETCH_HEAD && git log --oneline -1" >>"$OUT/summary.txt" 2>&1

log "installing"
if "$HERE/ssh.sh" 'bash -s' < "$HERE/guest-install.sh" > "$OUT/install.log" 2>&1; then
    INSTALL_STATUS=0
else
    INSTALL_STATUS=$?
fi
summarize '^STEP\|^greetd\|^niri\|^emaki\|^enable-services\|^NetworkManager\|^bluetooth\|^systemd-networkd\|^iwd' "$OUT/install.log"
[ "$INSTALL_STATUS" -eq 0 ] || fail "$INSTALL_STATUS" "guest install: failed steps (see install.log)"

log "rebooting"
OLD_BOOT=$("$HERE/ssh.sh" 'cat /proc/sys/kernel/random/boot_id')
if "$HERE/ssh.sh" 'sudo systemctl reboot' >/dev/null 2>&1; then
    :
else
    REBOOT_STATUS=$?
    [ "$REBOOT_STATUS" -eq 255 ] || fail "$REBOOT_STATUS" "guest reboot request failed"
    log "SSH disconnected during guest reboot (rc=255); verifying the new boot ID"
fi
sleep 5
if "$HERE/wait-ssh.sh" >>"$OUT/summary.txt" 2>&1; then
    :
else
    REBOOT_STATUS=$?
    log "SSH unavailable after reboot (rc=$REBOOT_STATUS); attempting failure screenshot"
    if BOOT_SHOT=$("$HERE/shot.sh" "$OUT/boot-failed.png"); then
        log "$BOOT_SHOT"
    else
        log "Failure screenshot unavailable (rc=$?)"
    fi
    fail "$REBOOT_STATUS" "guest did not return after reboot"
fi
NEW_BOOT=$("$HERE/ssh.sh" 'cat /proc/sys/kernel/random/boot_id')
[ "$NEW_BOOT" != "$OLD_BOOT" ] || fail 1 "guest boot ID did not change"
sleep 15
"$HERE/ssh.sh" 'echo "greetd: $(systemctl is-active greetd)"; echo "bluetooth: $(systemctl is-active bluetooth)"; echo "NetworkManager: $(systemctl is-active NetworkManager)"; systemctl --failed --no-legend; journalctl -b -u greetd --no-pager | tail -15' >> "$OUT/after-boot.log" 2>&1
summarize '^greetd\|^bluetooth\|^NetworkManager' "$OUT/after-boot.log"
shot after-boot
# C9 leaves the installed guest at the greeter for the host UI acceptance harness.
if [ "$GREETER_ONLY" = --greeter-only ]; then
    log "C9: ready for python3 tests/vm/check-greeter.py --output $OUT/greeter"
else
    log "logging in to Niri"
    "$HERE/login.sh" "$OUT" | tee -a "$OUT/summary.txt"
fi
log "RESULT: SCRIPTS PASSED: $OUT"
