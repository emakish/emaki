# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Assemble complete frames, then measure source AND decoded loop steps.

Final master: FFV1 v3 / gbrp16le, mathematically lossless 16-bit RGB. It preserves
PNG precision and makes the decoded loop proof exact. Delivery MP4: H.264 QP12,
all intra, yuv420p (same conservative encode as earlier probes). No crossfade.
"""
import sys
sys.dont_write_bytecode=True
import os,json,argparse,subprocess,fcntl
from pathlib import Path
import numpy as np,cv2
cv2.setNumThreads(1)

def steps_summary(steps,seam):
    return dict(seam_mae_8bit=float(seam),adjacent_mean=float(np.mean(steps)),adjacent_max=float(max(steps)),
        seam_le_max=bool(seam<=max(steps)+1e-10),seam_le_mean=bool(seam<=np.mean(steps)+1e-10),steps=steps)

def source_metrics(out,n):
    first=cv2.imread(str(out/'0000.png'),cv2.IMREAD_UNCHANGED)
    if first is None: raise RuntimeError('Missing frame 0')
    first=first.astype(np.float32)/257
    prev=first; steps=[]
    for f in range(1,n):
        current=cv2.imread(str(out/f'{f:04d}.png'),cv2.IMREAD_UNCHANGED).astype(np.float32)/257
        steps.append(float(np.abs(current-prev).mean())); prev=current
    r=steps_summary(steps,float(np.abs(prev-first).mean()))
    ep=out/f'{n:04d}.png'
    if ep.exists():
        endpoint=cv2.imread(str(ep),cv2.IMREAD_UNCHANGED).astype(np.float32)/257
        d=endpoint-first; r.update(endpoint_mae=float(np.abs(d).mean()),endpoint_rmse=float(np.sqrt((d*d).mean())),endpoint_max=float(np.abs(d).max()))
        a=json.loads((out/'0000.json').read_text()); b=json.loads((out/f'{n:04d}.json').read_text())
        r['endpoint_geometry_equal']=a['geometry']['position_sha256']==b['geometry']['position_sha256']
    return r

def read_exact(stream,size):
    chunks=[]; left=size
    while left:
        chunk=stream.read(left)
        if not chunk: break
        chunks.append(chunk); left-=len(chunk)
    return b''.join(chunks)

def decoded_metrics(path,w,h,n,sixteen=False,source=None):
    pix='rgb48le' if sixteen else 'rgb24'; dtype='<u2' if sixteen else 'u1'; factor=257 if sixteen else 1
    p=subprocess.Popen(['ffmpeg','-v','error','-threads','2','-i',str(path),'-f','rawvideo','-pix_fmt',pix,'-threads','2','-'],stdout=subprocess.PIPE)
    first=prev=None; steps=[]; count=0; exact=True
    while True:
        data=read_exact(p.stdout,w*h*3*(2 if sixteen else 1))
        if not data: break
        if len(data)!=w*h*3*(2 if sixteen else 1): raise RuntimeError('Incomplete decoded frame')
        integer=np.frombuffer(data,dtype=dtype).reshape(h,w,3)
        if source is not None:
            expected=cv2.imread(str(source/f'{count:04d}.png'),cv2.IMREAD_UNCHANGED)[:,:,::-1]
            exact=exact and np.array_equal(expected,integer)
        current=integer.astype(np.float32)/factor
        if first is None: first=current
        else: steps.append(float(np.abs(current-prev).mean()))
        prev=current; count+=1
    if p.wait()!=0 or count!=n: raise RuntimeError(f'Decode frame count {count}, expected {n}')
    r=steps_summary(steps,float(np.abs(prev-first).mean())); r['decoded_frames']=count
    if source is not None: r['lossless_pixels_exact']=bool(exact)
    return r

def encode(out,path,n,fps,codec):
    tmp=path.with_name(path.stem+'.tmp'+path.suffix)
    command=['nice','-n','19','ffmpeg','-v','error','-y','-threads','2','-framerate',str(fps),'-i',str(out/'%04d.png'),'-frames:v',str(n),'-an']
    if codec=='ffv1': command+=['-c:v','ffv1','-level','3','-coder','1','-context','1','-g','1','-slicecrc','1','-pix_fmt','gbrp16le','-vf','setparams=range=full:color_primaries=bt709:color_trc=iec61966-2-1:colorspace=gbr','-threads','2','-color_range','pc','-colorspace','0','-color_primaries','bt709','-color_trc','iec61966-2-1']
    else: command+=['-c:v','libx264','-preset','slow','-qp','12','-g','1','-bf','0','-pix_fmt','yuv420p','-vf','scale=out_color_matrix=bt709:out_range=tv,setparams=range=limited:color_primaries=bt709:color_trc=iec61966-2-1:colorspace=bt709','-color_range','tv','-colorspace','bt709','-color_primaries','bt709','-color_trc','iec61966-2-1','-threads','2','-movflags','+faststart']
    subprocess.run(command+[str(tmp)],check=True)
    os.replace(tmp,path)

def main():
    p=argparse.ArgumentParser(); p.add_argument('--out',type=Path,required=True); p.add_argument('--preview-only',action='store_true'); p.add_argument('--count',type=int)
    a=p.parse_args(); out=a.out.resolve(); config=json.loads((out/'manifest.json').read_text())
    n=a.count or config['total_frames']; w,h=config['width'],config['height']; fps=config['fps']
    if n<2 or n>config['total_frames']: p.error('invalid count')
    with open(out/'assembly.lock','a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        missing=[f for f in range(n) if not (out/f'{f:04d}.png').exists()]
        if missing: raise RuntimeError(f'Incomplete sequence; missing {missing[:12]}. Rerun --assemble-only after both workers finish.')
        if not a.count and not (out/f'{n:04d}.png').exists(): raise RuntimeError('Independent endpoint missing. Render frame total_frames before assembly.')
        result=dict(width=w,height=h,fps=fps,frames=n,duration=n/fps,partial_test=bool(a.count),source=source_metrics(out,n))
        if not a.preview_only:
            master=out.parent/(out.name+'-master.mkv')
            encode(out,master,n,fps,'ffv1')
            result['master']=decoded_metrics(master,w,h,n,True,out)
        mp4=out.parent/(out.name+'.mp4')
        encode(out,mp4,n,fps,'h264')
        result['h264']=decoded_metrics(mp4,w,h,n)
        result['pass']=result['source']['seam_le_max'] and result['h264']['seam_le_max']
        if 'master' in result: result['pass']=result['pass'] and result['master']['lossless_pixels_exact'] and result['master']['seam_le_max']
        if not a.count:
            result['pass']=result['pass'] and result['source'].get('endpoint_mae',1)<=.01 and result['source'].get('endpoint_max',2)<=1 and result['source'].get('endpoint_geometry_equal',False)
        path=out.parent/(out.name+'-loop-proof.json')
        temp=path.with_suffix('.tmp.json'); temp.write_text(json.dumps(result,indent=2)+'\n'); os.replace(temp,path)
        print(json.dumps({k:v for k,v in result.items() if k not in ('source','master','h264')},indent=2))
        # A twelve-frame smoke test is not expected to be a complete loop.
        if not a.count and not result['pass']:
            raise RuntimeError(f'Loop verification failed; see {path}')
if __name__=='__main__': main()
