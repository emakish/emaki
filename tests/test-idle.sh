#!/bin/sh
# Isolated emaki-idle test: swayidle, brightnessctl, niri and emaki-lock are PATH fakes.
# Nothing here dims, locks or touches this machine's idle state.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/.cache"
T=$(mktemp -d "$ROOT/.cache/idle-XXXXXXXX")
S="$ROOT/scripts/emaki-idle"
UNIT="$ROOT/systemd/emaki-idle.service"
REAL_RUNTIME="${XDG_RUNTIME_DIR:-}"
export XDG_RUNTIME_DIR="$T/runtime"
mkdir -p "$XDG_RUNTIME_DIR"

mk() { printf '#!/bin/sh\n%s\n' "$2" > "$T/$1"; chmod +x "$T/$1"; }
# swayidle: one argument per line, then exit.
mk swayidle 'printf "%s\n" "$@" > "'"$T"'/swayidle-args"'
# brightnessctl 0.5.1: record the call. Like the real one, --machine-readable describes only
# the first device of the class unless --list asks for all of them; every set/save/restore
# acts on one device (the first, or the one named with --device).
mk brightnessctl 'echo "brightnessctl $*" >> "'"$T"'/calls"; p=$(cat "'"$T"'/percent"); q=$(cat "'"$T"'/percent2"); case " $* " in *" --list "*) echo "amdgpu_bl1,backlight,$((p * 655)),$p%,65535"; echo "acpi_video0,backlight,$q,$q%,100"; [ -e "'"$T"'/extra" ] && cat "'"$T"'/extra" ;; *" --machine-readable "*) echo "amdgpu_bl1,backlight,$((p * 655)),$p%,65535" ;; esac; exit 0'
echo 40 > "$T/percent2"
mk niri      'echo "niri $*" >> "'"$T"'/calls"'
mk emaki-lock 'echo "emaki-lock $*" >> "'"$T"'/calls"'
cp "$S" "$T/emaki-idle"

fail=0
ok()   { echo "ok   $1"; }
bad()  { echo "FAIL $1: $2"; fail=1; }

argv_is() {   # argv_is "<name>" "<expected swayidle args separated by |>" [env...]
    name="$1"; want="$2"; shift 2
    : > "$T/swayidle-args"
    (cd "$T" && env -u EMAKI_IDLE_DIM -u EMAKI_IDLE_LOCK -u EMAKI_IDLE_OFF "$@" PATH="$T:$PATH" emaki-idle $ONLY >/dev/null 2>&1)
    rc=$?
    got=$(tr '\n' '|' < "$T/swayidle-args" | sed 's/|$//')
    if [ "$rc" -eq 0 ] && [ "$got" = "$want" ]; then ok "$name"; else bad "$name" "rc=$rc got [$got] want [$want]"; fi
}
refuses() {   # refuses "<name>" [env...]: exit 2 and swayidle never runs
    name="$1"; shift
    : > "$T/swayidle-args"
    (cd "$T" && env "$@" PATH="$T:$PATH" emaki-idle $ONLY >/dev/null 2>&1)
    rc=$?
    if [ "$rc" -eq 2 ] && [ ! -s "$T/swayidle-args" ]; then ok "$name"; else bad "$name" "rc=$rc args [$(cat "$T/swayidle-args")]"; fi
}

self="$T/emaki-idle"
DIM="timeout|150|$self --dim|resume|$self --undim"
LOCK="timeout|300|emaki-lock"
OFF="timeout|330|niri msg action power-off-monitors|resume|niri msg action power-on-monitors"
ONLY=
argv_is "defaults: dim 150, lock 300, off 330, nothing else" "-w|$DIM|$LOCK|$OFF"
argv_is "timings from the environment" "-w|timeout|5|$self --dim|resume|$self --undim|timeout|7|emaki-lock|timeout|9|niri msg action power-off-monitors|resume|niri msg action power-on-monitors" EMAKI_IDLE_DIM=5 EMAKI_IDLE_LOCK=7 EMAKI_IDLE_OFF=9
argv_is "0 removes the dim step" "-w|$LOCK|$OFF" EMAKI_IDLE_DIM=0
argv_is "0 removes the lock step" "-w|$DIM|$OFF" EMAKI_IDLE_LOCK=0
argv_is "0 removes the screen-off step" "-w|$DIM|$LOCK" EMAKI_IDLE_OFF=0
refuses "every step off refuses" EMAKI_IDLE_DIM=0 EMAKI_IDLE_LOCK=0 EMAKI_IDLE_OFF=0
refuses "a non-number refuses" EMAKI_IDLE_LOCK=soon
refuses "a negative number refuses" EMAKI_IDLE_DIM=-5
argv_is "an empty value means the default" "-w|$DIM|$LOCK|$OFF" EMAKI_IDLE_OFF=
ONLY="--only dim";  argv_is "--only dim"  "-w|$DIM"
ONLY="--only lock"; argv_is "--only lock" "-w|$LOCK"
ONLY="--only off";  argv_is "--only off"  "-w|$OFF"
ONLY="--only nap";  refuses "--only with an unknown step refuses"
ONLY="extra";       refuses "an unknown argument refuses"
ONLY=

