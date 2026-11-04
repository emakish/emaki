<!-- Copyright (C) 2026 Artur Yakymenko -->
<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Cube artwork sources

These procedural scenes are the editable source for the Emaki cube panorama,
wallpaper fallback and overview animation. The sources and resulting artwork are
licensed under GPL-3.0-or-later; see the repository `LICENSE`.

Use Blender **5.2.3 LTS**, Python 3 with NumPy, OpenEXR, OpenCV and Pillow, and
FFmpeg with libx264. Run from the repository root. Output, caches and temporary
files stay under this directory. Rendering needs substantial memory and disk space.

```sh
(cd art/cubes && LIGHT=original THREADS=12 nice -n 19 bash render8k.sh \
  27000 4320 20 work/strip-bands variant-a-fixed-8k --sharp)
nice -n 19 bash art/cubes/render-final-video.sh --look probe --device CPU \
  --threads 4 --width 5760 --height 2400 --samples 32 --fps 24 \
  --total-frames 360 --out videos/final-fixed --assemble
nice -n 19 python3 scripts/build-wallpaper-fallback \
  --source art/cubes/renders/variant-a-fixed-8k-13500x2160.png \
  --output art/wallpaper/fallback.png
```

The strip uses variant A, seed 31102026, 96 samples, twenty overlapping bands,
original overhead lighting and no distance softening. Assembly also produces
13500×2160 and 15000×2400 strips. The fallback is the unchanged centre rectangle
(4830, 0)–(8670, 2160) of the 13500×2160 strip. The video command produces
`videos/final-fixed.mp4` (15 seconds, H.264 QP12, all-intra) and a lossless FFV1
master, and checks the independent endpoint and decoded loop. Package staging
requires the separately encoded `videos/final-fixed-ship.mp4`; it never selects
the `final-fixed.mp4` master. The shipping encode is 5760×2400 HEVC Main 10 with the
ribbon videos' settings:

```sh
nice -n 19 ffmpeg -nostdin -i videos/final-fixed.mp4 -vf scale=5760:2400 -an \
  -c:v libx265 -preset slow -crf 16 -pix_fmt yuv420p10le \
  -x265-params pools=6:frame-threads=1:open-gop=0 -tag:v hvc1 -movflags +faststart \
  videos/final-fixed-ship.mp4
```

The shipped `videos/final-fixed-ship.mp4` has SHA-256
`0901b276f2e83c1e7679f7235294bca62ff17bbc0e7c1d490a861fa363e683e5` (23.4 MB); the
encoder version can change the bytes of a fresh encode.

To create package media, use `scripts/prepare-theme-assets --theme emaki --height
2160 --cubes art/cubes --ribbons art/ribbons/renders/strips8k --videos
art/ribbons/videos --output /tmp/emaki-cube-stage`. Its unchanged-copy and FFmpeg
commands produce `emaki/wallpaper.png`, `overview.mp4`, `overview.jpg` and
`thumbnail.jpg`; inspect `themes/catalog.json` for the current installed names.

Expected SHA-256 of the inspected source outputs:

| File | SHA-256 |
| --- | --- |
| `renders/variant-a-fixed-8k-27000x4320.png` | `ee9246b9b050301f7097e04ddf149ebb1f3b774f56ca8a7f136414273ff6ed0e` |
| `renders/variant-a-fixed-8k-13500x2160.png` | `9b758e0e3a46cc8060fedf0800b7e9a64545a2a80c1ae15a37f9da24557120b7` |
| `renders/variant-a-fixed-8k-15000x2400.png` | `76ff39d59ed5596cf763613b1cabc7275cdde87896f09fbe514d1e4cb5390b94` |
| `art/wallpaper/fallback.png` (repository root) | `4b60268e1bada9cc4902638ac18e249b90fc31d2aa2628b3c9873b9b8a509c56` |

The panorama metadata records the Blender version and settings above.
The final-video manifest records the stated settings for all six scene/export
dependencies; the shipping encode and its SHA-256 are given above. These hashes identify
existing outputs, not a claim of byte-identical results across renderer, codec
or Python dependency versions.
