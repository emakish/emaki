# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Periodic hand-built ribbon strip, reusing the unchanged R2 materials/passes."""
from pathlib import Path
import argparse, json, math, os, resource, sys, time
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import bpy
from mathutils import Vector
from types import SimpleNamespace
import ribbons_round2 as r2
r = r2.r
P=5136.0
H=821.0
S=H/7.2

# Geometry has a period of 5136 design units and a height of 821 units.
# Repeated patches pass across the wrap as geometry. No image textures enter the scene.
PALE_TOP=[(0,-350),(150,-500),(400,-220),(600,90),(800,240),(1000,386),(1150,436),(1300,416),(1450,340),(1600,170),(1800,15),(2000,-40),(2200,0),(2400,100),(2600,190),(2800,245),(3000,230),(3200,174),(3400,170),(3700,210),(4000,153),(4300,20),(4600,-65),(4900,-180)]
MID=[(0,375),(200,400),(400,425),(600,440),(800,485),(1000,550),(1200,570),(1400,560),(1600,470),(1800,378),(1900,368),(2100,415),(2300,464),(2400,470),(2600,429),(2800,336),(3000,235),(3200,176),(3350,163),(3500,190),(3800,290),(3950,298),(4200,235),(4400,179),(4550,165),(4700,190),(4900,275)]
DEEP_BOTTOM=[(0,375),(250,440),(500,480),(800,560),(1000,600),(1200,640),(1400,632),(1600,520),(1750,410),(1850,400),(1950,420),(2100,515),(2300,588),(2400,593),(2600,542),(2800,414),(3000,290),(3130,274),(3300,308),(3500,405),(3650,474),(3800,479),(4000,438),(4200,368),(4400,307),(4550,290),(4700,310),(4900,382),(5050,392)]
LOWER_WHITE=[(0,700),(300,760),(600,840),(900,900),(1200,910),(1500,840),(1800,630),(2000,490),(2200,568),(2400,600),(2600,565),(2800,506),(2900,501),(3000,516),(3200,615),(3400,700),(3500,710),(3700,685),(4000,601),(4200,552),(4380,535),(4500,543),(4700,588),(4900,645)]
HERO_TOP=[(-900,-900),(-600,-700),(-400,-430),(-200,-180),(0,137),(150,164),(300,158),(450,146),(600,140),(700,162),(800,225),(900,355),(1000,484),(1100,565),(1250,630),(1400,656),(1530,646),(1660,608),(1740,603),(1820,630),(1950,735),(2100,817),(2300,940)]
HERO_BOTTOM=[(-900,-600),(-600,-350),(-400,-150),(-200,27),(0,194),(150,260),(300,263),(450,247),(600,245),(700,263),(800,319),(900,444),(1000,589),(1100,682),(1250,769),(1400,830),(1700,920),(2000,1080),(2300,1200)]
BOWL_TOP=[(500,-500),(700,-240),(850,230),(1000,370),(1120,394),(1260,378),(1400,316),(1500,205),(1555,0),(1580,-250),(1700,-600),(1900,-700)]
BOWL_BOTTOM=[(500,-480),(700,-200),(850,265),(1000,401),(1150,435),(1300,416),(1450,340),(1600,170),(1800,15),(1900,-40)]
UPPER_PINK_TOP=[(140,-260),(160,-80),(200,15),(300,105),(450,145),(600,135),(800,200),(1000,320)]
UPPER_PINK_BOTTOM=[(140,-100),(170,60),(230,120),(310,159),(450,164),(600,150),(800,220),(1000,340)]
LEFT_LOWER=[(-900,300),(-600,355),(-300,405),(0,375),(180,349),(320,346),(450,370),(600,434),(750,550),(900,675),(1100,760),(1300,810),(1550,950)]
LEFT_LOWER_BOTTOM=[(-900,1000),(-600,1100),(-300,1150),(0,1100),(500,1200),(1550,1500)]
POCKET_EDGE=[(0,194),(150,260),(300,263),(450,285),(550,340),(650,453),(700,530),(750,550)]


