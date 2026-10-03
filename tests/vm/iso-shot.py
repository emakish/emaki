#!/usr/bin/env python3
"""Capture guest grim; fall back to monitor screendump before Wayland exists."""
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


def fixture_config(path):
    data = json.loads(Path(path).read_text())
    return data.get('config', data)


def ssh_args(args):
    return [str(HERE / 'iso-ssh.sh'), '--dir', args.dir, '--ssh-port', str(args.ssh_port),
            '--user', args.user, '--']


def screenshot(args):
    destination = Path(args.output).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    log = destination.with_suffix(destination.suffix + '.log')
    if not args.monitor:
        ssh = ssh_args(args)
        helper = HERE / 'iso-guest-shot.py'
        uploaded = subprocess.run(ssh + ['umask 077; cat > "$HOME/iso-guest-shot.py"'],
                                  input=helper.read_bytes(), capture_output=True)
        if uploaded.returncode == 0:
            password = fixture_config(args.fixture)['user']['password'] if args.fixture else None
            command = 'sudo -k -S -p "" python3 "$HOME/iso-guest-shot.py"' if password is not None else 'sudo -n python3 "$HOME/iso-guest-shot.py"'
            result = subprocess.run(ssh + [command], input=(password + '\n').encode() if password is not None else b'',
                                    capture_output=True, timeout=30)
            log.write_bytes(result.stderr)
            if result.returncode == 0 and result.stdout.startswith(b'\x89PNG\r\n\x1a\n'):
                destination.write_bytes(result.stdout)
                print(f'OK: guest grim: {destination}')
                return True
        else:
            log.write_bytes(uploaded.stderr)
    # HMP string quoting, not shell quoting. Backslashes/quotes must be escaped.
    quoted = json.dumps(str(destination))
    response = monitor.command(args.dir, f'screendump {quoted} -f png')
    with log.open('a') as stream:
        stream.write(response)
    if destination.is_file() and destination.read_bytes().startswith(b'\x89PNG\r\n\x1a\n'):
        print(f'OK: QEMU screendump: {destination}')
        return True
    destination.unlink(missing_ok=True)
    print(f'BAD: screenshot unavailable (virgl may report "no surface"); see {log}', file=sys.stderr)
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dir', default=os.environ.get('EMAKI_ISO_VM_DIR', str(Path.home() / 'VMs/iso-vm')))
    parser.add_argument('--ssh-port', type=int)
    parser.add_argument('--user', default='live')
    parser.add_argument('--fixture', help='fixture JSON supplying installed sudo password')
    parser.add_argument('--monitor', action='store_true', help='skip SSH/Wayland (firmware or GRUB)')
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
