# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reusable A2 lighting and depth-aware optical softness; no geometry changes.

Blender: apply_lighting(scene, 'neutral'|'warm') after scene setup.
Export: soften_distance(linear_rgb, radial_depth, pixels_per_radian, wrap).
Keep scene-linear EXRs until after this filter; convert to sRGB only once.
"""
import math

LOOK = {
    'fill_strength': 0.23,
    'key_location': (-500., -350., 700.),
    'key_target': (0., 0., 20.),
    'key_diameter': 320.,
    'key_power': 9000000.,
    'neutral': (1., 1., 1.),
    'warm': (1., .98, .96),
    'sharp_through_distance': 42.,
    'full_softness_distance': 75.,
    'max_sigma_at_5136': 2.8,
}


def apply_lighting(scene, variant='neutral'):
    import bpy
    from mathutils import Vector
    for obj in list(scene.objects):
        if obj.type == 'LIGHT':
            bpy.data.objects.remove(obj, do_unlink=True)
    world = scene.world.node_tree.nodes.get('Background')
    world.inputs[0].default_value = (1., 1., 1., 1.)
    world.inputs[1].default_value = LOOK['fill_strength']
    data = bpy.data.lights.new('A2 directional soft key / ' + variant, 'AREA')
    data.shape = 'DISK'
    data.size = LOOK['key_diameter']
    data.energy = LOOK['key_power']
    data.color = LOOK[variant]
    light = bpy.data.objects.new(data.name, data)
    scene.collection.objects.link(light)
    light.location = LOOK['key_location']
    light.rotation_euler = (Vector(LOOK['key_target']) - light.location).to_track_quat('-Z', 'Y').to_euler()
    # Direct light plus occluded neutral world: no coloured bounce tint.
    scene.cycles.max_bounces = 1
    scene.cycles.diffuse_bounces = 0
    scene.view_layers[0].use_pass_z = True
    scene.view_layers[0].use_pass_position = True
    return light


def soften_distance(rgb, depth, pixels_per_radian, wrap=True):
    """Depth-layered Gaussian lens approximation, not Blender aperture DOF.

    Sharp foreground is copied exactly. Each blurred level excludes nearer
    layers, preventing near silhouettes bleeding into the distant terraces.
    Angular blur scales with output resolution; horizontal padding wraps for
    a 360 camera, and reflects for a perspective camera. No hue or haze grade.
    """
    import cv2
    import numpy as np
    cv2.setNumThreads(1)
    lo, hi = LOOK['sharp_through_distance'], LOOK['full_softness_distance']
    t = np.clip((depth-lo)/(hi-lo), 0, 1)
    amount = t*t*(3-2*t)
    max_sigma = LOOK['max_sigma_at_5136'] * pixels_per_radian / (5136/math.tau)
    levels = np.linspace(0, 1, 9)
    result = np.zeros_like(rgb)
    pad = math.ceil(max_sigma*4)+2

    def blur(a, sigma):
        padded = np.pad(a, ((0,0),(pad,pad)) + (((0,0),) if a.ndim == 3 else ()),
                        mode='wrap' if wrap else 'reflect')
        return cv2.GaussianBlur(padded, (0,0), sigma, borderType=cv2.BORDER_REFLECT_101)[:,pad:-pad]

    for n, level in enumerate(levels):
        weight = np.maximum(0, 1 - np.abs(amount-level)*8)
        if n == 0:
            filtered = rgb
        else:
            eligible = (amount >= levels[max(0,n-1)]).astype(np.float32)
            denominator = blur(eligible, max_sigma*level)
            filtered = blur(rgb*eligible[:,:,None], max_sigma*level) / np.maximum(denominator[:,:,None], 1e-8)
        result += filtered * weight[:,:,None]
    result[depth <= lo] = rgb[depth <= lo]
    return result
