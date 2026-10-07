#!/usr/bin/env python3
"""Capture the host display; saving pixels does not judge their visible content."""
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('iso_monitor', HERE / 'iso-monitor.py')
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


def frame_metrics(path):
    from PIL import Image, ImageChops
    with Image.open(path) as frame:
        if frame.format != 'PNG':
            raise ValueError('capture is not PNG')
        frame.load()
        pixels = frame.convert('RGB')
        red, green, blue = pixels.split()
        histogram = ImageChops.lighter(ImageChops.lighter(red, green), blue).histogram()
        nonblack = 1 - histogram[0] / (pixels.width * pixels.height)
        if nonblack == 0:
            raise ValueError('host display is completely black')
        return {'width': pixels.width, 'height': pixels.height,
                'nonblack_fraction': nonblack}


def screenshot(args):
    destination = Path(args.output).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    destination.with_suffix(destination.suffix + '.json').unlink(missing_ok=True)
    log = destination.with_suffix(destination.suffix + '.log')
    source = 'QEMU screendump'
    vnc = Path(args.dir) / 'vnc.sock'
    if vnc.exists() and not args.monitor:
        spec = importlib.util.spec_from_file_location('vncshot', HERE / 'eyes/vncshot.py')
        vncshot = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(vncshot)
        vncshot.grab(str(vnc), str(destination))
        source = 'VNC'
        log.write_text('Host VNC framebuffer capture\n')
    else:
        response = monitor.command(args.dir, f'screendump {json.dumps(str(destination))} -f png')
        log.write_text(response)
    try:
        metrics = frame_metrics(destination)
    except (OSError, ValueError) as error:
        destination.unlink(missing_ok=True)
        print(f'BAD: screenshot unavailable or invalid: {error}; see {log}', file=sys.stderr)
        return False
    if source == 'VNC':
        print(f'SHOT: VNC: {destination} (not judged)')
    else:
        print(f'SHOT: QEMU screendump: {destination} (not judged)')
    print(f"FRAME: {metrics['width']}x{metrics['height']} nonblack={metrics['nonblack_fraction']:.6f}")
    destination.with_suffix(destination.suffix + '.json').write_text(json.dumps(
        dict(metrics, source=source, judgment='NOT TESTED'), indent=2) + '\n')
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dir', default=os.environ.get('EMAKI_ISO_VM_DIR', str(Path.home() / 'VMs/iso-vm')))
    parser.add_argument('--ssh-port', type=int)
    parser.add_argument('--user', default='live')
    parser.add_argument('--fixture', help='fixture JSON supplying installed sudo password')
    parser.add_argument('--monitor', action='store_true', help='use QEMU screendump instead of VNC')
    parser.add_argument('output')
    args = parser.parse_args()
    if args.ssh_port is None:
        port_file = Path(args.dir) / 'ssh-port'
        args.ssh_port = int(os.environ.get('EMAKI_ISO_SSH_PORT', port_file.read_text().strip() if port_file.exists() else '2223'))
    return 0 if screenshot(args) else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
        sys.exit(f'BAD: screenshot: {error}')
