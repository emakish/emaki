# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Atomic frame claims + resumable commits. Global flock serializes render/export.

Two workers may run simultaneously: claims divide the frames, while the render
slot keeps the laptop to one Blender process. Interrupted claims are recovered under a persistent kernel lock.
PNG is the completion marker, renamed only after export and validation finish.
"""
import sys
sys.dont_write_bytecode=True
import os,json,argparse,subprocess,time,hashlib,fcntl,socket,shutil,signal
from pathlib import Path
ROOT=Path(__file__).resolve().parent
WORK=ROOT/'videos/final-work'

def atomic_json(path,data):
    tmp=path.with_name(path.name+f'.{os.getpid()}.tmp')
    with open(tmp,'w') as f:
        f.write(json.dumps(data,indent=2)+'\n'); f.flush(); os.fsync(f.fileno())
    os.replace(tmp,path)
    directory_fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try: os.fsync(directory_fd)
    finally: os.close(directory_fd)

def claim(path):
    # Persistent lock inode: safe across hosts/PID namespaces, immune to stale
    # PID reuse. Kernel releases ownership even after SIGKILL/power loss.
    handle=open(path.with_suffix('.claim'), 'a+')
    try: fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close(); return None
    if path.exists(): shutil.rmtree(path) # stale temporary work, no live owner
    path.mkdir()
    atomic_json(path/'owner.json',dict(pid=os.getpid(),host=socket.gethostname()))
    return handle

def child_lifetime():
    # Linux: stop the renderer if its supervisor dies; inherited lock FDs also
    # prevent another worker claiming its frame until the child actually exits.
    import ctypes
    parent=os.getppid()
    if parent==1: os.kill(os.getpid(),signal.SIGTERM)
    if ctypes.CDLL(None).prctl(1,signal.SIGTERM)!=0: raise OSError('PR_SET_PDEATHSIG failed')
    if os.getppid()!=parent: os.kill(os.getpid(),signal.SIGTERM)

def run_limited(cmd,log,env,inherited_fds=()):
    # RSS guard includes descendants, so exporter/Blender stay below 10 GiB.
    with open(log,'w') as out:
        proc=subprocess.Popen(cmd,cwd=ROOT,env=env,stdout=out,stderr=subprocess.STDOUT,start_new_session=True,pass_fds=inherited_fds,preexec_fn=child_lifetime)
        peak=0
        try:
            while proc.poll() is None:
                ids=[proc.pid]; rss=0
                for pid in ids:
                    try:
                        children=Path(f'/proc/{pid}/task/{pid}/children').read_text().split()
                        ids.extend(map(int,children))
                        for line in Path(f'/proc/{pid}/status').read_text().splitlines():
                            if line.startswith('VmRSS:'): rss+=int(line.split()[1])*1024
                    except FileNotFoundError: pass
                peak=max(peak,rss)
                if rss>9.0*1024**3:
                    raise RuntimeError('9.0 GiB RSS guard exceeded')
                time.sleep(.2)
            if proc.returncode: raise RuntimeError(f'Command failed ({proc.returncode}); see {log}')
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid,signal.SIGTERM)
                try: proc.wait(timeout=10)
                except subprocess.TimeoutExpired: os.killpg(proc.pid,signal.SIGKILL); proc.wait()
    return peak/1024**2

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--threads',type=int,default=4)
    p.add_argument('--device',choices=['CPU','GPU'],default='CPU')
    p.add_argument('--width',type=int,default=5760); p.add_argument('--height',type=int)
    p.add_argument('--samples',type=int,default=32)
    p.add_argument('--fps',type=int,default=24); p.add_argument('--total-frames',type=int,default=360)
    p.add_argument('--start',type=int,default=0); p.add_argument('--end',type=int,help='inclusive; endpoint N allowed for proof')
    p.add_argument('--out',default='videos/final-frames')
    p.add_argument('--diagnostics',action='store_true')
    p.add_argument('--crop',type=int,nargs=4,metavar=('X0','Y0','X1','Y1'))
    p.add_argument('--assemble',action='store_true',help='assemble complete sequence after rendering; also independently render endpoint')
    p.add_argument('--assemble-only',action='store_true')
    p.add_argument('--look',choices=['a2','probe'],default='a2',help='probe: probe-1 light, no distance softening')
    a=p.parse_args()
    if a.threads<1 or a.samples<1 or a.total_frames<1 or a.fps<1: p.error('positive threads/samples/frame count/fps required')
    a.height=a.height or round(a.width/2.4)
    if a.width<2 or a.height<2 or a.width%2 or a.height%2: p.error('even positive resolution required')
    if abs(a.width/a.height-2.4)>1e-6: p.error('this composition requires the 2.4:1 canvas')
    if a.crop and (a.assemble or a.assemble_only): p.error('crop tests cannot be assembled as full-canvas frames')
    if a.crop and not (0<=a.crop[0]<a.crop[2]<=a.width and 0<=a.crop[1]<a.crop[3]<=a.height): p.error('invalid crop')
    end=a.end if a.end is not None else a.total_frames-1
    if not 0<=a.start<=end<=a.total_frames: p.error('frame range outside 0..total_frames')
    out=(ROOT/a.out).resolve()
    if not out.is_relative_to(ROOT) or not (out.relative_to(ROOT).as_posix().startswith('videos/final-') or out.relative_to(ROOT).as_posix().startswith('renders/motion-')):
        p.error('output must be inside videos/final-* or renders/motion-*')
    WORK.mkdir(parents=True,exist_ok=True); (WORK/'cache').mkdir(exist_ok=True); (WORK/'tmp').mkdir(exist_ok=True)
    out.mkdir(parents=True,exist_ok=True); (out/'locks').mkdir(exist_ok=True)
    config=dict(width=a.width,height=a.height,samples=a.samples,fps=a.fps,total_frames=a.total_frames,
        device=a.device,diagnostics=a.diagnostics,crop=a.crop,light=a.look,
        look='A2 neutral / 04-wide' if a.look=='a2' else 'probe-1 light, no softening / 04-wide',
        source_hashes={f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in ['motion_render.py','motion_export.py','video.py','frames.py','cubes.py','polish_look.py']})
    with open(out/'manifest.lock','a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        manifest=out/'manifest.json'
        if manifest.exists():
            if json.loads(manifest.read_text())!=config: raise RuntimeError('Output settings/source mismatch; use a fresh output directory')
        else: atomic_json(manifest,config)
    env=os.environ.copy(); env.update(OMP_NUM_THREADS=str(a.threads),OPENBLAS_NUM_THREADS=str(a.threads),
        OIDN_NUM_THREADS=str(a.threads),PYTHONDONTWRITEBYTECODE='1',ALSOFT_DRIVERS='null',
        XDG_CACHE_HOME=str(WORK/'cache'),TMPDIR=str(WORK/'tmp'))
    frames=list(range(a.start,end+1))
    if a.assemble and a.total_frames not in frames: frames.append(a.total_frames)
    if a.assemble_only: frames=[]
    for frame in frames:
        stem=f'{frame:04d}'; png=out/(stem+'.png'); lock=out/'locks'/stem
        if png.exists(): print(f'SKIP {frame}',flush=True); continue
        ownership=claim(lock)
        if ownership is None: print(f'CLAIMED_BY_OTHER {frame}',flush=True); continue
        try:
            if png.exists(): continue
            with open(WORK/'render-slot.lock','a+') as slot:
                fcntl.flock(slot,fcntl.LOCK_EX)
                tick=time.perf_counter(); started_unix=time.time()
                job=dict(config,threads=a.threads,frame=frame,exr=str(lock/'frame.exr'),
                    render_stats=str(lock/'stats.json'),png_tmp=str(lock/'frame.tmp.png'),diagnostic_path=str(out/(stem+'-diagnostic.npz')))
                atomic_json(lock/'job.json',job)
                print(f'RENDER {frame} worker={os.getpid()} threads={a.threads}',flush=True)
                blender=['nice','-n','19','blender','-b','--factory-startup','-noaudio','-t',str(a.threads),
                    '--python-exit-code','1','--python',str(ROOT/'motion_render.py'),'--','--job',str(lock/'job.json')]
                peak=run_limited(blender,out/(stem+'-blender.log'),env,(ownership.fileno(),slot.fileno()))
                export_peak=run_limited(['nice','-n','19','python3',str(ROOT/'motion_export.py'),'--job',str(lock/'job.json')],out/(stem+'-export.log'),env,(ownership.fileno(),slot.fileno()))
                # Check PNG header for resolution and 16-bit RGB before committing.
                import struct
                header=Path(job['png_tmp']).read_bytes()[:29]
                size=tuple(a.crop[i+2]-a.crop[i] for i in range(2)) if a.crop else (a.width,a.height)
                if header[:8]!=b'\x89PNG\r\n\x1a\n' or struct.unpack('>II',header[16:24])!=size or header[24:26]!=bytes([16,2]):
                    raise RuntimeError('Invalid 16-bit RGB output')
                stats=json.loads(Path(job['render_stats']).read_text()); stats.update(worker_pid=os.getpid(),started_unix=started_unix,finished_unix=time.time(),
                    frame_wall_seconds=time.perf_counter()-tick,monitored_blender_peak_mib=peak,monitored_export_peak_mib=export_peak)
                atomic_json(out/(stem+'.json'),stats)
                with open(job['png_tmp'],'rb') as ready: os.fsync(ready.fileno())
                os.replace(job['png_tmp'],png)
                directory_fd=os.open(out,os.O_RDONLY|os.O_DIRECTORY)
                try: os.fsync(directory_fd)
                finally: os.close(directory_fd)
                print(f'DONE {frame} {stats["frame_wall_seconds"]:.2f}s',flush=True)
        finally:
            shutil.rmtree(lock)
            ownership.close()
    if a.assemble or a.assemble_only:
        subprocess.run(['python3',str(ROOT/'motion_assemble.py'),'--out',str(out)],check=True,env=env)
if __name__=='__main__': main()