# Continuous geometry closure. The long face stays open across x=0, rather
# than collapsing a bulged cross-section into a camera-visible vertical fin.
PALE_TOP=[(0,-350),(150,-500),(400,-100),(500,0),(600,80),(700,158),(800,230),(1000,401)]+[(x,y) for x,y in PALE_TOP if x>1000]
BODY_TOP=[(0,375),(180,349),(320,346),(450,370),(600,434),(750,550),(900,675),(1100,760),(1300,810),(1400,700),(1550,580),(1600,520)]+[(x,y) for x,y in DEEP_BOTTOM if x>=1750]
MID=[(0,375),(180,349),(320,346),(450,370),(600,434),(750,550),(900,675),(1000,610)]+[(x,y) for x,y in MID if x>1000]
DEEP_BOTTOM=[(0,425),(180,399),(320,396),(450,420),(600,484),(750,600),(900,725),(1000,660)]+[(x,y) for x,y in DEEP_BOTTOM if x>1000]
HERO_TOP=[(x,y) for x,y in HERO_TOP if x<0]+[(0,-400),(100,-200),(140,50),(200,145)]+[(x,y) for x,y in HERO_TOP if x>=300]
UPPER_PINK_TOP=[(140,-260),(160,-80),(200,15),(300,105),(450,145),(600,135),(700,170),(750,210)]
UPPER_PINK_BOTTOM=[(140,-100),(170,60),(230,120),(310,159),(450,164),(600,150),(700,190),(750,240)]
UPPER_PINK_OUTER=[(140,-1000),(200,-800),(400,-100),(500,0),(600,80),(700,158),(750,200)]

def cyclic(points,x):
    # The same adjacent knots on both sides give identical values AND slopes.
    return r.curve([(a-P,b) for a,b in points]+points+[(a+P,b) for a,b in points], x%P)

def ordinary(points,x):return r.curve(points,x)

def surface(name,top,bottom,mat,z0,z1,bulge,domain=None,periodic=True,hybrid=False,tint=False):
    start,end=(0,P) if domain is None else domain
    # One continuous mesh across five periods, including shadow/bounce neighbours.
    # Nonperiodic patches are instanced at whole periods; their ends are off frame.
    reps=range(-2,3)
    for repeat in reps:
        if periodic:
            if repeat!=-2:continue
            lo,hi=-2*P,3*P
        else:lo,hi=start,end
        nx=math.ceil((hi-lo)/8);nv=32
        verts=[];uvs=[];mix=[];tints=[]
        for i in range(nx+1):
            x=lo+(hi-lo)*i/nx
            a=(cyclic if periodic else ordinary)(top,x)
            b=(cyclic if periodic else ordinary)(bottom,x)
            if name=='Left rose underface':
                # The pre-wrap return begins at the *bottom* of the deep wave,
                # rather than cutting through its pale face. Open it gradually
                # underneath the hero, where the source shows the inner curl.
                t=min(1,max(0,x/350));t=t*t*(3-2*t)
                a=max(a,cyclic(DEEP_BOTTOM,x))*(1-t)+a*t
                b=max(a,b)
            for j in range(nv+1):
                v=j/nv;y=a+(b-a)*v
                z=z0+(z1-z0)*v+bulge*math.sin(math.pi*v)
                if name.startswith('Hero'):z+=.15*math.sin((x+200)/440)
                if name=='Left rose underface':
                    fade=min(1,max(0,(x+700)/600));fade=fade*fade*(3-2*fade)
                    z=-1.5*(1-fade)+z*fade
                if name=='Deep continuous return':
                    u=x%P
                    # Closed, C1 depth field. Lower the long wave behind the
                    # left hero; restore its normal depth before the bowl.
                    points=[(0,0),(650,0),(850,.05),(1100,1),(1800,1),(3500,1),(4600,1),(4900,.6)]
                    if name=='Pale continuous sweep':points=[(0,1),(500,0),(650,0),(850,.05),(1100,1),(1800,1),(3500,1),(4600,1),(4900,1)]
                    f=cyclic(points,u)
                    f=max(0,min(1,f));z=-.3*(1-f)+z*f
                if name=='White bowl':
                    f=min(1,max(0,(x-850)/150));f=f*f*(3-2*f);z=-1.5*(1-f)+z*f
                # Hero patch repeated exactly, including this depth modulation.
                xx=x if periodic else x+repeat*P
                verts.append((xx/S,(H/2-y)/S,z));uvs.append((x/P,v))
                tints.append(max(0,min(1,cyclic([(0,.4),(300,1),(700,1),(1100,.3),(1500,0),(4500,0),(4900,0)],x))))
                mix.append(cyclic([(0,.05),(500,0),(1200,0),(1800,0),(2100,.12),(2500,.8),(2800,1),(3500,1),(4400,1),(4800,.6)],x) if hybrid else 0)
        faces=[(i*(nv+1)+j,(i+1)*(nv+1)+j,(i+1)*(nv+1)+j+1,i*(nv+1)+j+1)[::-1] for i in range(nx) for j in range(nv)]
        mesh=bpy.data.meshes.new(name);mesh.from_pydata(verts,[],faces);mesh.materials.append(mat);mesh.update()
        obj=bpy.data.objects.new(name+(f' {repeat:+d}' if not periodic else ''),mesh);bpy.context.collection.objects.link(obj)
        uv=mesh.uv_layers.new(name='Satin across width')
        for poly in mesh.polygons:
            poly.use_smooth=True
            for li in poly.loop_indices:uv.data[li].uv=uvs[mesh.loops[li].vertex_index]
        if hybrid:
            attr=mesh.attributes.new('WhiteMix','FLOAT','POINT');attr.data.foreach_set('value',mix)
        if tint:
            attr=mesh.attributes.new('UnderMix','FLOAT','POINT');attr.data.foreach_set('value',tints)
        sub=obj.modifiers.new('Continuous satin','SUBSURF');sub.levels=1;sub.render_levels=1
        # A closed thickness is deliberately omitted: these surfaces are the
        # paired faces of folds, with subpixel edge thickness at this scale.


