#!/bin/bash
# F0, runs INSIDE the VM: install Emaki from ~/emaki as a user following the repo instructions would.
# Each step prints “STEP <name> rc=<code>”; a failed step does not stop later ones —
# we need the full list of failures. The script exits 1 at the end if any step failed.
cd ~/emaki || exit 1
FAILED=()
step() {
    local name=$1; shift
    echo "=== $name: $*"
    "$@" > "/tmp/step-$name.log" 2>&1
    local rc=$?
    echo "STEP $name rc=$rc"
    if [ $rc -ne 0 ]; then
        tail -15 "/tmp/step-$name.log"
        FAILED+=("$name")
    fi
    return 0
}

# The clean snapshot keeps the package database of the day it was made; once the mirrors
# rotate a package (libpng 404 on 2026-09-30), every makepkg -si below fails. Refresh first.
step sync sudo pacman -Syu --noconfirm

# 0. Our packages (forks with our patches) before the metapackage: quickshell-emaki conflicts with
#    stock quickshell; if the metapackage installs stock first, replacement needs a
#    “y” response, which --noconfirm does not provide. Until F10 (our own repo), makepkg builds them.
for p in quickshell-emaki niri-emaki; do
    [ -f "packaging/$p/PKGBUILD" ] && step "$p" bash -c "cd packaging/$p && makepkg -si --noconfirm"
done

# 1. Dependencies: the metapackage, if present in the repo.
if [ -f packaging/emaki-desktop/PKGBUILD ]; then
    step deps bash -c 'cd packaging/emaki-desktop && makepkg -si --noconfirm'
else
    echo "STEP deps rc=NA (packaging/emaki-desktop/PKGBUILD missing — dependencies are not declared anywhere)"
    FAILED+=(deps)
fi

# 2. Build as a regular user (core, shaders), if the Makefile has such a target.
if make -n build >/dev/null 2>&1; then
    step build make build
fi

# 3. Installation.
step install sudo make install

# 3b. Fork-based “niri (Emaki)”, if the target exists.
if make -n install-niri-emaki >/dev/null 2>&1; then
    step install-niri-emaki sudo make install-niri-emaki
fi

# 4. Enable the login screen: explicit Makefile target, if present.
for t in enable-greeter enable; do
    if make -n "$t" >/dev/null 2>&1; then
        step "enable" sudo make "$t"
        break
    fi
done

# 4b. Shell services (networking, Bluetooth) — a separate step, if the target exists.
if make -n enable-services >/dev/null 2>&1; then
    step enable-services sudo make enable-services
    cat /tmp/step-enable-services.log
fi

# Screenshot from inside the guest (test only, not part of Emaki).
step lock-test-tools sudo pacman -S --needed --noconfirm grim python-evdev

# Disposable VM input fixture, set the way the installer sets a real install: the layouts
# live in /etc/vconsole.conf (render.vconsole_conf(["us", "ru"]) gives exactly these bytes)
# and niri reads them through systemd-localed on the login screen, in the session and on
# the lock screen. The VM user's niri config gets the include line only; an xkb section in
# it would override the system list. Production defaults never choose a user's layouts.
# A file, not stdin: this script itself arrives on the guest's stdin.
vm_layouts=$(mktemp)
printf 'KEYMAP=us\nXKBLAYOUT=us,ru\n' > "$vm_layouts"
step vm-layouts sudo install -m 0644 "$vm_layouts" /etc/vconsole.conf
rm -f "$vm_layouts"
mkdir -p "$HOME/.config/niri"
if [ ! -e "$HOME/.config/niri/config.kdl" ]; then
    printf '%s\n' 'include "/usr/share/emaki/niri/default.kdl"' > "$HOME/.config/niri/config.kdl"
fi
step lock-test-layout niri validate -c "$HOME/.config/niri/config.kdl"

# 4. Results before reboot.
echo "=== state"
echo "greetd enabled: $(systemctl is-enabled greetd 2>&1)"
for s in NetworkManager bluetooth systemd-networkd iwd; do
    echo "$s enabled: $(systemctl is-enabled "$s.service" 2>&1)"
done
echo "niri: $(command -v niri || echo missing)  quickshell: $(command -v quickshell qs 2>/dev/null | head -1 || echo missing)"
echo "emaki: $(command -v emaki || echo missing)  emaki-shell: $(command -v emaki-shell || echo missing)"
ls /usr/share/emaki 2>&1 | head -20

if [ "${#FAILED[@]}" -ne 0 ]; then
    echo "FAILED STEPS: ${FAILED[*]}"
    exit 1
fi
