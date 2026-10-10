#!/bin/sh
# niri entry point without a personal config: /etc/niri/config.kdl (niri/system.kdl) and
# “niri (Emaki)”. Installation paths are redirected to a copy of niri/ in a temporary directory;
# system and home directories are left alone. Fork checks run only if niri-emaki is available
# (required in CI, optional for a local stock-only check).
set -u
R="$(cd "$(dirname "$0")/.." && pwd)"
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
fail=0
ok()  { echo "ok   $1"; }
bad() { echo "FAIL $1"; fail=1; }

# Shell IPC commands need distinct useful labels in niri's shortcut help.
while IFS='|' read -r key title; do
    if grep -Fq "$key hotkey-overlay-title=\"$title\" {" "$R/niri/default.kdl"; then
        ok "hotkey help: $key -> $title"
    else
        bad "hotkey help: $key is missing its descriptive title"
    fi
done <<'TITLES'
Mod+D|Open launcher
Mod+N|Notifications, calendar and music
Mod+Shift+S|Take an interactive screenshot
Mod+V|Clipboard history
Mod+T|Open terminal
Mod+L allow-when-locked=true|Lock screen
Mod+Escape|Power menu
Ctrl+Alt+T|Open terminal (works without Super)
TITLES

# Volume and brightness keys keep working on the lock screen (niri runs a bind under the
# lock only with allow-when-locked=true). One check per key, not a count: other binds may
# gain the property later. Mod+L too: after the locker crashed for good, niri keeps the
# session locked and accepts a new lock client, so Super+L must start one from there.
for key in XF86AudioRaiseVolume XF86AudioLowerVolume XF86AudioMute \
           XF86MonBrightnessUp XF86MonBrightnessDown Mod+L; do
    pattern=$(printf '%s' "$key" | sed 's/+/\\+/g')
    if grep -Eq "^[[:space:]]*$pattern[[:space:]][^{]*allow-when-locked=true[^{]*\{" "$R/niri/default.kdl"; then
        ok "works when locked: $key"
    else
        bad "works when locked: $key lacks allow-when-locked=true"
    fi
done

if sed -n '/^hotkey-overlay {/,/^}/p' "$R/niri/default.kdl" | grep -qx '    hide-not-bound'; then
    ok "hotkey help hides unbound stock actions"
else
    bad "hotkey help includes unbound stock actions"
fi

# The Welcome window floats instead of opening as a full-width column. It shares the app id
# org.quickshell with every Quickshell window; its title tells it apart.
if awk '/^window-rule \{/ { inside = 1; block = "" } inside { block = block " " $0 } inside && /^\}/ { print block; inside = 0 }' "$R/niri/default.kdl" |
    grep -F 'match app-id=r#"^org\.quickshell$"# title="^Welcome to Emaki$"' | grep -q 'open-floating true'; then
    ok "window rule: the Welcome window opens floating"
else
    bad "window rule: no open-floating rule for the Welcome window (org.quickshell, \"Welcome to Emaki\")"
fi

# Super+V opens the clipboard history in the shell; when no shell answers (emaki-shell call
# exits 255), the same bind falls back to cliphist in fuzzel. The bind's command runs here
# with stubs that log their arguments.
clip=$(sed -n 's/^[[:space:]]*Mod+V [^{]*{ spawn-sh "\(.*\)"; }$/\1/p' "$R/niri/default.kdl" | sed 's/\\"/"/g')
mkdir -p "$T/clip"
for name in emaki-shell emaki-config-path cliphist fuzzel wl-copy; do
    printf '#!/bin/sh\necho "%s${*:+ $*}" >> "%s/clip.log"\n' "$name" "$T" > "$T/clip/$name"
