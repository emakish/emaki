# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender-only A2 neutral / 04-wide breathe renderer; no original outputs."""
import sys
sys.dont_write_bytecode = True
import os, json, math, time, resource, argparse
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import frames as F
import video as V
from polish_look import apply_lighting, LOOK
os.environ['XDG_CACHE_HOME'] = str(ROOT/'videos/final-work/cache')
os.environ['TMPDIR'] = str(ROOT/'videos/final-work/tmp')

def main():
    import bpy
    p=argparse.ArgumentParser()
    p.add_argument('--job',required=True)
    a=p.parse_args(sys.argv[sys.argv.index('--')+1:])
    job=json.loads(Path(a.job).read_text())
    start=time.perf_counter()
    config=F.CANDIDATES['04-wide']
    V.CAMERA,V.YAW,V.DOWN,V.HFOV=config['camera'],config['yaw'],config['down'],config['hfov']
    args=argparse.Namespace(width=job['width'], samples=job['samples'])
    s,carriers,_,bounds=V.setup(args)
    s.render.threads=job['threads']
    s.render.resolution_y=job['height']
    s.render.fps=job['fps']
    s.camera.data.clip_end=500
    if job.get('light','a2')=='a2': apply_lighting(s,'neutral')
    else: s.view_layers[0].use_pass_position=True # probe-1 light, no side shadows.
    s.view_layers[0].use_pass_z=False # Position is sufficient for radial distance.
    s.view_layers[0].cycles.denoising_store_passes=job['diagnostics']
    s.cycles.use_denoising=True
    s.cycles.denoiser='OPENIMAGEDENOISE'
    s.cycles.denoising_prefilter='ACCURATE'
    if hasattr(s.cycles,'denoising_use_gpu'): s.cycles.denoising_use_gpu=False
    s.cycles.use_adaptive_sampling=False
    s.cycles.use_animated_seed=False
    s.cycles.seed=V.A.SEED
    devices=[]
    if job['device']=='GPU':
        prefs=bpy.context.preferences.addons['cycles'].preferences
        prefs.compute_device_type='HIP'
        prefs.get_devices()
        for d in prefs.devices:
            d.use=d.type=='HIP'
            if d.use: devices.append(dict(name=d.name,id=d.id,type=d.type))
        if not devices: raise RuntimeError('No Cycles HIP device found; refusing silent CPU fallback')
        s.cycles.device='GPU'
    else: s.cycles.device='CPU'
    phase=job['frame']/job['total_frames'] # endpoint deliberately NOT reduced modulo one
    positions,audit=F.geometry(config,phase)
    import hashlib
    audit['position_sha256']=[hashlib.sha256(pos.tobytes()).hexdigest() for pos in positions]
    for obj,pos in zip(carriers,positions):
        mesh=bpy.data.meshes.new('Motion voxel centres')
        mesh.vertices.add(len(pos)); mesh.vertices.foreach_set('co',pos.ravel()); mesh.update()
        old=obj.data; obj.data=mesh; bpy.data.meshes.remove(old)
    if job.get('crop'):
        x0,y0,x1,y1=job['crop']; w,h=job['width'],job['height']
        s.render.use_border=True; s.render.use_crop_to_border=True
        s.render.border_min_x=x0/w; s.render.border_max_x=x1/w
        s.render.border_min_y=1-y1/h; s.render.border_max_y=1-y0/h
    if hasattr(s.render.image_settings,'media_type'): s.render.image_settings.media_type='MULTI_LAYER_IMAGE'
    s.render.image_settings.file_format='OPEN_EXR_MULTILAYER'
    s.render.image_settings.color_mode='RGBA'
    s.render.image_settings.color_depth='32'
    s.render.image_settings.exr_codec='ZIP'
    s.render.filepath=job['exr']
    tick=time.perf_counter(); bpy.ops.render.render(write_still=True)
    result=dict(frame=job['frame'],phase=phase,render_seconds=time.perf_counter()-tick,
        blender_seconds=time.perf_counter()-start,peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,
        width=job['width'],height=job['height'],samples=s.cycles.samples,threads=s.render.threads,
        nice=os.getpriority(os.PRIO_PROCESS,0),device=s.cycles.device,devices=devices,
        seed=s.cycles.seed,animated_seed=False,adaptive=False,denoiser='OIDN accurate / CPU',
        camera=list(s.camera.location),config=config,look=LOOK,geometry=audit,master_dimensions=bounds)
    Path(job['render_stats']).write_text(json.dumps(result,indent=2)+'\n')
    sys.stdout.flush(); sys.stderr.flush(); os._exit(0)
if __name__=='__main__': main()
