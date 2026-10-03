#!/bin/bash
# Log in to both Emaki desktops in the VM through greetd and check the desktop.
#   tests/vm/login.sh <run-directory>
# First “Niri” (stock niri), then log out and enter “niri (Emaki)” (glass fork, if installed).
# Open kitty in each so the screenshot shows a desktop with a window, panel, and dock.
HERE=$(dirname "$(readlink -f "$0")")
REPO=$(git -C "$HERE" rev-parse --show-toplevel)
OUT=${1:?run directory}
"$HERE/ssh.sh" 'cat > /tmp/guest-login.py' < "$HERE/guest-login.py"

# niri msg in the guest: find the socket ourselves; ssh lacks the desktop environment variables.
NIRI='export XDG_RUNTIME_DIR=/run/user/1000; export NIRI_SOCKET=$(ls /run/user/1000/niri.*.sock 2>/dev/null | head -1)'

session() {  # <name> <desktop command> <compositor service>
    local name=$1 cmd=$2 unit=$3
    printf %s arch | "$HERE/ssh.sh" "sudo -n python3 /tmp/guest-login.py arch $cmd" > "$OUT/login-$name.log" 2>&1
    echo "[$name] login rc=$? ($(tail -1 "$OUT/login-$name.log"))"
    sleep 20
    "$HERE/ssh.sh" "$NIRI
        echo \"[$name] $unit: \$(systemctl --user is-active $unit)\"
        echo \"[$name] emaki-shell: \$(systemctl --user is-active emaki-shell.service)\"
        systemctl --user --failed --no-legend
        niri-emaki msg action spawn -- kitty 2>/dev/null || niri msg action spawn -- kitty
        journalctl --user -b --no-pager -p warning | tail -40" > "$OUT/session-$name.log" 2>&1
    grep -E "^\[$name\]" "$OUT/session-$name.log"
    sleep 5
    "$HERE/shot.sh" "$OUT/desktop-$name.png"
    # Regression on 27.09: the bar covered the top of every window (kitty prompt invisible).
    python3 "$HERE/check-window-under-bar.py" "$name" "$OUT/desktop-$name.png"
}

# C11 27.09: launcher glass on the real Qt path (make glass-shots does not check it).
# Owner's wallpaper (wallpaper-neo-pink); restart the shell on workspace 1 with black kitty (its
# first captured frames), then open the launcher on empty workspace 2 — at 1 and 1.25. Detects
# glass showing a stale capture (27.09: a muddy dark plate over the wallpaper).
glass_scene() {  # <desktop name>
    local name=$1
    local G="$NIRI; export WAYLAND_DISPLAY=\$(ls /run/user/1000 | grep -E '^wayland-[0-9]+\$' | head -1)
        OUTPUT=\$(niri msg --json outputs | python3 -c 'import json,sys; print(list(json.load(sys.stdin))[0])')"
    "$HERE/ssh.sh" 'cat > ~/wallpaper.jpg' < "$REPO/docs/mockups/liquid-glass/wallpaper-neo-pink.jpg"
    "$HERE/ssh.sh" "$G
        mkdir -p ~/.config/wpaperd
        printf '[default]\npath = \"/home/arch/wallpaper.jpg\"\n' > ~/.config/wpaperd/config.toml
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
    "$HERE/ssh.sh" "$G; niri msg action focus-workspace-down; sleep 2" >/dev/null 2>&1
    "$HERE/shot.sh" "$OUT/wall-s1.png" >/dev/null
    "$HERE/ssh.sh" "$G; emaki-shell call launcher open; sleep 2.5" >/dev/null 2>&1
    "$HERE/shot.sh" "$OUT/launcher-s1.png"
    # 1.25: first open the launcher over kitty (its last captured frame is the terminal),
    # then on an empty workspace.
    "$HERE/ssh.sh" "$G; emaki-shell call launcher close; sleep 1
        niri msg output \$OUTPUT scale 1.25; sleep 3" >/dev/null 2>&1
    "$HERE/ssh.sh" "$G; echo \"[$name] scale for 1.25: \$(niri msg outputs | grep -m1 Scale)\""
    "$HERE/ssh.sh" "$G
        niri msg action focus-workspace 1; sleep 1.5
        emaki-shell call launcher open; sleep 2; emaki-shell call launcher close; sleep 1
        niri msg action focus-workspace-down; sleep 2" >/dev/null 2>&1
    "$HERE/shot.sh" "$OUT/wall-s125.png" >/dev/null
    "$HERE/ssh.sh" "$G; emaki-shell call launcher open; sleep 2.5" >/dev/null 2>&1
    "$HERE/shot.sh" "$OUT/launcher-s125.png"
    # Grid over fine detail (27.09, “stripes”): dense text wallpaper, like kitty output,
    # launcher on the same empty workspace at 1.25; then restore the owner's wallpaper.
    python3 "$HERE/make-text-wall.py" "$OUT/text-wall.png" >/dev/null
    "$HERE/ssh.sh" 'cat > ~/text-wall.png' < "$OUT/text-wall.png"
    "$HERE/ssh.sh" "$G; emaki-shell call launcher close; sleep 1
        printf '[default]\npath = \"/home/arch/text-wall.png\"\n' > ~/.config/wpaperd/config.toml
        pkill -x wpaperd; sleep 1; niri msg action spawn -- wpaperd; sleep 3
        emaki-shell call launcher open; sleep 2.5" >/dev/null 2>&1
    "$HERE/shot.sh" "$OUT/launcher-text-s125.png"
    "$HERE/ssh.sh" "$G; emaki-shell call launcher close; sleep 1
        printf '[default]\npath = \"/home/arch/wallpaper.jpg\"\n' > ~/.config/wpaperd/config.toml
        pkill -x wpaperd; sleep 1; niri msg action spawn -- wpaperd; sleep 3" >/dev/null 2>&1
    "$HERE/ssh.sh" "$G; emaki-shell call launcher close; sleep 1; niri msg output \$OUTPUT scale 1
        niri msg action focus-workspace 1" >/dev/null 2>&1
    python3 "$HERE/check-launcher-glass.py" "$name" "$OUT"
    python3 "$HERE/check-launcher-lattice.py" "$name" "$OUT/launcher-text-s125.png"
}

