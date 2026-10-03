#!/bin/sh
# niri entry point without a personal config: /etc/niri/config.kdl (niri/system.kdl) and
# “niri (Emaki)”. Installation paths are redirected to a copy of niri/ in a temporary directory;
# system and home directories are left alone. Fork checks run only if niri-emaki is available
# (absent in CI: the fork is optional).
set -u
R="$(cd "$(dirname "$0")/.." && pwd)"
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
fail=0
ok()  { echo "ok   $1"; }
bad() { echo "FAIL $1"; fail=1; }

# The system entry point includes defaults at the exact path used by make install.
if grep -qx 'include "/usr/share/emaki/niri/default.kdl"' "$R/niri/system.kdl"; then
    ok "system.kdl includes the installed default.kdl"
else
    bad "system.kdl must include \"/usr/share/emaki/niri/default.kdl\""
fi

# Copy of niri/ with installation paths redirected to a temporary directory.
mkdir -p "$T/share" "$T/etc" "$T/home/.config/niri" "$T/empty"
cp "$R"/niri/*.kdl "$T/share/"
sed -i -e "s|/usr/share/emaki/niri/|$T/share/|g" -e "s|/etc/niri/config.kdl|$T/etc/config.kdl|g" "$T"/share/*.kdl
cp "$T/share/system.kdl" "$T/etc/config.kdl"
printf 'include "%s/share/default.kdl"\n' "$T" > "$T/home/.config/niri/config.kdl"

if niri validate -c "$T/etc/config.kdl" >"$T/log" 2>&1; then
    ok "system config (/etc/niri/config.kdl) is valid"
else
    bad "system config (/etc/niri/config.kdl) is invalid:"; cat "$T/log"
fi

# Fork: both entry points are valid, but fork.kdl requires a personal config (hence the selection).
NIRI_EMAKI="${NIRI_EMAKI:-$(command -v niri-emaki || true)}"
if [ -n "$NIRI_EMAKI" ]; then
    if HOME="$T/home" "$NIRI_EMAKI" validate -c "$T/share/fork.kdl" >"$T/log" 2>&1; then
        ok "fork.kdl with a personal config is valid"
    else
        bad "fork.kdl with a personal config:"; cat "$T/log"
    fi
    if HOME="$T/empty" "$NIRI_EMAKI" validate -c "$T/share/fork-system.kdl" >"$T/log" 2>&1; then
        ok "fork-system.kdl without a personal config is valid"
    else
        bad "fork-system.kdl without a personal config:"; cat "$T/log"
    fi
    if HOME="$T/empty" "$NIRI_EMAKI" validate -c "$T/share/fork.kdl" >/dev/null 2>&1; then
        bad "fork.kdl without a personal config unexpectedly valid"
    else
        ok "fork.kdl without a personal config fails (why the session chooses)"
    fi
else
    echo "skip fork configs: niri-emaki not installed"
fi

# Config selection in niri-emaki-session --compositor (as called by niri-emaki.service).
mkdir -p "$T/bin"
printf '#!/bin/sh\ncat >/dev/null\n' > "$T/bin/systemd-cat"
chmod +x "$T/bin/systemd-cat"
printf '#!/bin/sh\nprintf "%%s\\n" "$*" > "%s/args"\n' "$T" > "$T/bin/niri-emaki"
chmod +x "$T/bin/niri-emaki"
chooses() {   # chooses "<name>" "<HOME>" "<expected arguments>"
    rm -f "$T/args"
    HOME="$2" PATH="$T/bin:$PATH" EMAKI_LOGIN_HANDOFF='' bash "$R/scripts/niri-emaki-session" --compositor
    got=$(cat "$T/args" 2>/dev/null)
    if [ "$got" = "$3" ]; then ok "$1"; else bad "$1: got [$got] want [$3]"; fi
}
chooses "session: personal config -> fork.kdl" "$T/home" \
    "--session -c /usr/share/emaki/niri/fork.kdl"
chooses "session: no personal config -> fork-system.kdl" "$T/empty" \
    "--session -c /usr/share/emaki/niri/fork-system.kdl"
# Startup does not create the user's file.
if [ -e "$T/empty/.config" ]; then bad "session created files in HOME"; else ok "session writes nothing to HOME"; fi

[ "$fail" -eq 0 ] && echo "ALL GREEN"
exit "$fail"