done
printf 'exit "$SHELL_RC"\n' >> "$T/clip/emaki-shell"
printf 'echo /fixture/fuzzel.ini\n' >> "$T/clip/emaki-config-path"
printf '[ "$5" = list ] && echo "1 fixture" || cat\n' >> "$T/clip/cliphist"
# fuzzel: picks the first entry; FUZZEL_MODE=esc prints nothing and fails, empty prints nothing.
printf 'case "$FUZZEL_MODE" in esc) exit 1 ;; empty) exit 0 ;; esac\nhead -n 1\n' >> "$T/clip/fuzzel"
printf 'cat > "%s/clip.copied"\n' "$T" >> "$T/clip/wl-copy"
chmod +x "$T"/clip/*
clip_run() {   # clip_run <exit status of emaki-shell> [fuzzel mode]: the logged calls, one per line
    rm -f "$T/clip.log" "$T/clip.copied"
    XDG_RUNTIME_DIR="$T/clip" SHELL_RC="$1" FUZZEL_MODE="${2:-pick}" PATH="$T/clip:$PATH" sh -c "$clip" >/dev/null 2>&1
    cat "$T/clip.log" 2>/dev/null
}
down=$(clip_run 255)
missing=""
for call in "cliphist -config-path /dev/null -db-path $T/clip/emaki-cliphist.db list" 'fuzzel --config /fixture/fuzzel.ini --dmenu' "cliphist -config-path /dev/null -db-path $T/clip/emaki-cliphist.db decode" 'wl-copy'; do
    printf '%s\n' "$down" | grep -qxF "$call" || missing="$missing [$call]"
done
if [ -n "$clip" ] && [ -z "$missing" ] && [ "$(cat "$T/clip.copied" 2>/dev/null)" = "1 fixture" ]; then
    ok "Super+V without a running shell falls back to cliphist in fuzzel"
else
    bad "Super+V without a running shell: missing$missing, copied '$(cat "$T/clip.copied" 2>/dev/null)'"
fi
# Esc in the fallback menu (fuzzel prints nothing) leaves the clipboard alone: wl-copy given
# empty input would replace the clipboard with an empty selection.
for mode in esc empty; do
    calls=$(clip_run 255 "$mode")
    if printf '%s\n' "$calls" | grep -q '^fuzzel' && ! printf '%s\n' "$calls" | grep -qE '^(cliphist .* decode|wl-copy)'; then
        ok "Super+V fallback with nothing chosen ($mode) does not touch the clipboard"
    else
        bad "Super+V fallback with nothing chosen ($mode): $(printf '%s' "$calls" | tr '\n' ';')"
    fi
done
up=$(clip_run 0)
if printf '%s\n' "$up" | grep -qxF 'emaki-shell call launcher mode Clipboard' && ! printf '%s\n' "$up" | grep -q '^fuzzel'; then
    ok "Super+V with the shell running opens only the shell's clipboard history"
else
    bad "Super+V with the shell running: unexpected calls: $up"
fi

# The microphone-mute key works like the other audio keys, also on the lock screen.
if grep -Eq "^[[:space:]]*XF86AudioMicMute[[:space:]][^{]*allow-when-locked=true[^{]*\{[[:space:]]*spawn \"wpctl\" \"set-mute\" \"@DEFAULT_AUDIO_SOURCE@\" \"toggle\";" "$R/niri/default.kdl"; then
    ok "works when locked: XF86AudioMicMute toggles the default microphone"
else
    bad "works when locked: XF86AudioMicMute is not bound with allow-when-locked=true to wpctl set-mute @DEFAULT_AUDIO_SOURCE@ toggle"
fi

# The system entry point includes defaults at the exact path used by make install.
if grep -qx 'include "@EMAKI_DATADIR@/niri/default.kdl"' "$R/niri/system.kdl"; then
    ok "system.kdl includes the installed default.kdl"
else
    bad "system.kdl must include \"@EMAKI_DATADIR@/niri/default.kdl\""
fi

# Copy of niri/ with installation paths redirected to a temporary directory.
mkdir -p "$T/share" "$T/etc" "$T/home/.config/niri" "$T/empty"
cp "$R"/niri/*.kdl "$T/share/"
sed -i -e "s|@EMAKI_DATADIR@/niri/|$T/share/|g" -e "s|/etc/niri/config.kdl|$T/etc/config.kdl|g" "$T"/share/*.kdl
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
