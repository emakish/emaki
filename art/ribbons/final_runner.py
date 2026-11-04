# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Atomic, resumable frame jobs. Two coordinators, one Blender render at a time."""
from pathlib import Path
import argparse
import fcntl
import hashlib
import json
import os
import shutil
import signal
import socket
import struct
import sys
import subprocess
import time
import uuid
from PIL import Image

ROOT = Path(__file__).resolve().parent

def inside(value):
    p = (ROOT / value).resolve()
    if not p.is_relative_to(ROOT): raise ValueError('All outputs must remain inside the project')
    return p

def write_json(path, value):
    temp = path.with_name(path.name + f'.{os.getpid()}.tmp')
    temp.write_text(json.dumps(value, indent=2)+'\n')
    with temp.open('rb') as f: os.fsync(f.fileno())
    os.replace(temp, path)

def valid_png(path, width, height):
    if not path.exists(): return False
    with path.open('rb') as f: header=f.read(29)
    if (header[:8] != b'\x89PNG\r\n\x1a\n' or len(header)<29 or
        struct.unpack('>II',header[16:24]) != (width,height) or header[24] != 16):
        raise RuntimeError(f'Existing frame is not a complete {width}x{height} 16-bit PNG: {path}')
    with Image.open(path) as im: im.verify()
    return True

