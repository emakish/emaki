# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Scene-linear A2 softness, 16-bit sRGB PNG; optional stability evidence."""
import sys
sys.dont_write_bytecode=True
import json,math,time,resource,argparse
from pathlib import Path
import OpenEXR,numpy as np,cv2
from polish_look import soften_distance,LOOK
cv2.setNumThreads(1)

def srgb(a):
    return np.where(a<=.0031308,12.92*a,1.055*np.maximum(a,0)**(1/2.4)-.055).clip(0,1)

def read_exr(path):
    exr=OpenEXR.File(str(path),separate_channels=True)
    channels={key:value for part in exr.parts for key,value in part.channels.items()}
    def read(label,components):
        names=[n for n in channels if f'.{label}.' in n]
        if not names: return None
        prefix=names[0].rsplit('.',1)[0]
        return np.stack([channels[prefix+'.'+c].pixels for c in components],axis=-1)
    return read('Combined','RGB'),read('Position','XYZ'),read('Noisy Image','RGB')

def main():
    p=argparse.ArgumentParser(); p.add_argument('--job',required=True); a=p.parse_args()
    job=json.loads(Path(a.job).read_text()); tick=time.perf_counter()
    stats=json.loads(Path(job['render_stats']).read_text())
    rgb,pos,raw=read_exr(job['exr'])
    depth=np.linalg.norm(pos-np.array(stats['camera'],dtype=np.float32),axis=-1)
    ppr=job['width']/(2*math.tan(math.radians(84/2)))
    # the probe look has no distance softening at all.
    soft=soften_distance(rgb,depth,ppr,wrap=False) if job.get('light','a2')=='a2' else rgb
    if not np.isfinite(soft).all(): raise RuntimeError('Non-finite filtered pixels')
    png=np.rint(srgb(soft)*65535).astype(np.uint16)
    if not cv2.imwrite(job['png_tmp'],png[:,:,::-1],[cv2.IMWRITE_PNG_COMPRESSION,3]): raise IOError('PNG write failed')
    stats['filter']=dict(pixels_per_radian=ppr,max_sigma=LOOK['max_sigma_at_5136']*ppr/(5136/math.tau),
        boundary='reflect',sharp_fraction=float((depth<=42).mean()),
        sharp_unchanged=bool(np.array_equal(rgb[depth<=42],soft[depth<=42])),
        depth_percentiles=np.percentile(depth,[0,25,50,75,95,100]).tolist())
    if job['diagnostics']:
        if raw is None: raise RuntimeError('Missing Noisy Image diagnostic pass')
        np.savez_compressed(job['diagnostic_path'],raw=raw,denoised=rgb,soft=soft,position=pos,depth=depth)
    stats['export_seconds']=time.perf_counter()-tick
    stats['export_peak_rss_mib']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024
    Path(job['render_stats']).write_text(json.dumps(stats,indent=2)+'\n')
if __name__=='__main__': main()
