#!/usr/bin/env bash
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Interrupted-publish drill, channel testing only.
# A scratch pacman syncs <url>/testing/x86_64 over and over and logs every exit code, while
# publish.sh is killed (SIGKILL, no cleanup) after each of steps 1-6 and re-run each time; then
# a second publisher must stop on the lock. Pass = zero failing syncs over the whole drill, the
# client ends on the database the channel serves, the second publisher was refused, and no
# GnuPG daemon started for the drill is left running.
#
#   tests/publish-drill.sh --packages DIR [--url https://pkgs.emaki.sh] [--log FILE] \
#       [--keyring FILE --trusted FILE] [--interval SECONDS] [-- publish.sh options]
#
# Against the real bucket it needs the R2 token of the package bucket and the signing key.
# Every run publishes DIR to testing seven times (identical packages, new snapshots).
set -Eeuo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(dirname -- "$HERE")
url=https://pkgs.emaki.sh
packages='' log='' interval=1
keyring=$ROOT/packaging/emaki-keyring/emaki.gpg
trusted=$ROOT/packaging/emaki-keyring/emaki-trusted
publish_options=()
fail() { printf 'DRILL ERROR: %s\n' "$*" >&2; exit 2; }
pause() { sleep "$(awk -v i="$interval" -v n="$1" 'BEGIN { print i * n }')"; }
while (($#)); do
    case $1 in
        --packages|--url|--log|--keyring|--trusted|--interval)
            (($# >= 2)) || fail "missing value for $1"
            case $1 in
                --packages) packages=$2 ;; --url) url=${2%/} ;; --log) log=$2 ;;
                --keyring) keyring=$2 ;; --trusted) trusted=$2 ;; --interval) interval=$2 ;;
            esac
            shift 2 ;;
        --) shift; publish_options=("$@"); break ;;
        *) fail "unknown option: $1" ;;
    esac
done
[[ -d $packages ]] || fail '--packages DIR with signed packages is required'
for tool in fakeroot pacman pacman-key python3 curl awk; do command -v "$tool" >/dev/null || fail "missing $tool"; done
work=$(mktemp -d "${TMPDIR:-/tmp}/emaki-drill.XXXXXX")
log=${log:-$work/drill.log}
client_pid=''
stop_client_gnupg() {
    # pacman-key ran under fakeroot, whose uid 0 has no /run/user/0/gnupg: the keyring holds its
    # GnuPG sockets itself, and only gpgconf under fakeroot finds them (a plain one exits 0).
    fakeroot -- gpgconf --homedir "$work/client/gnupg" --kill all 2>/dev/null || true
}
# shellcheck disable=SC2329  # invoked by the EXIT trap
cleanup() {
    if [[ -n $client_pid ]]; then kill "$client_pid" 2>/dev/null || true; wait "$client_pid" 2>/dev/null || true; fi
    stop_client_gnupg
    rm -rf -- "$work/client" "$work/t"
}
trap cleanup EXIT
# The publishers make their temporary homes under TMPDIR: here, so that what the killed runs
# leave is counted at the end and removed with the client. Short: sun_path is 108 bytes.
export TMPDIR=$work/t
mkdir -- "$TMPDIR"

# The client: an installed machine's view of [emaki], kept between syncs.
client=$work/client
mkdir -p -- "$client"/{root,db,cache,hooks}
cat >"$client/pacman.conf" <<CONF
[options]
RootDir = $client/root
DBPath = $client/db
CacheDir = $client/cache
LogFile = $client/pacman.log
GPGDir = $client/gnupg
HookDir = $client/hooks
Architecture = x86_64
SigLevel = Required
[emaki]
Server = $url/testing/x86_64
CONF
fakeroot -- pacman-key --config "$client/pacman.conf" --gpgdir "$client/gnupg" --init >/dev/null 2>&1
fakeroot -- pacman-key --config "$client/pacman.conf" --gpgdir "$client/gnupg" --add "$keyring" >/dev/null 2>&1
while IFS=: read -r fingerprint _; do
    [[ -n $fingerprint ]] || continue
    fakeroot -- pacman-key --config "$client/pacman.conf" --gpgdir "$client/gnupg" --lsign-key "$fingerprint" >/dev/null 2>&1
