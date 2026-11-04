# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Isolated, resumable CPU band renderer and memory-bounded palette compositor.

Uses its own guard only. Every output is under renders/s8k-*; old scripts/palettes
are imported read-only with bytecode disabled. No video-final state is consulted.
"""
import os,sys
os.environ.update(OMP_NUM_THREADS='4',OPENBLAS_NUM_THREADS='4',MKL_NUM_THREADS='4',NUMEXPR_NUM_THREADS='4',PYTHONDONTWRITEBYTECODE='1')
sys.dont_write_bytecode=True
from pathlib import Path
import argparse,ctypes,fcntl,gc,hashlib,json,math,mmap,resource,signal,subprocess,time
import cv2,numpy as np
import OpenImageIO as oiio
from PIL import Image,ImageDraw
import colour_pipeline as cp
ROOT=Path(__file__).resolve().parent
WORK=ROOT/'renders/s8k-work';PROOF=ROOT/'renders/s8k-proofs'
cv2.setNumThreads(4);oiio.attribute('threads',4);Image.MAX_IMAGE_PIXELS=None
OVERLAP=160;FADE=128

def jsonwrite(p,data):
    q=p.with_suffix(p.suffix+'.tmp');q.write_text(json.dumps(data,indent=2)+'\n');os.replace(q,p)

def render(width,height,first,last,target,samples=32,save=False):
    if target.exists() and target.with_suffix('.json').exists():return
    env=os.environ.copy();env.update(TMPDIR=str(ROOT/'renders/s8k-tmp'),XDG_CACHE_HOME=str(ROOT/'renders/s8k-cache'))
    for key in ('TMPDIR','XDG_CACHE_HOME'):Path(env[key]).mkdir(parents=True,exist_ok=True)
    cmd=['nice','-n','19','blender','-b','-noaudio','--factory-startup','--threads','4','--python',str(ROOT/'strips8k_scene.py'),'--','--width',str(width),'--height',str(height),'--first',str(first),'--last',str(last),'--samples',str(samples),'--out',str(target)]
    if save:cmd.append('--save-blend')
    with (WORK/'render.guard').open('a') as guard:
        fcntl.flock(guard,fcntl.LOCK_EX)
        start=time.perf_counter()
        with target.with_suffix('.log').open('w') as log:
            child=subprocess.Popen(cmd,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,pass_fds=(guard.fileno(),),start_new_session=True)
            peak=0
            try:
                while child.poll() is None:
                    rss=0
                    for pid in [child.pid,os.getpid()]:
                        try:
                            text=(Path('/proc')/str(pid)/'status').read_text()
                            rss+=int(next(q.split()[1] for q in text.splitlines() if q.startswith('VmRSS:')))*1024
                        except (FileNotFoundError,StopIteration):pass
                    peak=max(peak,rss)
                    if rss>9.3*1024**3:raise RuntimeError('S8K render/compositor memory guard exceeded 9.3 GiB; use narrower bands')
                    time.sleep(1)
                if child.returncode:raise subprocess.CalledProcessError(child.returncode,cmd)
            finally:
                if child.poll() is None:
                    os.killpg(child.pid,signal.SIGTERM)
                    try:child.wait(timeout=10)
                    except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait()
        meta=json.loads(target.with_suffix('.json').read_text());meta['process_seconds']=time.perf_counter()-start;meta['monitored_combined_peak_rss_mib']=peak/1024**2
        jsonwrite(target.with_suffix('.json'),meta)
    print('RENDERED',target.name,json.dumps(meta),flush=True)

def coefficients(path):
    """Exact affine expansion of colour_pipeline.recolour, before display.

    4 material RGB coefficient triplets plus additive RGB. Crossfading these
    coefficients is exactly equivalent to crossfading each theme's linear rays.
    """
    prepared=cp.prepare(cp.read_passes(path))
    effective,diffuse,spec,weights,k,sheen=prepared
    inverse=1/np.maximum(effective,1e-5)
    light=diffuse*k*inverse
    c=np.empty((*effective.shape[:2],5,3),np.float32)
    for i in range(3):c[:,:,i,:]=light*weights[:,:,i,None]
    c[:,:,3,:]=light*(1-weights.sum(2))[:,:,None]*.9
    c[:,:,4,:]=diffuse*sheen*inverse+spec
    return c

def colour(c,palette):
    prim=np.array([cp.linear(palette[k]) for k in ['base','pale','deep','white']],np.float32)
    return np.maximum(np.einsum('...kc,kc->...c',c[...,:4,:],prim,optimize=True)+c[...,4,:],0)

def weights(first,last,lo,hi):
    x=np.arange(first,last);center=x+.5
    w=np.clip((center-(lo-FADE/2))/FADE,0,1)*np.clip(((hi+FADE/2)-center)/FADE,0,1)
    return x,w.astype(np.float32)

def bands(width,height,count,samples):
    dest=WORK/f'master-{width}x{height}';dest.mkdir(exist_ok=True)
    bounds=np.rint(np.linspace(0,width,count+1)).astype(int).tolist()
    cfg=dict(width=width,height=height,bounds=bounds,count=count,overlap=OVERLAP,fade=FADE,samples=samples,threads=4,
             sources={n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in ['strips8k_scene.py','ribbons.py','ribbons_round2.py','colour_pipeline.py']})
    manifest=dest/'job.json'
    if manifest.exists() and json.loads(manifest.read_text())!=cfg:raise RuntimeError('This output belongs to different source/settings')
    jsonwrite(manifest,cfg)
    for i,(lo,hi) in enumerate(zip(bounds,bounds[1:])):
        exr=dest/f'band-{i:02d}.exr';npy=dest/f'band-{i:02d}.npy'
        if npy.exists():continue
        render(width,height,lo-OVERLAP,hi+OVERLAP,exr,samples,save=False)
        t=time.perf_counter();c=coefficients(exr)
        temp=npy.with_suffix('.tmp.npy');np.save(temp,c);del c;os.replace(temp,npy);exr.unlink()
        gc.collect();ctypes.CDLL(None).malloc_trim(0)
        print('COEFFICIENTS',i,round(time.perf_counter()-t,2),flush=True)
    return dest,cfg

def stitch(dest,cfg):
    width,height=cfg['width'],cfg['height'];target=dest/'coefficients.npy'
    if target.exists():return target
    path=dest/'coefficients.tmp.npy';full=np.lib.format.open_memmap(path,mode='w+',dtype='float32',shape=(height,width,5,3));full[:]=0
    total=np.zeros(width,np.float32)
    for i,(lo,hi) in enumerate(zip(cfg['bounds'],cfg['bounds'][1:])):
        c=np.load(dest/f'band-{i:02d}.npy',mmap_mode='r')
        x,w=weights(lo-OVERLAP,hi+OVERLAP,lo,hi);idx=x%width;total[idx]+=w
        for y in range(0,height,64):full[y:y+64,idx]+=c[y:y+64]*w[None,:,None,None]
        full.flush();full._mmap.madvise(mmap.MADV_DONTNEED);del c
    if not np.allclose(total,1,atol=2e-6):raise RuntimeError(f'Weights {total.min()}..{total.max()}')
    del full;os.replace(path,target);jsonwrite(dest/'stitch.json',dict(weight_min=float(total.min()),weight_max=float(total.max()),method='128 px pixel-centred linear crossfade, 160 px real-ray guards per side; periodic wrap included'))
    return target

def decode(a):return np.where(a<=.04045,a/12.92,((a+.055)/1.055)**2.4)
def encode(a):return np.where(a<=.0031308,a*12.92,1.055*np.maximum(a,0)**(1/2.4)-.055)

def column_stats(a,joins):
    # Work in row chunks to stay below the memory limit on the 116.7 MP image.
    h,w=a.shape[:2];steps=np.zeros(w,np.float64)
    for y in range(0,h,64):
        b=a[y:y+64].astype(np.float32)/257
        steps[1:]+=np.abs(b[:,1:]-b[:,:-1]).sum((0,2));steps[0]+=np.abs(b[:,0]-b[:,-1]).sum()
    steps/=h*3
    ordinary=steps[1:]
    def at(x):
        local=steps[(np.arange(x-100,x+101)%w)];local=np.delete(local,100)
        return dict(column=int(x),mae_255=float(steps[x]),ordinary_percentile=float((ordinary<=steps[x]).mean()*100),local_200_mean=float(local.mean()),ratio_to_local=float(steps[x]/max(local.mean(),1e-9)))
    return dict(ordinary_mean=float(ordinary.mean()),ordinary_median=float(np.median(ordinary)),ordinary_p95=float(np.percentile(ordinary,95)),ordinary_max=float(ordinary.max()),wrap=at(0),joins=[at(x) for x in joins])

def png(path,a):
    temp=path.with_name(path.stem+'.tmp.png')
    if not cv2.imwrite(str(temp),a[:,:,::-1],[cv2.IMWRITE_PNG_COMPRESSION,3]):raise RuntimeError(str(path))
    os.replace(temp,path)

def ship(coeff,cfg):
    width,height=cfg['width'],cfg['height'];c=np.load(coeff,mmap_mode='r')
    records={};proof=[]
    for p in sorted((ROOT/'renders/r2-palettes').glob('[0-9][0-9]-*.json')):
        theme=p.stem;record_path=PROOF/(theme+'-delivery.json')
        if record_path.exists():records[theme]=json.loads(record_path.read_text());continue
        tick=time.perf_counter();palette=json.loads(p.read_text())
        master=np.lib.format.open_memmap(WORK/'encoded.tmp.npy',mode='w+',dtype='uint16',shape=(height,width,3))
        linear=np.lib.format.open_memmap(WORK/'linear.tmp.npy',mode='w+',dtype='float32',shape=(height,width,3))
        for y in range(0,height,64):
            s=cp.display(colour(c[y:y+64],palette))
            master[y:y+64]=np.rint(np.clip(s,0,1)*65535).astype(np.uint16)
            # Quantize before decoding: downsizes come from the delivered master.
            linear[y:y+64]=decode(master[y:y+64].astype(np.float32)/65535)
            if y%512==0:
                master.flush();linear.flush();c._mmap.madvise(mmap.MADV_DONTNEED);master._mmap.madvise(mmap.MADV_DONTNEED);linear._mmap.madvise(mmap.MADV_DONTNEED)
        outputs={}
        for target_h in [height,2160,2400]:
            target_w=width if target_h==height else (13512 if target_h==2160 else 15014)
            tag=f'{width}x{height}' if target_h==height else str(target_h)
            path=ROOT/'renders'/f's8k-{theme}-{tag}.png'
            if target_h==height:a=master
            else:
                a=cv2.resize(linear,(target_w,target_h),interpolation=cv2.INTER_AREA)
                # In-place row conversion bounds temporary allocation.
                result=np.empty((target_h,target_w,3),np.uint16)
                for y in range(0,target_h,64):result[y:y+64]=np.rint(np.clip(encode(a[y:y+64]),0,1)*65535).astype(np.uint16)
                del a;a=result
            png(path,a)
            wrap=np.concatenate([a[:,-600:],a[:,:600]],axis=1);png(path.with_name(path.stem+'-loopcheck.png'),wrap)
            joins=[round(x*target_w/width) for x in cfg['bounds'][1:-1]]
            outputs[path.name]=dict(width=target_w,height=target_h,bit_depth=16,seams=column_stats(a,joins),bytes=path.stat().st_size)
            if target_h==height:
                thumbnail=cv2.resize(a,(1500,240),interpolation=cv2.INTER_AREA);png(PROOF/(theme+'-preview.png'),thumbnail)
                if theme=='01-rose-light':
                    # All crops are direct master pixels, never rescaled.
                    scale=width/PERIOD_UNITS
                    positions={'white-fold':(round(850*scale),round(300*height/821)), 'rose-curve':(round(2900*scale),round(260*height/821)), 'band-join':(cfg['bounds'][max(1,len(cfg['bounds'])//2)]-600,round(280*height/821))}
                    for label,(x,y) in positions.items():png(PROOF/('native-'+label+'.png'),a[y:y+900,x:x+1200])
                    png(PROOF/'native-wrap-seam.png',wrap)
            if target_h!=height:del a,result
            print('DELIVERED',path.name,flush=True)
        record=dict(theme=theme,seconds=time.perf_counter()-tick,palette=palette,outputs=outputs,peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024)
        jsonwrite(record_path,record);records[theme]=record
        del master,linear
        (WORK/'encoded.tmp.npy').unlink();(WORK/'linear.tmp.npy').unlink()
        c._mmap.madvise(mmap.MADV_DONTNEED)
    jsonwrite(PROOF/'delivery.json',records)
    sheet=Image.new('RGB',(1848,9*180+16),(28,29,33));draw=ImageDraw.Draw(sheet)
    for i,p in enumerate(sorted(PROOF.glob('[0-9][0-9]-*-preview.png'))):
        im=Image.open(p).convert('RGB').resize((900,144),Image.Resampling.LANCZOS);x=16+(i%2)*916;y=16+(i//2)*180;sheet.paste(im,(x,y+24));draw.text((x,y+4),p.stem.replace('-preview',''),fill='white')
    sheet.save(ROOT/'renders/s8k-all-colours-contact.png')
    del c

PERIOD_UNITS=5136

def main():
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=['render','ship','all']);ap.add_argument('--width',type=int,default=27024);ap.add_argument('--height',type=int,default=4320);ap.add_argument('--bands',type=int,default=24);ap.add_argument('--samples',type=int,default=32)
    a=ap.parse_args();WORK.mkdir(exist_ok=True);PROOF.mkdir(exist_ok=True)
    if a.action in ['render','all']:dest,cfg=bands(a.width,a.height,a.bands,a.samples)
    else:dest=WORK/f'master-{a.width}x{a.height}';cfg=json.loads((dest/'job.json').read_text())
    if a.action in ['ship','all']:ship(stitch(dest,cfg),cfg)
if __name__=='__main__':main()
