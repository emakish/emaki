#!/usr/bin/env python3
"""Offline fastfetch contract: reproducible assets, precedence and synthetic TTYs."""
import base64
import fcntl
import json
import os
from pathlib import Path
import pty
import re
import select
import shutil
import struct
import subprocess
import sys
import tempfile
import termios
import time
import zlib

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
FASTFETCH = shutil.which('fastfetch')
assert FASTFETCH, 'fastfetch is required for make check'
subprocess.run([sys.executable, str(ROOT / 'scripts/build-fetch'), '--check'], check=True)
ANSI = re.compile(rb'\x1b\[[0-9;?]*[A-Za-z]')
KITTY_IMAGE = re.compile(rb'\x1b_G[^\x1b]*\x1b\\')
OS_LINE = re.compile(rb'(?m)OS: Emaki( [0-9]+\.[0-9]+\.[0-9]+)?[ \t]*$')
KEYS = ['OS', 'Host', 'Kernel', 'Uptime', 'Packages', 'Shell', 'WM', 'Terminal',
        'CPU', 'GPU', 'Memory', 'Disk', 'Battery', 'Locale']
config = json.loads((ROOT / 'fetch/details.jsonc').read_text())
assert [m['key'] for m in config['modules']] == KEYS
assert all(m['type'].lower() not in ('publicip', 'localip', 'netio', 'weather', 'wifi')
           for m in config['modules'])
# Emaki's own release file, never the host's os-release (Arch on a builder).
os_module = config['modules'][0]
assert os_module['type'] == 'command' and '/usr/lib/emaki-release' in os_module['text'], os_module
with tempfile.TemporaryDirectory() as release_dir:
    release = Path(release_dir) / 'emaki-release'
    release.write_text('VERSION=0.1.1\nCHANNEL=stable\n')
    for path, expected in ((release, b'Emaki 0.1.1\n'), (Path(release_dir) / 'missing', b'Emaki\n')):
        line = subprocess.run(['sh', '-c', os_module['text'].replace('/usr/lib/emaki-release', str(path))],
                              capture_output=True, check=True).stdout
        assert line == expected, (path, line)
assert config['logo']['type'] == 'none', 'details alone must never select a distro logo'


def assert_os_line(output):
    """The OS line reads Emaki (and the version on Emaki) once escapes are removed; no Arch name."""
    text = ANSI.sub(b'', KITTY_IMAGE.sub(b'', output)).replace(b'\r', b'')
    assert OS_LINE.search(text), text[-2000:]
    assert b'Arch Linux' not in text, text[-2000:]


def tty_run(args, env):
    master, slave = pty.openpty()
    # A private terminal with 10 × 20 physical pixel cells; no live display/socket.
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 40, 140, 1400, 800))

    def session():
        os.setsid()
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)

    proc = subprocess.Popen([FASTFETCH, *args], stdin=slave, stdout=slave, stderr=slave,
                            env=env, preexec_fn=session)
    os.close(slave)
    output = bytearray()
    deadline = time.monotonic() + 15
    try:
        while time.monotonic() < deadline:
            if not select.select([master], [], [], .1)[0]:
                if proc.poll() is not None:
                    break
                continue
            try:
                data = os.read(master, 65536)
            except OSError:
                break
            output.extend(data)
            # Fastfetch may query cell/window sizes; answer only this private PTY.
            for query, reply in [(b'\x1b[6n', b'\x1b[1;1R'),
                                 (b'\x1b[16t', b'\x1b[6;20;10t'),
                                 (b'\x1b[14t', b'\x1b[4;800;1400t')]:
                if query in data:
                    os.write(master, reply)
        assert proc.wait(timeout=2) == 0, output[-1000:]
        return bytes(output)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        os.close(master)


