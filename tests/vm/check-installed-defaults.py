#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Read installed defaults; run only inside a disposable acceptance guest."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib


def sudo_files(root):
    paths = [root / 'etc/sudoers']
    directory = root / 'etc/sudoers.d'
    if not directory.is_dir():
        raise ValueError('Missing sudo policy directory [sudoers].')
    paths.extend(path for path in directory.rglob('*') if not path.is_dir())
    # The packaged gateway includes this directory outside sudoers.d.
    managed = root / 'etc/emaki/sudoers.d'
    if managed.exists():
        paths.extend(path for path in managed.rglob('*') if not path.is_dir())
    for path in paths:
        for line in path.read_text().splitlines():
            active = line.split('#', 1)[0]
            if re.search(r'\bNOPASSWD\s*:', active):
                raise ValueError('Passwordless access found [sudoers]: ' + str(path))
    if (directory / '10-live').exists():
        raise ValueError('Live policy reached the installation [sudoers].')


def sudo_user(run=subprocess.run):
    environment = dict(os.environ, LC_ALL='C')
    cleared = run(['sudo', '-K'], capture_output=True, text=True, env=environment, timeout=10)
    if cleared.returncode:
        raise ValueError('Could not clear saved authorization [sudo].')
    result = run(['sudo', '-n', '/usr/bin/true'], capture_output=True, text=True,
                 env=environment, timeout=10)
    if result.returncode != 1 or 'a password is required' not in result.stderr:
        raise ValueError('Password requirement was not proved [sudo].')


def wallpaper_files(root, home):
    shipped = root / 'etc/skel/.config/wpaperd/config.toml'
    current = home / '.config/wpaperd/config.toml'
    expected = tomllib.loads(shipped.read_text())
    if tomllib.loads(current.read_text()) != expected:
        raise ValueError('The session does not use the shipped settings [wpaperd].')
    if not expected or any(not isinstance(value, dict) or 'path' not in value
                           for value in expected.values()):
        raise ValueError('The shipped settings have no image [wpaperd].')
    from PIL import Image
    for value in expected.values():
        path = Path(value['path'])
        if not path.is_absolute() or '..' in path.parts:
            raise ValueError('The shipped image path is invalid [wpaperd].')
        with Image.open(root / path.relative_to('/')) as image:
            image.load()
            if min(image.size) <= 0:
                raise ValueError('The shipped image is empty [wpaperd].')


def wallpaper_runtime(run=subprocess.run, proc=Path('/proc'), home=None):
    home = home or Path.home()
    process = run(['pgrep', '-u', str(os.getuid()), '-x', 'wpaperd'],
                  capture_output=True, text=True, timeout=10)
    if process.returncode or not process.stdout.strip():
        raise ValueError('The default wallpaper process is absent [wpaperd].')
    for pid in process.stdout.split():
        if not pid.isdigit():
            raise ValueError('Invalid wallpaper process identity [wpaperd].')
        command = (proc / pid / 'cmdline').read_bytes().rstrip(b'\0').split(b'\0')
        environment = dict(item.split(b'=', 1) for item in
                           (proc / pid / 'environ').read_bytes().split(b'\0') if b'=' in item)
        config = environment.get(b'XDG_CONFIG_HOME') or environment.get(b'HOME', b'') + b'/.config'
        if len(command) != 1 or Path(os.fsdecode(config)) != home / '.config':
            raise ValueError('The wallpaper process overrides the default settings [wpaperd].')
    journal = run(['journalctl', '--user', '-b', '--no-pager', '-o', 'cat'],
                  capture_output=True, text=True, timeout=20)
    if journal.returncode:
        raise ValueError('Could not read the session log [journalctl].')
    if 'Failed to pass the image data to the texture' in journal.stdout:
        raise ValueError('The default image did not reach the texture [wpaperd].')
    evidence = {}
    for query in ('outputs', 'layers'):
        response = run(['niri', 'msg', '--json', query], capture_output=True,
                       text=True, timeout=10)
        if response.returncode:
            raise ValueError('Could not read wallpaper evidence [niri ' + query + '].')
        try:
            evidence[query] = json.loads(response.stdout)
        except ValueError as error:
            raise ValueError('Invalid wallpaper evidence [niri ' + query + '].') from error
    outputs, layers = evidence['outputs'], evidence['layers']
    if (not isinstance(outputs, dict) or
            any(not isinstance(value, dict) or 'current_mode' not in value
                or (value['current_mode'] is not None and
                    (type(value['current_mode']) is not int or value['current_mode'] < 0))
                for value in outputs.values()) or
            not isinstance(layers, list) or
            any(not isinstance(layer, dict) for layer in layers)):
        raise ValueError('Invalid wallpaper evidence [niri].')
    active = {name for name, value in outputs.items() if value['current_mode'] is not None}
    if not active:
        raise ValueError('No active output proves the wallpaper [niri].')
    # Niri reports mapped layer-shell surfaces; wpaperd names each after its output.
    missing = [name for name in sorted(active) if not any(
        layer.get('output') == name and layer.get('namespace') == 'wpaperd-' + name
        and layer.get('layer') == 'Background' for layer in layers)]
    if missing:
        raise ValueError('The default wallpaper is not mapped on: ' + ', '.join(missing) + ' [wpaperd].')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('check', choices=('sudo-files', 'sudo-user', 'wallpaper'))
    args = parser.parse_args()
    guest = subprocess.run(['systemd-detect-virt', '--vm'], capture_output=True, text=True)
    if guest.returncode or guest.stdout.strip() not in ('qemu', 'kvm') or Path('/run/archiso/bootmnt').exists():
        raise ValueError('An installed test guest is required [QEMU/KVM].')
    if args.check == 'sudo-files':
        sudo_files(Path('/'))
    elif args.check == 'sudo-user':
        sudo_user()
    else:
        wallpaper_files(Path('/'), Path.home())
        wallpaper_runtime()
    print('PASS installed defaults: ' + args.check)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        sys.exit(str(error))
