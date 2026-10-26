#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run actual capture/view methods with protocol peers, without a display or sockets.

Requires an explicit QUICKSHELL_SOURCE_DIR; never fetches or patches source.
"""
import os
import argparse
from pathlib import Path
import re
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def method(text, owner, name):
    pattern = rf"(?:void|bool|QSGNode\*) {owner}::{name}\("
    match = re.search(pattern, text)
    if not match:
        raise RuntimeError(f"missing production method {owner}::{name}")
    start = text.index("{", match.end())
    depth = 1
    end = start + 1
    while depth:
        if end >= len(text):
            raise RuntimeError(f"unterminated method {owner}::{name}")
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[match.start():end]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-compatible", action="store_true", help=(
        "run the outside-crop regression against old source; supply only absent damage "
        "APIs as unknown/no-op peers, retaining every existing production body"))
    args = parser.parse_args()
    source = os.environ.get("QUICKSHELL_SOURCE_DIR")
    if not source:
        raise RuntimeError("QUICKSHELL_SOURCE_DIR must name an existing source tree")
    source = Path(source).resolve() / "src/wayland/screencopy"
    capture = (source / "wlr_screencopy/wlr_screencopy.cpp").read_text()
    view = (source / "view.cpp").read_text()
    missing_api = {
        "captureStarted": "void WlrScreencopyManager::captureStarted() {}",
        "captureStopped": "void WlrScreencopyManager::captureStopped() {}",
        "damageExclusive": "bool WlrScreencopyManager::damageExclusive(quint64&) const { return false; }",
        "zwlr_screencopy_frame_v1_damage": (
            "void WlrScreencopyContext::zwlr_screencopy_frame_v1_damage("
            "uint32_t, uint32_t, uint32_t, uint32_t) {}"),
        "damageIntersects": "bool ScreencopyView::damageIntersects(const QRectF&) const { return true; }",
    }

    def selected_method(text, owner, name):
        try:
            return method(text, owner, name)
        except RuntimeError:
            if not args.baseline_compatible or name not in missing_api:
                raise
            print(f"BASELINE peer for absent {owner}::{name}: conservative/no-op", flush=True)
            return missing_api[name]

    methods = [selected_method(capture, "WlrScreencopyContext", name) for name in (
        "captureFrame", "zwlr_screencopy_frame_v1_damage",
        "zwlr_screencopy_frame_v1_buffer_done", "submitFrame",
        "zwlr_screencopy_frame_v1_flags", "zwlr_screencopy_frame_v1_ready")]
    methods += [selected_method(view, "ScreencopyView", name) for name in (
        "onFrameCaptured", "damageIntersects", "destroyContext", "updatePaintNode")]
    methods += [selected_method(capture, "WlrScreencopyManager", name) for name in (
        "captureStarted", "captureStopped", "damageExclusive")]
    manager_declaration = (source / "wlr_screencopy/wlr_screencopy.hpp").read_text()
    state = re.search(r"quint64 captureEpoch = 0;\s*uint captureCount = 0;", manager_declaration)
    if not args.baseline_compatible:
        if not state:
            raise RuntimeError("missing production manager capture state")
        constructor = capture.split("WlrScreencopyContext::WlrScreencopyContext(", 1)[1].split(
            "WlrScreencopyContext::~WlrScreencopyContext()", 1)[0]
        destructor = capture.split("WlrScreencopyContext::~WlrScreencopyContext()", 1)[1].split(
            "void WlrScreencopyContext::", 1)[0]
        if "this->manager->captureStarted();" not in constructor or (
                "this->manager->captureStopped();" not in destructor):
            raise RuntimeError("production context lifetime must register/unregister capture membership")
        if "this->destroy();" not in destructor or destructor.index("this->destroy();") > (
                destructor.index("this->manager->captureStopped();")):
            raise RuntimeError("production context must destroy outstanding frame before unregistering")
    scratch = ROOT / ".cache/damage/native"
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / "manager-state.inc").write_text(state.group() if state else "")
    (scratch / "extracted.inc").write_text("\n\n".join(methods) + "\n")
    manager_header = (source / "manager.hpp").read_text()
    declaration = re.search(r"struct ScreencopyDamage\s*\{[^}]*\};", manager_header)
    capture_header = (source / "wlr_screencopy/wlr_screencopy_p.hpp").read_text()
    accessor = re.search(
        r"ScreencopyDamage frameDamage\(\) const override\s*\{[^}]*\}", capture_header)
    if (not declaration or not accessor) and not args.baseline_compatible:
        raise RuntimeError("missing production damage value or accessor")
    (scratch / "damage-type.inc").write_text((declaration.group() if declaration else
        "struct ScreencopyDamage { QRegion region; bool known = false; };") + "\n")
    (scratch / "damage-accessor.inc").write_text(
        (accessor.group().replace(" override", "") if accessor else
         "ScreencopyDamage frameDamage() const { return {}; }") + "\n")
    flags = shlex.split(subprocess.check_output(
        ["pkg-config", "--cflags", "--libs", "Qt6Core", "Qt6Gui"], text=True))
    binary = scratch / "damage-capture"
    subprocess.run([os.environ.get("CXX", "c++"), "-std=c++20", "-Wall", "-Wextra",
                    "-Werror", "-I", str(scratch),
                    str(ROOT / "tests/fixtures/damage-capture.cpp"), "-o", str(binary),
                    *flags], check=True, timeout=90)
    subprocess.run([str(binary), *(["--outside-crop-only"] if args.baseline_compatible else [])],
                   check=True, timeout=20)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        print(f"FAIL damage capture: {error}", file=sys.stderr)
        sys.exit(1)
