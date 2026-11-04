<!-- Copyright (C) 2026 Artur Yakymenko -->
<!-- SPDX-License-Identifier: GPL-3.0-or-later -->

# Panoramic lane wallpaper

Patch 0005 adds `emaki-wallpaper { mode "strip"; ring "/path/to/strip.png"; }`.
`image` keeps the background-layer fallback and `ring` keeps the indexed living
wallpaper. IPC wallpaper status reports `strip`, then `loading`, `ready`, or
`failed`; a failed decode or upload reveals the background layer.

Strips accept opaque RGB/RGBA PNG, including 16-bit input converted to 8-bit.
Width is arbitrary up to 32768, height up to 8192, with a 128-megapixel bound.
The decoder has a 512 MiB allocation/file bound and runs on the existing bounded,
latest-job worker. Full-colour storage is four bytes per pixel; 13500×2160 is
111.2 MiB, 15000×2400 is 137.3 MiB, and 27000×4320 is 445.0 MiB before guards.
Decode/conversion and staging temporarily need additional CPU memory.

The worker splits strips into horizontal tiles of at most 4096 texels, including
a guard column on each edge. Guard columns sample the neighbouring tile or the
opposite end of the strip. The renderer maps physical lane motion into the strip,
scales to output height, and repeats its actual circumference. Retained elements
avoid damage on unchanged frames. No trains, animation timer, or video decode is
started by strip mode. Source art must itself join at its ends; guards prevent
sampling cracks but cannot remove an artistic discontinuity.

Every upload checks the actual GPU texture limit. The strip height must fit:
2160/2400-high assets require a limit at least that large; a 4320-high asset does
not fit a 4096-limit GPU and fails safely. There is no silent quality reduction
or one oversized panoramic texture. Select the smaller asset on such hardware.

Tests added to the patch cover RGB decode, opaque RGBA, rejected transparency and
truncated PNG, tiled pixels/guards for 13502-, 15002- and 27000-wide strips, and
software-GLES pixel equivalence across tile boundaries, negative origins, wrap,
and repeated-frame damage. Run `cargo test --release emaki_wallpaper_strip` in
the patched source and the same filter with `-p niri-config` for parsing.