# The dim step: every backlight device to half of its own level, never up, saved once,
# restored exactly - and only the devices it dimmed.
dim_calls() {   # dim_calls "<name>" <percent> <percent2> "<expected brightnessctl calls separated by |>"
    echo "$2" > "$T/percent"; echo "$3" > "$T/percent2"; : > "$T/calls"; rm -f "$XDG_RUNTIME_DIR/emaki-idle-dimmed"
    (cd "$T" && PATH="$T:$PATH" emaki-idle --dim >/dev/null 2>&1)
    rc=$?
    got=$(tr '\n' '|' < "$T/calls" | sed 's/|$//')
    if [ "$rc" -eq 0 ] && [ "$got" = "$4" ]; then ok "$1"; else bad "$1" "rc=$rc got [$got] want [$4]"; fi
}
list="brightnessctl --class=backlight --list --machine-readable"
set1="brightnessctl --class=backlight --device=amdgpu_bl1 --save set"
set2="brightnessctl --class=backlight --device=acpi_video0 --save set"
dim_calls "dim at 60 % and 40 % sets 30 % and 20 %" 60 40 "$list|$set1 30%|$set2 20%"
dim_calls "dim at 5 % sets 2 %, never 10 %" 5 40 "$list|$set1 2%|$set2 20%"
dim_calls "a device at 1 % is left alone, the other dims" 1 40 "$list|$set2 20%"
dim_calls "dim at 0 % and 1 % changes nothing" 0 1 "$list"
[ ! -e "$XDG_RUNTIME_DIR/emaki-idle-dimmed" ] && ok "nothing dimmed, no flag" || bad "nothing dimmed" "flag written"
printf '%s\n' 'bad*name,backlight,50,50%,100' ',backlight,50,50%,100' 'odd,backlight,x,x%,100' > "$T/extra"
dim_calls "a device name with glob characters, none, or no number is skipped" 60 40 "$list|$set1 30%|$set2 20%"
rm -f "$T/extra"
undim_calls() {   # undim_calls "<name>" <percent> <percent2> "<expected calls>": dim twice, undim twice
    echo "$2" > "$T/percent"; echo "$3" > "$T/percent2"; : > "$T/calls"; rm -f "$XDG_RUNTIME_DIR/emaki-idle-dimmed"
    (cd "$T" && PATH="$T:$PATH" emaki-idle --dim && PATH="$T:$PATH" emaki-idle --dim && PATH="$T:$PATH" emaki-idle --undim && PATH="$T:$PATH" emaki-idle --undim) >/dev/null 2>&1
    got=$(tr '\n' '|' < "$T/calls" | sed 's/|$//')
    if [ "$got" = "$4" ] && [ ! -e "$XDG_RUNTIME_DIR/emaki-idle-dimmed" ]; then ok "$1"; else bad "$1" "got [$got] want [$4]"; fi
}
undim_calls "a second dim saves nothing; undim restores each device once and clears the flag" 60 40 \
    "$list|$set1 30%|$set2 20%|brightnessctl --class=backlight --device=amdgpu_bl1 --restore|brightnessctl --class=backlight --device=acpi_video0 --restore"
undim_calls "undim restores only the devices the dim changed" 1 40 \
    "$list|$set2 20%|brightnessctl --class=backlight --device=acpi_video0 --restore"
: > "$T/calls"
(cd "$T" && PATH="$T:$PATH" emaki-idle --undim) >/dev/null 2>&1
[ ! -s "$T/calls" ] && ok "undim without a dim does nothing" || bad "undim without a dim" "$(cat "$T/calls")"

