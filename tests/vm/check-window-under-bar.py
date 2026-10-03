"""F0/C11, on the host: check window visibility under the shell bar in a desktop screenshot from login.sh.
  python3 tests/vm/check-window-under-bar.py <desktop> <screenshot.png>
login.sh opens kitty; on a 1280×800 screen, niri places the window below the bar's reserved area (52 px),
with the `[arch@archlinux ~]$` prompt in rectangle x 4..190, y 56..72.
If its light pixels are present, the window is visible; otherwise the bar covers it (27.09: xray from a niri rule
painted wallpaper across the entire 84 px bar surface; see GOTCHAS). Prints
`[<desktop>] window-under-bar: visible` or `...: COVERED` (plus a line with the pixel count);
exit code 0 / 1."""
import os
import sys

from PIL import Image

name, path = sys.argv[1], sys.argv[2]
if not os.path.exists(path):
    print(f"[{name}] window-under-bar: NO-SHOT ({path} missing)")
    sys.exit(1)
im = Image.open(path).convert("RGB")
# The prompt text is light (~#dddddd) on kitty's black background; a correct screenshot has
# 400–560 such pixels (the cursor blinks), while a covered one has 0.
light = sum(1 for y in range(56, 73) for x in range(4, 191) if min(im.getpixel((x, y))) > 150)
state = "visible" if light >= 50 else "COVERED"
print(f"[{name}] window-under-bar: {state}")
print(f"[{name}]   light pixels in x 4..190, y 56..72: {light} (threshold 50)")
sys.exit(0 if state == "visible" else 1)
