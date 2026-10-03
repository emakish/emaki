#!/usr/bin/env python3
"""Read each HMP response through its prompt (including screendump errors)."""
import argparse
import os
from pathlib import Path
import socket
import sys


def command(directory, text, timeout=5):
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(timeout)
        connection.connect(str(Path(directory) / 'mon.sock'))

        def response(eof_ok=False):
            data = bytearray()
            while b'(qemu) ' not in data:
                part = connection.recv(65536)
                if not part:
                    if eof_ok:
                        break
                    raise RuntimeError('QEMU disconnected before its prompt')
                data.extend(part)
            return data.decode(errors='replace')

        response()
        connection.sendall(text.encode() + b'\n')
        return response(text == 'quit')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dir', default=os.environ.get('EMAKI_ISO_VM_DIR', str(Path.home() / 'VMs/iso-vm')))
    parser.add_argument('command', nargs='+')
    args = parser.parse_args()
    print(command(args.dir, ' '.join(args.command)))


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError) as error:
        sys.exit(f'BAD: monitor: {error}')
