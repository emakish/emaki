#!/bin/bash
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
# Encode every colour sequence of the final (renders/r2-final-all-colours/colours/<theme>) into a seamless
# HEVC 10-bit loop. Resumable: a colour counts as done once its .loop-proof.json exists.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
src=renders/r2-final-all-colours/colours
for dir in "$src"/*/; do
  theme=$(basename "$dir")
  video=renders/r2-final-$theme-3840x2160-hevc10.mp4
  [ -f "${video%.mp4}.loop-proof.json" ] && continue
  nice -n 19 ./assemble_final.sh --input "$dir" --output "$video" --threads 4
done
echo ALL_COLOURS_ASSEMBLED