def hybrid_material(source):
    mat=source.copy();mat.name='Rose to porcelain | travelling lower fold'
    n=mat.node_tree.nodes;l=mat.node_tree.links
    bs=n.get('Principled BSDF');bounce=bs.inputs['Base Color'].links[0].from_node
    base=bounce.inputs[1].links[0].from_socket
    attr=n.new('ShaderNodeAttribute');attr.attribute_name='WhiteMix'
    rgb=n.new('ShaderNodeMixRGB');l.new(attr.outputs['Fac'],rgb.inputs[0]);l.new(base,rgb.inputs[1]);rgb.inputs[2].default_value=(.9,.9,.9,1)
    l.new(rgb.outputs[0],bounce.inputs[1])
    for aov in [q for q in n if q.bl_idname=='ShaderNodeOutputAOV']:
        if aov.aov_name=='SurfaceAlbedo':l.new(rgb.outputs[0],aov.inputs['Color'])
        if aov.aov_name=='PaletteWeights':
            old=aov.inputs['Color'].links[0].from_socket
            w=n.new('ShaderNodeMixRGB');l.new(attr.outputs['Fac'],w.inputs[0]);l.new(old,w.inputs[1]);w.inputs[2].default_value=(0,0,0,1);l.new(w.outputs[0],aov.inputs['Color'])
    return mat


def under_material(source):
    mat=source.copy();mat.name='Pale to rose | continuous inner face'
    n=mat.node_tree.nodes;l=mat.node_tree.links
    bs=n.get('Principled BSDF');bounce=bs.inputs['Base Color'].links[0].from_node
    base=bounce.inputs[1].links[0].from_socket
    attr=n.new('ShaderNodeAttribute');attr.attribute_name='UnderMix'
    rgb=n.new('ShaderNodeMixRGB');l.new(attr.outputs['Fac'],rgb.inputs[0]);l.new(base,rgb.inputs[1]);rgb.inputs[2].default_value=r.rgb(r.PALETTE['base'])
    l.new(rgb.outputs[0],bounce.inputs[1])
    for aov in [q for q in n if q.bl_idname=='ShaderNodeOutputAOV']:
        if aov.aov_name=='SurfaceAlbedo':l.new(rgb.outputs[0],aov.inputs['Color'])
        if aov.aov_name=='PaletteWeights':
            old=aov.inputs['Color'].links[0].from_socket
            w=n.new('ShaderNodeMixRGB');l.new(attr.outputs['Fac'],w.inputs[0]);l.new(old,w.inputs[1]);w.inputs[2].default_value=(1,0,0,1);l.new(w.outputs[0],aov.inputs['Color'])
    return mat


