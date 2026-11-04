# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Encode a PNG sequence and measure its actual decoded loop at native size."""
from pathlib import Path
import argparse
import json
import os
import subprocess
import time
import cv2
import numpy as np
from final_runner import inside, valid_png, write_json

cv2.setNumThreads(4)

def delta(a,b):
    d=a.astype(np.float32)-b.astype(np.float32)
    return dict(mae=float(np.abs(d).mean()),rmse=float(np.sqrt(np.mean(d*d))),maximum=float(np.abs(d).max()))

def summary(steps,seam):
    result={'ordinary_steps':{},'last_to_first':seam}
    for key in ['mae','rmse']:
        a=[d[key] for d in steps]
        result['ordinary_steps'][key]=dict(min=float(min(a)),median=float(np.median(a)),max=float(max(a)),mean=float(np.mean(a)))
        result['seam_'+key+'_over_median']=seam[key]/max(float(np.median(a)),1e-12)
        result['passes_'+key+'_max_step']=seam[key]<=max(a)
    result['mae_each_step']=[d['mae'] for d in steps]
    return result

def read_png(path):
    a=cv2.imread(str(path),cv2.IMREAD_UNCHANGED)
    if a is None:raise RuntimeError(f'Cannot read {path}')
    return a.astype(np.float32)*(255/65535 if a.dtype==np.uint16 else 1)

def measure_pngs(paths,endpoint=None):
    first=read_png(paths[0]);previous=first;steps=[]
    for path in paths[1:]:
        current=read_png(path);steps.append(delta(current,previous));previous=current
    out=summary(steps,delta(previous,first))
    if endpoint is not None:out['endpoint_vs_first']=delta(read_png(endpoint),first)
    return out

def measure_video(path):
    cap=cv2.VideoCapture(str(path));first=None;previous=None;steps=[];count=0
    while True:
        ok,a=cap.read()
        if not ok:break
        if first is None:first=a
        else:steps.append(delta(a,previous))
        previous=a;count+=1
    cap.release()
    if count<2:raise RuntimeError('Video contains fewer than two decodable frames')
    return dict(frame_count=count,width=first.shape[1],height=first.shape[0],**summary(steps,delta(previous,first)))

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',default='renders/r2-final-frames')
    p.add_argument('--output',default='renders/r2-final-3840x2160-hevc10.mp4')
    p.add_argument('--start',type=int,default=0);p.add_argument('--end',type=int,default=233)
    p.add_argument('--fps',type=int,default=24);p.add_argument('--threads',type=int,default=4)
    p.add_argument('--codec',choices=['hevc','h264'],default='hevc')
    p.add_argument('--proof-only',action='store_true')
    a=p.parse_args();folder=inside(a.input);out=inside(a.output);out.parent.mkdir(parents=True,exist_ok=True)
    if a.end<=a.start or a.fps<1 or a.threads<1:p.error('At least two frames and positive fps/threads required')
    paths=[folder/f'frame-{f:04d}.png' for f in range(a.start,a.end+1)]
    dimensions=None
    for path in paths:
        im=cv2.imread(str(path),cv2.IMREAD_UNCHANGED)
        if im is None:raise RuntimeError(f'Missing frame {path}')
        if dimensions is None:dimensions=im.shape[:2]
        if im.shape[:2]!=dimensions:raise RuntimeError('Mixed frame sizes')
        if not valid_png(path,dimensions[1],dimensions[0]):raise RuntimeError('Missing PNG')
    count=len(paths);start=time.perf_counter()
    if not a.proof_only:
        temp=out.with_name(out.stem+'.part'+out.suffix)
        cmd=['nice','-n','19','ffmpeg','-hide_banner','-y','-threads',str(a.threads),
             '-framerate',str(a.fps),'-start_number',str(a.start),'-i',str(folder/'frame-%04d.png'),
             '-frames:v',str(count),'-an','-filter_threads',str(a.threads)]
        if a.codec=='hevc':
            cmd+=['-c:v','libx265','-threads',str(a.threads),'-preset','slow','-crf','16','-pix_fmt','yuv420p10le',
                  '-x265-params',f'pools={a.threads}:frame-threads=1:open-gop=0:log-level=error','-tag:v','hvc1']
        else:
            cmd+=['-c:v','libx264','-threads',str(a.threads),'-preset','slow','-crf','16','-pix_fmt','yuv420p']
        cmd+=['-vf','scale=out_color_matrix=bt709:out_range=tv','-color_primaries','bt709',
              '-color_trc','iec61966-2-1','-colorspace','bt709','-color_range','tv','-movflags','+faststart',str(temp)]
        with out.with_suffix('.ffmpeg.log').open('w') as log:subprocess.run(cmd,check=True,stdout=log,stderr=subprocess.STDOUT)
        os.replace(temp,out)
    endpoint=folder/f'frame-{round(9.75*a.fps):04d}.png'
    full=(a.start==0 and count==round(9.75*a.fps))
    proof=dict(metric='sRGB code-value MAE/RMSE over every native pixel, scaled to 0–255',
               width=dimensions[1],height=dimensions[0],fps=a.fps,included_frames=[a.start,a.end],
               duration_seconds=count/a.fps,full_period=full,
               note='Full-loop test' if full else 'Short pipeline test; this range is not a complete loop',
               source_png=measure_pngs(paths,endpoint if full and endpoint.exists() else None),
               decoded_video=measure_video(out),assembly_seconds=time.perf_counter()-start)
    if proof['decoded_video']['frame_count']!=count:raise RuntimeError('Encoded frame count mismatch')
    if (folder/'geometry-loop.json').exists():proof['geometry']=json.loads((folder/'geometry-loop.json').read_text())
    write_json(out.with_suffix('.loop-proof.json'),proof)
    print(json.dumps({k:v for k,v in proof.items() if k not in ['source_png','decoded_video','geometry']},indent=2))

if __name__=='__main__':main()
