# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""New camera-only compositions of the unchanged probe-1 world.

Run with ./render-compositions.sh. Never calls either original output pipeline.
"""
import argparse
import json
import math
import os
from pathlib import Path
import resource
import sys
import time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import video as V
import numpy as np

# cubes.py sets these at import; redirect all subsequent temporary/cache writes.
os.environ['XDG_CACHE_HOME'] = str(ROOT / 'compositions/cache')
os.environ['TMPDIR'] = str(ROOT / 'compositions/tmp')

CANDIDATES = {
    '01-basin': dict(title='Basin / centred amphitheatre', camera=(.23, -.17, 27.), yaw=32., down=27., hfov=66.),
    '03-overlook': dict(title='Overlook / diagonal terraces', camera=(-7.77, -.17, 37.), yaw=45., down=38., hfov=68.),
    '04-wide': dict(title='Wide / open bowl', camera=(.23, -6.17, 32.), yaw=28., down=31., hfov=84.),
}


def geometry(config, phase):
    """Exact probe-1 heights/colour hash, expanded camera-relative culling only."""
    cx, cy, _ = config['camera']
    yaw = math.radians(config['yaw'])
    sy, co = math.sin(yaw), math.cos(yaw)
    tan_h = math.tan(math.radians(config['hfov']/2))
    down = math.radians(config['down'])
    # The bottom perspective corners have a wider ground-plane azimuth than
    # HFOV/2 when the camera tilts down. Cull against those rays, not HFOV alone.
    tangent = tan_h / (math.cos(down) - (tan_h/2.4)*math.sin(down))
    far, guard = 220., 8.
    corners = [(cx+d*sy+s*co, cy+d*co-s*sy)
               for d in (-guard, far) for s in (-far*tangent-guard, far*tangent+guard)]
    xs, ys = zip(*corners)
    i, j = np.meshgrid(np.arange(math.floor(min(xs)), math.ceil(max(xs))+1),
                       np.arange(math.floor(min(ys)), math.ceil(max(ys))+1), indexing='ij')
    dx, dy = i+.5-cx, j+.5-cy
    depth, side = dx*sy+dy*co, dx*co-dy*sy
    visible = (depth > -guard) & (depth < far) & (np.abs(side) < np.maximum(depth, 0)*tangent+guard)
    i, j = i[visible], j[visible]
    h = V.heights(i, j, phase, 1)
    low = np.minimum.reduce([h, V.heights(i-1,j,phase,1), V.heights(i+1,j,phase,1),
                             V.heights(i,j-1,phase,1), V.heights(i,j+1,phase,1)])
    low = np.maximum(0, low-2)
    count = h-low
    ii, jj = np.repeat(i,count), np.repeat(j,count)
    starts = np.repeat(np.cumsum(count)-count,count)
    kk = np.repeat(low,count)+np.arange(count.sum())-starts
    colors = V.hash32(ii,jj,kk)%4
    pos = np.column_stack((ii+.5,jj+.5,kk+.5)).astype(np.float32)
    audit = dict(columns=len(h), cubes=len(pos), height_range=[int(h.min()),int(h.max())],
                 grid_error=float(np.max(np.abs(pos-.5-np.rint(pos-.5)))), far=far, guard=guard)
    return [pos[colors==n] for n in range(4)], audit


def main():
    import bpy
    ap = argparse.ArgumentParser()
    ap.add_argument('--candidate', choices=CANDIDATES, required=True)
    ap.add_argument('--phase', type=float, choices=[0., .5], required=True)
    ap.add_argument('--width', type=int, default=1920)
    ap.add_argument('--samples', type=int, default=32)
    args = ap.parse_args(sys.argv[sys.argv.index('--')+1:])
    config = CANDIDATES[args.candidate]
    V.CAMERA, V.YAW, V.DOWN, V.HFOV = config['camera'], config['yaw'], config['down'], config['hfov']
    start = time.perf_counter()
    scene, carriers, light, bounds = V.setup(args)
    scene.camera.data.clip_end = 500
    positions, audit = geometry(config, args.phase)
    for obj, pos in zip(carriers,positions):
        mesh = bpy.data.meshes.new('Composition voxel centres')
        mesh.vertices.add(len(pos)); mesh.vertices.foreach_set('co',pos.ravel()); mesh.update()
        old = obj.data; obj.data = mesh; bpy.data.meshes.remove(old)
    name = f'{args.candidate}-phase-{args.phase:g}'
    scene.render.filepath = str(ROOT / 'compositions' / (name+'.png'))
    tick = time.perf_counter()
    bpy.ops.render.render(write_still=True)
    elapsed = time.perf_counter()-start
    result = dict(candidate=args.candidate, config=config, phase=args.phase,
                  width=scene.render.resolution_x, height=scene.render.resolution_y,
                  render_seconds=time.perf_counter()-tick, total_seconds=elapsed,
                  engine=scene.render.engine, device=scene.cycles.device,
                  samples=scene.cycles.samples, threads=scene.render.threads,
                  nice=os.getpriority(os.PRIO_PROCESS,0), affinity=sorted(os.sched_getaffinity(0)),
                  peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,
                  master_dimensions=bounds, geometry=audit)
    (ROOT / 'compositions' / (name+'-stats.json')).write_text(json.dumps(result,indent=2)+'\n')
    print('COMPOSITION_DONE '+json.dumps(result),flush=True)
    sys.stdout.flush(); sys.stderr.flush(); os._exit(0)


if __name__ == '__main__':
    main()