# The never-sleep rule as a test: no code line of the script or the unit sleeps the machine.
# (The header comment keeps the history of why; comments are not code.)
sleepers='suspend|hibernate|hybrid-sleep|systemctl sleep|loginctl suspend|IdleAction'
if grep -v '^[[:space:]]*#' "$S" | grep -Eqi "$sleepers"; then bad "no sleep in emaki-idle" "$(grep -v '^[[:space:]]*#' "$S" | grep -Ei "$sleepers")"; else ok "no sleep words in emaki-idle code"; fi
if grep -v '^[[:space:]]*#' "$S" | grep -Eq 'lock .emaki-lock|before-sleep'; then bad "one owner per job" "idle still handles lock/before-sleep"; else ok "idle leaves logind Lock and PrepareForSleep to the guard"; fi
if [ -f "$UNIT" ]; then
    python3 "$ROOT/scripts/render-paths" --text "$UNIT" > "$T/emaki-idle.service"
    UNIT="$T/emaki-idle.service"
    if grep -v '^[[:space:]]*#' "$UNIT" | grep -Eqi "$sleepers"; then bad "no sleep in the unit" "$(grep -Ei "$sleepers" "$UNIT")"; else ok "no sleep words in emaki-idle.service"; fi
    for line in 'ConditionPathIsDirectory=!/etc/emaki-live' 'StartLimitIntervalSec=0' 'Restart=on-failure' 'RestartSec=2' 'RestartPreventExitStatus=2 127 255' 'ExecStart=/usr/bin/emaki-settings-power run' 'ConditionUser=!root' 'Requisite=graphical-session.target'; do
        grep -qxF "$line" "$UNIT" && ok "unit: $line" || bad "unit: $line" "missing"
    done
    grep -q 'StartLimitBurst' "$UNIT" && bad "unit: StartLimitBurst" "present (only StartLimitIntervalSec=0 disables rate limiting)" || ok "unit: no StartLimitBurst"
    # systemd resets its restart counter only on a manual start, reset-failed or
    # RESTART_RESET=1 (systemd 262 src/core/service.c), so with RestartSteps= the delay
    # stays at its maximum for the rest of the session after a few exits.
    if grep -v '^[[:space:]]*#' "$UNIT" | grep -Eq '^(RestartSteps|RestartMaxDelaySec)='; then
        bad "unit: flat restart delay" "RestartSteps/RestartMaxDelaySec present; nothing resets the counter in a session"
    else
        ok "unit: flat restart delay (no RestartSteps/RestartMaxDelaySec)"
    fi
    # Only an explicit personal settings choice enables the policy for future sessions.
    grep -qxF 'WantedBy=graphical-session.target' "$UNIT" && ok "unit: personal session enablement" || bad "unit: personal session enablement" "missing"
    if grep -q '^enable emaki-idle.service' "$ROOT/systemd/50-emaki.preset"; then bad "unit: inert by default" "distribution preset enables idle"; else ok "unit: inert by default"; fi
    if grep -v '^[[:space:]]*//' "$ROOT/niri/default.kdl" | grep -q 'emaki-idle'; then bad "default.kdl starts emaki-idle" "it must not before the live trials"; else ok "default.kdl does not start emaki-idle"; fi
    if command -v systemd-analyze >/dev/null 2>&1; then
        # Exit 0 with a misspelt key: only the message tells. Exit non-zero: the unit was
        # not checked (no user manager here) - say so, never pass silently.
        # Verify this checkout's executable payload without requiring installation.
        mkdir -p "$T/payload/bin" "$T/verify"
        cp "$ROOT/scripts/emaki-settings-power" "$T/payload/bin/"
        python3 "$ROOT/scripts/render-paths" --prefix "$T/payload" \
            --text "$ROOT/systemd/emaki-idle.service" > "$T/verify/emaki-idle.service"
        out=$(XDG_RUNTIME_DIR="$REAL_RUNTIME" systemd-analyze --user verify "$T/verify/emaki-idle.service" 2>&1); rc=$?
        if [ "$rc" -ne 0 ]; then
            case "$out" in
                *"Failed to initialize manager"*) echo "skip unit: systemd-analyze could not start a user manager here; the unit was not checked" ;;
                *) bad "unit: systemd-analyze" "rc=$rc $out" ;;
            esac
        else
            case "$out" in *"Unknown key"*|*"Failed to parse"*) bad "unit: systemd-analyze" "$out" ;; *) ok "unit: systemd-analyze knows every key" ;; esac
        fi
    fi
else
    echo "skip systemd/emaki-idle.service: not in this tree"
fi

rm -rf "$T"
[ "$fail" -eq 0 ] && echo "ALL GREEN"
exit "$fail"
