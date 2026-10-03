"""F0, INSIDE the VM, as root: real pointer input through uinput — like a qemu tablet
(absolute ABS_X/ABS_Y + buttons; udev sees a mouse, libinput an absolute pointer).
Clicks follow the same path as physical input: kernel → libinput → niri → shell's wl_pointer.
  sudo python3 guest-pointer.py <steps…>
Steps: `move:<fx>,<fy>` (screen width/height fractions 0..1), `glide:<fx0>,<fy0>,<fx1>,<fy1>,<ms>`,
`click`, `wheel:<clicks>` (+ up, − down), `wait:<seconds>`.
Example: move:0.05,0.04 wait:0.5 click
Requires python-evdev (sudo pacman -S --needed python-evdev)."""
import sys
import time

from evdev import AbsInfo, UInput, ecodes as e

RANGE = 32767
caps = {
    e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE],
    e.EV_REL: [e.REL_WHEEL],
    e.EV_ABS: [(e.ABS_X, AbsInfo(0, 0, RANGE, 0, 0, 0)), (e.ABS_Y, AbsInfo(0, 0, RANGE, 0, 0, 0))],
}
ui = UInput(caps, name="emaki-test-pointer", bustype=e.BUS_USB)
# libinput takes time to pick up a new device.
time.sleep(1.5)
for step in sys.argv[1:]:
    kind, _, arg = step.partition(":")
    if kind == "move":
        fx, fy = (float(v) for v in arg.split(","))
        # Two events: libinput sends no motion if the position matches the previous one.
        for dx in (1, 0):
            ui.write(e.EV_ABS, e.ABS_X, min(RANGE, round(fx * RANGE) + dx))
            ui.write(e.EV_ABS, e.ABS_Y, round(fy * RANGE))
            ui.syn()
            time.sleep(0.05)
    elif kind == "glide":
        # glide:<fx0>,<fy0>,<fx1>,<fy1>,<ms> — steady motion, one event every 8 ms.
        fx0, fy0, fx1, fy1, ms = (float(v) for v in arg.split(","))
        n = max(1, round(ms / 8))
        for i in range(n + 1):
            t = i / n
            ui.write(e.EV_ABS, e.ABS_X, round((fx0 + (fx1 - fx0) * t) * RANGE))
            ui.write(e.EV_ABS, e.ABS_Y, round((fy0 + (fy1 - fy0) * t) * RANGE))
            ui.syn()
            time.sleep(0.008)
    elif kind == "click":
        ui.write(e.EV_KEY, e.BTN_LEFT, 1)
        ui.syn()
        time.sleep(0.08)
        ui.write(e.EV_KEY, e.BTN_LEFT, 0)
        ui.syn()
    elif kind == "wheel":
        for _ in range(abs(int(arg))):
            ui.write(e.EV_REL, e.REL_WHEEL, 1 if int(arg) > 0 else -1)
            ui.syn()
            time.sleep(0.3)
    elif kind == "wait":
        time.sleep(float(arg))
    else:
        sys.exit(f"unknown step: {step}")
    time.sleep(0.1)
time.sleep(0.3)
ui.close()
