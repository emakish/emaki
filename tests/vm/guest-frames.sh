#!/bin/bash
# F0, INSIDE the VM (as arch, logged in): capture a sequence of screenshots while a command runs.
#   tests/vm/ssh.sh 'bash -s -- <frames> <x,y_wxh|full> <delay before command> "<command>"' \
#       < tests/vm/guest-frames.sh | tar -C <directory> -xf -
# Geometry uses grim logical pixels, with the space replaced by “_”. PPM frames (grim -t ppm, ~10 ms per
# frame in the VM), named <number>-<epoch-ms>.ppm; stdout is a tar archive of them.
count=$1; geom=$2; pre=$3; cmd=$4
# Optional cadence bounds evidence size for multi-second lock recovery bursts.
cadence=${5:-0}
export XDG_RUNTIME_DIR=/run/user/1000
export WAYLAND_DISPLAY=$(ls $XDG_RUNTIME_DIR | grep -E '^wayland-[0-9]+$' | head -1)
export NIRI_SOCKET=$(ls $XDG_RUNTIME_DIR/niri.*.sock 2>/dev/null | head -1)
d=$XDG_RUNTIME_DIR/frames
rm -rf "$d"; mkdir -p "$d"
g=(); [ "$geom" != full ] && g=(-g "${geom/_/ }")
(
    for i in $(seq -w 0 $((count - 1))); do
        grim -t ppm "${g[@]}" "$d/$i-$(date +%s%3N).ppm"
        [ "$cadence" = 0 ] || sleep "$cadence"
    done
) &
sleep "$pre"
[ -n "$cmd" ] && eval "$cmd" >/dev/null 2>&1
wait
tar -C "$d" -cf - .
