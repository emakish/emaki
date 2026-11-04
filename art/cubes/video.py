# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Perspective Cycles motion probes. Shared scene for the overview animation; source A is read only."""
import sys, os, math, time, json, argparse, resource
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))
import cubes as A
import numpy as np

WIDTH, HEIGHT, FPS, SECONDS, SAMPLES = 960, 400, 12, 10, 32
PERIOD = 12                 # flyover spatial repeat, integer grid cells
CAMERA = (.23, -.17, 24.)
YAW, DOWN, HFOV = 15., 22., 70.
BREATHE_AMPLITUDE = .85     # total sea level excursion < two cubes
NAMES = {1:'probe-1-breathe',2:'probe-2-pixels',3:'probe-3-flyover'}


def continuous_a(x,y):
    p=A.VARIANTS['A']
    h=p['base_height']+p['radial_amplitude']*(1-np.cos(math.tau*np.hypot(x-A.CAMERA_XY[0],y-A.CAMERA_XY[1])/A.RADIAL_WAVELENGTH))
    for amp,w,d,phase,_ in p['waves']:
        h += amp*np.sin(math.tau*(x*np.cos(d)+y*np.sin(d))/w+phase)
    return h


def hash32(i,j,k=0):
    n=((i.astype(np.int64)*73856093)^(j.astype(np.int64)*19349663)^(np.asarray(k,dtype=np.int64)*83492791)^A.SEED)&0xffffffff
    n=((n^(n>>16))*0x7feb352d)&0xffffffff
    n=((n^(n>>15))*0x846ca68b)&0xffffffff
    return n^(n>>16)


def heights(i,j,phase,probe):
    x,y=i+.5,j+.5
    if probe==3:
        # Broad cross-field terraces with one gentle, exactly periodic forward swell.
        h=continuous_a(x,np.full_like(y,24.)) + 1.25*np.sin(math.tau*y/PERIOD+.06*x)
    else:
        h=continuous_a(x,y)
        if probe==1:
            h += BREATHE_AMPLITUDE*np.sin(math.tau*(.32*x+y)/100-math.tau*phase)
        else:
            h=np.floor(h+.5)
            seed=hash32(i,j)
            local=(phase+(seed%1009)/1009.)%1
            pulse=np.maximum(0,np.sin(math.tau*local))**2
            # Roughly 1 in 100 columns rises, staggered; each level holds for seconds.
            h += (seed%101==0)*np.floor(pulse*(1.55+(seed%3==0)))
    return np.maximum(1,np.floor(h+.5)).astype(np.int32)


def geometry(probe,phase):
    # Fixed visible wedge with a 5-cell shadow/frustum guard. No surrounding panorama.
    i,j=np.meshgrid(np.arange(-220,221),np.arange(-15,321),indexing='ij')
    yaw=math.radians(YAW)
    cy=CAMERA[1]+(PERIOD*phase if probe==3 else 0)
    dx,dy=i+.5-CAMERA[0],j+.5-cy
    depth=dx*math.sin(yaw)+dy*math.cos(yaw)
    side=dx*math.cos(yaw)-dy*math.sin(yaw)
    visible=(depth>-3)&(depth<(240 if probe==3 else 155))&(np.abs(side)<np.maximum(depth,0)*math.tan(math.radians(HFOV/2))+5)
    i,j=i[visible],j[visible]
    h=heights(i,j,phase,probe)
    low=np.minimum.reduce([h,heights(i-1,j,phase,probe),heights(i+1,j,phase,probe),heights(i,j-1,phase,probe),heights(i,j+1,phase,probe)])
    low=np.maximum(0,low-2)  # hidden interior omitted; two-layer closed bevel/shadow shell
    count=h-low
    ii=np.repeat(i,count); jj=np.repeat(j,count)
    starts=np.repeat(np.cumsum(count)-count,count)
    kk=np.repeat(low,count)+np.arange(count.sum())-starts
    colorj=jj%PERIOD if probe==3 else jj
    c=hash32(ii,colorj,kk)%4
    if probe==2:
        colseed=hash32(ii,jj)
        # Very sparse top cubes switch to one other exact palette entry, then return.
        active=((phase+(colseed%997)/997.)%1)<.30
        c=(c+((colseed%251==0)&active&(kk==np.repeat(h,count)-1))*(1+colseed%3))%4
    pos=np.column_stack((ii+.5,jj+.5,kk+.5)).astype(np.float32)
    return [pos[c==n] for n in range(4)],dict(columns=len(h),cubes=len(pos),height_range=[int(h.min()),int(h.max())],grid_error=float(np.max(np.abs(pos-.5-np.rint(pos-.5)))))


