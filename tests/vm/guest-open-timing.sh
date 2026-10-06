#!/bin/bash
# C11/F0, INSIDE the VM (as arch, logged in): Qt windows and first frames when opening panels.
#   tests/vm/ssh.sh 'bash -s -- <desktop>' < tests/vm/guest-open-timing.sh
# Restart the shell with QSG_RENDER_TIMING=1 (frame times for each QQuickWindow in the journal),
# open the launcher twice, the shade and right panel once each; use the journal to identify the Qt window
# drawing each panel (expect one window for all) and the slowest of its first three frames.
# Finally, unset the variable and restart the shell normally.
# 27.09: each opening created a new window (Quickshell PanelWindow visible:false destroys the
# window), with a first frame of 17–77 ms on a laptop. Prints
# `[<desktop>] overlay-windows: ok|BAD …` and `[<desktop>] open-first-frame: ok|BAD …`.
name=${1:-glass}
export XDG_RUNTIME_DIR=/run/user/1000
export WAYLAND_DISPLAY=$(ls $XDG_RUNTIME_DIR | grep -E '^wayland-[0-9]+$' | head -1)
export NIRI_SOCKET=$(ls $XDG_RUNTIME_DIR/niri.*.sock 2>/dev/null | head -1)
c() { emaki-shell call "$@" >/dev/null 2>&1; }
systemctl --user set-environment QSG_RENDER_TIMING=1
systemctl --user restart emaki-shell
sleep 8
niri msg action focus-workspace-down >/dev/null; sleep 1.5
since=$(date '+%Y-%m-%d %H:%M:%S')
sleep 1
marks=()
for step in "launcher open" "launcher open" "drawer open" "system open sound"; do
    marks+=("$(date +%s%6N)")
    c $step
    sleep 1.5
    c ${step%% *} close
    sleep 1.2
done
sleep 0.5
journalctl --user -u emaki-shell --since "$since" --no-pager -o short-unix > /tmp/open-timing.log
systemctl --user unset-environment QSG_RENDER_TIMING
systemctl --user restart emaki-shell
niri msg action focus-workspace-up >/dev/null
python3 - "$name" /tmp/open-timing.log "${marks[@]}" <<'EOF'
import re
import sys

name, path, marks = sys.argv[1], sys.argv[2], [int(m) / 1e6 for m in sys.argv[3:]]
frame = re.compile(r"^(\d+\.\d+) .*\[window (0x[0-9a-f]+)\]\[render thread [^]]*\] syncAndRender: frame rendered in (\d+)ms, sync=(\d+), render=(\d+), swap=(\d+)")
start = re.compile(r"\[window (0x[0-9a-f]+)\]")
first_seen = {}
frames = []
for line in open(path, errors="replace"):
    line = re.sub(r"\x1b\[[0-9;]*m", "", line)
    m = start.search(line)
    if m and m.group(1) not in first_seen:
        first_seen[m.group(1)] = float(line.split()[0])
    m = frame.match(line)
    if m:
        frames.append((float(m.group(1)), m.group(2), int(m.group(3)), int(m.group(4)), int(m.group(5)), int(m.group(6))))
windows = sorted(first_seen, key=first_seen.get)
print(f"[{name}]   Qt windows in the journal across 4 openings: {len(windows)}")
# Windows drawing before the first opening (the bar redraws every 0.4 s at rest) are not the
# panel: on a launcher opening the bar animates too and can outdraw the panel (27.09, 36 vs 30).
idle = sorted({f[1] for f in frames if f[0] < marks[0]})
print(f"[{name}]   drawing while idle before opening (not a panel): {', '.join(idle) or 'none'}")
worst = []
panel_windows = []
for i, m in enumerate(marks):
    after = [f for f in frames if 0 <= f[0] - m < .6 and f[1] not in idle]
    # Busiest non-idle window of this opening = the panel's window.
    per = {}
    for f in after:
        per.setdefault(f[1], []).append(f)
    if not per:
        worst.append(None)
        continue
    w = max(per, key=lambda k: len(per[k]))
    panel_windows.append(w)
    first3 = per[w][:3]
    worst.append(max(f[2] - f[5] for f in first3))  # total minus swap (vsync wait)
    print(f"[{name}]   opening {i + 1}: window {w}, first frames " + ", ".join(f"{f[2]} ms (sync {f[3]}, render {f[4]}, swap {f[5]})" for f in first3))
ok_windows = len(set(panel_windows)) == 1 and len(panel_windows) == len(marks)
print(f"[{name}]   panel window across 4 openings: {len(set(panel_windows))} distinct (expect one)")
print(f"[{name}] overlay-windows: {'ok' if ok_windows else f'BAD windows={len(set(panel_windows))}'}")
repeat = [v for v in worst[1:] if v is not None]
best = min(repeat) if repeat else None
ok_first = best is not None and best < 16
print(f"[{name}]   first frame excluding vsync wait on repeat openings: {repeat} ms (best < 16)")
print(f"[{name}] open-first-frame: {'ok' if ok_first else f'BAD best={best}'}")
sys.exit(0 if ok_windows and ok_first else 1)
EOF
