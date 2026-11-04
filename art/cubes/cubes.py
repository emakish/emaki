# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Emaki: a real, instanced voxel landscape and a closed cylindrical camera.

Render: nice -n 19 blender -b --factory-startup -t 8 --python cubes.py -- --variant A
The same file runs its PNG/measurement stage with system Python after rendering.
"""

# ----------------------- ART DIRECTION / PARAMETERS -----------------------
PALETTE = ("#B01E78", "#EC2A55", "#FF6A2A", "#FFB62E")
SEED = 31102026
CUBE_SIZE = 1.0
GRID_HALF_EXTENT = 112          # square, 224 x 224 columns; units are cube sizes
BEVEL_WIDTH = 0.045             # 4.5% of cube size, envelope remains 1 x 1 x 1
BEVEL_SEGMENTS = 3
VARIANTS = {
    "A": dict(base_height=9.0, amplitude=1.0, camera_height=24.0, radial_amplitude=14.0, lighting_gain=1.0,
              waves=((4.2, 57.0, 0.31, 0.8, 1),
                     (3.1, 75.0, 1.73, 2.2, -1),
                     (1.5, 34.0, 2.54, 4.0, 2))),
    "B": dict(base_height=13.0, amplitude=1.65, camera_height=26.0, radial_amplitude=18.0, lighting_gain=1.09,
              waves=((4.2, 49.0, 0.31, 0.8, 1),
                     (3.1, 68.0, 1.73, 2.2, -1),
                     (1.5, 31.0, 2.54, 4.0, 2))),
}
# Each wave: (height, wavelength, direction radians, phase radians, time harmonic).
ANIMATION_PERIOD = 12.0
TIME_SECONDS = 0.0
RADIAL_WAVELENGTH = 250.0       # broad cosine basin, keeps the upper frame full
CAMERA_XY = (0.23, -0.17)
CAMERA_YAW_DEGREES = 23.0
CYLINDER_RADIUS = 1.0
CYLINDER_V_TOP = 0.06           # top ray looks up 3.43 degrees, into distant terraces
# Vertical span = horizontal span / aspect: equal local x/y projection scale.
WORLD_STRENGTH = 0.805
LIGHT_HEIGHT = 150.0
LIGHT_SIZE = 240.0             # white disk softbox, diameter in cube units
LIGHT_POWER = 70000.0
RESOLUTION = (5136, 821)
SAMPLES = 96
ADAPTIVE_THRESHOLD = 0.01
THREADS = 8                    # hard upper limit, also set with blender -t 8
DENOISE = True
WRAP_GUARD_PIXELS = 96         # real overlapping rays around both edges
LOOPCHECK_WIDTH = 600
OUTPUT_DIR = "renders"
WORK_DIR = "work"
# -------------------------------------------------------------------------

import argparse
import json
import math
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent
# Keep thumbnail, temporary and colour-management caches in this project too.
for cache_folder in (ROOT / WORK_DIR / "cache", ROOT / WORK_DIR / "tmp"):
    cache_folder.mkdir(parents=True, exist_ok=True)
os.environ["XDG_CACHE_HOME"] = str(ROOT / WORK_DIR / "cache")
os.environ["TMPDIR"] = str(ROOT / WORK_DIR / "tmp")


def srgb_to_linear(v):
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def palette_linear():
    return [tuple(srgb_to_linear(int(h[i:i+2], 16) / 255.0)
                  for i in (1, 3, 5)) for h in PALETTE]


def cube_colour(i, j, k):
    """Stable coordinate hash; independent of height, iteration order and time."""
    n = ((i * 73856093) ^ (j * 19349663) ^ (k * 83492791) ^ SEED) & 0xffffffff
    n = ((n ^ (n >> 16)) * 0x7feb352d) & 0xffffffff
    n = ((n ^ (n >> 15)) * 0x846ca68b) & 0xffffffff
    return (n ^ (n >> 16)) % len(PALETTE)


def column_height(i, j, variant, t=TIME_SECONDS):
    p = VARIANTS[variant]
    h = p["base_height"]
    radius = math.hypot(i + .5 - CAMERA_XY[0], j + .5 - CAMERA_XY[1])
    h += p["radial_amplitude"] * (1 - math.cos(math.tau * radius / RADIAL_WAVELENGTH))
    for amp, wavelength, direction, phase, harmonic in p["waves"]:
        u = (i + .5) * math.cos(direction) + (j + .5) * math.sin(direction)
        h += p["amplitude"] * amp * math.sin(
            math.tau * u / wavelength + phase + harmonic * math.tau * t / ANIMATION_PERIOD)
    return max(1, int(math.floor(h + .5)))


def build_scene(args):
    import bpy
    import numpy as np

    start = time.perf_counter()
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.render.threads_mode = "FIXED"
    scene.render.threads = min(THREADS, 8)
    scene.cycles.samples = args.samples
    scene.cycles.seed = SEED
    scene.cycles.use_animated_seed = False
    scene.cycles.use_adaptive_sampling = True
    scene.cycles.adaptive_threshold = ADAPTIVE_THRESHOLD
    scene.cycles.adaptive_min_samples = min(16, args.samples)
    scene.cycles.use_denoising = DENOISE
    scene.cycles.denoiser = "OPENIMAGEDENOISE"
    scene.cycles.denoising_prefilter = "ACCURATE"
    # Direct neutral illumination: no coloured interreflection from neighbours.
    # Environment occlusion and the softbox's physical shadows remain present.
    scene.cycles.max_bounces = 1
    scene.cycles.diffuse_bounces = 0
    scene.cycles.glossy_bounces = 0
    scene.cycles.transmission_bounces = 0
    scene.cycles.volume_bounces = 0
    scene.render.film_transparent = False
    scene.render.image_settings.media_type = "MULTI_LAYER_IMAGE"
    scene.render.image_settings.file_format = "OPEN_EXR_MULTILAYER"
    scene.render.image_settings.color_depth = "32"
    scene.render.image_settings.exr_codec = "ZIP"
    scene.render.use_file_extension = True
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0
    scene.render.image_settings.color_mode = "RGBA"
    scene.view_layers[0].use_pass_diffuse_color = True
    scene.view_layers[0].use_pass_normal = True
    scene.world.use_nodes = True
    scene.world.node_tree.nodes["Background"].inputs[0].default_value = (1, 1, 1, 1)
    gain = VARIANTS[args.variant]["lighting_gain"]
    scene.world.node_tree.nodes["Background"].inputs[1].default_value = WORLD_STRENGTH * gain

    light_data = bpy.data.lights.new("Neutral large softbox", "AREA")
    light_data.shape = "DISK"
    light_data.size = LIGHT_SIZE * CUBE_SIZE
    light_data.energy = LIGHT_POWER * gain * CUBE_SIZE ** 2
    light = bpy.data.objects.new("Neutral large softbox", light_data)
    scene.collection.objects.link(light)
    light.location = (0, 0, LIGHT_HEIGHT * CUBE_SIZE)

    sources = bpy.data.collections.new("Four identical cube masters (not in scene)")
    positions = [[] for _ in PALETTE]
    histogram = [0] * len(PALETTE)
    heights = []
    for i in range(-GRID_HALF_EXTENT, GRID_HALF_EXTENT):
        for j in range(-GRID_HALF_EXTENT, GRID_HALF_EXTENT):
            h = column_height(i, j, args.variant, args.time)
            heights.append(h)
            for k in range(h):
                c = cube_colour(i, j, k)
                positions[c].extend(((i + .5) * CUBE_SIZE,
                                     (j + .5) * CUBE_SIZE,
                                     (k + .5) * CUBE_SIZE))
                histogram[c] += 1
    for c, rgb in enumerate(palette_linear()):
        mat = bpy.data.materials.new(f"{c + 1} {PALETTE[c]} linear sRGB")
        mat.diffuse_color = (*rgb, 1)
        mat.use_nodes = True
        mat.node_tree.nodes.clear()
        out = mat.node_tree.nodes.new("ShaderNodeOutputMaterial")
        diffuse = mat.node_tree.nodes.new("ShaderNodeBsdfDiffuse")
        diffuse.inputs["Color"].default_value = (*rgb, 1)
        diffuse.inputs["Roughness"].default_value = 0.0  # ideal matte Lambertian, no specular lobe
        mat.node_tree.links.new(diffuse.outputs[0], out.inputs["Surface"])

        bpy.ops.mesh.primitive_cube_add(size=CUBE_SIZE)
        proto = bpy.context.object
        proto.name = f"Cube master {c + 1} — unit envelope"
        proto.data.materials.append(mat)
        bevel = proto.modifiers.new("Small physical rounded edges", "BEVEL")
        bevel.width = BEVEL_WIDTH * CUBE_SIZE
        bevel.segments = BEVEL_SEGMENTS
        bpy.ops.object.modifier_apply(modifier=bevel.name)
        # Keep broad faces exactly planar; smooth only the narrow bevel strips.
        for face in proto.data.polygons:
            face.use_smooth = len(face.vertices) != 4 or face.area < .25 * CUBE_SIZE ** 2
        weighted = proto.modifiers.new("Weighted corner normals", "WEIGHTED_NORMAL")
        weighted.keep_sharp = True
        bpy.ops.object.modifier_apply(modifier=weighted.name)
        for collection in list(proto.users_collection):
            collection.objects.unlink(proto)
        sources.objects.link(proto)

        mesh = bpy.data.meshes.new(f"Palette {c + 1}: integer voxel centres")
        mesh.vertices.add(histogram[c])
        mesh.vertices.foreach_set("co", np.asarray(positions[c], dtype=np.float32))
        mesh.update()
        positions[c] = None
        carrier = bpy.data.objects.new(f"Palette {c + 1}: {histogram[c]:,} instances", mesh)
        scene.collection.objects.link(carrier)
        group = bpy.data.node_groups.new(f"Instance solid cubes {c + 1}", "GeometryNodeTree")
        group.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        group.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        node_in = group.nodes.new("NodeGroupInput")
        node_out = group.nodes.new("NodeGroupOutput")
        source = group.nodes.new("GeometryNodeObjectInfo")
        source.inputs["Object"].default_value = proto
        source.inputs["As Instance"].default_value = True
        instance = group.nodes.new("GeometryNodeInstanceOnPoints")
        group.links.new(node_in.outputs["Geometry"], instance.inputs["Points"])
        group.links.new(source.outputs["Geometry"], instance.inputs["Instance"])
        group.links.new(instance.outputs["Instances"], node_out.inputs["Geometry"])
        mod = carrier.modifiers.new("Instances; never realised", "NODES")
        mod.node_group = group

    camera_data = bpy.data.cameras.new("Closed 360 degree central cylinder")
    camera_data.type = "PANO"
    camera_data.panorama_type = "CENTRAL_CYLINDRICAL"
    camera_data.central_cylindrical_radius = CYLINDER_RADIUS
    guard = args.guard
    du = math.tau / args.width
    camera_data.central_cylindrical_range_u_min = -math.pi - guard * du
    camera_data.central_cylindrical_range_u_max = math.pi + guard * du
    camera_data.central_cylindrical_range_v_max = CYLINDER_V_TOP
    camera_data.central_cylindrical_range_v_min = CYLINDER_V_TOP - math.tau * args.height / args.width
    camera_data.clip_end = 1000 * CUBE_SIZE
    camera = bpy.data.objects.new("Cylindrical camera", camera_data)
    scene.collection.objects.link(camera)
    camera.location = (*[x * CUBE_SIZE for x in CAMERA_XY],
                       VARIANTS[args.variant]["camera_height"] * CUBE_SIZE)
    camera.rotation_euler = (math.pi / 2, 0, math.radians(CAMERA_YAW_DEGREES))
    scene.camera = camera
    scene.render.resolution_x = args.width + 2 * guard
    scene.render.resolution_y = args.height
    scene.render.resolution_percentage = 100
    scene.render.fps = 24

    stats = dict(variant=args.variant, resolution=[args.width, args.height],
                 rendered_resolution=[args.width + 2 * guard, args.height],
                 samples=args.samples, threads=scene.render.threads, seed=SEED,
                 cubes=sum(histogram), columns=len(heights), height_range=[min(heights), max(heights)],
                 palette_counts=histogram, palette_srgb=PALETTE, palette_linear=palette_linear(),
                 cube_size=CUBE_SIZE, bevel=BEVEL_WIDTH, time_seconds=args.time,
                 build_seconds=time.perf_counter() - start,
                 camera_location=list(camera.location),
                 lighting_gain=gain,
                 camera_v_range=[camera_data.central_cylindrical_range_v_min, CYLINDER_V_TOP],
                 guard=guard, blender_version=bpy.app.version_string)
    return scene, stats


def linear_to_srgb(a):
    import numpy as np
    a = np.maximum(a, 0)
    return np.where(a <= .0031308, 12.92 * a, 1.055 * np.power(a, 1/2.4) - .055)


def postprocess(exr_path, png_path, stats_path):
    """Crop the real ray overscan, write sRGB PNGs and measure duplicate rays."""
    import numpy as np
    import OpenEXR
    from PIL import Image
    stats = json.loads(Path(stats_path).read_text())
    exr = OpenEXR.File(str(exr_path), separate_channels=True)
    channels = exr.channels()

    def rgb_pass(name):
        names = list(channels)
        key = next(n.rsplit(".", 1)[0] for n in names if f".{name}." in n)
        return np.stack([channels[f"{key}.{ch}"].pixels for ch in "RGB"], axis=-1)

    beauty = rgb_pass("Combined")
    albedo_exr = OpenEXR.File(str(Path(exr_path).with_name(Path(exr_path).stem + "-albedo.exr")), separate_channels=True)
    albedo_channels = albedo_exr.channels()
    key = next(n.rsplit(".", 1)[0] for n in albedo_channels if ".Combined." in n)
    albedo = np.stack([albedo_channels[f"{key}.{ch}"].pixels for ch in "RGB"], axis=-1)
    g, w = stats["guard"], stats["resolution"][0]
    clean = beauty[:, g:g+w, :]
    alb = albedo[:, g:g+w, :]

    def png(a, path):
        srgb = np.rint(np.clip(linear_to_srgb(a), 0, 1) * 255).astype(np.uint8)
        Image.fromarray(srgb).save(path)
        return srgb

    img = png(clean, png_path)
    albedo_path = Path(png_path).with_name(Path(png_path).stem + "-albedo.png")
    alb_img = png(alb, albedo_path)
    crop = min(LOOPCHECK_WIDTH, w // 2)
    Image.fromarray(np.concatenate((img[:, -crop:], img[:, :crop]), axis=1)).save(
        Path(png_path).with_name(Path(png_path).stem + "-loopcheck.png"))
    # These compare the SAME directions a full turn apart, not dissimilar edge pixels.
    linear_duplicate = beauty[:, :2*g] - beauty[:, w:w+2*g]
    albedo_duplicate = albedo[:, :2*g] - albedo[:, w:w+2*g]
    a = linear_to_srgb(beauty[:, :2*g]).clip(0, 1) * 255
    b = linear_to_srgb(beauty[:, w:w+2*g]).clip(0, 1) * 255
    s = img.astype(np.float64)
    step = np.abs(s - np.roll(s, 1, axis=1)).mean(axis=(0, 2))
    stats["loop"] = dict(
        same_ray_overlap_columns=2*g,
        same_ray_beauty_srgb_mae=float(np.abs(a-b).mean()),
        same_ray_beauty_linear_rmse=float(np.sqrt(np.mean(linear_duplicate**2))),
        same_ray_albedo_linear_rmse=float(np.sqrt(np.mean(albedo_duplicate**2))),
        seam_adjacent_column_mae=float(step[0]),
        all_adjacent_column_mae=float(step.mean()),
        seam_difference_percentile=float(100 * (step <= step[0]).mean()),
        ray_endpoint_error=float(np.linalg.norm(np.array([math.sin(-math.pi), math.cos(-math.pi)]) -
                                                np.array([math.sin(math.pi), math.cos(math.pi)]))))
    stats["sky_pixels"] = int((np.max(alb, axis=2) < 1e-6).sum())
    stats["files"] = dict(preview=str(Path(png_path).relative_to(ROOT)),
                          albedo=str(albedo_path.relative_to(ROOT)), raw=str(Path(exr_path).relative_to(ROOT)))
    Path(stats_path).write_text(json.dumps(stats, indent=2) + "\n")
    print(json.dumps(stats, indent=2), flush=True)


def main():
    if "--postprocess" in sys.argv:
        i = sys.argv.index("--postprocess")
        postprocess(*sys.argv[i+1:i+4])
        return
    import bpy
    import subprocess
    bpy.context.preferences.filepaths.temporary_directory = str(ROOT / WORK_DIR / "tmp")
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", choices=list(VARIANTS), default="A")
    parser.add_argument("--width", type=int, default=RESOLUTION[0])
    parser.add_argument("--height", type=int, default=RESOLUTION[1])
    parser.add_argument("--samples", type=int, default=SAMPLES)
    parser.add_argument("--guard", type=int, default=WRAP_GUARD_PIXELS)
    parser.add_argument("--time", type=float, default=TIME_SECONDS)
    parser.add_argument("--name", default=None)
    parser.add_argument("--save-blend", action="store_true")
    args = parser.parse_args(sys.argv[sys.argv.index("--")+1:] if "--" in sys.argv else [])
    name = args.name or "variant-" + args.variant.lower()
    for folder in (OUTPUT_DIR, WORK_DIR):
        (ROOT / folder).mkdir(exist_ok=True)
    exr_path = ROOT / WORK_DIR / f"{name}.exr"
    png_path = ROOT / OUTPUT_DIR / f"{name}.png"
    stats_path = ROOT / OUTPUT_DIR / f"{name}-stats.json"
    started = time.perf_counter()
    scene, stats = build_scene(args)
    scene.render.filepath = str(exr_path)
    if args.save_blend:
        bpy.ops.wm.save_as_mainfile(filepath=str(ROOT / WORK_DIR / f"{name}.blend"))
    t = time.perf_counter()
    print("EMAKI_BUILD", json.dumps(stats), flush=True)
    bpy.ops.render.render(write_still=True)
    stats["render_seconds"] = time.perf_counter() - t
    # A separate unlit diagnostic is robust across Blender's EXR pass-export
    # versions. It is NOT used as the wallpaper; it checks actual material IDs.
    diagnostic_start = time.perf_counter()
    for mat in bpy.data.materials:
        if mat.use_nodes and mat.name.endswith("linear sRGB"):
            nodes = mat.node_tree.nodes
            diffuse = next(n for n in nodes if n.type == "BSDF_DIFFUSE")
            out = next(n for n in nodes if n.type == "OUTPUT_MATERIAL")
            emission = nodes.new("ShaderNodeEmission")
            emission.inputs["Color"].default_value = diffuse.inputs["Color"].default_value
            mat.node_tree.links.new(emission.outputs[0], out.inputs["Surface"])
    scene.cycles.use_denoising = False
    scene.cycles.samples = min(16, args.samples)
    scene.cycles.use_adaptive_sampling = False
    scene.render.filepath = str(exr_path.with_name(exr_path.stem + "-albedo.exr"))
    bpy.ops.render.render(write_still=True)
    stats["diagnostic_seconds"] = time.perf_counter() - diagnostic_start
    stats["total_blender_seconds"] = time.perf_counter() - started
    import resource
    stats["peak_rss_mib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    stats_path.write_text(json.dumps(stats, indent=2) + "\n")
    subprocess.run(["python3", str(Path(__file__).resolve()), "--postprocess",
                    str(exr_path), str(png_path), str(stats_path)], check=True,
                   env={**os.environ, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": str(min(THREADS, 8))})
    print("EMAKI_DONE", name, flush=True)
    # Avoid this machine's headless audio-backend shutdown hang; all files closed.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
