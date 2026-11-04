# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Palette extraction and scene-linear recolouring from reusable Cycles passes."""
from pathlib import Path
import argparse
import json
import os
import time
import cv2
import numpy as np
import OpenImageIO as oiio
import PyOpenColorIO as ocio
from final_runner import inside, write_json

cv2.setNumThreads(4)
oiio.attribute('threads',4)
SOURCE_PALETTE=dict(base='#E78099',pale='#F5AEC2',deep='#A32C61',white='#FFFFFF')
CONFIG=os.environ.get('EMAKI_BLENDER_OCIO')

def linear(hex_value):
    a=np.array([int(hex_value[i:i+2],16)/255 for i in (1,3,5)],np.float32)
    return np.where(a<=.04045,a/12.92,((a+.055)/1.055)**2.4)

def extract(folder,output):
    folder=Path(folder);output=inside(output);output.mkdir(parents=True,exist_ok=True)
    src=cv2.imread(str(folder/'01-rose-light.png'))[:,:,::-1]
    small=src[::4,::4].astype(np.float32);points={}
    for k,h in SOURCE_PALETTE.items():
        if k=='white':continue
        rgb=np.array([int(h[i:i+2],16) for i in (1,3,5)])
        d=((small-rgb)**2).sum(2)
        ys,xs=np.unravel_index(np.argsort(d.ravel())[:64],d.shape)
        points[k]=[(int(x*4),int(y*4)) for x,y in zip(xs,ys)]
    records={}
    for path in sorted(folder.glob('*.png')):
        im=cv2.imread(str(path))[:,:,::-1]
        # The strips differ by a few pixels in width. Sample normalized positions.
        im=cv2.resize(im,(src.shape[1],src.shape[0]),interpolation=cv2.INTER_AREA)
        result={}
        for k,coords in points.items():
            rgb=np.median([im[y,x] for x,y in coords],axis=0).round().astype(int)
            result[k]='#'+''.join(f'{v:02X}' for v in rgb)
        rgb=np.median(im[src.min(2)>245],axis=0).round().astype(int) if 'dark' in path.stem or path.stem=='18-graphite' else np.array([255]*3)
        result['white']='#'+''.join(f'{v:02X}' for v in rgb)
        write_json(output/(path.stem+'.json'),result);records[path.stem]=result
    write_json(output/'sampling.json',dict(method='Median of 64 corresponding target-strip pixels nearest each calibrated rose material colour in the source strip; normalized image coordinates, stride-4 search. Dark neutral from source pixels with all channels >245.',
               source=str(folder/'01-rose-light.png'),source_palette=SOURCE_PALETTE,coordinates=points,palettes=records))
    return records

def read_passes(path):
    f=oiio.ImageInput.open(str(path))
    if not f:raise RuntimeError(oiio.geterror())
    result={};i=0
    while f.seek_subimage(i,0):
        name=f.spec().get_string_attribute('oiio:subimagename').split('.',1)[-1]
        result[name]=f.read_image(i,0,0,3,oiio.FLOAT)[:,:,:3]
        i+=1
    f.close()
    return result

def display(rgb,exposure=-.15):
    a=np.ascontiguousarray(np.maximum(rgb,0)*2**exposure,dtype=np.float32)
    if not CONFIG:
        raise RuntimeError('Set EMAKI_BLENDER_OCIO to the Blender 5.2 colormanagement/config.ocio file')
    cfg=ocio.Config.CreateFromFile(CONFIG)
    t=ocio.DisplayViewTransform(src='Linear Rec.709',display='sRGB',view='Khronos PBR Neutral')
    processor=cfg.getProcessor(t).getDefaultCPUProcessor()
    processor.applyRGB(a)
    return np.clip(a,0,1)

def save_rgb(path,a):
    path=inside(path);path.parent.mkdir(parents=True,exist_ok=True)
    if not cv2.imwrite(str(path),(np.clip(a[:,:,::-1],0,1)*65535).round().astype(np.uint16),[cv2.IMWRITE_PNG_COMPRESSION,3]):
        raise RuntimeError(f'Could not write {path}')

def prepare(passes):
    albedo=passes['SurfaceAlbedo']
    effective=passes['Diffuse Color']
    # Glossy/sheeny illumination is additive. It is small and smooth; filter only
    # this noisy pass, then retain Cycles' denoised Combined for all fine edges.
    spec=(passes['Glossy Direct']+passes['Glossy Indirect'])*passes['Glossy Color']
    spec=cv2.bilateralFilter(spec,5,.08,2)
    diffuse=np.maximum(passes['Combined']-spec,0)
    # Cycles includes the neutral sheen in Diffuse Color, not Glossy Color.
    # At each pixel effective_albedo = k * base_colour + neutral_sheen.
    # Solve that affine relationship across RGB so recolouring does not tint or
    # amplify the neutral sheen (especially visible in deep folds and silver).
    centred=albedo-albedo.mean(2,keepdims=True)
    variance=(centred*centred).sum(2)
    k=np.divide((centred*(effective-effective.mean(2,keepdims=True))).sum(2),
                variance,out=np.full(variance.shape,.97,np.float32),where=variance>1e-7)
    k=np.clip(k,0,1)[:,:,None]
    sheen=effective.mean(2,keepdims=True)-k*albedo.mean(2,keepdims=True)
    return effective,diffuse,spec,passes['PaletteWeights'],k,sheen

def recolour(prepared,palette):
    effective,diffuse,spec,weights,k,sheen=prepared
    primary=np.array([linear(palette[k]) for k in ['base','pale','deep']],np.float32)
    target=np.einsum('hwc,cd->hwd',weights,primary,optimize=True)
    target+=(1-weights.sum(2))[:,:,None]*linear(palette['white'])*.90
    # Shared neutral-bounce lighting; palette enters only the primary albedo.
    target_effective=k*np.maximum(target,0)+sheen
    ratio=np.maximum(target_effective,0)/np.maximum(effective,1e-5)
    return np.maximum(diffuse*ratio+spec,0)

def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    s=sub.add_parser('extract');s.add_argument('--strips',required=True);s.add_argument('--output',default='renders/r2-palettes')
    s=sub.add_parser('recolour');s.add_argument('--input',required=True);s.add_argument('--palette',required=True);s.add_argument('--output',required=True)
    s=sub.add_parser('all');s.add_argument('--input',required=True);s.add_argument('--palettes',default='renders/r2-palettes');s.add_argument('--output',required=True)
    a=p.parse_args()
    if a.action=='extract':print(json.dumps(extract(a.strips,a.output),indent=2));return
    start=time.perf_counter();passes=read_passes(inside(a.input));prepared=prepare(passes);load=time.perf_counter()-start
    if a.action=='recolour':
        palette=json.loads(inside(a.palette).read_text());rgb=recolour(prepared,palette);save_rgb(a.output,display(rgb))
        print(json.dumps(dict(load_seconds=load,total_seconds=time.perf_counter()-start)))
    else:
        times={};out=inside(a.output);out.mkdir(parents=True,exist_ok=True)
        for path in sorted(inside(a.palettes).glob('[0-9][0-9]-*.json')):
            t=time.perf_counter();palette=json.loads(path.read_text())
            save_rgb(out/(path.stem+'.png'),display(recolour(prepared,palette)))
            times[path.stem]=time.perf_counter()-t
        write_json(out/'timings.json',dict(load_seconds=load,palettes=times,total_seconds=time.perf_counter()-start))

if __name__=='__main__':main()
