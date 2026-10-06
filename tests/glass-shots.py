#!/usr/bin/env python3
"""GPU pictures of the liquid glass without a live session (opt-in: `make glass-shots`).

Offscreen Qt draws no ShaderEffect, so `make check-shell` sees the glass only as its flat
stand-in. This tool renders the real thing for review:
  1. the production shell runs offscreen (tests/fixtures/GlassShots.qml) and dumps, per
     state, every visible glass draw (item rect + uniforms), the layer under the glass and
     the layers on it;
  2. headless Firefox runs shaders/dock.frag and shaders/gauss.frag, translated by qsb to
     GLSL ES 300, over the same wallpaper texture helpers/wallpaper.py gives the backdrop
     (tests/fixtures/glass-harness.html), and saves one PNG per state.
Firefox is headless (no window) with a throwaway profile, but it needs the session's
WAYLAND_DISPLAY/DISPLAY for an EGL display: without one it has no WebGL at all.
Defaults: the shipped wallpaper (art/wallpaper/ring.png), the system's apps and icon theme (as a
person sees them).
Output: .cache/glass-shots/glass-<state>@<scale>x.png. Not part of check-shell.
EMAKI_GLASS_MOTION=1: frames of drops in flight instead (GlassShots.qml motionSteps), each
also rendered without dispersion, and the numbers of the colour fringe per sequence
(.cache/glass-shots/motion/, report.json).
"""
import base64
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / '.cache'
OUT = CACHE / 'glass-shots'
QSB = os.environ.get('QSB') or shutil.which('qsb') or '/usr/lib/qt6/bin/qsb'
WALLPAPER = Path(os.environ.get('EMAKI_GLASS_WALLPAPER') or ROOT / 'art/wallpaper/ring.png')
SCALE = os.environ.get('EMAKI_GLASS_SCALE', '1')
PORT = int(os.environ.get('EMAKI_GLASS_MARIONETTE_PORT', '28284'))
MOTION = os.environ.get('EMAKI_GLASS_MOTION') == '1'
if MOTION:
    OUT = OUT / 'motion'


