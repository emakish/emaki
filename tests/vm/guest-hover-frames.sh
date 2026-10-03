#!/bin/bash
# C11/F0, INSIDE the VM (as arch, logged in): capture the launcher's first tile row while the pointer
# moves steadily across it and the selection bubble follows. For check-launcher-hover.py.
#   tests/vm/ssh.sh 'bash -s -- <frames> <movement-ms>' < tests/vm/guest-hover-frames.sh \
#       | tar -C <directory> -xf -
# Requires /tmp/guest-pointer.py (python-evdev). Workspace 2 must be empty.
frames=${1:-240}; ms=${2:-1500}
export XDG_RUNTIME_DIR=/run/user/1000
export WAYLAND_DISPLAY=$(ls $XDG_RUNTIME_DIR | grep -E '^wayland-[0-9]+$' | head -1)
export NIRI_SOCKET=$(ls $XDG_RUNTIME_DIR/niri.*.sock 2>/dev/null | head -1)
read LW LH < <(niri msg -j outputs | python3 -c 'import json,sys; o=list(json.load(sys.stdin).values())[0]["logical"]; print(o["width"], o["height"])')
f() { python3 -c "print(f'{$1/$LW:.4f},{$2/$LH:.4f}')"; }
niri msg action focus-workspace 2 >/dev/null; sleep 1
emaki-shell call launcher open >/dev/null; sleep 1.5
# First tile row: the first tile is selected on opening; its bubble (panel at (10, 8)) sits
# 2 px above the tile; tile height is 84. Row height differs with and without Frequent.
sel() { emaki-shell call launcher status | python3 -c "import json,sys; s=json.load(sys.stdin)[\"launcher_glass\"][\"select\"]; print(round(8 + s[\"y\"] + 2), s[\"x\"])"; }
read row x0 < <(sel)
y=$((row + 42))
sudo python3 /tmp/guest-pointer.py "move:$(f 60 $y)" wait:0.8 >/dev/null 2>&1
d=$XDG_RUNTIME_DIR/frames; rm -rf "$d"; mkdir -p "$d"
# guest-pointer.py waits 1.5 s for libinput to pick up the device: capture frames afterwards.
sudo python3 /tmp/guest-pointer.py "glide:$(f 60 $y),$(f 680 $y),$ms" >/dev/null 2>&1 &
sleep 1.55
for i in $(seq -w 0 $((frames - 1))); do grim -t ppm -g "0,$((row - 10)) 740x110" "$d/$i-$(date +%s%3N).ppm"; done
wait
read _ x1 < <(sel)
echo "$row $x0 $x1" > "$d/row.txt"
emaki-shell call launcher close >/dev/null
sudo python3 /tmp/guest-pointer.py move:0.5,0.95 >/dev/null 2>&1
niri msg action focus-workspace 1 >/dev/null
tar -C "$d" -cf - .
