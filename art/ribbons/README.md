<!-- Copyright (C) 2026 Artur Yakymenko -->
<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Ribbon artwork sources

These scripts build the editable scenes, materials, periodic motion and final
colour passes for 18 ribbon themes. They use Blender 5.2.3 LTS (build `e13b728c6880`), Cycles CPU, its bundled
OpenColorIO configuration, Python 3 with NumPy, OpenCV, Pillow, OpenImageIO and
PyOpenColorIO, and FFmpeg with libx265. The recorded video encoder identified
itself as Lavf/Lavc 63.1.102. No texture images or prebuilt scene are required.
The 18 JSON files under `renders/r2-palettes/` are source colour data.

Run from this directory. Set `EMAKI_BLENDER_OCIO` to Blender 5.2.3 LTS's
`datafiles/colormanagement/config.ocio`; locate that installation's data root with
`nice -n 19 blender -b --factory-startup --python-expr 'import bpy; print(bpy.utils.resource_path("SYSTEM"))'`.
Programs are resolved through `PATH`. Outputs and temporary files stay below
`renders/`; use fresh output directories after modifying sources. Do not enable
`--reuse-half-loop`: that optional shortcut requires a separate geometry proof.

```sh
export PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 MKL_NUM_THREADS=4
# Export EMAKI_BLENDER_OCIO to the configuration file located above first.
nice -n 19 bash render_final.sh --device CPU --threads 4 --profile final \
  --width 3840 --height 2160 --fps 24 --frames 0:233 \
  --colour-palettes renders/r2-palettes --output renders/r2-final-all-colours
nice -n 19 bash assemble_all_colours.sh
nice -n 19 python3 strips8k_runner.py all
nice -n 19 sha256sum --check SHA256SUMS
```

The first two commands produce `renders/r2-final-NN-theme-3840x2160-hevc10.mp4`:
234 frames, 24 fps, 9.75 seconds, HEVC 10-bit, slow preset, CRF 16. The strip
command produces variant A `renders/s8k-NN-theme-27024x4320.png` and linear-light
area downsizes `-2160.png` (13512×2160) and `-2400.png` (15014×2400). It uses 24
bands, 32 maximum samples and the calibrated closed periodic scene. The base
`ribbons.py` is a dependency; final videos use `ribbons_round2.py`, and strips
use `strips8k_scene.py`. These builders can save editable Blender scenes with
`--save-blend` when invoked through Blender; ordinary reproduction rebuilds the
scene directly from source.

`SHA256SUMS` records the existing 18 final videos, 54 variant A source strips
and 18 converted RGB8 shipping strips, using paths relative to this directory. These are expected output bytes, not a
claim that a fresh render was verified. Rendering and encoder/library versions
can affect byte identity. Scene geometry and colour calculations are defined in
the Python sources above.

## Variant A shipping strips

Use `prepare_shipping.py` to convert the eighteen `s8k-NN-theme-2160.png`
variant A sources. Variant B and loop-check images are never inputs. Run from
this directory with new output and proof directories:

```sh
env -u NIRI_SOCKET -u WAYLAND_DISPLAY -u DISPLAY \
  OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  nice -n 19 python3 prepare_shipping.py --renders renders \
  --output renders/ship-a-2160 --proofs renders/ship-a-2160-proofs \
  --previews ../../themes/previews
```

The converter processes one file at a time, with one decoding/filter/encoding
thread. FFmpeg converts packed RGB16 to planar RGB16, applies
`zscale=dither=error_diffusion:rangein=full:range=full`, then emits planar RGB8
and packed RGB24 PNG with compression level 9. The existing encoded RGB values
are retained: no resizing, colour-space transform, palette reduction or extra
blur. Source files are read-only and checked by SHA-256 before and after each
conversion. The converter refuses existing destination directories and enforces
48 million pixels and 32 MiB per strip.

The shipping filenames are `ship-a-2160/NN-theme-2160.png`, directly accepted
by `scripts/prepare-theme-assets --ribbons`. `SHA256SUMS` records both original
source and shipping hashes; the delivery folder also contains its own hash list.
The separately recorded proof directory contains full-resolution lower-half
fade crops (x=38–62%) and last/first 600-column wrap crops, before in RGB16 and
after in RGB8. `measurements.json` records dimensions, bytes, hashes, FFmpeg
version, quantization errors and 32×32 block-average errors against the RGB16
source values and an undithered rounding control. The seam metric is the original
`strips8k_runner.py` column mean absolute difference in 8-bit code values,
including the same 200-neighbour-column baseline and ordinary-column percentile.
These metrics support visual crop inspection; they are not a claim about every
display or viewing condition.

Catalog previews are generated from these exact shipping strips using the
staging script's FFmpeg central 16:9 crop and 320×180 scale. A JPEG comment adds
the repository copyright and licence without re-encoding the thumbnail.

For the later release stage, run `scripts/prepare-theme-assets` from the
repository root with `art/ribbons/renders/ship-a-2160` as `--ribbons`, the final
ribbon video directory as `--videos`, `art/cubes` as `--cubes` and a new
`--output` directory. The stage copies media unchanged and generates overview
stills and thumbnails. Cube video selection requires
`art/cubes/videos/final-fixed-ship.mp4`; `final-fixed.mp4` is a master and is
never selected. A full release stage and final video verification are separate
from the strip conversion.