def setup(args):
    import bpy
    from mathutils import Vector
    bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
    s=bpy.context.scene; s.render.engine='CYCLES'; s.cycles.device='CPU'
    s.render.threads_mode='FIXED'; s.render.threads=8
    s.cycles.samples=args.samples; s.cycles.seed=A.SEED; s.cycles.use_animated_seed=False
    s.cycles.use_adaptive_sampling=False
    s.cycles.use_denoising=True; s.cycles.denoiser='OPENIMAGEDENOISE'; s.cycles.denoising_prefilter='ACCURATE'
    s.cycles.max_bounces=1; s.cycles.diffuse_bounces=0; s.cycles.glossy_bounces=0; s.cycles.transmission_bounces=0; s.cycles.volume_bounces=0
    s.render.resolution_x=args.width; s.render.resolution_y=round(args.width/2.4); s.render.resolution_percentage=100
    s.render.image_settings.file_format='PNG'; s.render.image_settings.color_mode='RGB'; s.render.image_settings.color_depth='8'; s.render.image_settings.compression=15
    s.render.fps=FPS; s.view_settings.view_transform='Standard'; s.view_settings.look='None'
    s.world.use_nodes=True; s.world.node_tree.nodes['Background'].inputs[0].default_value=(1,1,1,1)
    s.world.node_tree.nodes['Background'].inputs[1].default_value=A.WORLD_STRENGTH
    ld=bpy.data.lights.new('A neutral softbox','AREA'); ld.shape='DISK'; ld.size=A.LIGHT_SIZE; ld.energy=A.LIGHT_POWER
    light=bpy.data.objects.new('A neutral softbox',ld); s.collection.objects.link(light); light.location=(0,0,A.LIGHT_HEIGHT)
    camd=bpy.data.cameras.new('Ordinary perspective'); camd.type='PERSP'; camd.lens=36/(2*math.tan(math.radians(HFOV/2))); camd.sensor_width=36; camd.sensor_fit='HORIZONTAL'; camd.clip_end=300
    cam=bpy.data.objects.new('Low perspective camera',camd); s.collection.objects.link(cam); cam.location=CAMERA
    yaw=math.radians(YAW)
    cam.rotation_euler=Vector((math.sin(yaw),math.cos(yaw),-math.tan(math.radians(DOWN)))).to_track_quat('-Z','Y').to_euler(); s.camera=cam
    sources=bpy.data.collections.new('Identical A masters, unlinked')
    carriers=[]; bounds=[]
    for n,rgb in enumerate(A.palette_linear()):
        mat=bpy.data.materials.new(A.PALETTE[n]); mat.use_nodes=True; nodes=mat.node_tree.nodes; nodes.clear()
        out=nodes.new('ShaderNodeOutputMaterial'); dif=nodes.new('ShaderNodeBsdfDiffuse'); dif.inputs['Color'].default_value=(*rgb,1); dif.inputs['Roughness'].default_value=0
        mat.node_tree.links.new(dif.outputs[0],out.inputs['Surface'])
        bpy.ops.mesh.primitive_cube_add(size=A.CUBE_SIZE); proto=bpy.context.object; proto.data.materials.append(mat)
        bevel=proto.modifiers.new('A bevel','BEVEL'); bevel.width=A.BEVEL_WIDTH; bevel.segments=A.BEVEL_SEGMENTS; bpy.ops.object.modifier_apply(modifier=bevel.name)
        for face in proto.data.polygons: face.use_smooth=len(face.vertices)!=4 or face.area<.25
        norm=proto.modifiers.new('A normals','WEIGHTED_NORMAL'); norm.keep_sharp=True; bpy.ops.object.modifier_apply(modifier=norm.name)
        bounds.append(list(proto.dimensions))
        for coll in list(proto.users_collection): coll.objects.unlink(proto)
        sources.objects.link(proto)
        mesh=bpy.data.meshes.new('Visible voxel centres'); carrier=bpy.data.objects.new('Palette '+str(n),mesh); s.collection.objects.link(carrier); carriers.append(carrier)
        g=bpy.data.node_groups.new('Unit instances','GeometryNodeTree'); g.interface.new_socket(name='Geometry',in_out='INPUT',socket_type='NodeSocketGeometry'); g.interface.new_socket(name='Geometry',in_out='OUTPUT',socket_type='NodeSocketGeometry')
        inp=g.nodes.new('NodeGroupInput'); out=g.nodes.new('NodeGroupOutput'); src=g.nodes.new('GeometryNodeObjectInfo'); src.inputs['Object'].default_value=proto; src.inputs['As Instance'].default_value=True
        inst=g.nodes.new('GeometryNodeInstanceOnPoints'); g.links.new(inp.outputs[0],inst.inputs['Points']); g.links.new(src.outputs['Geometry'],inst.inputs['Instance']); g.links.new(inst.outputs['Instances'],out.inputs[0]); carrier.modifiers.new('Cubes on grid','NODES').node_group=g
    return s,carriers,light,bounds