with tempfile.TemporaryDirectory(prefix='emaki-fetch-') as temporary:
    temp = Path(temporary)
    user = temp / 'user'
    system = temp / 'system'
    (system / 'fastfetch').mkdir(parents=True)
    (user / 'fastfetch').mkdir(parents=True)
    shutil.copyfile(ROOT / 'fetch/config.jsonc', system / 'fastfetch/config.jsonc')
    env = {'PATH': os.environ['PATH'], 'HOME': str(temp), 'XDG_CONFIG_HOME': str(user),
           'XDG_CONFIG_DIRS': str(system), 'XDG_CACHE_HOME': str(temp / 'cache'),
           'XDG_DATA_HOME': str(temp / 'data'), 'XDG_RUNTIME_DIR': str(temp / 'runtime'),
           'LANG': 'C.UTF-8', 'TERM': 'dumb', 'SHELL': '/bin/bash',
           'EMAKI_FETCH_DIR': str(ROOT / 'fetch'), 'XDG_CURRENT_DESKTOP': 'niri'}

    def run(args):
        result = subprocess.run([FASTFETCH, *args], env=env, capture_output=True, timeout=15)
        assert result.returncode == 0, result.stderr
        assert not result.stderr, result.stderr
        return result.stdout

    paths = [line.removesuffix(' (*)') for line in run(['--list-config-paths']).decode().splitlines()]
    assert paths.index(str(user / 'fastfetch') + '/') < paths.index(str(system / 'fastfetch') + '/')
    assert '/etc/fastfetch/' in paths
    # fastfetch marks a directory holding a config with ' (*)'; ours is there once Emaki is installed.
    default_paths = [line.removesuffix(' (*)') for line in subprocess.check_output([FASTFETCH, '--list-config-paths'], env={
        **env, 'XDG_CONFIG_DIRS': '/etc/xdg'}).decode().splitlines()]
    assert default_paths.index(str(user / 'fastfetch') + '/') < default_paths.index('/etc/xdg/fastfetch/')

    preview = run(['-c', str(ROOT / 'fetch/config.jsonc'), '--pipe'])
    assert_os_line(preview)
    assert b'\x1b' not in preview
    assert '▀'.encode() in preview and '▄'.encode() in preview
    assert b'-`' not in preview and b'ooo/' not in preview, 'Arch logo leaked'
    # Modules absent on some builders (battery/GPU/WM) are verified in the config;
    # require all reliably available lines from the actual installed binary.
    for key in ['OS', 'Host', 'Kernel', 'Uptime', 'Shell', 'Terminal', 'CPU', 'Memory', 'Disk', 'Locale']:
        assert (key + ':').encode() in preview, (key, preview)
    (ROOT / '.cache').mkdir(exist_ok=True)
    (ROOT / '.cache/fetch-preview.txt').write_bytes(preview)
    assert_os_line(run(['--pipe']))
    custom = user / 'fastfetch/config.jsonc'
    custom.write_text(json.dumps({'logo': {'type': 'none'}, 'modules': [
        {'type': 'custom', 'format': 'USER-CONFIG-WINS'}]}))
    assert run(['--pipe']).strip() == b'USER-CONFIG-WINS'
    assert_os_line(run(['-c', str(ROOT / 'fetch/config.jsonc'), '--pipe']))
    custom.unlink()

    text = tty_run([], {**env, 'TERM': 'xterm-256color'})
    assert b'\x1b_G' not in text and b'\x1b[38;2;' in text
    assert '▀'.encode() in text
    assert_os_line(text)
    for term, extra in [('xterm-kitty', {'TMUX': 'test'}), ('xterm-kitty', {'NO_COLOR': '1'})]:
        fallback = tty_run([], {**env, 'TERM': term, **extra})
        assert b'\x1b_G' not in fallback and '▀'.encode() in fallback

    piped = tty_run(['--pipe'], {**env, 'TERM': 'xterm-kitty', 'KITTY_PID': '1'})
    assert b'\x1b' not in piped and '▀'.encode() in piped

    kitty = tty_run([], {**env, 'TERM': 'xterm-kitty', 'KITTY_PID': '1'})
    chunks = re.findall(rb'\x1b_G([^;]*);([^\x1b]*)\x1b\\', kitty)
    assert chunks, 'native Kitty image missing'
    assert b's=400,v=96' in chunks[0][0], chunks[0][0]
    assert b'c=' not in chunks[0][0] and b'r=' not in chunks[0][0], 'terminal would resize image'
    compressed = b''.join(base64.b64decode(payload) for _, payload in chunks)
    rgba = zlib.decompress(compressed) if b'o=z' in chunks[0][0] else compressed
    with Image.open(ROOT / 'fetch/wordmark.png') as image:
        assert image.size == (400, 96)
        assert rgba == image.tobytes(), 'fastfetch resampled the canonical PNG'
    assert '▀'.encode() not in kitty
    assert_os_line(kitty)
    assert b'render.py' not in kitty, 'helper polluted shell detection'
    assert b'ooo/' not in kitty
    (ROOT / '.cache/fetch-kitty.ansi').write_bytes(kitty)

    # A failed native backend must be replaced before its Arch fallback is displayed.
    fakebin = temp / 'bin'
    fakebin.mkdir()
    fake = fakebin / 'fastfetch'
    fake.write_text('#!/bin/sh\ncase "$*" in *"--logo-type kitty"*) '
                    'printf "ARCH-LOGO-PROBE-FAILURE"; exit 0 ;; esac\n'
                    'exec /usr/bin/fastfetch "$@"\n')
    fake.chmod(0o755)
    failed = tty_run([], {**env, 'TERM': 'xterm-kitty',
                         'PATH': str(fakebin) + ':' + env['PATH']})
    assert b'ARCH-LOGO' not in failed and b'ooo/' not in failed
    assert b'\x1b_G' not in failed and '▀'.encode() in failed
    assert_os_line(failed)

print('PASS: real fastfetch config, user precedence, pipe/text and native Kitty pixels')
