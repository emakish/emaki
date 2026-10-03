#!/usr/bin/env python3
"""Select a native Kitty image or the same wordmark in terminal half blocks.

Called by fastfetch's command module, whose stdout/stderr are captured. Inspect
the calling fastfetch's output and pipe option, then exec the real fetch so shell
and terminal detection see the original caller, not this Python helper.
"""
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def color_output():
    """The command module's capture must not change the caller's pipe policy."""
    parent = Path('/proc') / str(os.getppid())
    try:
        fd = os.open(parent / 'fd/1', os.O_WRONLY | os.O_NONBLOCK | os.O_NOCTTY)
        try:
            terminal = os.isatty(fd)
        finally:
            os.close(fd)
        args = (parent / 'cmdline').read_bytes().split(b'\0')
    except OSError:
        return False
    pipe = not terminal
    for index, arg in enumerate(args):
        if arg.lower() == b'--pipe':
            pipe = args[index + 1].lower() != b'false'
    return not pipe and 'NO_COLOR' not in os.environ and os.environ.get('TERM') != 'dumb'


def main():
    color = color_output()
    kitty = color and os.environ.get('TERM') == 'xterm-kitty' and not any(
        os.environ.get(name) for name in ('TMUX', 'STY'))
    base = ['fastfetch', '-c', str(ROOT / 'details.jsonc'), '--pipe', str(not color).lower()]
    text = (ROOT / 'wordmark.txt').read_text()
    if not color:
        text = re.sub(r'\x1b\[[0-9;]*m', '', text)
    fallback = ['--logo-type', 'data-raw', '--logo', text]
    args = fallback
    if kitty and (ROOT / 'wordmark.png').is_file():
        # No cell dimensions: fastfetch transmits the exact 400 × 96 image pixels.
        args = ['--logo-type', 'kitty', '--logo', str(ROOT / 'wordmark.png')]
    if args is not fallback:
        probe = subprocess.run(
            ['fastfetch', '-c', 'none', '--structure', '', '--pipe', 'false', *args],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if probe.returncode or b'\x1b_G' not in probe.stdout:
            args = fallback
    os.execvp('fastfetch', base + args)



if __name__ == '__main__':
    sys.exit(main())
