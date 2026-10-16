# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
# Sourced by scripts/new-secureboot-release-key.sh and iso/secureboot-key-to-vm.sh: the rules for the
# publishing machine before the Secure Boot release key is unlocked in its memory (docs/signing.md
# section 5). Callers define fail(). EMAKI_PROC_ROOT and EMAKI_SYS_ROOT replace /proc and /sys in tests.
EMAKI_PROC=${EMAKI_PROC_ROOT:-/proc}
EMAKI_SYS=${EMAKI_SYS_ROOT:-/sys}

# Tracing would print commands and their data; refuse instead of trusting every line to hide secrets.
# Also refused: function tracing (bash -T, SHELLOPTS=functrace) and a startup file (BASH_ENV, ENV),
# which runs before this script and is the only way a DEBUG or RETURN trap reaches a new bash.
secret_no_trace() {
    local reason=''
    if [[ -o xtrace || -o verbose ]]; then
        reason='shell tracing (set -x or -v) is on and could print secrets'
    elif [[ -o functrace ]]; then
        reason='function tracing (bash -T or functrace) is on and could print secrets'
    elif [[ -n ${BASH_ENV:-} || -n ${ENV:-} ]]; then
        reason='a shell startup file (BASH_ENV or ENV) ran before this script and could trace it'
    fi
    if [[ -n $reason ]]; then
        printf 'ERROR: %s; run without it\n' "$reason" >&2
        exit 1
    fi
}

# Core dumps off (soft and hard) for this shell and every process it starts; RLIMIT_CORE survives
# exec, the dumpable flag does not, so this is the protection that reaches openssl and sbsign.
core_dumps_off() {
    { ulimit -S -c 0 && ulimit -H -c 0; } || fail 'cannot turn core dumps off for this process'
    [[ $(ulimit -S -c) == 0 && $(ulimit -H -c) == 0 ]] || fail 'core dumps are still on for this process'
}

# The core dump handler must honour that limit. Accepted, nothing else:
# - no pattern, or a file pattern (not starting with | or @): the kernel writes no core file under
#   RLIMIT_CORE 0 (core(5)); a socket pattern (@, @@) is refused: there the kernel lifts the limit;
# - systemd-coredump itself, given the crashing process's limit as %c in the place systemd reads it
#   (its 6th argument: %P|%p %u %g %s %t %c), followed only by further % specifiers; it stores no core
#   when that limit is 0 ("Resource limits disable core dumping"). Any other program, or a fixed
#   number instead of %c, is refused.
core_handler_check() {
    local pattern=$1 where=$2 words
    # '@' and '@@' hand the dump to a socket, where the kernel lifts the size limit (fs/coredump.c).
    [[ $pattern == @* ]] && fail "core dumps on $where go to the socket '${pattern#@}', where the zero core size limit does not apply; use systemd-coredump's standard pattern or a file pattern"
    [[ $pattern != '|'* ]] && return 0
    read -r -a words <<<"${pattern#|}"
    if [[ ${words[0]:-} == /usr/lib/systemd/systemd-coredump || ${words[0]:-} == /lib/systemd/systemd-coredump ]] \
        && [[ ${words[1]:-} == %[Pp] && ${words[2]:-} == %u && ${words[3]:-} == %g && ${words[4]:-} == %s \
            && ${words[5]:-} == %t && ${words[6]:-} == %c ]]; then
        local word
        for word in "${words[@]:7}"; do
            [[ $word =~ ^%[A-Za-z]$ ]] || fail "core dumps on $where go to systemd-coredump with an unexpected argument '$word'; use its standard pattern"
        done
        return 0
    fi
    fail "core dumps on $where go to '${pattern#|}', which is not known to honour the zero core size limit; use systemd-coredump's standard pattern or a file pattern"
}

# A sleep block held by this user while the key is in memory: hibernation, hybrid sleep and
# suspend-then-hibernate would write memory to the disk (logind ListInhibitors, its owner alive).
sleep_block_check() {
    local listing
    listing=$(busctl --json=short call org.freedesktop.login1 /org/freedesktop/login1 \
        org.freedesktop.login1.Manager ListInhibitors 2>/dev/null) \
        || fail 'cannot ask logind which sleep inhibitors are held'
    python3 -I -c '
import json, os, sys
proc, uid = sys.argv[1], int(sys.argv[2])
held = [pid for what, who, why, mode, owner, pid in json.loads(sys.stdin.read())["data"][0]
        if "sleep" in what.split(":") and mode == "block" and owner == uid and os.path.isdir(f"{proc}/{pid}")]
sys.exit(0 if held else 1)' "$EMAKI_PROC" "$(id -u)" <<<"$listing" \
        || fail "no sleep block is held: start one first (docs/signing.md): systemd-inhibit --what=sleep:idle:handle-lid-switch --mode=block --why='Emaki release key' sleep infinity"
}

# True when swap device or file NAME (TYPE from /proc/swaps) keeps its pages encrypted or in RAM.
swap_protected() {
    local name=$1 type=$2 device
    # zram keeps pages in RAM unless it writes them back to a backing device.
    if [[ $name =~ ^/dev/(zram[0-9]+)$ ]]; then
        # The device must be inspectable; no backing_dev file means a kernel without writeback.
        local block=$EMAKI_SYS/block/${BASH_REMATCH[1]} value
        [[ -d $block ]] || return 1
        [[ -e $block/backing_dev ]] || return 0
        value=$(<"$block/backing_dev") || return 1
        [[ $value == none ]]
        return
    fi
    if [[ $type == file ]]; then
        device=$(findmnt -n -o SOURCE --target "$name") || return 1
        device=${device%%\[*}
    else
        device=$name
    fi
    lsblk -s -n -o TYPE -- "$device" 2>/dev/null | grep -qx crypt
}

# Swap listed in the swaps file at $1 (this machine's or a copy of the VM's): none, zram or encrypted.
# A list that cannot be read or does not look like /proc/swaps fails: an inspection must succeed.
swaps_check() {
    local swaps=$1 where=$2 name type rest
    [[ $swaps == Filename* ]] || fail "cannot inspect the swap of $where"
    while read -r name type rest; do
        [[ $name == Filename || -z $name ]] && continue
        if [[ $where != 'this machine' ]]; then
            fail "swap $name is on in $where: the unlocked key could be written to its disk; turn it off there first (sudo swapoff -a)"
        fi
        swap_protected "$name" "$type" \
            || fail "swap $name on $where is on and not encrypted: the unlocked key could be written to a disk; turn it off for this step (sudo swapoff $name) and on again after (docs/signing.md)"
    done <<<"$swaps"
}

# This process and its children (openssl) never write a core dump; the dump handler must honour that.
host_memory_check() {
    local pattern swaps
    core_dumps_off
    pattern=$(<"$EMAKI_PROC/sys/kernel/core_pattern") || fail 'cannot read the core dump setting'
    core_handler_check "$pattern" 'this machine'
    swaps=$(<"$EMAKI_PROC/swaps") || fail 'cannot inspect the swap of this machine'
    swaps_check "$swaps" 'this machine'
    sleep_block_check
}
