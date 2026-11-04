#!/bin/bash
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
# Banded A2 render + assembly. Usage: [THREADS=4] [LIGHT=neutral|original] render8k.sh WIDTH HEIGHT BANDS OUT NAME [assemble options]
# Gentle on the laptop: nice 19, 4 render threads unless THREADS says otherwise.
set -euo pipefail
cd "$(dirname "$0")"
width=$1 height=$2 bands=$3 out=$4 name=$5
shift 5
threads=${THREADS:-4} light=${LIGHT:-neutral}
export XDG_CACHE_HOME="$PWD/work/cache" TMPDIR="$PWD/work/tmp"
export ALSOFT_DRIVERS=null OMP_NUM_THREADS="$threads" OPENBLAS_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1
mkdir -p logs "$out" "$XDG_CACHE_HOME" "$TMPDIR"
SECONDS=0
nice -n 19 blender -b --factory-startup -noaudio -t "$threads" --python-exit-code 1 \
  --python render8k.py -- --width "$width" --height "$height" --bands "$bands" --threads "$threads" \
  --light "$light" --out "$out"
echo "render seconds: $SECONDS"
nice -n 19 python3 render8k.py --assemble "$out" --name "$name" "$@"
echo "total seconds: $SECONDS"
