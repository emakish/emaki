#!/bin/bash
# Screenshot of the VM from inside the guest: tests/vm/shot.sh <file.png>
# With 3D graphics (virgl), qemu screendump returns “no surface”, so run grim in the
# Wayland environment currently on screen (greeter or desktop). /run/user/<uid> directories
# are private, so finding the socket requires sudo.
HERE=$(dirname "$(readlink -f "$0")")
OUT=$1
# Store the screenshot in the logged-in user's runtime directory: the sticky bit on shared /tmp
# prevents one user from overwriting another's screenshot, which would make cat return a stale image.
"$HERE/ssh.sh" 'sudo sh -c '\''for d in /run/user/*/wayland-[0-9]; do
    [ -S "$d" ] || continue
    u=$(stat -c %U "$d"); r=$(dirname "$d"); f="$r/emaki-shot.png"; rm -f "$f"
    sudo -u "$u" env XDG_RUNTIME_DIR="$r" WAYLAND_DISPLAY=$(basename "$d") grim "$f" && cat "$f" && break
done'\''' > "$OUT" 2>/dev/null
if [ -s "$OUT" ]; then echo "screenshot: $OUT"; else echo "screenshot failed"; rm -f "$OUT"; exit 1; fi
