# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Round 2 material/lighting layer; imports the untouched round-1 geometry."""
from pathlib import Path
import argparse
import json
import os
import resource
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import bpy
import ribbons as r

PROFILES = {
    'quality': dict(samples=96, threshold=.012, bounces=6, diffuse=4, glossy=3, subdivision=2),
    'final': dict(samples=32, threshold=.035, bounces=4, diffuse=2, glossy=2, subdivision=1),
    'probe': dict(samples=16, threshold=.05, bounces=4, diffuse=2, glossy=2, subdivision=1),
}

def setup(args):
    p = PROFILES[args.profile].copy()
    if args.samples: p['samples'] = args.samples
    r.args = SimpleNamespace(width=args.width, height=args.height, samples=p['samples'], fps=args.fps, framing='video')
    r.THREADS = args.threads
    r.SUBDIVISION = p['subdivision']
    r.PALETTE.update(world='#FFFFFF', light='#FFFFFF', base='#E78099', pale='#F5AEC2')
    r.EXPOSURE = -.15
    if args.palette:
        r.PALETTE.update(json.loads(r.project_path(args.palette).read_text()))
    r.WHITE_REFLECTANCE = .90
    r.BODY_PALE_MIX = .45
    r.MICRO_NORMAL_STRENGTH = 0
    r.ROUGHNESS = .65
    r.SHEEN_WEIGHT = .14
    r.KEY_ENERGY = 950
    r.FILL_ENERGY = 280
    r.WORLD_STRENGTH = .55
    scene = r.setup()
    scene.render.threads = args.threads
    scene.view_settings.view_transform = 'Khronos PBR Neutral'
    scene.cycles.adaptive_threshold = p['threshold']
    scene.cycles.adaptive_min_samples = 0 if args.profile == 'quality' else 8
    scene.cycles.max_bounces = p['bounces']
    scene.cycles.diffuse_bounces = p['diffuse']
    scene.cycles.glossy_bounces = p['glossy']
    scene.render.image_settings.color_depth = '16'
    scene.render.image_settings.compression = 25
    scene.render.use_file_extension = True
    scene.cycles.denoising_input_passes = 'RGB_ALBEDO_NORMAL'
    scene.cycles.denoising_prefilter = 'ACCURATE'
    if hasattr(scene.cycles, 'denoising_use_gpu'): scene.cycles.denoising_use_gpu = False
    key = bpy.data.objects['Large softbox | upper left']
    key.location = (-3, 6, 8)
    from mathutils import Vector
    key.rotation_euler = (Vector((0,0,0))-key.location).to_track_quat('-Z','Y').to_euler()
    bpy.data.lights['Gentle front fill'].color = (1,1,1)
    for mat in bpy.data.materials:
        if not mat.use_nodes: continue
        bs = mat.node_tree.nodes.get('Principled BSDF')
        if bs and bs.inputs['Base Color'].links:
            bs.inputs['Specular IOR Level'].default_value = .22
            bs.inputs['Sheen Roughness'].default_value = .65
            # No procedural normal perturbation: continuous smooth satin.
            for link in list(bs.inputs['Normal'].links): mat.node_tree.links.remove(link)
            # Art-directed neutral bounce cards: secondary diffuse hits use a
            # fixed grey, independent of theme. Primary satin colour is intact.
            # This removes pink spill from whites and permits reusable lighting.
            links=mat.node_tree.links;nodes=mat.node_tree.nodes
            base=bs.inputs['Base Color'].links[0].from_socket
            ray=nodes.new('ShaderNodeLightPath')
            mix=nodes.new('ShaderNodeMixRGB');mix.blend_type='MIX'
            links.new(ray.outputs['Is Diffuse Ray'],mix.inputs[0])
            links.new(base,mix.inputs[1])
            neutral=.90 if mat.name.startswith('Porcelain') else .55
            mix.inputs[2].default_value=(neutral,neutral,neutral,1)
            links.new(mix.outputs[0],bs.inputs['Base Color'])
            if args.passes:
                aov=nodes.new('ShaderNodeOutputAOV');aov.name='SurfaceAlbedo';aov.aov_name='SurfaceAlbedo'
                links.new(base,aov.inputs['Color'])
                # Reproduce the primary ramp using palette weights instead of RGB.
                ramp=base.node;weights=nodes.new('ShaderNodeValToRGB')
                weights.color_ramp.interpolation=ramp.color_ramp.interpolation
                links.new(ramp.inputs['Fac'].links[0].from_socket,weights.inputs['Fac'])
                def coeff(color):
                    import numpy as np
                    cols=[r.rgb(r.PALETTE[k])[:3] for k in ['base','pale','deep']]+[(.9,.9,.9)]
                    matrix=np.vstack([np.array(cols).T,np.ones(4)])
                    w=np.linalg.solve(matrix,np.r_[list(color)[:3],1.])
                    return tuple(float(v) for v in w[:3])+(1,)
                for i,e in enumerate(ramp.color_ramp.elements):
                    w=weights.color_ramp.elements[i] if i<2 else weights.color_ramp.elements.new(e.position)
                    w.position=e.position;w.color=coeff(e.color)
                aov=nodes.new('ShaderNodeOutputAOV');aov.aov_name='PaletteWeights'
                links.new(weights.outputs['Color'],aov.inputs['Color'])
    if args.passes:
        layer=scene.view_layers[0]
        for name in ['diffuse_color','diffuse_direct','diffuse_indirect','glossy_color','glossy_direct','glossy_indirect']:
            setattr(layer,'use_pass_'+name,True)
        for name in ['SurfaceAlbedo','PaletteWeights']:
            aov=layer.aovs.add();aov.name=name;aov.type='COLOR'
    if args.device == 'GPU':
        prefs = bpy.context.preferences.addons['cycles'].preferences
        prefs.compute_device_type = 'HIP'
        prefs.get_devices()
        selected = []
        for device in prefs.devices:
            device.use = device.type == 'HIP'
            if device.use: selected.append(device.name)
        if not selected: raise RuntimeError('No Cycles HIP devices found; GPU was requested. No CPU fallback.')
        scene.cycles.device = 'GPU'
        print('R2_HIP_DEVICES '+json.dumps(selected), flush=True)
    scene['round2_profile'] = json.dumps(p)
    return scene, p

