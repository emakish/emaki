#!/usr/bin/env python3
"""Emaki mark — a slanted E made of parallelograms (concept of 2026-09-27): a “/” stem and three
parallel bars of equal length, with ends cut sharper than the stem; the bars extend slightly
past the stem on the left and far to the right; the mark fills the entire 24×24 square.
  python3 art/logo/mark.py        prints the SVG path, writes art/logo/mark.svg (flat, accent)
                                  and art/logo/mark-large.svg (large, “Hot” gradient)
The same path is in shell/Icon.qml (kind "logo"); after changing a value, copy the path there.
The mark uses the “Hot” gradient along its slant everywhere, including the launcher button (2026-09-27):
light at the top right, deep at the bottom left; mark.svg is the flat accent version. These colors are only
for the mark; they are not in tokens.toml and the interface does not use them."""
import math
import pathlib

SPINE = 16    # stem tilt from vertical, degrees (“16 degrees looks best”)
ENDS = 45     # acute angle of bar ends, degrees (the stem would be 74)
STROKE = 4    # stem and bar thickness out of 24
OUT = 1       # bar extension left of the stem, measured at its midpoint
S = 24
FLAT = "#e2733f"   # tokens.toml accent
HOT = [(0, "#b01e78"), (.3, "#ec2a55"), (.62, "#ff6a2a"), (1, "#ffb62e")]   # bottom left → top right


def mark() -> str:
    rad = math.pi / 180
    k, m = math.tan(SPINE * rad), 1 / math.tan(ENDS * rad)
    h, w = STROKE, STROKE / math.cos(SPINE * rad)
    a = OUT + (h / 2) * m - (h / 2) * k           # bottom bar starts at the left edge
    spine_l = lambda y: a + (S - y) * k
    x0 = lambda yb: spine_l(yb - h / 2) - OUT - (h / 2) * m
    length = S - x0(h) - h * m                     # top bar reaches the right edge
    num = lambda v: f"{round(v, 3) + 0.0:.3f}".replace(".000", "")   # + 0.0: no "-0"
    poly = lambda pts: "M" + "L".join(f"{num(x)} {num(y)}" for x, y in pts) + "Z"
    bar = lambda yb: poly([(x0(yb), yb), (x0(yb) + length, yb), (x0(yb) + length + h * m, yb - h), (x0(yb) + h * m, yb - h)])
    y0, y1 = h / 2, S - h / 2                      # stem ends inside the top and bottom bars
    spine = poly([(spine_l(y1), y1), (spine_l(y1) + w, y1), (spine_l(y0) + w, y0), (spine_l(y0), y0)])
    return spine + bar(h) + bar((S + h) / 2) + bar(S)


if __name__ == "__main__":
    d = mark()
    here = pathlib.Path(__file__).resolve().parent
    (here / "mark.svg").write_text(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
                                   f'<path fill="{FLAT}" d="{d}"/></svg>\n')
    stops = "".join(f'<stop offset="{o}" stop-color="{c}"/>' for o, c in HOT)
    (here / "mark-large.svg").write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><defs><linearGradient id="hot" '
        f'gradientUnits="userSpaceOnUse" x1="3" y1="24" x2="21" y2="0">{stops}</linearGradient></defs>'
        f'<path fill="url(#hot)" d="{d}"/></svg>\n')
    print(d)
