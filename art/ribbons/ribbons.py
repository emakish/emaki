# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Rose satin ribbon geometry. Blender 5.2 / Cycles CPU, no external assets.

Run with nice -n 19 and --threads 4. All output is constrained to this project.
Geometry, materials and periodic motion live here; commands are in README.md.
"""
from pathlib import Path
import argparse
import bisect
import json
import math
import os
import sys
import time
import resource

# ---------------------- ART / OUTPUT PARAMETERS ----------------------
PALETTE = {
    'base': '#E780AA',
    'pale': '#F5B1CB',
    'deep': '#A32C61',
    'white': '#FFFFFF',
    'world': '#FFF6EF',
    'light': '#FFF4EA',
}
RESOLUTION = (3840, 2160)
SAMPLES = 96
FPS = 24
LOOP_SECONDS = 9.75
FRAME_START = 0
FRAME_END = round(LOOP_SECONDS * FPS) - 1  # Exclusive endpoint is never encoded.
THREADS = 8
SEED = 22117                         # Static seed: temporally stable sampling.
FRAMING = 'video'                    # video / master / strip
# Widths are in design units, height is always 720.
FRAMINGS = {'video': (640, 1280), 'master': (640, 1680), 'strip': (2052, 4504)}
MOTION_AMOUNT = 1.0
FLEXES_PER_LOOP = 2                  # Each loop makes two slow flexes.
SHEEN_WEIGHT = 0.08
ROUGHNESS = 0.68
WHITE_REFLECTANCE = 0.72             # Neutral white cloth reflectance, linear.
BODY_PALE_MIX = 0.28
MICRO_NORMAL_STRENGTH = 0.035         # Fine, quiet finish; no glitter or weave pattern.
KEY_ENERGY = 600
FILL_ENERGY = 250
WORLD_STRENGTH = 0.60
EXPOSURE = 0.0
LONGITUDINAL_STEP = 7.0              # Design units before Catmull-Clark.
CROSS_SEGMENTS = 24
SUBDIVISION = 2
SAVE_BLEND = False
ROOT = Path(__file__).resolve().parent

# Control curves use a 1280 x 720 design-unit coordinate system.
# Each pair holds a rest silhouette and the broad flex at about 2.44 seconds.
# The continuation beyond the video crop is genuine geometry for still strips.
HERO_TOP = [(-900,75),(-400,30),(-200,125),(0,150),(160,138),(310,125),(420,164),(500,236),(580,345),(650,415),(800,517),(970,568),(1080,575),(1190,548),(1280,531),(1410,552),(1600,700),(1800,760),(2100,610),(2450,475),(2780,500),(3150,645),(3500,630),(3900,485),(4300,510),(4700,650),(5300,580)]
HERO_TOP_FLEX = [(0,92),(100,127),(260,158),(400,187),(550,253),(700,360),(850,467),(1000,532),(1150,565),(1280,550)]
HERO_BOTTOM = [(-900,200),(-400,165),(-200,210),(0,234),(140,229),(260,214),(350,228),(430,275),(510,377),(590,491),(680,585),(800,661),(970,721),(1150,778),(1350,830),(1600,900),(1800,955),(2100,810),(2450,680),(2780,700),(3150,845),(3500,830),(3900,685),(4300,710),(4700,850),(5300,780)]
HERO_BOTTOM_FLEX = [(0,194),(120,232),(260,271),(400,318),(550,405),(700,533),(850,654),(950,720),(1150,789),(1280,815)]
LIP_TOP = [(-900,-800),(-200,-440),(0,-250),(250,0),(400,125),(500,215),(600,312),(690,339),(770,346),(900,328),(1030,265),(1110,178),(1150,75),(1165,0),(1190,-180),(1280,-500),(1600,-700),(5300,-700)]
LIP_TOP_FLEX = [(0,-300),(250,0),(350,140),(450,235),(600,295),(760,293),(930,268),(1050,213),(1150,98),(1170,0),(1280,-490)]
PALE_TOP = [(-900,-780),(-200,-440),(0,-250),(250,0),(400,125),(500,215),(600,329),(690,365),(800,382),(900,370),(1030,310),(1160,205),(1280,80),(1420,-16),(1580,-65),(1800,-30),(2100,105),(2400,140),(2700,80),(3000,85),(3300,155),(3650,90),(4000,20),(4350,110),(4700,180),(5300,20)]
PALE_TOP_FLEX = [(0,-300),(250,0),(350,140),(450,235),(600,311),(760,325),(900,305),(1030,268),(1160,174),(1280,70)]
PALE_BOTTOM = [(-900,40),(-400,-35),(-200,-80),(0,70),(80,113),(160,134),(250,132),(400,180),(550,320),(700,433),(850,492),(980,502),(1100,478),(1200,423),(1280,370),(1400,328),(1520,345),(1700,400),(1900,430),(2100,390),(2350,275),(2540,258),(2800,345),(3000,390),(3250,350),(3500,270),(3750,245),(4000,340),(4250,380),(4500,320),(4800,260),(5300,360)]
PALE_BOTTOM_FLEX = [(0,45),(160,140),(350,207),(550,280),(750,390),(900,450),(1050,464),(1180,430),(1280,390)]
FOLD_BOTTOM = [(-900,100),(-400,30),(-200,-20),(0,110),(80,144),(160,151),(250,158),(400,215),(550,355),(700,460),(850,530),(1000,562),(1080,551),(1160,505),(1250,405),(1400,350),(1520,395),(1700,485),(1900,535),(2100,495),(2350,345),(2540,330),(2800,445),(3000,490),(3250,440),(3500,330),(3750,310),(4000,440),(4250,480),(4500,410),(4800,330),(5300,455)]
FOLD_BOTTOM_FLEX = [(0,105),(160,155),(350,247),(550,330),(750,423),(900,495),(1050,542),(1150,520),(1280,410)]
LOWER_TOP = [(-900,460),(-400,370),(-200,335),(0,305),(100,310),(220,355),(390,470),(560,600),(740,681),(970,730),(1280,850),(1550,1000),(1900,950),(2300,870),(2700,820),(3100,980),(3500,950),(3900,825),(4500,950),(5300,900)]
LOWER_TOP_FLEX = [(0,278),(120,331),(260,429),(400,503),(550,581),(700,640),(850,686),(970,740),(1280,865)]

CURL_EDGE = [(-900,220),(-330,230),(0,234),(100,230),(200,264),(280,332),(340,408),(390,470),(560,600),(740,681),(970,730),(1280,850),(1550,1000),(1900,950),(2300,870),(2700,820),(3100,980),(3500,950),(3900,825),(4500,950),(5300,900)]
CURL_EDGE_FLEX = [(0,194),(100,233),(200,325),(290,420),(400,503),(550,581),(700,640),(850,686),(970,740),(1280,865)]

# ---------------------------- IMPLEMENTATION -------------------------
import bpy
from mathutils import Vector


def rgb(hex_value):
    a = [int(hex_value[i:i+2],16)/255 for i in (1,3,5)]
    return tuple(v/12.92 if v <= 0.04045 else ((v+0.055)/1.055)**2.4 for v in a)+(1,)


def curve(points, x):
    """C1 cubic Hermite interpolation, independent of optional Python packages."""
    xs = [p[0] for p in points]
    i = max(0, min(len(points)-2, bisect.bisect_right(xs,x)-1))
    x0,y0 = points[i]; x1,y1 = points[i+1]
    t = max(0, min(1, (x-x0)/(x1-x0)))
    a = max(0,i-1); b = min(len(points)-1,i+2)
    m0 = (y1-points[a][1])/(x1-points[a][0])
    m1 = (points[b][1]-y0)/(points[b][0]-x0)
    return (2*t**3-3*t*t+1)*y0+(t**3-2*t*t+t)*(x1-x0)*m0+(-2*t**3+3*t*t)*y1+(t**3-t*t)*(x1-x0)*m1


def pose(rest, flex, x, phase):
    if flex is None:
        return curve(rest,x)
    lo, hi = flex[0][0],flex[-1][0]
    clamped = min(hi,max(lo,x))
    delta = curve(flex,clamped)-curve(rest,clamped)
    # Ease motion into the extended strip without introducing a crop-boundary kink.
    envelope = math.exp(-((x-clamped)/450)**2)
    weight = 0.5-0.5*math.cos(phase)
    return curve(rest,x)+delta*weight*envelope*MOTION_AMOUNT


def satin(name, colors):
    mat=bpy.data.materials.new(name); mat.use_nodes=True
    nodes=mat.node_tree.nodes; links=mat.node_tree.links
    bs=nodes.get('Principled BSDF')
    bs.inputs['Roughness'].default_value=ROUGHNESS
    bs.inputs['Specular IOR Level'].default_value=0.12
    bs.inputs['Sheen Weight'].default_value=SHEEN_WEIGHT
    bs.inputs['Sheen Roughness'].default_value=0.75
    bs.inputs['Coat Weight'].default_value=0
    tex=nodes.new('ShaderNodeTexCoord')
    split=nodes.new('ShaderNodeSeparateXYZ');links.new(tex.outputs['UV'],split.inputs[0])
    ramp=nodes.new('ShaderNodeValToRGB')
    for elem in list(ramp.color_ramp.elements)[2:]: ramp.color_ramp.elements.remove(elem)
    for i,(pos,key) in enumerate(colors):
        elem=ramp.color_ramp.elements[i] if i<2 else ramp.color_ramp.elements.new(pos)
        elem.position=pos
        if key=='body':
            a,b=rgb(PALETTE['base']),rgb(PALETTE['pale'])
            elem.color=tuple(a[i]*(1-BODY_PALE_MIX)+b[i]*BODY_PALE_MIX for i in range(3))+(1,)
        else:
            color=rgb(PALETTE[key])
            elem.color=tuple(c*WHITE_REFLECTANCE for c in color[:3])+(1,) if key=='white' else color
    ramp.color_ramp.interpolation='EASE'
    links.new(split.outputs['Y'],ramp.inputs['Fac']);links.new(ramp.outputs['Color'],bs.inputs['Base Color'])
    noise=nodes.new('ShaderNodeTexNoise');noise.inputs['Scale'].default_value=370
    noise.inputs['Detail'].default_value=2;noise.inputs['Roughness'].default_value=0.6
    links.new(tex.outputs['Object'],noise.inputs['Vector'])
    bump=nodes.new('ShaderNodeBump');bump.inputs['Strength'].default_value=MICRO_NORMAL_STRENGTH
    bump.inputs['Distance'].default_value=0.004
    links.new(noise.outputs['Fac'],bump.inputs['Height']);links.new(bump.outputs[0],bs.inputs['Normal'])
    return mat


# type, top rest/flex, bottom rest/flex, material, z(top), z(bottom), bulge
SPECS = [
 ('rose_ground', [(-900,-1000),(5300,-1000)], None, [(-900,1800),(5300,1800)], None, 'base', -1.10,-1.10,0.25),
 ('white_return', LIP_TOP,LIP_TOP_FLEX,PALE_TOP,PALE_TOP_FLEX,'white',0.48,0.78,0.20),
 ('rose_return', PALE_BOTTOM,PALE_BOTTOM_FLEX,FOLD_BOTTOM,FOLD_BOTTOM_FLEX,'fold',0.65,0.58,-0.12),
 ('pale_sweep',PALE_TOP,PALE_TOP_FLEX,PALE_BOTTOM,PALE_BOTTOM_FLEX,'pale',0.79,0.79,0.30),
 ('rose_under_white',HERO_BOTTOM,HERO_BOTTOM_FLEX,CURL_EDGE,CURL_EDGE_FLEX,'under',0.70,0.41,0.10),
 ('left_turn_pocket',CURL_EDGE,CURL_EDGE_FLEX,LOWER_TOP,LOWER_TOP_FLEX,'pocket',-0.25,0.41,-0.20),
 ('rose_fold_tail',FOLD_BOTTOM,FOLD_BOTTOM_FLEX,HERO_BOTTOM,HERO_BOTTOM_FLEX,'lower',0.57,0.20,0.23),
 ('lower_rose',LOWER_TOP,LOWER_TOP_FLEX,[(-900,1600),(5300,1600)],None,'lower',0.41,-0.50,0.42),
 ('white_foreground',HERO_TOP,HERO_TOP_FLEX,HERO_BOTTOM,HERO_BOTTOM_FLEX,'white',1.73,0.70,0.29),
]


def surface_xyz(spec,x,v,phase):
    name,top,tf,bottom,bf,_,z0,z1,bulge=spec
    ya=pose(top,tf,x,phase);yb=pose(bottom,bf,x,phase)
    if name=='white_return' and x<600:
        fade=max(0,min(1,(x-520)/80))
        ya=yb+(ya-yb)*fade
    if name=='rose_fold_tail' and x<750:
        ya=max(ya,pose(HERO_TOP,HERO_TOP_FLEX,x,phase)+5)
    y=ya*(1-v)+yb*v
    # Slight traveling component makes the breathing motion flow along the ribbon.
    wave=math.sin(phase)*math.exp(-((x-660)/630)**2)*MOTION_AMOUNT
    if name!='rose_ground':
        y += 16*wave*math.sin(x/330)  # Shared edges must follow the same spatial field.
        x += 11*wave
    profile=math.sin(math.pi*v)
    z=z0*(1-v)+z1*v+bulge*profile
    if name=='white_foreground':
        z += 0.18*math.sin(x/390) + 0.075*wave*profile
    elif name=='pale_sweep':
        z += 0.08*math.sin(x/470)*profile
    elif name=='rose_under_white':
        smooth=v*v*(3-2*v)
        z=z0+(z1-z0)*smooth+0.12*profile*profile
    elif name=='white_return' and x<600:
        z -= 2.0*(1-max(0,min(1,(x-520)/80)))
    return ((x-640)/100, (360-y)/100, z)


def build_surface(spec,material):
    # Only construct the visible region plus a wide shadow margin, except when saving
    # the master scene or rendering a long strip, where all continuation is kept.
    center,width=FRAMINGS[args.framing]
    xlo=max(-900, center-width/2-330);xhi=min(5300,center+width/2+330)
    if spec[0]=='rose_fold_tail':xlo=max(500,xlo)
    if spec[0]=='left_turn_pocket':xhi=min(405,xhi)
    nx=math.ceil((xhi-xlo)/LONGITUDINAL_STEP)
    nv=CROSS_SEGMENTS
    params=[(xlo+(xhi-xlo)*i/nx,j/nv) for i in range(nx+1) for j in range(nv+1)]
    verts=[surface_xyz(spec,x,v,0) for x,v in params]
    # Reversed winding because screen y points down. Normals face the camera.
    faces=[(i*(nv+1)+j,(i+1)*(nv+1)+j,(i+1)*(nv+1)+j+1,i*(nv+1)+j+1)[::-1] for i in range(nx) for j in range(nv)]
    mesh=bpy.data.meshes.new(spec[0]);mesh.from_pydata(verts,[],faces);mesh.materials.append(material)
    mesh.update();obj=bpy.data.objects.new(spec[0],mesh);bpy.context.collection.objects.link(obj)
    uv=mesh.uv_layers.new(name='Satin across width')
    for poly in mesh.polygons:
        poly.use_smooth=True
        for li in poly.loop_indices:
            x,v=params[mesh.loops[li].vertex_index];uv.data[li].uv=((x+900)/6200,v)
    obj['description']='Analytic sculpted satin sheet; no image textures.'
    sub=obj.modifiers.new('Smooth continuous satin','SUBSURF');sub.levels=SUBDIVISION;sub.render_levels=SUBDIVISION
    if spec[0] in ('white_foreground','pale_sweep','white_return'):
        solid=obj.modifiers.new('Fine fabric edge','SOLIDIFY');solid.thickness=0.008;solid.offset=-1
    # Native Blender shape-key drivers preserve the motion in the saved blend.
    # The full deformation is affine in 1-cos(phase) and sin(phase), so two keys
    # are sufficient; there is no point-cache or simulation seam.
    obj.shape_key_add(name='Basis')
    for keyname,phase in [('Flex',math.pi),('Flow',math.pi/2)]:
        key=obj.shape_key_add(name=keyname)
        for idx,(x,v) in enumerate(params):
            p=Vector(surface_xyz(spec,x,v,phase))
            if keyname=='Flow':
                # Remove half of Flex to isolate the sine term.
                p-=0.5*(Vector(surface_xyz(spec,x,v,math.pi))-Vector(verts[idx]))
            key.data[idx].co=p
        key.slider_min=-1;key.slider_max=1
        drv=key.driver_add('value').driver
        angle=f'({2*math.pi*FLEXES_PER_LOOP:.17g}*frame/{LOOP_SECONDS*args.fps:.17g})'
        drv.expression=f'(1-cos({angle}))/2' if keyname=='Flex' else f'sin({angle})'
    return obj


def area(name,loc,power,size,target=(0,0,0)):
    data=bpy.data.lights.new(name,'AREA');data.energy=power;data.shape='DISK';data.size=size
    data.color=rgb(PALETTE['light'])[:3]
    obj=bpy.data.objects.new(name,data);bpy.context.collection.objects.link(obj);obj.location=loc
    obj.rotation_euler=(Vector(target)-obj.location).to_track_quat('-Z','Y').to_euler()


def setup():
    bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
    scene=bpy.context.scene
    scene.render.engine='CYCLES';scene.cycles.device='CPU';scene.cycles.samples=args.samples
    scene.cycles.use_adaptive_sampling=True;scene.cycles.adaptive_threshold=0.025 if args.samples<64 else 0.012
    scene.cycles.use_denoising=True;scene.cycles.denoiser='OPENIMAGEDENOISE'
    scene.cycles.seed=SEED;scene.cycles.use_animated_seed=False
    scene.cycles.max_bounces=6;scene.cycles.diffuse_bounces=4;scene.cycles.glossy_bounces=3
    scene.cycles.transmission_bounces=0;scene.cycles.volume_bounces=0
    scene.cycles.sample_clamp_indirect=3
    scene.render.threads_mode='FIXED';scene.render.threads=min(8,THREADS)
    scene.render.resolution_x=args.width;scene.render.resolution_y=args.height;scene.render.resolution_percentage=100
    scene.render.image_settings.file_format='PNG';scene.render.image_settings.color_mode='RGB';scene.render.image_settings.color_depth='8'
    scene.render.fps=args.fps;scene.render.fps_base=1
    scene.render.film_transparent=False;scene.render.use_persistent_data=True
    scene.view_settings.view_transform='Standard';scene.view_settings.look='None'
    scene.view_settings.exposure=EXPOSURE;scene.view_settings.gamma=1
    scene.world.use_nodes=True
    scene.world.node_tree.nodes['Background'].inputs['Color'].default_value=rgb(PALETTE['world'])
    scene.world.node_tree.nodes['Background'].inputs['Strength'].default_value=WORLD_STRENGTH
    mats={
      'base':satin('Rose | base satin',[(0,'base'),(1,'base')]),
      'white':satin('Porcelain | white satin',[(0,'white'),(1,'white')]),
      'pale':satin('Rose | pale face',[(0,'pale'),(.42,'pale'),(1,'pale')]),
      'fold':satin('Rose | deep turned fold',[(0,'deep'),(.72,'deep'),(1,'base')]),
      'under':satin('Rose | shaded flowing face',[(0,'deep'),(.38,'base'),(1,'base')]),
      'lower':satin('Rose | broad foreground',[(0,'body'),(.5,'body'),(1,'base')]),
      'pocket':satin('Rose | left inner curl',[(0,'deep'),(.50,'deep'),(1,'base')]),
    }
    for spec in SPECS: build_surface(spec,mats[spec[5]])
    center,width=FRAMINGS[args.framing]
    data=bpy.data.cameras.new('Static orthographic framing');camera=bpy.data.objects.new('Camera',data)
    bpy.context.collection.objects.link(camera);camera.location=((center-640)/100,0,25)
    data.type='ORTHO';data.ortho_scale=width/100;data.lens=50;data.clip_end=200
    scene.camera=camera
    area('Large softbox | upper left',(-3.8,5.5,10),KEY_ENERGY,8)
    area('Gentle front fill',(2,-5,8),FILL_ENERGY,10)
    bpy.data.lights['Gentle front fill'].color=rgb(PALETTE['pale'])[:3]
    # More softboxes over the extended strip, outside the video frustum.
    if args.framing=='strip':
        area('Strip softbox 2',(18,5.5,10),KEY_ENERGY,12,target=(18,0,0))
        area('Strip softbox 3',(35,5.5,10),KEY_ENERGY,12,target=(35,0,0))
    scene.frame_start=round(FRAME_START*args.fps/FPS);scene.frame_end=min(round((FRAME_END+1)*args.fps/FPS),round(LOOP_SECONDS*args.fps))-1
    scene['loop_seconds']=LOOP_SECONDS;scene['loop_endpoint']=LOOP_SECONDS*args.fps
    scene['palette']=json.dumps(PALETTE)
    scene['geometry_description']='Procedural ribbon geometry; no external images required.'
    return scene


def project_path(raw):
    p=(ROOT/raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
    if not p.is_relative_to(ROOT): raise ValueError('Output must be inside this project')
    return p


def verify_geometry_loop(scene, out):
    """Compare evaluated, subdivided geometry including all native drivers."""
    import numpy as np
    objects=[obj for obj in scene.objects if obj.type=='MESH']
    def snapshot(frame):
        scene.frame_set(frame)
        graph=bpy.context.evaluated_depsgraph_get()
        result={}
        for obj in objects:
            evaluated=obj.evaluated_get(graph);mesh=evaluated.to_mesh()
            co=np.empty(len(mesh.vertices)*3,dtype=np.float32)
            mesh.vertices.foreach_get('co',co)
            result[obj.name]=co
            evaluated.to_mesh_clear()
        return result
    start=snapshot(0);end=snapshot(round(LOOP_SECONDS*args.fps))
    errors={name:float(np.max(np.abs(start[name]-end[name]))) for name in start}
    record={'period_seconds':LOOP_SECONDS,'fps':args.fps,
            'exclusive_endpoint':round(LOOP_SECONDS*args.fps),
            'max_evaluated_vertex_error_world_units':max(errors.values()),
            'objects':errors,'vertices_compared':sum(len(a)//3 for a in start.values()),
            'driver':'(1-cos(4*pi*frame/period))/2 and sin(4*pi*frame/period)',
            'static_camera':True,'static_lights':True,'fixed_cycles_seed':SEED}
    (out/'geometry-loop.json').write_text(json.dumps(record,indent=2)+'\n')
    scene.frame_set(0)


def main():
    global args
    cli=argparse.ArgumentParser()
    cli.add_argument('--width',type=int,default=RESOLUTION[0]);cli.add_argument('--height',type=int,default=RESOLUTION[1])
    cli.add_argument('--samples',type=int,default=SAMPLES);cli.add_argument('--fps',type=int,default=FPS)
    cli.add_argument('--frames',default=str(FRAME_START),help='Comma-separated frames, or inclusive start:end[:step]')
    cli.add_argument('--output',default='renders');cli.add_argument('--framing',choices=FRAMINGS,default=FRAMING)
    cli.add_argument('--save-blend',action='store_true',default=SAVE_BLEND);cli.add_argument('--build-only',action='store_true')
    cli.add_argument('--verify-loop',action='store_true')
    args=cli.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    if abs(LOOP_SECONDS*args.fps-round(LOOP_SECONDS*args.fps))>1e-8:
        cli.error('LOOP_SECONDS * fps must be an integer for a seamless encoded loop')
    if min(args.width,args.height,args.samples,args.fps)<1:
        cli.error('Resolution, samples and fps must be positive')
    out=project_path(args.output);out.mkdir(parents=True,exist_ok=True)
    scene=setup()
    scene.frame_set(0)
    if args.verify_loop: verify_geometry_loop(scene,out)
    scene.render.filepath=str(out/'frame-')
    if args.save_blend:
        if hasattr(bpy.context.preferences.filepaths,'save_preview_images'):
            bpy.context.preferences.filepaths.save_preview_images=False
        bpy.ops.wm.save_as_mainfile(filepath=str(out/'rose-ribbons.blend'))
    if args.build_only:return
    if ':' in args.frames:
        vals=list(map(int,args.frames.split(':')));frames=list(range(vals[0],vals[1]+1,vals[2] if len(vals)==3 else 1))
    else:frames=list(map(int,args.frames.split(',')))
    timings=[]
    for f in frames:
        scene.frame_set(f)
        scene.render.filepath=str(out/f'frame-{f:04d}.png')
        start=time.perf_counter();bpy.ops.render.render(write_still=True);elapsed=time.perf_counter()-start
        rec={'frame':f,'seconds':elapsed,'width':args.width,'height':args.height,'samples':args.samples,'fps':args.fps,'peak_rss_mib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024}
        timings.append(rec);print('RIBBON_TIMING '+json.dumps(rec),flush=True)
        (out/'timings.json').write_text(json.dumps(timings,indent=2)+'\n')
    print('RIBBON_COMPLETE '+json.dumps({'frames':len(frames),'seconds':sum(t['seconds'] for t in timings)}),flush=True)

if __name__=='__main__':
    main()
    sys.stdout.flush();sys.stderr.flush()
    os._exit(0)
