#!/bin/bash
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
# Log in to both Emaki desktops in the VM through greetd and check the desktop.
#   tests/vm/login.sh <run-directory> [--fixture JSON --dir VM ...]
# First “Niri” (stock niri), then log out and enter “niri (Emaki)” (glass fork, if installed).
# Open kitty in each so the screenshot shows a desktop with a window, panel, and dock.
HERE=$(dirname "$(readlink -f "$0")")
if [ "${EMAKI_GLASS_RUNNING:-}" != 1 ]; then
    exec python3 "$HERE/glass-target.py" launch "$@"
fi
set -o pipefail
remote() { python3 "$HERE/glass-target.py" remote "$@"; }
guest_helper() { python3 "$HERE/glass-target.py" helper "$@"; }
OUT=${1:?run directory}
mkdir -p "$OUT"
remote 'cat > /tmp/guest-login.py' < "$HERE/guest-login.py" || exit 1

# niri msg in the guest: find the socket ourselves; ssh lacks the desktop environment variables.
NIRI='export XDG_RUNTIME_DIR=/run/user/$(id -u); export DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus; export NIRI_SOCKET=$(ls "$XDG_RUNTIME_DIR"/niri.*.sock 2>/dev/null | head -1)'

# Every check runs; failed ones are collected and the script exits 1 at the end.
FAILED=()
NOT_TESTED=0
check() {  # <label> <command...>
    local label=$1; shift
    "$@" || FAILED+=("$label")
}

session() {  # <name> <desktop command> <compositor service>
    local name=$1 cmd=$2 unit=$3 rc=0
    python3 "$HERE/glass-target.py" login "$cmd" > "$OUT/login-$name.log" 2>&1 || rc=$?
    echo "[$name] login rc=$rc ($(tail -1 "$OUT/login-$name.log"))"
    [ "$rc" -eq 0 ] || FAILED+=("login $name")
    sleep 20
    remote "$NIRI
        echo \"[$name] $unit: \$(systemctl --user is-active $unit)\"
        echo \"[$name] emaki-shell: \$(systemctl --user is-active emaki-shell.service)\"
        systemctl --user --failed --no-legend
        niri-emaki msg action spawn -- kitty 2>/dev/null || niri msg action spawn -- kitty
        journalctl --user -b --no-pager -p warning | tail -40" > "$OUT/session-$name.log" 2>&1
    grep -E "^\[$name\]" "$OUT/session-$name.log"
    sleep 5
    check "host capture" "$HERE/shot.sh" "$OUT/desktop-$name.png"
    # Regression on 27.09: the bar covered the top of every window (kitty prompt invisible).
    check "check-window-under-bar.py $name" python3 "$HERE/check-window-under-bar.py" "$name" "$OUT/desktop-$name.png"
}

