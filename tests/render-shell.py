#!/usr/bin/env python3
"""Render an Item fixture in real Quickshell with isolated surfaceless Qt Quick RHI.

Builds a test-only QML plugin from installed Qt development files; no downloads or
installation. Mesa llvmpipe supplies actual shader rendering without a display,
Wayland compositor, GPU device, or live session. The fixture's containing QML
directory is copied into a private profile before running it.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / '.cache' / 'render-shell'


def build_plugin(output_root=None):
    """Return the import directory containing the compiled EmakiRender module."""
    output_root = Path(output_root or CACHE / 'plugin')
    module = output_root / 'EmakiRender'
    module.mkdir(parents=True, exist_ok=True)
    with (module / 'build.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _build_plugin(output_root, module)


def _build_plugin(output_root, module):
    source = ROOT / 'tests/render-shell.cpp'
    version = subprocess.check_output(['pkg-config', '--modversion', 'Qt6Core'], text=True).strip()
    fingerprint = hashlib.sha256(source.read_bytes() + version.encode()).hexdigest()
    stamp = module / 'source.sha256'
    library = module / 'librendershell.so'
    if not library.exists() or not stamp.exists() or stamp.read_text() != fingerprint:
        includes = []
        for name in ('Core', 'Gui', 'Quick', 'Qml', 'QmlModels'):
            include = Path(subprocess.check_output(['pkg-config', '--variable=includedir', 'Qt6' + name], text=True).strip())
            includes.extend(['-I' + str(include / ('Qt' + name) / version),
                             '-I' + str(include / ('Qt' + name) / version / ('Qt' + name))])
        # Qt's includedir is /usr/include/qt6, while the module directory is QtCore.
        moc_dir = subprocess.check_output(['pkg-config', '--variable=libexecdir', 'Qt6Core'], text=True).strip()
        moc = str(Path(moc_dir) / 'moc') if moc_dir else '/usr/lib/qt6/moc'
        subprocess.run([moc, '-DEMAKI_RENDER_PLUGIN', str(source), '-o', str(module / 'render-shell.moc')], check=True)
        flags = shlex.split(subprocess.check_output(['pkg-config', '--cflags', '--libs', 'Qt6Quick', 'Qt6OpenGL', 'Qt6Gui', 'egl', 'glesv2'], text=True))
        temporary_library = module / 'librendershell-next.so'
        subprocess.run([os.environ.get('CXX', 'c++'), '-std=c++17', '-shared', '-fPIC', '-DEMAKI_RENDER_PLUGIN', str(source),
                        '-o', str(temporary_library), '-I' + str(module), *includes, *flags], check=True)
        temporary_library.replace(library)
        stamp.write_text(fingerprint)
    (module / 'qmldir').write_text('module EmakiRender\nplugin rendershell\n')
    return output_root.resolve()


def render(qml, png=None, stats=None, properties=None, width=640, height=480, scale=1,
           warmup=500, milliseconds=1000, shader_dir=None, extra_env=None, screen_scale=1,
           ready_property=''):
    CACHE.mkdir(parents=True, exist_ok=True)
    imports = build_plugin()
    qml = Path(qml).resolve()
    options = dict(width=width, height=height, scale=scale, warmup=warmup, milliseconds=milliseconds)
    if ready_property:
        options['ready-property'] = ready_property
    for name, value in (('png', png), ('stats', stats)):
        if value:
            path = Path(value).resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            options[name] = str(path)
    with tempfile.TemporaryDirectory(prefix='profile-', dir=CACHE) as temporary:
        profile = Path(temporary)
        for name in ('runtime', 'cache', 'config', 'state', 'data', 'tmp'):
            (profile / name).mkdir(mode=0o700)
        target = profile / 'qml'
        shutil.copytree(qml.parent, target, ignore=shutil.ignore_patterns('*.so', '*.o', '*.moc'))
        fixture = target / qml.name
        entry = target / '__render_entry.qml'
        entry.write_text('''import QtQuick
import Quickshell
import EmakiRender
ShellRoot {
    Loader {
        id: fixture
        Component.onCompleted: setSource(%s, %s)
        onLoaded: begin.start()
        onStatusChanged: if (status === Loader.Error) failed.start()
    }
    RenderProbe { id: probe }
    Timer { id: failed; interval: 1; onTriggered: Qt.quit() }
    Timer {
        id: begin
        interval: 1
        onTriggered: {
            probe.render(fixture.item, %s)
            Qt.quit()
        }
    }
}
''' % (json.dumps(fixture.as_uri()), json.dumps(properties or {}), json.dumps(options)))
        env = dict(os.environ, XDG_RUNTIME_DIR=str(profile / 'runtime'),
                   XDG_CACHE_HOME=str(profile / 'cache'), XDG_CONFIG_HOME=str(profile / 'config'),
                   XDG_STATE_HOME=str(profile / 'state'), XDG_DATA_HOME=str(profile / 'data'),
                   XDG_DATA_DIRS=str(profile / 'data'), TMPDIR=str(profile / 'tmp'),
                   QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='rhi', QML_DISABLE_DISK_CACHE='1',
                   QML_IMPORT_PATH=str(imports), EGL_PLATFORM='surfaceless', LIBGL_ALWAYS_SOFTWARE='1',
                   GALLIUM_DRIVER='llvmpipe', LP_NUM_THREADS='1', QS_DISABLE_CRASH_HANDLER='1', NIRI_SOCKET='',
                   DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(profile / 'none-session-bus'),
                   DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(profile / 'none-system-bus'))
        if shader_dir:
            env['EMAKI_SHELL_SHADER_DIR'] = str(Path(shader_dir).resolve())
        if extra_env:
            env.update(extra_env)
        # --scale sets only the redirected target's DPR; --screen-scale sets the
        # independent offscreen QScreen DPR (e.g. 2 versus target DPR 1.25).
        # Production wallpaper/blur DPR remains a separate fixture property.
        for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'QT_QPA_PLATFORMTHEME',
                     'QT_SCALE_FACTOR', 'QT_SCREEN_SCALE_FACTORS', 'QT_AUTO_SCREEN_SCALE_FACTOR'):
            env.pop(name, None)
        env['QT_SCALE_FACTOR'] = str(screen_scale)
        command = ['qs', '-p', str(entry), '--no-color']
        try:
            result = subprocess.run(command, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    timeout=60 + (warmup + milliseconds) / 1000)
        except subprocess.TimeoutExpired as error:
            output = error.stdout or ''
            if isinstance(output, bytes):
                output = output.decode(errors='replace')
            raise RuntimeError('render-shell timed out:\n' + output) from error
        if result.returncode or 'RENDER_SHELL ' not in result.stdout:
            raise RuntimeError(f'render-shell failed ({result.returncode}):\n{result.stdout}')
        return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qml', required=True, type=Path)
    parser.add_argument('--png', '--output', dest='png', type=Path)
    parser.add_argument('--stats', type=Path)
    parser.add_argument('--properties', default='{}')
    parser.add_argument('--width', type=int, default=640)
    parser.add_argument('--height', type=int, default=480)
    parser.add_argument('--scale', type=float, default=1)
    parser.add_argument('--screen-scale', type=float, default=1)
    parser.add_argument('--warmup', type=int, default=500)
    parser.add_argument('--ready-property', default='', help='Boolean root property required before warmup (15 s limit)')
    parser.add_argument('--milliseconds', type=int, default=1000)
    parser.add_argument('--shader-dir', type=Path)
    args = parser.parse_args()
    properties = json.loads(args.properties)
    if not isinstance(properties, dict):
        parser.error('--properties needs a JSON object')
    print(render(**{**vars(args), 'properties': properties}), end='')


if __name__ == '__main__':
    main()
