#!/bin/bash
# C11/F0, INSIDE the VM (as arch, logged in): a real click on “3” in the workspace strip.
#   tests/vm/ssh.sh 'bash -s -- <desktop>' < tests/vm/guest-strip-click.sh
# The click goes through uinput → libinput → niri → shell (guest-pointer.py must be in /tmp).
# 27.09: clicking the number did not switch workspaces — the island tooltip (TapHandler) covered the numbers
# and intercepted the click. Prints `[<desktop>] strip-click: ok` or `BAD active=<number>`.
name=${1:-glass}
export XDG_RUNTIME_DIR=/run/user/1000
export WAYLAND_DISPLAY=$(ls $XDG_RUNTIME_DIR | grep -E '^wayland-[0-9]+$' | head -1)
export NIRI_SOCKET=$(ls $XDG_RUNTIME_DIR/niri.*.sock 2>/dev/null | head -1)
sudo pacman -S --needed --noconfirm python-evdev >/dev/null 2>&1
# On a fresh system, the first click in the 27.09 run did not reach the shell (a repeat after the same login did;
# cause unknown), so first create a trial device and move the pointer aside.
sudo python3 /tmp/guest-pointer.py move:0.5,0.6 wait:1 >/dev/null 2>&1
active() { niri msg -j workspaces | python3 -c 'import json,sys; print([w["idx"] for w in json.load(sys.stdin) if w["is_active"]][0])'; }
# Number “3”: workspace island at x 54, 28-wide cells on a 30 pitch after padding 8 → center x 136, y 26
# (logical pixels); fractions are relative to logical screen size.
frac=$(niri msg -j outputs | python3 -c 'import json,sys; o=list(json.load(sys.stdin).values())[0]["logical"]; print(f"{136/o["width"]:.4f},{26/o["height"]:.4f}")')
niri msg action focus-workspace 1; sleep 1
before=$(active)
sudo python3 /tmp/guest-pointer.py "move:$frac" wait:0.5 click wait:0.8
sleep 0.5
after=$(active)
# Move the pointer aside so the tooltip does not linger in later screenshots.
sudo python3 /tmp/guest-pointer.py move:0.5,0.6 >/dev/null 2>&1
niri msg action focus-workspace 1
echo "[$name]   workspace before click $before, after $after (expect 3)"
if [ "$after" = 3 ]; then
    echo "[$name] strip-click: ok"
else
    echo "[$name] strip-click: BAD active=$after"
    exit 1
fi