done <"$trusted"

client_loop() {
    local code
    while :; do
        code=0
        LC_ALL=C fakeroot -- pacman --config "$client/pacman.conf" -Sy >"$work/sync.out" 2>&1 || code=$?
        printf '%s sync %s\n' "$(date -u +%H:%M:%S.%N | cut -c1-12)" "$code" >>"$log"
        if ((code)); then sed 's/^/    /' "$work/sync.out" >>"$log"; fi
        pause 1
    done
}

publish() {
    python3 "$ROOT/packaging/mirror/publish.py" "${publish_options[@]}" "$@"
}

printf 'drill against %s/testing/x86_64, %s\n' "$url" "$(date -u +%FT%TZ)" >"$log"
publish status >>"$log" 2>&1 || fail 'publish.sh status failed; see the log'
# Start from a channel that serves a complete release, as it will on the day of the drill.
publish publish testing "$packages" >>"$log" 2>&1 || fail 'the initial publish failed; see the log'
client_loop &
client_pid=$!
pause 1
for step in 1 2 3 4 5 6; do
    printf '%s kill after step %s\n' "$(date -u +%H:%M:%S)" "$step" >>"$log"
    code=0
    EMAKI_PUBLISH_TEST_KILL_AFTER=$step publish publish testing "$packages" >>"$log" 2>&1 || code=$?
    ((code == 137)) || fail "publish was expected to die after step $step (exit $code); see $log"
    pause 3
    printf '%s re-run\n' "$(date -u +%H:%M:%S)" >>"$log"
    publish publish testing "$packages" >>"$log" 2>&1 || fail "re-run after step $step failed; see $log"
    pause 2
done

printf '%s double start\n' "$(date -u +%H:%M:%S)" >>"$log"
code=0
EMAKI_PUBLISH_TEST_KILL_AFTER=1 publish publish testing "$packages" >>"$log" 2>&1 || code=$?
((code == 137)) || fail "the first publisher was expected to die holding the lock (exit $code)"
code=0
publish --state-dir "$work/second-publisher" publish testing "$packages" >"$work/second.out" 2>&1 || code=$?
cat "$work/second.out" >>"$log"
second_refused=0
if ((code == 2)) && grep -q 'locks/publish is held' "$work/second.out"; then second_refused=1; fi
publish publish testing "$packages" >>"$log" 2>&1 || fail 'finishing the first publisher failed'
pause 2
kill "$client_pid"; wait "$client_pid" 2>/dev/null || true; client_pid=''

# The client's last database must be the one the channel serves now.
LC_ALL=C fakeroot -- pacman --config "$client/pacman.conf" -Sy >>"$log" 2>&1 || true
served=$(curl -fsSL "$url/testing/x86_64/emaki.db" | sha256sum | cut -d' ' -f1)
held=$(sha256sum "$client/db/sync/emaki.db" | cut -d' ' -f1)
syncs=$(grep -c ' sync ' "$log" || true)
failed=$(grep -c ' sync [1-9]' "$log" || true)
# Nothing GnuPG started for the drill (the client keyring, the publishers) may still run.
stop_client_gnupg
daemons_left=0
python3 "$HERE/gnupg-daemons.py" "$work" | tee -a "$log" || daemons_left=1
{
    printf 'RESULT syncs=%s failed=%s second_publisher_refused=%s client_on_served_db=%s gnupg_daemons_left=%s\n' \
        "$syncs" "$failed" "$second_refused" "$([[ $served == "$held" ]] && echo yes || echo no)" "$daemons_left"
} | tee -a "$log"
if ((failed == 0 && syncs > 0 && second_refused == 1 && daemons_left == 0)) && [[ $served == "$held" ]]; then
    echo "PASS: log in $log"
    exit 0
fi
echo "FAIL: log in $log"
exit 1