def parser():
    p = argparse.ArgumentParser()
    p.add_argument('--width',type=int,default=3840);p.add_argument('--height',type=int,default=2160)
    p.add_argument('--fps',type=int,default=24);p.add_argument('--frames',default='0')
    p.add_argument('--threads',type=int,default=4);p.add_argument('--device',choices=['CPU','GPU'],default='CPU')
    p.add_argument('--profile',choices=PROFILES,default='final');p.add_argument('--samples',type=int)
    p.add_argument('--palette');p.add_argument('--output',default='renders/r2-final-frames')
    p.add_argument('--verify-loop',action='store_true');p.add_argument('--save-blend',action='store_true')
    p.add_argument('--passes',action='store_true')
    return p

def frames_from(spec):
    if ':' in spec:
        v = list(map(int,spec.split(':')))
        return list(range(v[0],v[1]+1,v[2] if len(v)>2 else 1))
    return list(map(int,spec.split(',')))

def main():
    args=parser().parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    if min(args.width,args.height,args.threads,args.fps)<1: raise ValueError('Positive sizes, threads and fps required')
    out=r.project_path(args.output);out.mkdir(parents=True,exist_ok=True)
    scene,p=setup(args)
    if args.verify_loop:r.verify_geometry_loop(scene,out)
    if args.save_blend:
        bpy.context.preferences.filepaths.save_preview_images=False
        bpy.ops.wm.save_as_mainfile(filepath=str(out/'r2-ribbons.blend'))
    timings=[]
    for frame in frames_from(args.frames):
        scene.frame_set(frame);scene.render.filepath=str(out/f'frame-{frame:04d}.png')
        start=time.perf_counter();bpy.ops.render.render(write_still=True)
        if args.passes:
            scene.render.image_settings.file_format='OPEN_EXR'
            scene.render.image_settings.media_type='MULTI_LAYER_IMAGE'
            scene.render.image_settings.color_depth='32'
            scene.render.image_settings.exr_codec='ZIP'
            bpy.data.images['Render Result'].save_render(str(out/f'frame-{frame:04d}.exr'),scene=scene)
            scene.render.image_settings.media_type='IMAGE'
            scene.render.image_settings.file_format='PNG';scene.render.image_settings.color_depth='16'
        rec=dict(frame=frame,seconds=time.perf_counter()-start,width=args.width,height=args.height,
                 fps=args.fps,threads=args.threads,device=args.device,profile=p,
                 peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024)
        timings.append(rec);print('R2_TIMING '+json.dumps(rec),flush=True)
        (out/'timings.json').write_text(json.dumps(timings,indent=2)+'\n')

if __name__=='__main__':
    try:
        main()
    except BaseException:
        import traceback
        traceback.print_exc();sys.stdout.flush();sys.stderr.flush();os._exit(1)
    sys.stdout.flush();sys.stderr.flush();os._exit(0)
