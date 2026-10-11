#!/usr/bin/env bash
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
# Runs inside a disposable test guest (live ISO or the system installed from it); never on a host.
# A virtual radio pair (mac80211_hwsim): wlan1 is a WPA2-PSK access point (hostapd + dnsmasq,
# unmanaged by NetworkManager), wlan0 is the client a person joins with. The work directory holds
# the verified hostapd and dnsmasq packages that wifi-copy-check.sh sent; nothing is installed.
# Usage: wifi-copy-guest.sh DIR ap-up|ap-down|join-page|join-panel|state|installed [UUID]
set -Eeuo pipefail
dir=$1 op=$2 uuid=${3:-}
[[ $dir == /tmp/* && -d $dir ]] || { echo 'BAD: work directory must exist under /tmp' >&2; exit 2; }
SSID=EmakiTest
# Test-only passphrase of the virtual network; it exists only in this disposable guest.
PASS=hwsim-test-pass
die() { printf 'BAD: %s\n' "$*" >&2; exit 1; }

tool() {  # tool NAME: the binary from its verified package, extracted once into DIR
    local name=$1
    if [[ ! -x $dir/usr/bin/$name ]]; then
        [[ -f $dir/$name.pkg.tar.zst ]] || die "missing $name package"
        bsdtar -xf "$dir/$name.pkg.tar.zst" -C "$dir" "usr/bin/$name"
    fi
    if ldd "$dir/usr/bin/$name" | grep -q 'not found'; then die "$name lacks libraries"; fi
    printf '%s\n' "$dir/usr/bin/$name"
}

wait_for() {  # wait_for SECONDS COMMAND...: poll until the command succeeds
    local limit=$1; shift
    local deadline=$((SECONDS + limit))
    until "$@"; do
        ((SECONDS < deadline)) || return 1
        sleep 1
    done
}

ap_up() {
    lsmod | grep -q '^mac80211_hwsim' || sudo -n modprobe mac80211_hwsim radios=2
    wait_for 20 test -e /sys/class/net/wlan1 || die 'no virtual radio wlan1'
    # The live image's world domain allows no 2.4 GHz channel to transmit on.
    sudo -n iw reg set US
    sudo -n nmcli device set wlan1 managed no
    local hostapd dnsmasq
    hostapd=$(tool hostapd)
    dnsmasq=$(command -v dnsmasq || tool dnsmasq)
    printf '%s\n' interface=wlan1 driver=nl80211 "ssid=$SSID" hw_mode=g channel=6 wpa=2 \
        wpa_key_mgmt=WPA-PSK rsn_pairwise=CCMP "wpa_passphrase=$PASS" >"$dir/hostapd.conf"
    if ! sudo -n test -s "$dir/hostapd.pid" || ! sudo -n kill -0 "$(sudo -n cat "$dir/hostapd.pid")" 2>/dev/null; then
        sudo -n "$hostapd" -B -P "$dir/hostapd.pid" -f "$dir/hostapd.log" "$dir/hostapd.conf"
    fi
    ip -4 addr show dev wlan1 | grep -q 10.77.0.1/ || sudo -n ip addr add 10.77.0.1/24 dev wlan1
    if ! sudo -n test -s "$dir/dnsmasq.pid" || ! sudo -n kill -0 "$(sudo -n cat "$dir/dnsmasq.pid")" 2>/dev/null; then
        sudo -n "$dnsmasq" --interface=wlan1 --bind-interfaces --except-interface=lo --port=0 --no-resolv \
            --dhcp-range=10.77.0.10,10.77.0.50,1h --pid-file="$dir/dnsmasq.pid" --log-facility="$dir/dnsmasq.log"
    fi
    wait_for 20 iw dev wlan1 info >/dev/null || die 'access point did not start'
    iw dev wlan1 info | grep -q 'type AP' || die 'wlan1 is not an access point'
    echo "AP_UP ssid=$SSID wlan1"
}

ap_down() {
    # The link drops as it does when a network goes away: the access point disappears.
    if sudo -n test -s "$dir/hostapd.pid"; then sudo -n kill "$(sudo -n cat "$dir/hostapd.pid")" || true; fi
    sudo -n rm -f "$dir/hostapd.pid"
    wait_for 60 bash -c '! nmcli -t -f TYPE,STATE connection show --active | grep -q "^802-11-wireless:"' \
        || die 'Wi-Fi stayed active after the access point went away'
    echo 'AP_DOWN no active Wi-Fi'
}

session_spawn() {  # session_spawn OUT COMMAND: run COMMAND from the compositor, as the installer UI and the panel are
    local out=$1 command=$2 socket
    socket=$(find "/run/user/$(id -u)" -maxdepth 1 -name 'niri.*.sock' -print -quit)
    [[ -S $socket ]] || die 'no compositor socket'
    rm -f -- "$out"
    XDG_RUNTIME_DIR=/run/user/$(id -u) NIRI_SOCKET=$socket niri msg action spawn -- sh -c "$command > $out.tmp 2>&1; mv $out.tmp $out"
    wait_for 90 test -s "$out" || die "no answer in $out"
}

scan() {
    wait_for 60 bash -c "nmcli -t -f SSID device wifi list ifname wlan0 --rescan yes | grep -qx $SSID" || die "$SSID not found by wlan0"
}

join_page() {
    # The installer's Network page: the UI sends this request to its ui-helper, which runs nmcli --ask.
    scan
    local bssid
    bssid=$(nmcli -t -f SSID,BSSID device wifi list ifname wlan0 | sed -n "s/^$SSID://p" | head -1 | tr -d '\\')
    printf '{"op": "join", "bssid": "%s", "device": "wlan0", "password": "%s"}\n' "$bssid" "$PASS" >"$dir/join.json"
    session_spawn "$dir/join.out" "python3 -I -B /usr/share/emaki-installer/ui/ui-helper.py < $dir/join.json"
    rm -f -- "$dir/join.json"
    echo "JOIN_PAGE $(cat "$dir/join.out")"
}

join_panel() {
    # The desktop panel: Quickshell's WifiNetwork.connectWithPsk calls AddAndActivateConnection with
    # only 802-11-wireless-security.psk, the device and the access point (src/network/nm/network.cpp).
    scan
    printf '%s' "$PASS" >"$dir/psk"
    cat >"$dir/panel-join.py" <<'EOF'
import json, sys, time
import gi
gi.require_version('Gio', '2.0')
from gi.repository import Gio, GLib
NM, ROOT = 'org.freedesktop.NetworkManager', '/org/freedesktop/NetworkManager'
bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
def call(path, iface, method, args, reply):
    return bus.call_sync(NM, path, iface, method, args, GLib.VariantType(reply), 0, 30000, None).unpack()
def prop(path, iface, name):
    return call(path, 'org.freedesktop.DBus.Properties', 'Get', GLib.Variant('(ss)', (iface, name)), '(v)')[0]
device = call(ROOT, NM, 'GetDeviceByIpIface', GLib.Variant('(s)', ('wlan0',)), '(o)')[0]
ap = next(a for a in prop(device, NM + '.Device.Wireless', 'AccessPoints')
          if bytes(prop(a, NM + '.AccessPoint', 'Ssid')) == sys.argv[2].encode())
settings = {'802-11-wireless-security': {'psk': GLib.Variant('s', open(sys.argv[1]).read())}}
profile, active = call(ROOT, NM, 'AddAndActivateConnection',
                       GLib.Variant('(a{sa{sv}}oo)', (settings, device, ap)), '(oo)')
state = 0
for _ in range(60):
    try:
        state = prop(active, NM + '.Connection.Active', 'State')
    except GLib.Error:
        state = -1
        break
    if state == 2:
        break
    time.sleep(1)
uuid = call(profile, NM + '.Settings.Connection', 'GetSettings', None, '(a{sa{sv}})')[0]['connection']['uuid']
print(json.dumps({'ok': state == 2, 'state': state, 'wifi_uuid': uuid}))
EOF
    session_spawn "$dir/panel.out" "python3 -I $dir/panel-join.py $dir/psk $SSID"
    rm -f -- "$dir/psk"
    echo "JOIN_PANEL $(cat "$dir/panel.out")"
}

facts() {  # facts UUID: what NetworkManager and the keyfile say, without the secret
    local uuid=$1 file
    file=$(nmcli -t --escape no -f UUID,FILENAME connection show | sed -n "s/^$uuid://p")
    [[ -n $file ]] || { echo "PROFILE $uuid absent"; return 0; }
    echo "PROFILE $uuid file=$file"
    echo "PROFILE flags=$(nmcli -g 802-11-wireless-security.psk-flags connection show uuid "$uuid")" \
         "key-mgmt=$(nmcli -g 802-11-wireless-security.key-mgmt connection show uuid "$uuid")" \
         "autoconnect=$(nmcli -g connection.autoconnect connection show uuid "$uuid")" \
         "timestamp=$(nmcli -g connection.timestamp connection show uuid "$uuid")"
    echo "KEYFILE $(sudo -n stat -c '%U:%G %a' -- "$file") psk-lines=$(sudo -n grep -c '^psk=' -- "$file" || true)" \
         "$(sudo -n grep -E '^(permissions|psk-flags|interface-name|mac-address)=' -- "$file" | tr '\n' ' ')"
}

state() {
    nmcli -t -f UUID,TYPE,TIMESTAMP,ACTIVE,DEVICE connection show | grep ':802-11-wireless:' | sed 's/^/SAVED /' || true
    local id
    for id in $(nmcli -t -f UUID,TYPE connection show | sed -n 's/:802-11-wireless$//p'); do facts "$id"; done
}

installed() {
    [[ -n $uuid ]] || die 'installed needs the copied UUID'
    ! test -d /run/archiso || die 'installed runs on the installed system, not the live image'
    facts "$uuid"
    ap_up
    # --active also lists a connection still activating: wait for activated and a DHCP address.
    # NetworkManager's own policy line proves it connected by itself, not on request.
    # grep reads the whole journal: stopping at the first match would end journalctl with SIGPIPE.
    local by=''
    if wait_for 120 bash -c "nmcli -t -f UUID,DEVICE,STATE connection show --active | grep -qx '$uuid:wlan0:activated' &&
                             nmcli -g IP4.ADDRESS device show wlan0 | grep -q '^10\.77\.0\.'"; then
        by=$(sudo -n journalctl -b -u NetworkManager -o cat | grep -oE "policy: auto-activating connection '[^']*' \($uuid\)" || true)
        by=${by%%$'\n'*}
        [[ -n $by ]] || echo "AUTOCONNECT activated, but no auto-activation line for $uuid"
    fi
    if [[ -n $by ]]; then
        echo "AUTOCONNECT ok $uuid on wlan0 activated $(nmcli -g IP4.ADDRESS device show wlan0 | head -1) by $by"
    else
        echo "AUTOCONNECT failed $uuid"
        sudo -n journalctl -b -u NetworkManager --no-pager -o cat | grep -iE 'wlan0|policy' | tail -20
        exit 1
    fi
}

case $op in
    ap-up) ap_up ;;
    ap-down) ap_down ;;
    join-page) join_page ;;
    join-panel) join_panel ;;
    state) state ;;
    installed) installed ;;
    *) die "unknown step $op" ;;
esac
