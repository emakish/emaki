#!/usr/bin/env python3
"""Emaki's GRUB background: a smooth wave in the logo colours on a deep magenta field.

The layout follows EndeavourOS's GRUB splash (one picture, no theme), mirrored: GRUB prints
its help lines bottom-left, so the hot crest sits bottom-right where it does not hide them.

Usage: background.py OUT.png [WIDTH HEIGHT]   (default 1920x1200; GRUB stretches it)
"""
import sys

import numpy as np
from PIL import Image

LOGO = ["#b01e78", "#ec2a55", "#ff6a2a", "#ffb62e"]   # art/logo/mark.py HOT, magenta → yellow
FIELD_TOP, FIELD_LOW = "#3a0832", "#5c1150"          # deep logo magenta
SWELL = ["#7c1766", "#a82a86"]                       # the far, pink wave


def hex_rgb(h):
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], float) / 255


def to_linear(c):
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def to_srgb(c):
    c = np.clip(c, 0, 1)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


def lin_to_oklab(c):
    m1 = np.array([[0.4122214708, 0.5363325363, 0.0514459929],
                   [0.2119034982, 0.6806995451, 0.1073969566],
                   [0.0883024619, 0.2817188376, 0.6299787005]])
    m2 = np.array([[0.2104542553, 0.7936177850, -0.0040720468],
                   [1.9779984951, -2.4285922050, 0.4505937099],
                   [0.0259040371, 0.7827717662, -0.8086757660]])
    return np.cbrt(c @ m1.T) @ m2.T


def oklab_to_lin(c):
    m1 = np.array([[1, 0.3963377774, 0.2158037573],
                   [1, -0.1055613458, -0.0638541728],
                   [1, -0.0894841775, -1.2914855480]])
    m2 = np.array([[4.0767416621, -3.3077115913, 0.2309699292],
                   [-1.2684380046, 2.6097574011, -0.3413193965],
                   [-0.0041960863, -0.7034186147, 1.7076147010]])
    return (c @ m1.T) ** 3 @ m2.T


def ramp(stops, t):
    """Linear-light colours for t in [0,1] along hex stops, interpolated in OKLab."""
    labs = np.array([lin_to_oklab(to_linear(hex_rgb(s))) for s in stops])
    pos = np.linspace(0, 1, len(stops))
    t = np.clip(t, 0, 1)
    out = np.stack([np.interp(t, pos, labs[:, k]) for k in range(3)], -1)
    return np.clip(oklab_to_lin(out), 0, 1)


def smooth(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


def sigmoid(x):
    return 1 / (1 + np.exp(-x))


class Canvas:
    def __init__(self, w, h, top, low):
        """Background: top colour fading to low colour towards the bottom (in OKLab)."""
        self.w, self.h = w, h
        self.x = np.arange(w, dtype=float)
        self.y = np.arange(h, dtype=float)[:, None]
        col = ramp([top, low], smooth(0.0, 1.0, np.arange(h) / h))
        self.img = np.broadcast_to(col[:, None, :], (h, w, 3)).copy()

    def glow(self, colour, cx, cy, rx, ry, strength):
        r2 = ((self.x[None, :] - cx) / rx) ** 2 + ((self.y - cy) / ry) ** 2
        self.img += np.exp(-r2)[..., None] * colour * strength

    def wave(self, edge, colours, soft, shade, rim, haze, haze_reach, fade=None):
        """Fill below y = edge(x) with per-column colours; rim = brighter line at the edge,
        haze = light the wave throws on the field above it."""
        ye = edge(self.x)
        d = (self.y - ye[None, :]) / np.sqrt(1 + np.gradient(ye)[None, :] ** 2)
        a = sigmoid(d / soft)
        if fade is not None:
            a = a * fade(self.x)[None, :]
        depth = np.clip(d / (self.h * 0.35), 0, 1)
        fill = colours[None, :, :] * (1 - shade * depth)[..., None]
        light = np.exp(-(d / (soft * 2.2)) ** 2) * rim
        fill = fill + light[..., None] * colours[None, :, :] * 0.7
        above = np.exp(-np.clip(-d, 0, None) / haze_reach) * (1 - a) * haze
        if fade is not None:
            above = above * fade(self.x)[None, :]
        self.img += above[..., None] * colours[None, :, :]
        self.img = self.img * (1 - a[..., None]) + fill * a[..., None]

    def save(self, path, seed=7):
        rng = np.random.default_rng(seed)
        srgb = to_srgb(self.img) * 255
        srgb += rng.uniform(-0.5, 0.5, srgb.shape)     # dither against banding
        Image.fromarray(np.clip(np.round(srgb), 0, 255).astype(np.uint8)).save(path, optimize=True)


def background(w, h):
    c = Canvas(w, h, FIELD_TOP, FIELD_LOW)
    c.glow(to_linear(hex_rgb(LOGO[0])), w * 0.55, h * 0.7, w * 0.5, h * 0.4, 0.10)

    def swell(x):
        u = x / w
        return h * (0.90 - 0.07 * smooth(0.25, 1.0, u) + 0.015 * np.sin(u * 5.0))
    c.wave(swell, ramp(SWELL, c.x / w), soft=h * 0.02, shade=0.25,
           rim=0.6, haze=0.10, haze_reach=h * 0.10)

    def crest(x):
        u = x / w
        return h * (0.71 + 0.22 * smooth(0.08, 0.55, u))
    c.wave(crest, ramp(LOGO[::-1], 1.6 * c.x / w), soft=h * 0.012, shade=0.2,
           rim=0.3, haze=0.20, haze_reach=h * 0.08, fade=lambda x: 1 - smooth(0.42, 0.62, x / w))
    c.img = c.img[:, ::-1].copy()      # crest to the right, away from GRUB's help lines
    return c


if __name__ == "__main__":
    w, h = (int(sys.argv[2]), int(sys.argv[3])) if len(sys.argv) > 3 else (1920, 1200)
    background(w, h).save(sys.argv[1])
