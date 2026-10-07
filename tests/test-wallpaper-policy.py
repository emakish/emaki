#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Execute the fork patch's power/geometry code without a compositor or GPU."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATCH = Path(os.environ.get("EMAKI_WALLPAPER_PATCH", ROOT / "packaging/niri-emaki/0005-emaki-wallpaper.patch"))


def source(path):
    patch = PATCH.read_text()
    match = re.search(r"^diff --git a/" + re.escape(path) + r" b/.*?\n(.*?)(?=^diff --git |\Z)", patch, re.M | re.S)
    if not match:
        raise AssertionError(f"Missing patched source: {path}")
    return "\n".join(line[1:] for line in match[1].splitlines()
                     if line.startswith(("+", " ")) and not line.startswith("+++"))


def body(text, signature):
    start = text.index(signature)
    start = text.index("{", start) + 1
    depth = 1
    for end in range(start, len(text)):
        depth += (text[end] == "{") - (text[end] == "}")
        if depth == 0:
            return text[start:end]
    raise AssertionError(signature)


def rust_test(code):
    cache = ROOT / ".cache/evidence"
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="wallpaper-policy-", dir=cache) as directory:
        directory = Path(directory)
        rust = directory / "policy.rs"
        rust.write_text(code)
        result = subprocess.run(["rustc", "--edition=2021", "--test", str(rust), "-o", str(directory / "policy")], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        result = subprocess.run([str(directory / "policy"), "--test-threads=1"], cwd=directory, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr


class WallpaperPolicy(unittest.TestCase):
    def test_power_sources_and_poll_deadline(self):
        rust_test(source("src/render_helpers/emaki_wallpaper/power.rs") + r'''
fn put(root: &Path, device: &str, name: &str, value: &str) {
    let path = root.join(device);
    std::fs::create_dir_all(&path).unwrap();
    std::fs::write(path.join(name), value).unwrap();
}
#[test]
fn supplies_and_resume() {
    let root = Path::new("supplies");
    assert!(!on_battery(root));
    std::fs::create_dir(root).unwrap();
    assert!(!on_battery(root));
    put(root, "mouse", "type", "Battery\n");
    put(root, "mouse", "scope", "Device\n");
    put(root, "mouse", "status", "Discharging\n");
    assert!(!on_battery(root));
    put(root, "BAT0", "type", "Battery\n");
    put(root, "BAT0", "present", "1\n");
    put(root, "BAT0", "status", "Discharging\n");
    assert!(on_battery(root));
    put(root, "AC", "type", "Mains\n");
    put(root, "AC", "online", "1\n");
    assert!(on_battery(root), "discharge overrides insufficient adapter");
    put(root, "BAT0", "status", "Full\n");
    assert!(!on_battery(root));
    let now = Instant::now();
    let mut power = PowerState::default();
    assert!(!power.refresh_from(now, root));
    assert_eq!(power.next_check, Some(now + Duration::from_secs(5)));
    put(root, "AC", "online", "0\n");
    assert!(!power.refresh_from(now + Duration::from_secs(4), root));
    assert!(power.refresh_from(now + Duration::from_secs(5), root));
    put(root, "AC", "online", "1\n");
    assert!(!power.refresh_from(now + Duration::from_secs(10), root));
    put(root, "AC", "online", "0\n");
    put(root, "BAT0", "status", "Charging\n");
    assert!(!on_battery(root), "charging proves external power without adapter node");
    put(root, "BAT0", "status", "Unknown\n");
    assert!(on_battery(root), "unknown present battery without AC pauses");
    put(root, "BAT0", "present", "0\n");
    assert!(!on_battery(root));
    put(root, "BAT1", "type", "Battery\n");
    put(root, "BAT1", "status", "Discharging\n");
    assert!(on_battery(root), "multiple and hotplugged batteries");
}
#[test]
fn eligibility_cancels_polling_and_preserves_rate_limit() {
    let root=Path::new("eligibility-supplies");
    put(root, "BAT0", "type", "Battery");
    put(root, "BAT0", "status", "Discharging");
    let now=Instant::now();
    let mut power=PowerState::default();
    assert_eq!(power.deadline(now,false),None);
    assert_eq!(power.deadline(now,true),Some(now));
    assert!(!power.on_battery(), "scheduling does no filesystem work");
    assert!(power.refresh_from(now,root));
    assert_eq!(power.deadline(now,true),Some(now+Duration::from_secs(5)));
    assert_eq!(power.deadline(now+Duration::from_secs(1),false),None);
    put(root, "BAT0", "status", "Charging");
    assert_eq!(power.deadline(now+Duration::from_secs(2),true),Some(now+Duration::from_secs(5)));
    assert!(power.on_battery(), "occlusion changes do not read sysfs");
    assert!(power.refresh_from(now+Duration::from_secs(4),root));
    assert_eq!(power.deadline(now+Duration::from_secs(60),false),None);
    assert_eq!(power.deadline(now+Duration::from_secs(60),true),Some(now+Duration::from_secs(5)));
    assert!(!power.refresh_from(now+Duration::from_secs(60),root));
}
''')

    def test_original_fractional_scale_and_full_height(self):
        render = source("src/render_helpers/emaki_wallpaper/mod.rs")
        layout = source("src/layout/scrolling.rs")
        art_scale = body(render, "pub fn art_scale(").replace("self.size.h", "height").replace("self.scale", "scale")
        camera_scale = body(layout, "pub fn wallpaper_art_scale(").replace("self.view_size.h", "height").replace("self.scale", "scale").replace("super::workspace::WALLPAPER_RING_HEIGHT", "1200.")
        offset = body(render, "fn y_offset(").replace("self.size.h", "height").replace("self.scale", "scale").replace("self.art_scale()", "art_scale(height, scale)")
        ring = body(render, "fn update_ring(")
        self.assertEqual(body(render, "pub fn art_scale(").strip(),
                         "(self.size.h * self.scale / RING_HEIGHT as f64).max(1.)")
        self.assertEqual(body(layout, "pub fn wallpaper_art_scale(").strip(),
                         "(self.view_size.h * self.scale / super::workspace::WALLPAPER_RING_HEIGHT).max(1.)")
        # Both atlas and tiled rendering retain the original full-height mapping.
        spans = re.findall(r"view\.y_offset\(\),\s*(?:view\.width\(\)|source_width),\s*([^,]+),", ring)
        self.assertEqual(len(spans), 2)
        for span in spans:
            self.assertEqual(span, "RING_HEIGHT as f64 - view.y_offset()")
            rust_span = span.replace("view.size.h", "height").replace("view.scale", "scale").replace("view.art_scale()", "art_scale(height, scale)").replace("view.y_offset()", "offset(height, scale)")
            rust_test(f'''const RING_HEIGHT: usize = 1200;
fn art_scale(height:f64, scale:f64)->f64 {{{art_scale}}}
fn camera_scale(height:f64, scale:f64)->f64 {{{camera_scale}}}
fn offset(height:f64, scale:f64)->f64 {{{offset}}}
fn span(height:f64, scale:f64)->f64 {{{rust_span}}}
#[test]
fn pixels() {{
    for (physical, factor, padding) in [(1200.,1.,0.),(1600.,4./3.,0.),(1800.,1.5,0.),(2160.,1.8,0.),(2400.,2.,0.),(3000.,2.5,0.)] {{
        for scale in [1.,1.25,1.5,2.] {{
            let height=physical/scale;
            assert_eq!(art_scale(height,scale),factor);
            assert_eq!(camera_scale(height,scale),factor);
            assert_eq!(physical-1200.*factor,padding);
            for y in 0..physical as usize {{
                let sampled=offset(height,scale)+(y as f64+0.5)/physical*span(height,scale);
                assert!((sampled-(y as f64+0.5)/factor).abs()<1e-9);
            }}
        }}
    }}
    assert_eq!(offset(1080.,1.),120.);
}}
''')
        shader = source("src/render_helpers/shaders/emaki_wallpaper.frag")
        self.assertIn("clamp(p.y, 0.0, 1199.0)", shader)
        self.assertIn("p = clamp(p, vec2(0.0), tex_size - 1.0)", shader)
        self.assertIn("lookup(floor(p))", shader)
        self.assertEqual(ring.count(".with_nearest_sampling()"), 2)

    def test_poll_eligibility_transitions(self):
        niri = source("src/niri.rs")
        eligible = body(niri, "fn wallpaper_power_eligible(")
        rust_test('''use std::cell::RefCell;
struct Output(bool);
impl Output { fn name(&self)->bool { self.0 } }
struct Layout(Vec<Output>);
impl Layout { fn outputs(&self)->impl Iterator<Item=&Output> { self.0.iter() } }
struct Wallpaper;
impl Wallpaper { fn trains_visible(&self,output:&bool)->bool { *output } }
struct Screenshot(bool);
impl Screenshot { fn is_open(&self)->bool { self.0 } }
struct Niri { locked:bool, screenshot_ui:Screenshot, monitors_active:bool,
layout:Layout, emaki_wallpaper:RefCell<Wallpaper> }
impl Niri { fn is_locked(&self)->bool { self.locked }
fn eligible(&self)->bool {''' + eligible + '''} }
#[test] fn transition_gates() {
let mut n=Niri { locked:false,screenshot_ui:Screenshot(false),monitors_active:true,
layout:Layout(vec![Output(true)]),emaki_wallpaper:RefCell::new(Wallpaper) };
assert!(n.eligible());
n.locked=true;assert!(!n.eligible());
n.locked=false;n.monitors_active=false;assert!(!n.eligible());
n.monitors_active=true;n.screenshot_ui.0=true;assert!(!n.eligible());
n.screenshot_ui.0=false;n.layout.0[0].0=false;assert!(!n.eligible());
n.layout.0.push(Output(true));assert!(n.eligible());
n.layout.0.clear();assert!(!n.eligible());
}''')

    def test_pause_clock_and_resume_timer(self):
        render = source("src/render_helpers/emaki_wallpaper/mod.rs")
        niri = source("src/niri.rs")
        refresh = body(niri, "fn refresh_emaki_wallpaper(")
        timer = body(niri, "fn refresh_wallpaper_power_timer(")
        self.assertNotIn(".power.refresh(", refresh)
        self.assertNotIn("queue_redraw", timer)
        self.assertIn("if state.niri.wallpaper_power_eligible()", timer)
        self.assertRegex(timer, r"(?s)insert_source.*?\.power\s*\.refresh\(Instant::now\(\)\)")
        self.assertNotIn("chain(power_check)", refresh)
        self.assertIn("self.refresh_wallpaper_power_timer()", refresh)
        self.assertIn("self.refresh_wallpaper_power_timer()", body(niri, "pub(crate) fn update_emaki_wallpaper_occlusion"))
        self.assertIn("self.event_loop.remove(token)", timer)
        clock = body(render, "pub fn clock(")
        rust_test('''use std::time::Duration;
#[derive(Default)] struct Output { animated: bool, deadline: Option<f64> }
#[derive(Default)] struct Power(bool);
impl Power { fn on_battery(&self)->bool { self.0 } }
#[derive(Default)] struct Clock { last_clock: Option<Duration>, now:f64, paused:bool,
trains_paused:bool, power:Power,
sample_now:Option<f64>, outputs:std::collections::HashMap<String,Output> }
impl Clock { fn clock(&mut self,time:Duration,paused:bool) {''' + clock + '''} }
#[test] fn pause_and_resume() {
let mut c=Clock::default();
c.outputs.insert("screen".into(),Output{animated:true,deadline:Some(20.)});
c.clock(Duration::from_secs(0),false);
c.clock(Duration::from_secs(1),false);
c.clock(Duration::from_secs(2),true);
assert!(!c.outputs["screen"].animated);
assert_eq!(c.outputs["screen"].deadline,None);
c.clock(Duration::from_secs(100),true);
c.clock(Duration::from_secs(101),false);
assert_eq!(c.now,1.);
c.clock(Duration::from_secs(102),false);
assert_eq!(c.now,2.);
c.power.0=true;
c.clock(Duration::from_secs(103),false);
assert!(!c.paused, "battery keeps the wallpaper visible");
assert!(c.trains_paused);
c.clock(Duration::from_secs(200),false);
assert_eq!(c.now,2.);
c.power.0=false;
c.clock(Duration::from_secs(201),false);
assert!(!c.trains_paused);
assert_eq!(c.now,2.);
c.clock(Duration::from_secs(202),false);
assert_eq!(c.now,3.);
}''')


if __name__ == "__main__":
    unittest.main()
