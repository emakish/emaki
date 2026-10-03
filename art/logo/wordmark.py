#!/usr/bin/env python3
"""Emaki wordmark — variant 07 (chosen 2026-09-27): E is the mark from mark.py; lowercase “maki”
follows its rules: stems are parallelograms tilted by SPINE, horizontal bars have
45° cuts and STROKE thickness. Lowercase tops align with the E's middle bar (14 of 24); k has a
full-height stem and two 45° diagonals; the i dot aligns with the E's top bar; letters have a horizontal GAP
(tight spacing).
  python3 art/logo/wordmark.py    writes art/logo/wordmark.svg (flat, accent) and prints the path
Wordmark colors are not yet chosen; pixelation is a separate step based on this geometry."""
import math
import pathlib
import re
import sys

sys.dont_write_bytecode = True   # no __pycache__ in art/logo
from mark import FLAT, SPINE, STROKE, S, mark

X = 14        # lowercase height out of 24: top of the E's middle bar
COUNTER = 5   # horizontal gap between m stems and inside a
TAIL = 3      # a tail extending right past the stem
GAP = 2.5     # horizontal gap between letters

K = math.tan(math.radians(SPINE))
W = STROKE / math.cos(math.radians(SPINE))   # horizontal width of the slanted stem
T = STROKE * math.sqrt(2)                     # horizontal width of a 45° diagonal
YX = S - X                                    # lowercase top line


def left(xb, y):
    """Left edge of the stem at height y; xb is the left edge at the bottom."""
    return xb + (S - y) * K


def stem(xb, yt, yb=S):
    s = (yb - yt) * K
    return [(xb, yb), (xb + W, yb), (xb + W + s, yt), (xb + s, yt)]


def bar(xl, yb, length):
    """Bar like the E's: both ends cut at 45°; xl is the bottom-left corner."""
    return [(xl, yb), (xl + length, yb), (xl + length + STROKE, yb - STROKE), (xl + STROKE, yb - STROKE)]


def arm(xb, yb, yt):
    dy = yb - yt
    return [(xb, yb), (xb + T, yb), (xb + T + dy, yt), (xb + dy, yt)]


def leg(xt, yt, yb):
    dy = yb - yt
    return [(xt + dy, yb), (xt + dy + T, yb), (xt + T, yt), (xt, yt)]


def letter_m():
    p = W + COUNTER
    xl = left(0, YX + STROKE)
    right = left(2 * p, YX + STROKE) + W
    return [stem(0, YX), stem(p, YX), stem(2 * p, YX), bar(xl, YX + STROKE, right - xl)]


def letter_a():
    q = W + COUNTER
    xl = left(0, YX + STROKE)
    return [stem(q, YX), stem(0, YX),
            bar(xl, YX + STROKE, left(q, YX + STROKE) + W - xl),
            bar(0, S, left(q, S) + W + TAIL)]


def letter_k():
    ym = YX + X / 2
    xb = left(0, ym) + W - 2
    return [stem(0, 0), arm(xb, ym, YX), leg(xb, ym, S)]


def letter_i():
    return [stem(0, YX), stem(left(0, STROKE), 0, STROKE)]


def span(polys, y, side):
    """Outermost letter point at height y: side=max for right, min for left."""
    xs = []
    for poly in polys:
        for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
            if y1 != y2 and (y1 - y) * (y2 - y) <= 0:
                xs.append(x1 + (y - y1) * (x2 - x1) / (y2 - y1))
    return (max(xs) if side == "max" else min(xs)) if xs else None


def place(placed, new):
    """Shift a letter right so the gap to its neighbor is exactly GAP at every height."""
    shift = max(a + GAP - b for i in range(S * 100 + 1)
                if (a := span(placed, i / 100, "max")) is not None
                and (b := span(new, i / 100, "min")) is not None)
    return [[(x + shift, y) for x, y in p] for p in new]


def wordmark():
    nums = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", mark())]
    pts = list(zip(nums[0::2], nums[1::2]))
    polys = [pts[i:i + 4] for i in range(0, len(pts), 4)]
    for letter in (letter_m(), letter_a(), letter_k(), letter_i()):
        polys += place(polys, letter)
    width = max(x for p in polys for x, _ in p)
    num = lambda v: f"{round(v, 3) + 0.0:.3f}".rstrip("0").rstrip(".")
    d = "".join("M" + "L".join(f"{num(x)} {num(y)}" for x, y in p) + "Z" for p in polys)
    return d, width


if __name__ == "__main__":
    d, width = wordmark()
    here = pathlib.Path(__file__).resolve().parent
    (here / "wordmark.svg").write_text(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 '
                                       f'{round(width, 3)} {S}"><path fill="{FLAT}" d="{d}"/></svg>\n')
    print(d)