def main():
    import bpy
    ap=argparse.ArgumentParser(); ap.add_argument('--probe',type=int,choices=[1,2,3],required=True); ap.add_argument('--width',type=int,default=WIDTH); ap.add_argument('--samples',type=int,default=SAMPLES); ap.add_argument('--test',action='store_true'); ap.add_argument('--benchmark',action='store_true'); ap.add_argument('--frames',type=int,default=FPS*SECONDS); args=ap.parse_args(sys.argv[sys.argv.index('--')+1:])
    name=NAMES[args.probe]; out=ROOT/'videos'/('benchmark' if args.benchmark else 'checks' if args.test else 'frames')/name; out.mkdir(parents=True,exist_ok=True)
    start=time.perf_counter(); scene,carriers,light,bounds=setup(args)
    times=[]; audits=[]
    frames=[0] if args.benchmark else [0,args.frames//4,args.frames//2,3*args.frames//4,args.frames] if args.test else range(args.frames+1)
    for f in frames:
        tick=time.perf_counter(); phase=f/args.frames
        positions,audit=geometry(args.probe,phase)
        for obj,pos in zip(carriers,positions):
            mesh=bpy.data.meshes.new('Visible voxel centres'); mesh.vertices.add(len(pos)); mesh.vertices.foreach_set('co',pos.ravel()); mesh.update(); old=obj.data; obj.data=mesh; bpy.data.meshes.remove(old)
        if args.probe==3:
            scene.camera.location.y=CAMERA[1]+PERIOD*phase
            # Softbox follows the camera; relative illumination repeats exactly too.
            light.location.y=PERIOD*phase
        scene.render.filepath=str(out/f'{f:04d}.png'); bpy.ops.render.render(write_still=True)
        elapsed=time.perf_counter()-tick; times.append(elapsed); audits.append(audit)
        result=dict(probe=name,engine=scene.render.engine,device=scene.cycles.device,render_threads=scene.render.threads,nice=os.getpriority(os.PRIO_PROCESS,0),cpu_affinity=sorted(os.sched_getaffinity(0)),width=args.width,height=scene.render.resolution_y,fps=FPS,frames=args.frames,samples=args.samples,frame_seconds=times,total_seconds=time.perf_counter()-start,peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,master_dimensions=bounds,audits=audits)
        (out/'render-stats.json').write_text(json.dumps(result,indent=2)+'\n')
        print(f'VIDEO_FRAME {name} {f}/{args.frames} {elapsed:.3f}s',flush=True)
    sys.stdout.flush(); sys.stderr.flush(); os._exit(0)
if __name__=='__main__': main()
