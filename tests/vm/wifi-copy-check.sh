#!/usr/bin/env bash
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
# The installer keeps the Wi-Fi joined in the live session, proven with a real NetworkManager and a
# virtual radio (mac80211_hwsim + hostapd). One variant per run, in its own VM directory:
#   page   joined on the installer's Network page (its ui-helper, spawned in the session as the UI is)
#   panel  joined from the desktop panel (Quickshell's AddAndActivateConnection call)
#   --drop the access point goes away before the install, so no Wi-Fi is active when the worker starts
# Then the installed disk boots: the keyfile must hold the secret (psk-flags 0) and NetworkManager
# must connect by itself once the network is back.
# Test image only. --source installs this checkout's installer code into the live image for the run
# (a bind mount over the packaged module, then the worker restarts); without it the image's own runs.
# hostapd and dnsmasq come as signature-checked Arch packages given by path; they are unpacked under
# /tmp in the guest and never installed. Nothing on the host changes outside --dir.
# Commands in single quotes run in the guest.
# shellcheck disable=SC2016
set -Eeuo pipefail
umask 077
HERE=$(cd -- "$(dirname -- "$(readlink -f -- "$0")")" && pwd)
ROOT=$(dirname -- "$(dirname -- "$HERE")")
iso='' dir='' port='' variant='' drop=0 source=0 hostapd='' dnsmasq='' fixture=erase-btrfs
die() { printf 'BAD: %s\n' "$*" >&2; exit 1; }
while (($#)); do
    case $1 in
        --iso|--dir|--ssh-port|--variant|--hostapd|--dnsmasq|--fixture)
            (($# >= 2)) || die "missing value for $1"
            case $1 in
                --iso) iso=$2 ;; --dir) dir=$2 ;; --ssh-port) port=$2 ;; --variant) variant=$2 ;;
                --hostapd) hostapd=$2 ;; --dnsmasq) dnsmasq=$2 ;; --fixture) fixture=$2 ;;
            esac
            shift 2 ;;
        --drop) drop=1; shift ;;
        --source) source=1; shift ;;
        --help) echo 'Usage: wifi-copy-check.sh --iso TEST.iso --dir NEW_DIR --ssh-port PORT --variant page|panel [--drop] [--source] --hostapd PKG --dnsmasq PKG [--fixture erase-btrfs]'; exit 0 ;;
        *) die "unknown option: $1" ;;
    esac
