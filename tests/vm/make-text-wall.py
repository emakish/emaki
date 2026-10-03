"""C11, on the host: “terminal with text” wallpaper for checking grid artifacts on glass.
  python3 tests/vm/make-text-wall.py <file.png> [width height]
Dark background with dense lines of light monospace text, like kitty output: fine,
bright details that poor blur underneath glass turned into a grid (27.09, a laptop at
1.25 over kitty — ~10 px cells on both axes). Use the VM's physical screen size
so wpaperd displays it pixel for pixel. Text is synthetic and deterministic."""
import random
import sys

from PIL import Image, ImageDraw, ImageFont

path = sys.argv[1]
width, height = (int(sys.argv[2]), int(sys.argv[3])) if len(sys.argv) > 3 else (1280, 800)
image = Image.new("RGB", (width, height), (34, 22, 30))
draw = ImageDraw.Draw(image)
try:
    font = ImageFont.truetype("DejaVuSansMono.ttf", 16)
except OSError:
    font = ImageFont.load_default(size=16)
rng = random.Random(27092026)
words = ["make", "install", "niri", "glass", "shell", "emaki", "status", "ok", "rc=0", "commit",
         "blur", "frame", "window", "launcher", "--help", "error:", "warning", "|", "->", "0x7f25"]
y = 4
while y < height:
    x = 6
    line = []
    while len(" ".join(line)) < rng.randint(40, 150):
        line.append(rng.choice(words))
    draw.text((x, y), " ".join(line), font=font, fill=(236, 226, 232))
    y += 22
image.save(path)
print(f"{path}: {width}x{height}")