def frame_list(value):
    if ':' not in value: return sorted(set(map(int,value.split(','))))
    v=list(map(int,value.split(':')))
    if len(v) not in (2,3) or (len(v)==3 and v[2]<1): raise ValueError('Use start:end[:positive_step]')
    return list(range(v[0],v[1]+1,v[2] if len(v)==3 else 1))

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--threads',type=int,default=4)
    p.add_argument('--device',choices=['CPU','GPU'],default='CPU')
    p.add_argument('--frames',default='0:233')
    p.add_argument('--width',type=int,default=3840);p.add_argument('--height',type=int,default=2160)
    p.add_argument('--fps',type=int,default=24)
    p.add_argument('--profile',choices=['final','quality','probe'],default='final')
    p.add_argument('--samples',type=int);p.add_argument('--palette')
    p.add_argument('--colour-palettes',help='Render reusable passes and composite every NN-theme.json in this folder')
    p.add_argument('--output',default='renders/r2-final-frames')
    p.add_argument('--loop-proof',action='store_true',help='Also render excluded mathematical endpoint')
    p.add_argument('--reuse-half-loop',action='store_true',help='Reuse verified identical frames 0–116 for 117–233 when already complete')
    p.add_argument('--recover-locks',action='store_true',help='Remove only demonstrably dead local claims')
    a=p.parse_args()
    if min(a.threads,a.width,a.height,a.fps)<1: p.error('Positive threads, size and fps required')
    if a.samples is not None and a.samples<1:p.error('Positive samples required')
    if a.palette and a.colour_palettes:p.error('Use the calibrated rose master with --colour-palettes; omit --palette')
    if abs(9.75*a.fps-round(9.75*a.fps))>1e-8:p.error('fps must give an integer loop length')
    frames=frame_list(a.frames)
    endpoint=round(9.75*a.fps)
    if not frames or min(frames)<0 or max(frames)>endpoint:p.error('Frames must be within the loop including its proof endpoint')
    if a.loop_proof:frames=sorted(set(frames+[endpoint]))
    out=inside(a.output);out.mkdir(parents=True,exist_ok=True)
    logs=out/'logs';logs.mkdir(exist_ok=True)
    locks=out/'.locks';locks.mkdir(exist_ok=True)
    config={k:getattr(a,k) for k in ['width','height','fps','profile','samples','device']}
    config['source_sha256']={n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in ['ribbons.py','ribbons_round2.py']}
    if a.reuse_half_loop:
        proof=json.loads((ROOT/'renders/r2-repeat-proof.json').read_text())
        if a.fps!=24 or a.profile=='quality' or not proof['all_vertices_bit_identical'] or proof['source_sha256']!=config['source_sha256']:
            raise RuntimeError('Exact repeated-motion proof does not apply to this script/fps/profile; omit --reuse-half-loop')
    config['palette']=json.loads(inside(a.palette).read_text()) if a.palette else None
    config['colour_palettes']={p.name:json.loads(p.read_text()) for p in sorted(inside(a.colour_palettes).glob('[0-9][0-9]-*.json'))} if a.colour_palettes else None
    if a.colour_palettes and not config['colour_palettes']:p.error('No NN-theme.json palettes found')
    if a.colour_palettes:config['colour_pipeline_sha256']=hashlib.sha256((ROOT/'colour_pipeline.py').read_bytes()).hexdigest()
    with (out/'.config.lock').open('a') as f:
        fcntl.flock(f,fcntl.LOCK_EX)
        path=out/'job.json'
        if path.exists() and json.loads(path.read_text())!=config:
            raise RuntimeError('Output directory belongs to a different scene/settings. Choose a new --output.')
        if not path.exists():write_json(path,config)
    worker=f'{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}'
    if a.recover_locks:
        for lock in locks.glob('frame-*'):
            owner=lock/'owner.json'
            if not owner.exists():raise RuntimeError(f'Cannot prove owner dead: {lock}')
            meta=json.loads(owner.read_text())
            if meta['hostname']!=socket.gethostname():continue
            try:os.kill(meta['pid'],0);continue
            except ProcessLookupError:pass
            if meta.get('child_pid'):
                try:os.kill(meta['child_pid'],0);continue
                except ProcessLookupError:pass
            elif meta.get('phase')!='waiting':
                raise RuntimeError(f'Cannot rule out an orphaned Blender process for {lock}; inspect it before removing the claim')
            shutil.rmtree(lock)
    tmp_root=ROOT/'renders/r2-tmp';tmp_root.mkdir(exist_ok=True)
    cache=ROOT/'renders/r2-cache';cache.mkdir(exist_ok=True)
    env=os.environ.copy()
    env.update(TMPDIR=str(tmp_root),XDG_CACHE_HOME=str(cache),OMP_NUM_THREADS=str(a.threads),
               OPENBLAS_NUM_THREADS=str(a.threads),MKL_NUM_THREADS=str(a.threads),PYTHONDONTWRITEBYTECODE='1')
    child=None
    def interrupted(signum, frame): raise KeyboardInterrupt(f'Signal {signum}')
    signal.signal(signal.SIGTERM,interrupted)
    counts=dict(rendered=0,skipped=0,claimed_elsewhere=0)
    for frame in frames:
        final=out/f'frame-{frame:04d}.png'
        if valid_png(final,a.width,a.height):counts['skipped']+=1;continue
        lock=locks/f'frame-{frame:04d}'
        try:lock.mkdir()
        except FileExistsError:counts['claimed_elsewhere']+=1;continue
        work=out/f'.part-{frame:04d}-{os.getpid()}'
        try:
            owner=dict(pid=os.getpid(),hostname=socket.gethostname(),created=time.time(),frame=frame,phase='waiting')
            write_json(lock/'owner.json',owner)
            if a.reuse_half_loop and 117<=frame<234:
                prior=out/f'frame-{frame-117:04d}.png'
                if valid_png(prior,a.width,a.height):
                    start=time.perf_counter();work.mkdir(exist_ok=True)
                    source=work/f'frame-{frame:04d}.png';shutil.copyfile(prior,source)
                    if a.colour_palettes:
                        for name in config['colour_palettes']:
                            dest=out/'colours'/Path(name).stem
                            old=dest/f'frame-{frame-117:04d}.png'
                            if not valid_png(old,a.width,a.height):raise RuntimeError(f'Missing completed source colour: {old}')
                            temp=work/(Path(name).stem+'.png');shutil.copyfile(old,temp)
                            os.replace(temp,dest/f'frame-{frame:04d}.png')
                    meta=json.loads(prior.with_suffix('.json').read_text())
                    meta.update(frame=frame,reused_from_frame=frame-117,worker=worker,seconds=0,
                                wall_seconds=time.perf_counter()-start,start_unix=time.time()-(time.perf_counter()-start),end_unix=time.time())
                    if a.colour_palettes:meta['colour_seconds']=0
                    write_json(out/f'frame-{frame:04d}.json',meta)
                    with source.open('rb') as f:os.fsync(f.fileno())
                    os.replace(source,final)
                    fd=os.open(out,os.O_RDONLY);os.fsync(fd);os.close(fd)
                    counts['reused']=counts.get('reused',0)+1
                    print(f'{worker}: reused {frame-117} as {frame}',flush=True)
                    continue
            # One render at a time across all output directories in this project.
            # Claim first so both coordinators do useful work without duplicate frames.
            with (ROOT/'renders/r2-render.guard').open('a') as guard:
                fcntl.flock(guard,fcntl.LOCK_EX)
                if valid_png(final,a.width,a.height):counts['skipped']+=1;continue
                work.mkdir(exist_ok=True)
                cmd=['nice','-n','19','blender','-b','-noaudio','--factory-startup','--threads',str(a.threads),
                     '--python',str(ROOT/'ribbons_round2.py'),'--','--threads',str(a.threads),'--device',a.device,
                     '--width',str(a.width),'--height',str(a.height),'--fps',str(a.fps),'--frames',str(frame),
                     '--profile',a.profile,'--output',str(work)]
                if a.samples:cmd+=['--samples',str(a.samples)]
                if a.palette:cmd+=['--palette',str(inside(a.palette))]
                if a.colour_palettes:cmd+=['--passes']
                if frame==endpoint:cmd+=['--verify-loop']
                start=time.perf_counter();start_unix=time.time()
                print(f'{worker}: rendering {frame}',flush=True)
                with (logs/f'frame-{frame:04d}-{worker}.log').open('w') as log:
                    owner['phase']='starting';write_json(lock/'owner.json',owner)
                    # The child retains the render guard even if its coordinator
                    # is SIGKILLed. Recovery also checks the recorded child PID.
                    child=subprocess.Popen(cmd,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,
                                           pass_fds=(guard.fileno(),))
                    owner.update(phase='rendering',child_pid=child.pid);write_json(lock/'owner.json',owner)
                    code=child.wait();child=None
                if code:raise RuntimeError(f'Blender failed ({code}), see {logs}')
                source=work/f'frame-{frame:04d}.png'
                if not valid_png(source,a.width,a.height):raise RuntimeError('Blender did not produce a complete frame')
                meta=json.loads((work/'timings.json').read_text())[0]
                meta.update(worker=worker,wall_seconds=time.perf_counter()-start,start_unix=start_unix,end_unix=time.time(),sha256=hashlib.sha256(source.read_bytes()).hexdigest())
                if a.colour_palettes:
                    colour_start=time.perf_counter()
                    subprocess.run(['nice','-n','19',sys.executable,str(ROOT/'colour_pipeline.py'),'all',
                                    '--input',str(work/f'frame-{frame:04d}.exr'),
                                    '--palettes',str(inside(a.colour_palettes)),'--output',str(work/'colours')],check=True,env=env)
                    for name in config['colour_palettes']:
                        theme=Path(name).stem;dest=out/'colours'/theme;dest.mkdir(parents=True,exist_ok=True)
                        coloured=work/'colours'/(theme+'.png')
                        valid_png(coloured,a.width,a.height)
                        os.replace(coloured,dest/f'frame-{frame:04d}.png')
                    meta['colour_seconds']=time.perf_counter()-colour_start
                write_json(out/f'frame-{frame:04d}.json',meta)
                if (work/'geometry-loop.json').exists():os.replace(work/'geometry-loop.json',out/'geometry-loop.json')
                with source.open('rb') as f:os.fsync(f.fileno())
                os.replace(source,final)
                fd=os.open(out,os.O_RDONLY);os.fsync(fd);os.close(fd)
                counts['rendered']+=1
        finally:
            if child is not None:
                os.killpg(child.pid,signal.SIGTERM)
                try:child.wait(timeout=10)
                except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait()
                child=None
            if work.exists():shutil.rmtree(work)
            if lock.exists():shutil.rmtree(lock)
    write_json(logs/f'worker-{worker}.json',counts)
    print(json.dumps(counts),flush=True)

if __name__=='__main__':main()