# C11 27.09: launcher glass on the real Qt path (make glass-shots does not check it).
# Shipped wallpaper (art/wallpaper/ring.png, centred); restart the shell on workspace 1 with
# black kitty (its first captured frames), then open the launcher on empty workspace 2 — at 1
# and 1.25. Detects glass showing a stale capture (27.09: a muddy dark plate over the wallpaper).
glass_scene() {  # <desktop name>
    local name=$1
    local G="$NIRI; export WAYLAND_DISPLAY=\$(ls \$XDG_RUNTIME_DIR | grep -E '^wayland-[0-9]+\$' | head -1)
        OUTPUT=\$(niri msg --json outputs | python3 -c 'import json,sys; print(list(json.load(sys.stdin))[0])')"
    python3 "$HERE/glass-target.py" wallpaper || return 1
    remote "$G
        mkdir -p ~/.config/wpaperd
        printf '[default]\npath = \"%s/wallpaper.png\"\nmode = \"center\"\n' \"\$HOME\" > ~/.config/wpaperd/config.toml
        pkill -x wpaperd; sleep 1; niri msg action spawn -- wpaperd; sleep 2
        niri msg action focus-workspace 1; sleep 1
        systemctl --user restart emaki-shell" >/dev/null 2>&1
    local i state=""
    for i in $(seq 1 20); do
        sleep 2
        state=$("$HERE/shell-status.sh" '.material.wallpaper' 2>/dev/null | tr -d '"')
        [ "$state" = ready ] && break
    done
    echo "[$name] material.wallpaper: ${state:-no response}"
    remote "$G; niri msg action focus-workspace-down; sleep 2" >/dev/null 2>&1
    check "host capture" "$HERE/shot.sh" "$OUT/wall-s1.png" >/dev/null
    remote "$G; emaki-shell call launcher open; sleep 2.5" >/dev/null 2>&1
    check "host capture" "$HERE/shot.sh" "$OUT/launcher-s1.png"
    # 1.25: first open the launcher over kitty (its last captured frame is the terminal),
    # then on an empty workspace.
    remote "$G; emaki-shell call launcher close; sleep 1
        niri msg output \$OUTPUT scale 1.25; sleep 3" >/dev/null 2>&1
    remote "$G; echo \"[$name] scale for 1.25: \$(niri msg outputs | grep -m1 Scale)\""
    remote "$G
        niri msg action focus-workspace 1; sleep 1.5
        emaki-shell call launcher open; sleep 2; emaki-shell call launcher close; sleep 1
        niri msg action focus-workspace-down; sleep 2" >/dev/null 2>&1
    check "host capture" "$HERE/shot.sh" "$OUT/wall-s125.png" >/dev/null
    remote "$G; emaki-shell call launcher open; sleep 2.5" >/dev/null 2>&1
    check "host capture" "$HERE/shot.sh" "$OUT/launcher-s125.png"
    # Grid over fine detail (27.09, “stripes”): dense text wallpaper, like kitty output,
    # launcher on the same empty workspace at 1.25; then restore the shipped wallpaper.
    python3 "$HERE/make-text-wall.py" "$OUT/text-wall.png" >/dev/null
    remote 'cat > ~/text-wall.png' < "$OUT/text-wall.png"
    remote "$G; emaki-shell call launcher close; sleep 1
        printf '[default]\npath = \"%s/text-wall.png\"\n' \"\$HOME\" > ~/.config/wpaperd/config.toml
        pkill -x wpaperd; sleep 1; niri msg action spawn -- wpaperd; sleep 3
        emaki-shell call launcher open; sleep 2.5" >/dev/null 2>&1
    check "host capture" "$HERE/shot.sh" "$OUT/launcher-text-s125.png"
    remote "$G; emaki-shell call launcher close; sleep 1
        printf '[default]\npath = \"%s/wallpaper.png\"\nmode = \"center\"\n' \"\$HOME\" > ~/.config/wpaperd/config.toml
        pkill -x wpaperd; sleep 1; niri msg action spawn -- wpaperd; sleep 3" >/dev/null 2>&1
    remote "$G; emaki-shell call launcher close; sleep 1; niri msg output \$OUTPUT scale 1
        niri msg action focus-workspace 1" >/dev/null 2>&1
    check "check-launcher-glass.py $name" python3 "$HERE/check-launcher-glass.py" "$name" "$OUT"
    check "check-launcher-lattice.py $name" python3 "$HERE/check-launcher-lattice.py" "$name" "$OUT/launcher-text-s125.png"
}

session niri niri-session niri.service

if remote 'command -v niri-emaki-session' >/dev/null 2>&1; then
    remote "$NIRI; niri msg action quit --skip-confirmation" >/dev/null 2>&1
    sleep 20
    session glass niri-emaki-session niri-emaki.service
    # Click the number before glass_scene: after scale changes 1 → 1.25 → 1, the bar receives
    # no pointer input at all (also on main 60bfd59, 27.09; separate bug, in GOTCHAS).
    check "pointer fixture upload" remote 'cat > /tmp/guest-pointer.py' < "$HERE/guest-pointer.py"
    check "guest-strip-click.sh glass" guest_helper guest-strip-click.sh glass
    check "glass scene" glass_scene glass
    # The launcher's selection bubble follows the pointer: tiles must not vanish for one frame (scale 1).
    mkdir -p "$OUT/frames-hover"
    guest_helper guest-hover-frames.sh 240 1500 | tar -C "$OUT/frames-hover" -xf - || FAILED+=("hover capture")
    check "check-launcher-hover.py glass" python3 "$HERE/check-launcher-hover.py" glass "$OUT/frames-hover" 1
    # Bar flicker: black kitty slides underneath the islands (workspace 1 → empty workspace 2); capture
    # consecutive frames of the screen top (scale 1 after glass_scene).
    remote "$NIRI; niri msg action focus-workspace 1" >/dev/null 2>&1
    sleep 3
    mkdir -p "$OUT/frames-switch"
    remote 'bash -s -- 150 0,0_1280x70 0.3 "niri msg action focus-workspace-down"' \
        < "$HERE/guest-frames.sh" | tar -C "$OUT/frames-switch" -xf - || FAILED+=("workspace capture")
    check "check-bar-flicker.py glass" python3 "$HERE/check-bar-flicker.py" glass "$OUT/frames-switch" 1
    # One overlay window for all openings; repeat openings have a short first frame.
    check "guest-open-timing.sh glass" guest_helper guest-open-timing.sh glass
else
    # guest-install.sh installs niri-emaki in the same run: an unrun check is not a pass.
    echo "RESULT: NOT TESTED glass desktop (niri-emaki-session missing)"
    NOT_TESTED=1
fi

if [ "${#FAILED[@]}" -ne 0 ]; then
    printf 'FAILED: %s\n' "${FAILED[@]}"
fi
echo "HUMAN REVIEW REQUIRED: desktop composition and text in $OUT; pixel checks cover only their named regions and effects"
[ "${#FAILED[@]}" -eq 0 ] && [ "$NOT_TESTED" -eq 0 ] || exit 1