def setup(args):
    # Only imports/reads the video code; all Blender datablocks belong to this process.
    scene,profile=r2.setup(SimpleNamespace(width=args.width,height=args.height,fps=24,threads=4,profile='final',samples=args.samples,palette=None,passes=True,device='CPU'))
    mats={key:bpy.data.materials[name] for key,name in dict(base='Rose | base satin',white='Porcelain | white satin',pale='Rose | pale face',fold='Rose | deep turned fold',under='Rose | shaded flowing face',lower='Rose | broad foreground',pocket='Rose | left inner curl').items()}
    for obj in list(scene.objects):
        if obj.type in {'MESH','LIGHT'}:bpy.data.objects.remove(obj,do_unlink=True)
    # Remove now unused video geometry, including shape keys.
    for mesh in list(bpy.data.meshes):
        if mesh.users==0:bpy.data.meshes.remove(mesh)
    surface('Rose ground',[(0,-1000),(2568,-1000)],[(0,1900),(2568,1900)],mats['base'],-1.1,-1.1,.25)
    surface('Deep continuous return',MID,DEEP_BOTTOM,mats['fold'],.65,.58,-.12)
    surface('Pale continuous sweep',PALE_TOP,MID,under_material(mats['pale']),.79,.79,.30,tint=True)
    surface('Rose face below return',BODY_TOP,LOWER_WHITE,mats['lower'],.57,.20,.23)
    surface('Lower porcelain sweep',LOWER_WHITE,[(0,1500),(2568,1500)],hybrid_material(mats['lower']),.20,-.4,.35,hybrid=True)
    surface('White bowl',BOWL_TOP,BOWL_BOTTOM,mats['white'],.48,.78,.20,(500,1900),False)
    
    surface('Upper left turned edge',UPPER_PINK_TOP,UPPER_PINK_BOTTOM,mats['fold'],1.05,.98,-.08,(140,750),False)
    surface('Upper left pale',UPPER_PINK_OUTER,UPPER_PINK_TOP,mats['pale'],1.10,1.10,.15,(140,750),False)
    # Upper-left pale sheet ends under the hero.
    
    
    
    
    surface('Hero white fold',HERO_TOP,HERO_BOTTOM,mats['white'],1.73,.70,.29,(-900,2300),False)
    # Directional broad sources give position-invariant illumination. Match R2's
    # neutral world and key/fill direction; finite softboxes cannot tile exactly.
    for name,direction,energy,angle in [('Periodic neutral key',(-3,6,8),2.6,.8),('Periodic neutral fill',(2,-5,8),.65,1.2)]:
        data=bpy.data.lights.new(name,'SUN');data.energy=energy;data.angle=angle;data.color=(1,1,1)
        obj=bpy.data.objects.new(name,data);bpy.context.collection.objects.link(obj)
        obj.rotation_euler=(-Vector(direction)).to_track_quat('-Z','Y').to_euler()
    scene.camera.location=(P/(2*S),0,25);scene.camera.data.type='ORTHO';scene.camera.data.sensor_fit='HORIZONTAL';scene.camera.data.ortho_scale=P/S
    scene.render.use_persistent_data=False
    scene.cycles.seed=22117;scene.frame_set(0)
    scene['strip_period']=P/S
    scene['strip_lighting']='Neutral R2 satin, world 0.55; translation-invariant soft directional key/fill.'
    # Unneeded illumination AOVs add memory but are not used by colour_pipeline.
    scene.view_layers[0].use_pass_diffuse_direct=False;scene.view_layers[0].use_pass_diffuse_indirect=False
    return scene,profile


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--width',type=int,default=5136);ap.add_argument('--height',type=int,default=821)
    ap.add_argument('--first',type=int);ap.add_argument('--last',type=int);ap.add_argument('--samples',type=int,default=32)
    ap.add_argument('--out',required=True);ap.add_argument('--save-blend',action='store_true')
    a=ap.parse_args(sys.argv[sys.argv.index('--')+1:]);out=r.project_path(a.out);out.parent.mkdir(parents=True,exist_ok=True)
    if not out.relative_to(ROOT).as_posix().startswith('renders/s8k-'):raise ValueError('s8k outputs only')
    tick=time.perf_counter();scene,profile=setup(a)
    first=0 if a.first is None else a.first;last=a.width if a.last is None else a.last
    bandw=last-first
    scene.render.resolution_x=bandw;scene.render.resolution_y=a.height
    # Explicit horizontal fit keeps exactly the full image's orthographic pixel
    # footprints even for tall, narrow bands (otherwise AUTO changes the scale).
    scene.camera.data.ortho_scale=(P/S)*bandw/a.width
    scene.camera.location.x=(P/S)*(first+last)/(2*a.width)
    # Probe uses the exact final vertical field, independent of rounding aspect.
    scene.render.pixel_aspect_x=1;scene.render.pixel_aspect_y=(H/P)*(a.width/a.height)
    if a.save_blend:
        if hasattr(bpy.context.preferences.filepaths,'save_preview_images'):bpy.context.preferences.filepaths.save_preview_images=False
        bpy.ops.wm.save_as_mainfile(filepath=str(out.with_suffix('.blend')))
    setup_time=time.perf_counter()-tick;tick=time.perf_counter()
    scene.render.image_settings.file_format='OPEN_EXR';scene.render.image_settings.media_type='MULTI_LAYER_IMAGE';scene.render.image_settings.color_depth='32';scene.render.image_settings.exr_codec='ZIP'
    partial=out.with_name(out.stem+'.partial.exr');scene.render.filepath=str(partial)
    bpy.ops.render.render(write_still=True);os.replace(partial,out)
    meta=dict(width=a.width,height=a.height,first=first,last=last,samples=a.samples,threads=4,setup_seconds=setup_time,render_seconds=time.perf_counter()-tick,peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,profile=profile)
    out.with_suffix('.json').write_text(json.dumps(meta,indent=2)+'\n');print('S8K_RENDER '+json.dumps(meta),flush=True)

if __name__=='__main__':
    try:main()
    except BaseException:
        import traceback;traceback.print_exc();sys.stdout.flush();sys.stderr.flush();os._exit(1)
    sys.stdout.flush();sys.stderr.flush();os._exit(0)
