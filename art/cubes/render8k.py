# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""A2 neutral at master size, rendered in overlapping panorama bands so memory stays low.

Same scene and look as polish.py (cubes.py + polish_look 'neutral'). Each band uses the same
central-cylindrical camera with a narrower u range, so every pixel sees the rays it would see in one
big render; bands overlap and are cross-faded, then the distance softening runs on the closed 360 image.

Render (resumable, finished bands are skipped):
  blender -b --factory-startup -noaudio -t 4 --python render8k.py -- \
      --width 27000 --height 4320 --bands 20 --out work/a2-8k
Assemble (system python):
  python render8k.py --assemble work/a2-8k --name variant-a2-neutral-8k
"""
import argparse
import json
import math
import os
from pathlib import Path
import sys
import time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import cubes as A
from polish_look import LOOK, apply_lighting, soften_distance

OVERLAP = 160          # extra real rays on each side of a band
FADE = 128             # cross-fade width, centred on each band boundary (must be <= 2 * OVERLAP)
SHIP_HEIGHTS = (2160, 2400)


def render(args):
    import bpy
    import resource
    if args.width % args.bands:
        raise SystemExit('width must divide into equal bands')
    scene_args = argparse.Namespace(variant='A', guard=0, time=0., width=args.width,
                                    height=args.height, samples=args.samples)
    scene, stats = A.build_scene(scene_args)
    if args.light == 'neutral':
        apply_lighting(scene, 'neutral')
    else:
        # no side-light shadows — keep cubes.py's original soft overhead light (variant A).
        scene.view_layers[0].use_pass_position = True
    scene.render.threads = args.threads
    scene.render.use_persistent_data = True
    camera = scene.camera.data
    du = math.tau / args.width
    core = args.width // args.bands
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    meta = dict(width=args.width, height=args.height, bands=args.bands, core=core, overlap=OVERLAP,
                fade=FADE, samples=args.samples, threads=args.threads, light=args.light,
                camera_location=list(scene.camera.location), scene=stats, look=LOOK, band_times={})
    meta_path = out/'meta.json'
    if meta_path.exists():
        meta['band_times'] = json.loads(meta_path.read_text()).get('band_times', {})
    for k in range(args.bands):
        target = out/f'band-{k:02d}.exr'
        if target.exists():
            continue
        first, last = k*core - OVERLAP, (k+1)*core + OVERLAP
        camera.central_cylindrical_range_u_min = -math.pi + first*du
        camera.central_cylindrical_range_u_max = -math.pi + last*du
        scene.render.resolution_x = last - first
        partial = out/f'band-{k:02d}.partial.exr'
        scene.render.filepath = str(partial)
        tick = time.perf_counter()
        bpy.ops.render.render(write_still=True)
        partial.rename(target)
        meta['band_times'][str(k)] = dict(seconds=round(time.perf_counter()-tick, 1),
            peak_rss_mib=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024))
        meta_path.write_text(json.dumps(meta, indent=2)+'\n')
        print('BAND_DONE', k, meta['band_times'][str(k)], flush=True)
    meta_path.write_text(json.dumps(meta, indent=2)+'\n')
    print('RENDER_DONE', flush=True)
    sys.stdout.flush(); sys.stderr.flush(); os._exit(0)


def read_band(path):
    import OpenEXR
    import numpy as np
    exr = OpenEXR.File(str(path), separate_channels=True)
    channels = {key: value for part in exr.parts for key, value in part.channels.items()}
    def read(label, components):
        prefix = next(n.rsplit('.', 1)[0] for n in channels if f'.{label}.' in n)
        return np.stack([channels[prefix+'.'+c].pixels for c in components], axis=-1).astype(np.float32)
    return read('Combined', 'RGB'), read('Position', 'XYZ')


def seam_stats(png, joins):
    import numpy as np
    step = np.abs(png.astype(np.float32) - np.roll(png.astype(np.float32), 1, axis=1)).mean(axis=(0, 2))
    def at(x):
        return dict(column=int(x), mae=float(step[x]), percentile=float(100*(step <= step[x]).mean()))
    return dict(all_adjacent_column_mae=float(step.mean()), wrap=at(0), band_joins=[at(x) for x in joins])


def assemble(out, name, dest, ship=True, sharp=False):
    import cv2
    import numpy as np
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    out = Path(out)
    meta = json.loads((out/'meta.json').read_text())
    W, H, N, core, O, C = (meta[k] for k in ('width', 'height', 'bands', 'core', 'overlap', 'fade'))
    rgb = np.zeros((H, W, 3), np.float32)
    pos = np.zeros((H, W, 3), np.float32)
    weight = np.zeros(W, np.float32)
    for k in range(N):
        band_rgb, band_pos = read_band(out/f'band-{k:02d}.exr')
        x = np.arange(k*core - O, (k+1)*core + O)
        centre = x + .5
        w = (np.clip((centre - (k*core - C/2))/C, 0, 1) *
             np.clip((((k+1)*core + C/2) - centre)/C, 0, 1)).astype(np.float32)
        cols = x % W
        rgb[:, cols] += band_rgb*w[None, :, None]
        pos[:, cols] += band_pos*w[None, :, None]
        weight[cols] += w
    if not np.allclose(weight, 1, atol=1e-5):
        raise SystemExit(f'band weights do not sum to 1: {weight.min()}..{weight.max()}')
    depth = np.linalg.norm(pos - np.array(meta['camera_location'], np.float32), axis=-1)
    del pos
    # Softening on the closed 360 image, in column chunks with real circular neighbours.
    ppr = W/math.tau
    sigma = LOOK['max_sigma_at_5136']*ppr/(5136/math.tau)
    margin = 2*(math.ceil(sigma*4)+2) + 16
    soft = rgb if sharp else np.empty_like(rgb)
    chunk = 2048
    for c0 in range(0, W, chunk) if not sharp else ():
        c1 = min(W, c0+chunk)
        idx = np.arange(c0-margin, c1+margin) % W
        part = soften_distance(rgb[:, idx], depth[:, idx], ppr, wrap=False)
        soft[:, c0:c1] = part[:, margin:margin+c1-c0]
    if not sharp:
        del rgb

    def srgb(a):
        return np.rint(np.clip(A.linear_to_srgb(a), 0, 1)*255).astype(np.uint8)
    renders = Path(dest)
    renders.mkdir(parents=True, exist_ok=True)
    report = dict(meta=meta, outputs={})
    images = {f'{name}-{W}x{H}': soft}
    for h in SHIP_HEIGHTS if ship else ():
        w = round(W*h/H)
        images[f'{name}-{w}x{h}'] = cv2.resize(soft, (w, h), interpolation=cv2.INTER_AREA)
    for label, linear in images.items():
        png = srgb(linear)
        h, w = png.shape[:2]
        Image.fromarray(png).save(renders/f'{label}.png')
        Image.fromarray(np.concatenate((png[:, -600:], png[:, :600]), axis=1)).save(renders/f'{label}-loopcheck.png')
        scale = w/W
        joins = sorted({round(k*core*scale) % w for k in range(1, N)})
        report['outputs'][label] = dict(size=[w, h], seams=seam_stats(png, joins))
        print(label, json.dumps(report['outputs'][label]['seams']['wrap']), flush=True)
    master = srgb(soft)
    x0 = round(1800*W/5136)
    Image.fromarray(master[:, x0:x0+round(1200*W/5136)]).save(renders/f'{name}-detail.png')
    (renders/f'{name}-report.json').write_text(json.dumps(report, indent=2)+'\n')
    print('ASSEMBLE_DONE', flush=True)


def main():
    if '--assemble' in sys.argv:
        ap = argparse.ArgumentParser()
        ap.add_argument('--assemble', required=True)
        ap.add_argument('--name', required=True)
        ap.add_argument('--dest', default=str(ROOT/'renders'))
        ap.add_argument('--no-ship', action='store_true')
        ap.add_argument('--sharp', action='store_true', help='skip the distance softening')
        args = ap.parse_args()
        assemble(args.assemble, args.name, args.dest, not args.no_ship, args.sharp)
        return
    ap = argparse.ArgumentParser()
    ap.add_argument('--width', type=int, required=True)
    ap.add_argument('--height', type=int, required=True)
    ap.add_argument('--bands', type=int, required=True)
    ap.add_argument('--samples', type=int, default=96)
    ap.add_argument('--threads', type=int, default=4)
    ap.add_argument('--light', choices=['neutral', 'original'], default='neutral')
    ap.add_argument('--out', required=True)
    args = ap.parse_args(sys.argv[sys.argv.index('--')+1:])
    render(args)


if __name__ == '__main__':
    main()