done
[[ -r $iso && -n $dir && $port =~ ^[0-9]+$ && $variant =~ ^(page|panel)$ ]] || die 'see --help'
[[ -r $hostapd && -r $dnsmasq ]] || die 'hostapd and dnsmasq packages are required'
[[ $fixture =~ ^[a-z0-9-]+$ && -f $ROOT/installer/fixtures/plan-$fixture.json ]] || die "missing fixture $fixture"
[[ ! -e $dir ]] || die "$dir exists; every run gets a new directory"
mkdir -p -- "$dir/evidence"
dir=$(realpath -- "$dir") ev=$dir/evidence
exec > >(tee -a "$ev/check.log") 2>&1
echo "WIFI_CHECK variant=$variant drop=$drop source=$source fixture=$fixture iso=$iso"
live=(--dir "$dir/live" --ssh-port "$port")
installed=(--dir "$dir/live" --ssh-port "$port" --user "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["user"]["login"])' "$ROOT/installer/fixtures/plan-$fixture.json")")
gssh() { "$HERE/iso-ssh.sh" "$@"; }
stop() { "$HERE/iso-stop.sh" --dir "$dir/live" >/dev/null || true; }
trap stop EXIT
send() {  # send SSH_ARGS... -- : the helper and the two packages into /tmp/w1 of the guest
    gssh "$@" -- 'rm -rf /tmp/w1 && mkdir -m 700 /tmp/w1 && cat > /tmp/w1/guest.sh' <"$HERE/wifi-copy-guest.sh"
    gssh "$@" -- 'cat > /tmp/w1/hostapd.pkg.tar.zst' <"$hostapd"
    gssh "$@" -- 'cat > /tmp/w1/dnsmasq.pkg.tar.zst' <"$dnsmasq"
}
# A VM left running elsewhere can hold the port, and SSH alone cannot tell it from ours: the base
# install of 2026-10-10 landed in another directory's disk that way (GOTCHAS 2026-10-10).
start_vm() {  # start_vm LOG RUN_ISO_ARGS...: start QEMU, then prove it alone listens on the SSH port
    local log=$1 pid='' listeners='' line deadline=$((SECONDS + 60))
    shift
    [[ -z $(ss -Hltn "sport = :$port") ]] || die "port $port is in use; stop the VM that holds it"
    setsid -f "$HERE/run-iso.sh" "$@" "${live[@]}" >"$log" 2>&1 </dev/null
    until [[ -s $dir/live/qemu.pid ]] && listeners=$(ss -Hltnp "sport = :$port") && [[ -n $listeners ]]; do
        ((SECONDS < deadline)) || die "QEMU did not listen on port $port; see $log"
        sleep 1
    done
    pid=$(<"$dir/live/qemu.pid")
    [[ $pid =~ ^[1-9][0-9]{0,9}$ && $(readlink -- "/proc/$pid/exe") == */qemu-system-x86_64 ]] \
        || die 'the pidfile names no running QEMU'
    tr '\0' '\n' <"/proc/$pid/cmdline" | grep -qxF -- "file=$dir/live/target.qcow2,if=none,id=target,format=qcow2,discard=unmap" \
        || die "QEMU $pid does not run this directory's disk"
    while read -r line; do
        [[ $line == *"pid=$pid,"* ]] || die "another process listens on port $port: $line"
    done <<<"$listeners"
    echo "VM_OWNED qemu=$pid port=$port disk=$dir/live/target.qcow2"
}

# 1. The live image, with this checkout's worker if asked. Its empty disk starts with a random mark
# that the guest must read back before anything is written to it.
mark=emaki-wifi-check-$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')
mkdir -m 700 -- "$dir/live"
printf '%s\n' "$mark" >"$dir/mark.raw"
truncate -s 1M -- "$dir/mark.raw"
qemu-img convert -q -f raw -O qcow2 -- "$dir/mark.raw" "$dir/live/target.qcow2"
qemu-img resize -q -- "$dir/live/target.qcow2" 40G
rm -- "$dir/mark.raw"
start_vm "$ev/run-live.log" --iso "$iso"
"$HERE/iso-wait-ssh.sh" "${live[@]}" --user live
seen=$(gssh "${live[@]}" -- "sudo -n head -c ${#mark} /dev/disk/by-id/virtio-emaki-target" | tr -dc 'a-z0-9-')
[[ $seen == "$mark" ]] || die "the guest on port $port does not have this run's disk"
echo 'VM_DISK mark read back by the guest'
gssh "${live[@]}" -- 'grep -qw emaki.test=1 /proc/cmdline && test -d /run/archiso' || die 'not a test image'
gssh "${live[@]}" -- 'until systemctl is-active -q emaki-installerd && test -S /run/emaki-installer/sock; do sleep 1; done'
send "${live[@]}"
if ((source)); then
    tar -C "$ROOT/installer" --exclude=__pycache__ -cf - emaki_installer \
        | gssh "${live[@]}" -- 'mkdir /tmp/w1/src && tar -C /tmp/w1/src -xf -'
    gssh "${live[@]}" -- 'set -e
        packaged=$(python3 -c "import emaki_installer, os; print(os.path.dirname(emaki_installer.__file__))")
        sudo -n chown -R root:root /tmp/w1/src
        sudo -n mount --bind /tmp/w1/src/emaki_installer "$packaged"
        sudo -n systemctl restart emaki-installerd
        until systemctl is-active -q emaki-installerd && test -S /run/emaki-installer/sock; do sleep 1; done
        echo "SOURCE bound over $packaged: $(grep -c choose_wifi "$packaged/worker.py") choose_wifi references"'