session niri niri-session niri.service

if "$HERE/ssh.sh" 'command -v niri-emaki-session' >/dev/null 2>&1; then
    "$HERE/ssh.sh" "$NIRI; niri msg action quit --skip-confirmation" >/dev/null 2>&1
    sleep 10
    session glass niri-emaki-session niri-emaki.service
    # Click the number before glass_scene: after scale changes 1 → 1.25 → 1, the bar receives
    # no pointer input at all (also on main 60bfd59, 27.09; separate bug, in GOTCHAS).
    "$HERE/ssh.sh" 'cat > /tmp/guest-pointer.py' < "$HERE/guest-pointer.py"
    "$HERE/ssh.sh" 'bash -s -- glass' < "$HERE/guest-strip-click.sh"
    glass_scene glass
    # The launcher's selection bubble follows the pointer: tiles must not vanish for one frame (scale 1).
    mkdir -p "$OUT/frames-hover"
    "$HERE/ssh.sh" 'bash -s -- 240 1500' < "$HERE/guest-hover-frames.sh" | tar -C "$OUT/frames-hover" -xf -
    python3 "$HERE/check-launcher-hover.py" glass "$OUT/frames-hover" 1
    # Bar flicker: black kitty slides underneath the islands (workspace 1 → empty workspace 2); capture
    # consecutive frames of the screen top (scale 1 after glass_scene).
    "$HERE/ssh.sh" "$NIRI; niri msg action focus-workspace 1" >/dev/null 2>&1
    sleep 3
    mkdir -p "$OUT/frames-switch"
    "$HERE/ssh.sh" 'bash -s -- 150 0,0_1280x70 0.3 "niri msg action focus-workspace-down"' \
        < "$HERE/guest-frames.sh" | tar -C "$OUT/frames-switch" -xf -
    python3 "$HERE/check-bar-flicker.py" glass "$OUT/frames-switch" 1
    # One overlay window for all openings; repeat openings have a short first frame.
    "$HERE/ssh.sh" 'bash -s -- glass' < "$HERE/guest-open-timing.sh"
else
    echo "[glass] niri-emaki-session is not installed — glass desktop was not tested"
fi
