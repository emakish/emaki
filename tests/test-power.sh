#!/bin/sh
# Isolated emaki-power test: fuzzel, systemctl, niri and emaki-lock are PATH fakes.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/.cache"
T=$(mktemp -d "$ROOT/.cache/power-XXXXXXXX")
S="$(cd "$(dirname "$0")/.." && pwd)/scripts/emaki-power"
LOG="$T/calls"

mk() { printf '#!/bin/sh\n%s\n' "$2" > "$T/$1"; chmod +x "$T/$1"; }
# fuzzel: write arguments to fuzzel-args and return successive responses from answers;
# an empty line means Esc (exit code 1).
mk fuzzel 'printf "%s\n" "$*" >> "'"$T"'/fuzzel-args"; cat >/dev/null; a=$(head -n1 "'"$T"'/answers"); sed -i 1d "'"$T"'/answers"; [ -n "$a" ] || exit 1; printf "%s\n" "$a"'
# Real config layer selection, not a stub: its result is checked below.
cp "$(dirname "$S")/emaki-config-path" "$T/emaki-config-path"
# The test runner's personal files must not affect the result.
export XDG_CONFIG_HOME="$T/config"
mk systemctl 'echo "systemctl $*" >> "'"$LOG"'"'
mk niri      'echo "niri $*" >> "'"$LOG"'"'
mk emaki-lock 'echo "emaki-lock $*" >> "'"$LOG"'"; [ ! -f "'"$T"'/lock-fail" ] || exit 1; echo "confirmed" >> "'"$LOG"'"'

fail=0
run() {   # run "<name>" "<responses separated by |>" "<expected calls separated by |>"
    : > "$LOG"; printf '%s' "$2" | tr '|' '\n' > "$T/answers"; echo >> "$T/answers"
    PATH="$T:$PATH" sh "$S" >/dev/null 2>&1
    got=$(tr '\n' '|' < "$LOG" | sed 's/|$//')
    if [ "$got" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: got [$got] want [$3]"; fail=1; fi
}

run "Esc in main menu"        ""                    ""
run "Lock"                    "Lock"                "emaki-lock --wait|confirmed"
run "Suspend locks first"     "Suspend"             "emaki-lock --wait|confirmed|systemctl suspend"
run "Hibernate + Yes"         "Hibernate|Yes"       "emaki-lock --wait|confirmed|systemctl hibernate"
: > "$T/lock-fail"
run "Suspend cancelled on lock failure" "Suspend" "emaki-lock --wait"
run "Hibernate cancelled on lock failure" "Hibernate|Yes" "emaki-lock --wait"
rm "$T/lock-fail"
run "Hibernate + No"          "Hibernate|No"        ""
run "Hibernate + Esc"         "Hibernate|"          ""
run "Log out + Yes"           "Log out|Yes"         "niri msg action quit --skip-confirmation"
run "Log out + No"            "Log out|No"          ""
run "Reboot + Yes"            "Reboot|Yes"          "systemctl reboot"
run "Reboot + No"             "Reboot|No"           ""
run "Shut down + Yes"         "Shut down|Yes"       "systemctl poweroff"
run "Shut down + Esc"         "Shut down|"          ""
run "garbage answer"          "whatever"            ""

# fuzzel gets our file via a flag (/etc/xdg/fuzzel belongs to the fuzzel package),
# while the user's ~/.config/fuzzel/fuzzel.ini takes precedence.
config_is() {   # config_is "<name>" "<expected path>"
    : > "$T/fuzzel-args"; echo > "$T/answers"
    PATH="$T:$PATH" sh "$S" >/dev/null 2>&1
    got=$(head -n1 "$T/fuzzel-args" | sed -n 's/^--config \([^ ]*\) .*/\1/p')
    if [ "$got" = "$2" ]; then echo "ok   $1"; else echo "FAIL $1: got [$got] want [$2]"; fail=1; fi
}
config_is "fuzzel config: Emaki default"   "/usr/share/emaki/fuzzel/fuzzel.ini"
mkdir -p "$T/config/fuzzel"; : > "$T/config/fuzzel/fuzzel.ini"
config_is "fuzzel config: personal wins"   "$T/config/fuzzel/fuzzel.ini"

rm -rf "$T"
[ "$fail" -eq 0 ] && echo "ALL GREEN"
exit "$fail"
