#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Execute the shader's lens arithmetic across an appearing drop's centre."""

import argparse
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parent.parent


def function(source, name):
    match = re.search(r"float " + name + r"\([^)]*\)\s*\{[^}]*\}", source)
    return match.group(0) if match else ""


def check(source_root):
    source = (source_root / "shell/shaders/dock.frag").read_text()
    # Compile the actual edge expression and slope function. The old shader has
    # its edge inline, so it is exercised too instead of failing a source check.
    edge = re.search(r"float liquidDepth=max\(-liquid.z,0.0\),edge=([^;]+);", source)
    if not edge:
        raise AssertionError("Cannot locate the mixed-material optical edge")
    mixed = source.split("vec4 mixedDock(vec2 p) {", 1)[1]
    scale = re.search(r"float opticalScale=([^;]+);", mixed)
    thickness = re.search(r"float slope=dockSlope\(liquidDepth,edge,([^;]+)\);", mixed)
    bend = re.search(r"float bend=([^;]+);", mixed)
    dispersion = re.search(r"float dispersion=([^;]+);", mixed)
    refraction = re.search(r"vec2 bent=p-normal\*([^;]+);", mixed)
    blur = re.search(r"vec3 clear=mix\(sharp,soft,(.+)\*uBlur\);", mixed)
    assert thickness and bend and dispersion and refraction and blur
    program = """
#include <algorithm>
#include <cmath>
#include <iomanip>
#include <iostream>
#include <stdexcept>
struct vec4 { double x,y,z,w; };
double min(double a,double b) { return std::min(a,b); }
double max(double a,double b) { return std::max(a,b); }
double clamp(double x,double a,double b) { return min(max(x,a),b); }
double smoothstep(double a,double b,double x) {
    if (!(a < b)) throw std::runtime_error("Undefined optical blur interval");
    double t=clamp((x-a)/(b-a),0,1);
    return t*t*(3-2*t);
}
double uEdge=18, uThickness=9, uRefraction=14, uDispersion=16, uWaveEnabled=0;
// The light theme: a dark theme's domes drop dispersion entirely, the light one keeps it.
double uThemeDark=0, uDropDome=0;
vec4 uBulge={18,0,1,0};
""" + "\n".join(function(source, name) for name in ("dockSlope", "dropErosion", "dropOpticalScale", "dropOpticalEdge")) + r"""
int main() {
    double extent,ndLife,wave,mode;
    while(std::cin>>extent>>ndLife>>wave>>mode) {
        uWaveEnabled=wave;uBulge.y=mode;
        vec4 nd={0,0,max(67,extent),extent};
        double opticalScale=SCALE;
        double edge=EXPRESSION;
        double erosion=mode<.5&&wave<.5?(1-ndLife)*26:0;
        double half=extent*.5-erosion;
        double liquidDepth=max(half*.5,0),blur=BLUR;
        double thickness=THICKNESS;
        double slope=dockSlope(half-.000001,edge,thickness),life=ndLife;
        double bend=BEND;
        double dispersion=DISPERSION;
        double reach=REFRACTION;
        double jump=2*(reach+dispersion);
        slope=8;bend=BEND;dispersion=DISPERSION;reach=REFRACTION;
        std::cout<<std::setprecision(12)<<edge<<' '<<jump<<' '
                 <<opticalScale<<' '<<thickness<<' '<<reach<<' '<<dispersion<<'\n';
    }
}
"""
    expressions = {
        "EXPRESSION": edge.group(1), "SCALE": scale.group(1) if scale else "1",
        "THICKNESS": thickness.group(1), "BEND": bend.group(1),
        "DISPERSION": dispersion.group(1), "REFRACTION": refraction.group(1),
        "BLUR": blur.group(1),
    }
    for name, expression in expressions.items():
        program = program.replace(name, expression)
    rows = []
    for extent in (26, 32, 50, 66, 84):
        for index in range(101):
            life = index / 100
            rows.append((extent, life, 0, 0))
        for life in (.4, .6, 1):
            rows.extend(((extent, life, 1, 0), (extent, life, 0, 1)))
    temporary_root = Path(os.environ.get("TMPDIR", tempfile.gettempdir()))
    temporary_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dispersion-", dir=temporary_root) as directory:
        work = Path(directory)
        (work / "probe.cpp").write_text(program)
        subprocess.run(["c++", "-std=c++17", str(work / "probe.cpp"), "-o", str(work / "probe")], check=True)
        result = subprocess.run([str(work / "probe")], input="".join(" ".join(map(str, row)) + "\n" for row in rows), text=True, capture_output=True, check=True)
    maximum = 0
    battery_jump = None
    failures = []
    for row, line in zip(rows, result.stdout.splitlines(), strict=True):
        extent, life, wave, mode = row
        edge, jump, optical_scale, thickness, reach, dispersion = map(float, line.split())
        maximum = max(maximum, jump)
        if row == (32, .5, 0, 0):
            battery_jump = jump
        if jump > .01:
            failures.append(f"height={extent}, life={life}: centre sample jumps {jump:.3f}px")
        erosion = 0 if wave or mode else (1 - life) * 26
        if not 0 <= optical_scale <= 1:
            failures.append(f"height={extent}, life={life}: optical scale outside [0, 1]")
        if edge > max(extent / 2 - erosion, 0) + 1e-6:
            failures.append(f"height={extent}, life={life}: optical edge exceeds eroded half-height")
        if life == 1 or wave or mode:
            expected = min(18, 23.5, max(extent * .34, 4))
            assert math.isclose(edge, expected, rel_tol=1e-6), (row, edge, expected)
    assert battery_jump is not None
    assert not failures, f"Battery height=32, life=0.5: centre sample jumps {battery_jump:.3f}px\n" + "\n".join(failures[:8])
    print(f"PASS: {len(rows)} lens states; maximum centre sample jump {maximum:.6f}px; settled and wave edges unchanged")
    print(f"Battery height=32, life=0.5: centre sample jump {battery_jump:.6f}px")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    check(parser.parse_args().source_root)