fi

# 2. Join the network as a person does, then perhaps lose the link.
gssh "${live[@]}" -- 'bash /tmp/w1/guest.sh /tmp/w1 ap-up'
if [[ $variant == page ]]; then
    joined=$(gssh "${live[@]}" -- 'bash /tmp/w1/guest.sh /tmp/w1 join-page')
else
    joined=$(gssh "${live[@]}" -- 'bash /tmp/w1/guest.sh /tmp/w1 join-panel')
fi
echo "$joined"
uuid=$(python3 -c 'import json, sys; reply = json.loads(sys.argv[1].split(" ", 1)[1]); assert reply["ok"], reply; print(reply["wifi_uuid"])' "$joined")
((drop == 0)) || gssh "${live[@]}" -- 'bash /tmp/w1/guest.sh /tmp/w1 ap-down'
gssh "${live[@]}" -- 'bash /tmp/w1/guest.sh /tmp/w1 state' | tee "$ev/live-state.txt"

# 3. Install with the fixture. Only the Network page records its join in the plan, as the UI does.
python3 -c 'import json, sys
plan = json.load(open(sys.argv[1]))
if sys.argv[2] == "page":
    plan["wifi_uuid"] = sys.argv[3]
print(json.dumps(plan))' "$ROOT/installer/fixtures/plan-$fixture.json" "$variant" "$uuid" \
    | gssh "${live[@]}" -- 'umask 077; cat > "$HOME/emaki-plan.json"'
set +e
gssh "${live[@]}" -- 'emaki-install-cli --plan "$HOME/emaki-plan.json" --yes' >"$ev/install.ndjson" 2>"$ev/install.stderr"
status=$?
set -e
gssh "${live[@]}" -- 'rm -f "$HOME/emaki-plan.json"; sudo -n grep "Wi-Fi" /var/log/emaki-install.log' | tee "$ev/install-wifi.log"
tail -n 1 "$ev/install.ndjson"
((status == 0)) || die "installation failed ($status); see $ev/install.ndjson"
if gssh "${live[@]}" -- 'sudo -n grep -q "hwsim-test-pass" /var/log/emaki-install.log'; then die 'the install log holds the Wi-Fi password'; fi
grep -q 'hwsim-test-pass' "$ev/install.ndjson" "$ev/install.stderr" && die 'the installer output holds the Wi-Fi password'
stop
sleep 2

# 4. The installed system: keyfile facts, then autoconnect once the network is there.
start_vm "$ev/run-installed.log" --no-cd
"$HERE/iso-wait-ssh.sh" "${installed[@]}"
send "${installed[@]}"
# The installed account's sudo asks for the fixture's test-only password.
python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["user"]["password"])' "$ROOT/installer/fixtures/plan-$fixture.json" \
    | gssh "${installed[@]}" -- "sudo -S -p '' bash /tmp/w1/guest.sh /tmp/w1 installed $uuid" | tee "$ev/installed.txt"
grep -q "^PROFILE flags=0 key-mgmt=wpa-psk autoconnect=yes" "$ev/installed.txt" || die 'secret not saved or autoconnect off'
grep -q "^KEYFILE root:root 600 psk-lines=1" "$ev/installed.txt" || die 'keyfile not private or without its secret'
if grep -q 'interface-name=' "$ev/installed.txt"; then die 'the copy is still bound to the live adapter name'; fi
grep -q "^AUTOCONNECT ok $uuid .* by policy: auto-activating connection '[^']*' ($uuid)\$" "$ev/installed.txt" \
    || die 'NetworkManager did not connect by itself'
echo "WIFI_CHECK PASS variant=$variant drop=$drop uuid=$uuid evidence=$ev"
