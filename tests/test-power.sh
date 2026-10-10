#!/bin/sh
# Isolated emaki-power test: fuzzel, systemctl, niri, busctl and emaki-lock are PATH fakes.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/.cache"
T=$(mktemp -d "$ROOT/.cache/power-XXXXXXXX")
S="$(cd "$(dirname "$0")/.." && pwd)/scripts/emaki-power"
LOG="$T/calls"

mk() { printf '#!/bin/sh\n%s\n' "$2" > "$T/$1"; chmod +x "$T/$1"; }
# fuzzel: write arguments to fuzzel-args and return successive responses from answers;
# an empty line means Esc (exit code 1).
mk fuzzel 'printf "%s\n" "$*" >> "'"$T"'/fuzzel-args"; cat > "'"$T"'/menu-items"; a=$(head -n1 "'"$T"'/answers"); sed -i 1d "'"$T"'/answers"; [ -n "$a" ] || exit 1; printf "%s\n" "$a"'
# Real config layer selection, not a stub: its result is checked below.
cp "$(dirname "$S")/emaki-config-path" "$T/emaki-config-path"
cp "$(dirname "$S")/paths" "$T/paths"
# The test runner's personal files must not affect the result.
export XDG_CONFIG_HOME="$T/config"
mk emaki-wallet-start 'echo "wallet $*" >> "'"$LOG"'"; if [ -f "'"$T"'/wallet-stall" ]; then trap "" TERM; while :; do sleep 1; done; fi; [ ! -f "'"$T"'/wallet-fail" ]'
mk systemctl 'echo "systemctl $*" >> "'"$LOG"'"'
# logind's CanHibernate answer, as busctl --json=short prints it; "yes" unless the test says otherwise.
mk busctl 'echo "busctl $*" >> "'"$T"'/busctl-args"; printf "{\"type\":\"s\",\"data\":[\"%s\"]}\n" "$(cat "'"$T"'/can-hibernate")"'
echo yes > "$T/can-hibernate"
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
run "Suspend locks first"     "Sleep"               "emaki-lock --wait|confirmed|systemctl suspend"
run "Hibernate + Yes"         "Hibernate|Yes"       "emaki-lock --wait|confirmed|systemctl hibernate"
: > "$T/lock-fail"
run "Suspend cancelled on lock failure" "Sleep" "emaki-lock --wait"
run "Hibernate cancelled on lock failure" "Hibernate|Yes" "emaki-lock --wait"
rm "$T/lock-fail"
run "Hibernate + No"          "Hibernate|No"        ""
run "Hibernate + Esc"         "Hibernate|"          ""
run "Log out + Yes"           "Log out|Yes"         "wallet --flush|niri msg action quit --skip-confirmation"
run "Log out + No"            "Log out|No"          ""
run "Reboot + Yes"            "Restart|Yes"         "wallet --flush|systemctl reboot"
run "Reboot + No"             "Restart|No"          ""
run "Shut down + Yes"         "Shut down|Yes"       "wallet --flush|systemctl poweroff"
run "Shut down + Esc"         "Shut down|"          ""
: > "$T/wallet-fail"
run "Logout proceeds when saving fails" "Log out|Yes" "wallet --flush|niri msg action quit --skip-confirmation"
run "Reboot proceeds when saving fails" "Restart|Yes" "wallet --flush|systemctl reboot"
run "Power off proceeds when saving fails" "Shut down|Yes" "wallet --flush|systemctl poweroff"
rm "$T/wallet-fail"
: > "$T/wallet-stall"
run "Logout proceeds after a stalled flush" "Log out|Yes" "wallet --flush|niri msg action quit --skip-confirmation"
rm "$T/wallet-stall"
run "garbage answer"          "whatever"            ""

# The main menu lists Hibernate only when logind says the machine can (same rule as
# the panel's power row); the other words stay and keep their order.
items_are() {   # items_are "<name>" "<CanHibernate answer>" "<expected items separated by |>"
    echo "$2" > "$T/can-hibernate"; : > "$T/menu-items"; echo > "$T/answers"
    PATH="$T:$PATH" sh "$S" >/dev/null 2>&1
    got=$(tr '\n' '|' < "$T/menu-items" | sed 's/|$//')
    if [ "$got" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: got [$got] want [$3]"; fail=1; fi
}
items_are "menu with hibernation"    "yes" "Lock|Sleep|Hibernate|Log out|Restart|Shut down"
items_are "menu without hibernation" "no"  "Lock|Sleep|Log out|Restart|Shut down"
items_are "menu when logind is silent" ""  "Lock|Sleep|Log out|Restart|Shut down"
if grep -q -- '--system --json=short call org.freedesktop.login1 /org/freedesktop/login1 org.freedesktop.login1.Manager CanHibernate' "$T/busctl-args"; then
    echo "ok   CanHibernate asked the way the panel asks"; else echo "FAIL busctl call: $(cat "$T/busctl-args")"; fail=1; fi
echo yes > "$T/can-hibernate"

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