class Firefox:
    """Just enough Marionette: navigate, run a script, screenshot an element."""

    def __init__(self, profile, width, height):
        (profile / 'user.js').write_text(''.join(f'user_pref("{k}", {json.dumps(v)});\n' for k, v in {
            'marionette.port': PORT, 'browser.shell.checkDefaultBrowser': False,
            'datareporting.policy.dataSubmissionEnabled': False, 'toolkit.telemetry.reportingpolicy.firstRun': False,
            'browser.startup.homepage_override.mstone': 'ignore', 'layout.css.devPixelsPerPx': SCALE,
            'webgl.disable-fail-if-major-performance-caveat': True}.items()))
        env = dict(os.environ, MOZ_HEADLESS='1', MOZ_HEADLESS_WIDTH=str(width), MOZ_HEADLESS_HEIGHT=str(height))
        self.log = open(profile / 'firefox.log', 'w')
        self.proc = subprocess.Popen(['firefox', '--headless', '--marionette', '--no-remote', '--profile', str(profile), 'about:blank'],
                                     env=env, stdout=self.log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 40
        while True:
            try:
                self.sock = socket.create_connection(('127.0.0.1', PORT), timeout=5)
                break
            except OSError:
                if time.monotonic() > deadline or self.proc.poll() is not None:
                    raise SystemExit('glass-shots: Firefox/Marionette did not start: ' + (profile / 'firefox.log').read_text()[-2000:])
                time.sleep(.3)
        self.sock.settimeout(180)
        self.buffer, self.id = b'', 0
        self.read()
        self.command('WebDriver:NewSession', {'capabilities': {}})
        self.command('WebDriver:SetTimeouts', {'script': 170000})
        self.command('WebDriver:SetWindowRect', {'width': width, 'height': height})

    def read(self):
        while b':' not in self.buffer:
            self.buffer += self.sock.recv(65536)
        length, rest = self.buffer.split(b':', 1)
        while len(rest) < int(length):
            rest += self.sock.recv(1 << 20)
        message, self.buffer = rest[:int(length)], rest[int(length):]
        return json.loads(message)

    def command(self, name, params=None):
        self.id += 1
        data = json.dumps([0, self.id, name, params or {}]).encode()
        self.sock.sendall(str(len(data)).encode() + b':' + data)
        while True:
            reply = self.read()
            if isinstance(reply, list) and reply[0] == 1 and reply[1] == self.id:
                if reply[2]:
                    raise RuntimeError(f'{name}: {reply[2].get("message", reply[2])}')
                return reply[3]

    def close(self):
        try:
            self.command('Marionette:Quit', {'flags': ['eForceQuit']})
        except Exception:
            pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.log.close()


def layer(out, grab):
    """Straight-alpha PNG of a grabbed layer: as grabbed if it kept its alpha, else from the
    black/white pair (premultiplied on black: B = C·a, on white: W = C·a + 1 − a)."""
    from PIL import Image
    import numpy
    black = Image.open(out / grab['file']).convert('RGB')
    white = Image.open(out / grab['white']).convert('RGB')
    if white.size != black.size:
        return out / grab['file']
    b = numpy.asarray(black, dtype=numpy.float64)
    w = numpy.asarray(white, dtype=numpy.float64)
    alpha = numpy.clip(255 - (w - b).mean(axis=2), 0, 255)
    colour = numpy.where(alpha[..., None] > 0, numpy.clip(b * 255 / numpy.maximum(alpha[..., None], 1), 0, 255), 0)
    image = Image.fromarray(numpy.dstack([colour, alpha]).round().astype(numpy.uint8), 'RGBA')
    target = out / ('rgba-' + grab['file'])
    image.save(target)
    return target


def motion_report(states):
    """Per sequence (sweep, melt, player…), inside the state's `roi`, in 0–255 levels (mean of
    RGB): `fringe` — the colour dispersion adds (|with − without dispersion|); `flicker` — how
    much that fringe changes from one frame to the next; `ghost` — all the lens does to what
    lies there (|with − without refraction and dispersion|): mirrored, split copies of text."""
    from PIL import Image
    import numpy
    sequences = {}
    for state in states:
        name = state['state']
        if state.get('roi') and '-' in name:
            sequences.setdefault(name.rsplit('-', 1)[0], []).append(state)
    report = {}
    for seq, frames in sequences.items():
        rows, last = [], None
        for state in frames:
            x, y, w, h = (round(v * float(SCALE)) for v in state['roi'])
            load = lambda suffix: numpy.asarray(Image.open(OUT / f'glass-{state["state"]}@{SCALE}x{suffix}.png').convert('RGB').crop((x, y, x + w, y + h)), dtype=numpy.float64)
            shot = load('')
            fringe = numpy.abs(shot - load('-nodisp')).mean(axis=2)
            ghost = numpy.abs(shot - load('-flat')).mean(axis=2)
            row = {'state': state['state'], 'fringe_mean': round(float(fringe.mean()), 3), 'fringe_p99': round(float(numpy.percentile(fringe, 99)), 1),
                   'ghost_mean': round(float(ghost.mean()), 3), 'ghost_p99': round(float(numpy.percentile(ghost, 99)), 1)}
            if last is not None:
                change = numpy.abs(fringe - last)
                row['flicker_mean'] = round(float(change.mean()), 3)
                row['flicker_p99'] = round(float(numpy.percentile(change, 99)), 1)
            rows.append(row)
            last = fringe
        steps = [r for r in rows if 'flicker_mean' in r]
        report[seq] = {'frames': rows, 'fringe_mean': round(sum(r['fringe_mean'] for r in rows) / len(rows), 3),
                       'fringe_p99_max': max(r['fringe_p99'] for r in rows),
                       'flicker_mean': round(sum(r['flicker_mean'] for r in steps) / max(1, len(steps)), 3),
                       'flicker_p99_max': max((r['flicker_p99'] for r in steps), default=0),
                       'ghost_mean': round(sum(r['ghost_mean'] for r in rows) / len(rows), 3),
                       'ghost_p99_max': max(r['ghost_p99'] for r in rows)}
        print(f'motion {seq}: ' + ', '.join(f'{k} {v}' for k, v in report[seq].items() if k != 'frames'))
    (OUT / f'report@{SCALE}x.json').write_text(json.dumps(report, indent=2) + '\n')


def data_url(path):
    kind = 'jpeg' if path.suffix.lower() in ('.jpg', '.jpeg') else 'png'
    return f'data:image/{kind};base64,' + base64.b64encode(path.read_bytes()).decode()


def main():
    CACHE.mkdir(exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    profile = Path(tempfile.mkdtemp(prefix='gs-', dir=CACHE))
    for part in ('runtime', 'cache', 'config', 'state', 'data', 'tmp', 'out', 'ff', 'glsl'):
        (profile / part).mkdir(mode=0o700)
    # 1. The production shaders in GLSL ES 300 (what WebGL2 compiles).
    sources = {}
    for name in ('dock', 'gauss'):
        qsb = profile / 'glsl' / f'{name}.qsb'
        subprocess.run([QSB, '--glsl', '300 es', '-o', str(qsb), str(ROOT / f'shell/shaders/{name}.frag')], check=True, timeout=60)
        subprocess.run([QSB, '-x', 'glsl,300es', '-o', str(profile / 'glsl' / f'{name}.frag'), str(qsb)], check=True, timeout=60)
        sources[name] = (profile / 'glsl' / f'{name}.frag').read_text()
    # 2. The shell offscreen: the wallpaper through wpaperd's config, the system's apps.
    shutil.copytree(ROOT / 'shell', profile / 'qml')
    shutil.copyfile(ROOT / 'tests/fixtures/GlassShots.qml', profile / 'qml/shell.qml')
    shutil.copyfile(ROOT / 'tests/fixtures/SystemMockup.qml', profile / 'qml/SystemMockup.qml')
    with (profile / 'qml/qmldir').open('a') as module:
        module.write('SystemMockup 1.0 SystemMockup.qml\n')
    (profile / 'config/wpaperd').mkdir()
    (profile / 'config/wpaperd/config.toml').write_text('[default]\npath = ' + json.dumps(str(WALLPAPER)) + '\nmode = "center"\n')
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QML_DISABLE_DISK_CACHE='1',
               QT_SCALE_FACTOR=SCALE, EMAKI_SHELL_TRAY='0', EMAKI_TEST_SYSTEM='0', EMAKI_BIN='', EMAKI_SETTINGS_PROFILE='',
               EMAKI_TEST_MPRIS='0', EMAKI_SHELL_NOTIFICATIONS='0', EMAKI_GLASS_SHOT_DIR=str(profile / 'out'),
               EMAKI_SHELL_GLASS_RENDERER='canvas', PYTHONDONTWRITEBYTECODE='1', QS_ICON_THEME=os.environ.get('QS_ICON_THEME', 'Adwaita'),
               XDG_RUNTIME_DIR=str(profile / 'runtime'), XDG_CACHE_HOME=str(profile / 'cache'),
               XDG_CONFIG_HOME=str(profile / 'config'), XDG_STATE_HOME=str(profile / 'state'),
               XDG_DATA_HOME=str(profile / 'data'), XDG_DATA_DIRS=os.environ.get('EMAKI_GLASS_DATA_DIRS', '/usr/local/share:/usr/share'),
               TMPDIR=str(profile / 'tmp'), NIRI_SOCKET='',
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(profile / 'no-session.sock'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(profile / 'no-system.sock'))
    for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'QT_SCREEN_SCALE_FACTORS', 'QT_LOGGING_RULES'):
        env.pop(name, None)
    # 3. The backdrop texture of the output, exactly as Surfaces' dockWallpaper gets it. The
    #    glass is always the light one (2026-09-27), so no palette is read from it.
    width, height = 1536, 960
    helper = subprocess.run(['python3', '-B', str(ROOT / 'shell/helpers/wallpaper.py'), str(width), str(height),
                             SCALE, 'Fixture', 'sharp'], env=env, text=True, capture_output=True, check=True, timeout=60)
    wallpaper = json.loads(helper.stdout)
    if wallpaper.get('state') != 'ready':
        raise SystemExit(f'glass-shots: wallpaper helper said {wallpaper.get("state")}')
    with (profile / 'qs.log').open('w') as log:
        subprocess.run(['qs', '-p', str(profile / 'qml'), '--no-color'], env=env, text=True,
                       stdout=log, stderr=subprocess.STDOUT, timeout=240)
    text = (profile / 'qs.log').read_text()
    if 'GLASS_COMPLETE' not in text:
        raise SystemExit('glass-shots: the shell did not finish:\n' + text[-4000:])
    runtime_errors = [line for line in text.splitlines()
                      if any(kind in line for kind in ('TypeError:', 'ReferenceError:', 'Binding loop detected', 'Unable to assign'))]
    if runtime_errors:
        raise SystemExit('glass-shots: QML runtime errors:\n' + '\n'.join(runtime_errors))
    states = [json.loads(line[line.index('GLASS {') + 6:]) for line in text.splitlines() if 'GLASS {' in line]
    for state in states:
        expected_wave = int(state['state'].startswith('lock-'))
        if any(draw['uniforms']['uWaveEnabled'] != expected_wave for draw in state['draws']):
            raise SystemExit('glass-shots: unexpected wave opt-in in ' + state['state'])
        for draw in state['draws']:
            # The browser owns cropped textures, unlike shared production Images.
            # Missing uniforms silently zero-initialize in GL and sample one texel.
            for name in ('uSharpRect', 'uSharpBounds', 'uWallSharpRect', 'uWallSharpBounds', 'uDynamicRect'):
                if draw['uniforms'].get(name) != [0, 0, 1, 1]:
                    raise SystemExit('glass-shots: expected identity ' + name + ' in ' + state['state'])
            if draw['uniforms'].get('uHasDynamicIcons') != 0:
                raise SystemExit('glass-shots: dynamic meter must be in the static grab')
    # Safe CI path: no browser, GPU or live display. Preserve every dump for inspection.
    (OUT / f'draws@{SCALE}x.json').write_text(json.dumps(states, indent=2) + '\n')
    if os.environ.get('EMAKI_GLASS_DUMP_ONLY') == '1':
        print(f'Offscreen dumps: {len(states)} states; profile {profile.relative_to(ROOT)}')
        return
    # 4. The GPU: one render and one screenshot per state.
    firefox = Firefox(profile / 'ff', int(width * float(SCALE)), int((height + 200) * float(SCALE)))
    try:
        firefox.command('WebDriver:Navigate', {'url': (ROOT / 'tests/fixtures/glass-harness.html').as_uri()})
        renderer = firefox.command('WebDriver:ExecuteScript', {'script': 'return window.glassHarness.init(arguments[0], arguments[1]);',
                                                                 'args': [sources['dock'], sources['gauss']]})['value']
        canvas = firefox.command('WebDriver:FindElement', {'using': 'css selector', 'value': '#glass'})['value']
        element = next(iter(canvas.values()))
        for state in states:
            images = {g['name']: data_url(layer(profile / 'out', g)) for g in state['grabs']}
            # Motion: the same frame once more without dispersion and once without any lens (the
            # references of motion_report).
            for suffix in ('', '-nodisp', '-flat') if MOTION else ('',):
                drawn = json.loads(json.dumps(state))
                for d in drawn['draws'] if suffix else ():
                    d['uniforms']['uDispersion'] = 0
                    if suffix == '-flat':
                        d['uniforms']['uRefraction'] = 0
                result = firefox.command('WebDriver:ExecuteAsyncScript', {
                    'script': 'const [s, i, w, done] = arguments; window.glassHarness.render(s, i, w).then(() => done(true), e => done(String(e && e.stack || e)));',
                    'args': [drawn, images, wallpaper['texture']]})['value']
                if result is not True:
                    raise SystemExit(f'glass-shots: render of {state["state"]} failed: {result}')
                shot = firefox.command('WebDriver:TakeScreenshot', {'id': element, 'full': False, 'hash': False})['value']
                target = OUT / f'glass-{state["state"]}@{SCALE}x{suffix}.png'
                target.write_bytes(base64.b64decode(shot))
            (OUT / f'glass-{state["state"]}@{SCALE}x.json').write_text(json.dumps({k: state[k] for k in ('state', 'launcher', 'left', 'clock', 'system')}, indent=2) + '\n')
            probe = firefox.command('WebDriver:ExecuteScript', {'script': 'return window.glassHarness.probe;'})['value']
            print(f'{target.relative_to(ROOT)}: {len(state["draws"])} glass draws; backdrop centre texels {probe}')
    finally:
        firefox.close()
    print(f'GPU: {renderer}; profile {profile.relative_to(ROOT)}')
    if MOTION:
        motion_report(states)


if __name__ == '__main__':
    sys.dont_write_bytecode = True
    main()
