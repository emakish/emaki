# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Rounded monochrome cursors, drawn from paths on a 24-unit canvas.

Every shape is a solid body with a contrasting outline that lies wholly outside it, one unit
wide (one pixel at 24 px), plus a soft drop shadow. Thin strokes are centred on half units, so
at 24 px and its integer multiples they cover whole pixels. Pillow rasterizes at six times
the requested size before area downsampling. Both palettes share geometry, hotspots and
timing. No external artwork is used.
"""
import math

from PIL import Image, ImageDraw, ImageFilter

FRAMES = 12
FRAME_MS = 80
SUPERSAMPLE = 6
OUTLINE = 1.0
# (body, outline) per variant.
PALETTES = {'white': ('#ffffff', '#1d1e21'), 'dark': ('#1d1e21', '#ffffff')}
SHADOW = dict(alpha=0.32, blur=0.75, offset=0.8)
HOTSPOTS = {name: (12, 12) for name in (
    'text', 'wait', 'not-allowed', 'crosshair', 'move', 'grab', 'grabbing',
    'ew-resize', 'ns-resize', 'nwse-resize', 'nesw-resize')}
HOTSPOTS.update(default=(4, 3), progress=(4, 3), pointer=(9, 3))
# Text and crosshair strokes are centred on 12.5 (see render); their hotspot follows the stroke.
HOTSPOTS.update(text=(12.5, 12.5), crosshair=(12.5, 12.5))


def frames(name):
    return FRAMES if name in ('wait', 'progress') else 1


def rounded(points, radius=0.45):
    """Trim polygon corners and join them with quadratic curves."""
    result = []
    for i, point in enumerate(points):
        before, after = points[i - 1], points[(i + 1) % len(points)]
        lengths = [math.dist(point, p) for p in (before, after)]
        trim = min(radius, min(lengths) / 3)
        start, end = [tuple(a + (b - a) * trim / length for a, b in zip(point, p))
                      for p, length in zip((before, after), lengths)]
        for j in range(9):
            t = j / 8
            result.append(tuple((1 - t)**2 * a + 2 * t * (1 - t) * b + t*t * c
                                for a, b, c in zip(start, point, end)))
    return result


def path(start, commands):
    """Flatten line and quadratic segments; control points remain editable source."""
    result = [start]
    for command in commands:
        if len(command) == 2:
            result.append(command)
        else:
            origin = result[-1]
            control, end = command[:2], command[2:]
            for step in range(1, 17):
                t = step / 16
                result.append(tuple((1-t)**2*a + 2*t*(1-t)*b + t*t*c
                                    for a, b, c in zip(origin, control, end)))
    return result


def around(points, cx=12, cy=12, factor=1.0, angle=0.0):
    """Scale and rotate points about a centre."""
    c, s = math.cos(angle), math.sin(angle)
    return [(cx + factor * ((x-cx)*c - (y-cy)*s), cy + factor * ((x-cx)*s + (y-cy)*c))
            for x, y in points]


def arc(cx, cy, radius, start, sweep, steps=64):
    return [(cx + radius*math.cos(start + sweep*t/steps), cy + radius*math.sin(start + sweep*t/steps))
            for t in range(steps + 1)]


# A classic arrow: straight left edge, a tail notch and a slanted stem.
ARROW = rounded([(4, 3), (4, 18.6), (7.9, 15.1), (10.5, 20.6), (13.3, 19.3),
                 (10.8, 14.0), (15.9, 14.0)], 0.55)
POINTER = path((7.5, 12), [
    (7.5, 4.3), (7.5, 3, 9, 3), (10.5, 3, 10.5, 4.3), (10.5, 9.3),
    (11.2, 8.5, 12.1, 9), (13, 9.3, 13, 10.1),
    (14, 9.3, 15, 10), (15.7, 10.4, 15.7, 11.2),
    (17.5, 10.5, 18.2, 12), (18.8, 14, 17.5, 17.1),
    (16.5, 18.6, 16.3, 20.2), (8.7, 20.2), (8.4, 18.5, 7, 17.3),
    (4.5, 14.6), (3.3, 13.3, 4.3, 12.3), (5.3, 11.4, 6.5, 12.6), (7.5, 13.6)])
OPEN = path((6.6, 12), [
    (5.9, 7), (5.6, 5.5, 7, 5.3), (8.4, 5.1, 8.6, 6.6), (9.1, 10.5),
    (9.1, 4.6), (9.1, 3.1, 10.5, 3.1), (11.9, 3.1, 11.9, 4.6), (11.9, 10),
    (12.6, 5.4), (12.8, 4, 14.1, 4.2), (15.5, 4.4, 15.2, 5.9), (14.6, 11),
    (16.2, 7.7), (16.8, 6.5, 18, 7.1), (19.2, 7.7, 18.5, 9.1),
    (17.2, 13.2), (16.9, 15.7, 15.5, 18), (15.4, 20), (8.8, 20),
    (8.4, 18.5, 6.8, 17.2), (3.9, 14), (2.9, 12.8, 4, 11.9),
    (4.9, 11.2, 5.8, 12.1), (6.9, 13.3)])
FIST = path((6.5, 11), [
    (6.5, 8.7), (6.5, 7.2, 8, 7.2), (9, 7.2, 9.5, 8),
    (10.4, 6.4, 11.8, 7.1), (12.5, 7.3, 12.7, 8),
    (14, 6.8, 15.2, 7.7), (15.7, 8.1, 15.7, 8.8),
    (17.6, 7.8, 18.2, 9.8), (18.7, 13.4, 16.4, 16.5),
    (15.8, 17.5, 15.7, 19), (8.6, 19), (8.2, 17.5, 6.9, 16.5),
    (4.6, 13.7), (3.5, 12.4, 4.4, 11.4), (5.3, 10.5, 6.7, 11.8), (8.6, 13.3)])
# Two-headed arrow along x through (12, 12): shaft 3 units thick, heads 9 units wide.
DOUBLE = [(3.5, 12), (8, 7.5), (8, 10.5), (16, 10.5), (16, 7.5), (20.5, 12),
          (16, 16.5), (16, 13.5), (8, 13.5), (8, 16.5)]
def _move():
    """Four-way arrow: shaft half-width 1.2, heads 3.7 units long and 3 units each side."""
    arm = [(13.2, 10.8), (13.2, 6.5), (15, 6.5), (12, 2.8), (9, 6.5), (10.8, 6.5)]
    points = []
    for quarter in range(4):
        points += around(arm, angle=-quarter * math.pi / 2)
    return points


MOVE = _move()


def render(name, nominal, variant, frame=0):
    """Return a straight-alpha RGBA image and its integer hotspot."""
    fill, edge = PALETTES[variant]
    scale = nominal * SUPERSAMPLE / 24
    extent = nominal * SUPERSAMPLE
    foreground = Image.new('RGBA', (extent, extent))
    draw = ImageDraw.Draw(foreground)

    def xy(points):
        return [(x*scale, y*scale) for x, y in points]

    def line(points, colour, width, closed=False):
        """A polyline with round joins and caps."""
        pts = xy(points + points[:1] if closed else points)
        stroke = max(1, round(width * scale))
        draw.line(pts, fill=colour, width=stroke, joint='curve')
        r = stroke / 2
        for x, y in (pts if closed else (pts[0], pts[-1])):
            draw.ellipse((x-r, y-r, x+r, y+r), fill=colour)

    def shape(points):
        # Outline first at twice its width, so the body covers the inner half.
        line(points, edge, 2 * OUTLINE, closed=True)
        draw.polygon(xy(points), fill=fill)

    def strokes(lines, width):
        # Outline the union first so crossing strokes do not acquire internal seams.
        for points in lines:
            line(points, edge, width + 2 * OUTLINE)
        for points in lines:
            line(points, fill, width)

    def spinner(cx, cy, radius, width):
        # An open arc, three quarters of a turn, that rotates clockwise frame by frame.
        start = frame * math.tau / FRAMES - math.pi / 2
        strokes([arc(cx, cy, radius, start, math.pi * 1.5, 48)], width)

    if name in ('default', 'progress'):
        shape(ARROW)
        if name == 'progress':
            spinner(17, 17.6, 3.1, 1.5)
    elif name in ('pointer', 'grab', 'grabbing'):
        shape({'pointer': POINTER, 'grab': OPEN, 'grabbing': FIST}[name])
        if name == 'pointer':
            for x, y in ((10.5, 11.0), (13, 11.6), (15.6, 12.3)):
                line([(x, y), (x, 14.2)], edge, 0.7)
        elif name == 'grabbing':
            for x, y in ((9.5, 8.8), (12.6, 8.8), (15.5, 9.6)):
                line([(x, y), (x, 11)], edge, 0.7)
    elif name == 'text':
        # Centred on 12.5 so the 1-unit stem fills whole pixels at 24 px (column 12).
        strokes([[(12.5, 5), (12.5, 19)], [(10, 4.5), (11.5, 5), (12.5, 6)],
                 [(15, 4.5), (13.5, 5), (12.5, 6)], [(10, 19.5), (11.5, 19), (12.5, 18)],
                 [(15, 19.5), (13.5, 19), (12.5, 18)]], 1.0)
    elif name == 'wait':
        spinner(12, 12, 6.4, 2.2)
    elif name == 'not-allowed':
        circle = arc(12, 12, 6.8, 0, math.tau, 96)
        strokes([circle, around([(12, 5.4), (12, 18.6)], angle=-math.pi/4)], 2.0)
    elif name == 'crosshair':
        c = 12.5
        strokes([[(c, 3.5), (c, 9.5)], [(c, 15.5), (c, 21.5)],
                 [(3.5, c), (9.5, c)], [(15.5, c), (21.5, c)]], 1.0)
    elif name == 'move':
        shape(rounded(MOVE, 0.4))
    elif name.endswith('-resize'):
        angle = {'ew-resize': 0, 'ns-resize': math.pi/2,
                 'nwse-resize': math.pi/4, 'nesw-resize': -math.pi/4}[name]
        factor = 0.95 if angle % (math.pi/2) else 1.0
        shape(rounded(around(DOUBLE, factor=factor, angle=angle), 0.4))
    else:
        raise ValueError(name)

    blur = foreground.getchannel('A').filter(ImageFilter.GaussianBlur(SHADOW['blur'] * scale))
    shadow = Image.new('RGBA', foreground.size, (0, 0, 0, 0))
    shadow.putalpha(blur.point(lambda alpha: round(alpha * SHADOW['alpha'])))
    canvas = Image.new('RGBA', foreground.size)
    canvas.alpha_composite(shadow, (0, round(SHADOW['offset'] * scale)))
    canvas.alpha_composite(foreground)
    canvas = canvas.resize((nominal, nominal), Image.Resampling.BOX)
    # The hotspot is the pixel that contains the hotspot point.
    xhot, yhot = (math.floor(value * nominal / 24) for value in HOTSPOTS[name])
    return canvas, xhot, yhot
