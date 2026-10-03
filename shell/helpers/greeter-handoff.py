#!/usr/bin/env python3
"""Publish a password-free final greeter image, addressed only by its login nonce."""
import json
import os
from pathlib import Path
import re
import stat
import sys
import time

ROOT = Path('/var/lib/emaki-greeter/handoff')
TOKEN = re.compile(r'[0-9a-f]{32}\Z')


def handoff(operation, token):
    if not TOKEN.fullmatch(token):
        raise ValueError('invalid token')
    directory = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(directory)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o711:
            raise ValueError('unsafe handoff root')
        name = token + '.png'
        if operation == 'prepare':
            # No credentials are ever captured. Still expire old decorative pictures.
            for entry in os.listdir(directory):
                if re.fullmatch(r'[0-9a-f]{32}(?:-plate)?\.png', entry):
                    old = os.stat(entry, dir_fd=directory, follow_symlinks=False)
                    if old.st_uid == os.geteuid() and time.time() - old.st_mtime > 60:
                        os.unlink(entry, dir_fd=directory)
            descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=directory)
            os.close(descriptor)
        elif operation == 'publish':
            from PIL import Image
            for filename in (name, token + '-plate.png'):
                descriptor = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                with os.fdopen(descriptor, 'rb') as stream:
                    info = os.fstat(stream.fileno())
                    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                            or info.st_nlink != 1 or not 24 <= info.st_size <= 64*1024*1024
                            or stream.read(8) != b'\x89PNG\r\n\x1a\n'):
                        raise ValueError('invalid picture')
                    stream.seek(0)
                    with Image.open(stream, formats=('PNG',)) as image:
                        if image.width * image.height > 48_000_000:
                            raise ValueError('picture too large')
                        # grabToImage of the plate has alpha; its on-screen backing is black.
                        rgba = image.convert('RGBA')
                        flat = Image.new('RGB', image.size, 'black')
                        flat.paste(rgba, mask=rgba.getchannel('A'))
                # The directory is writable only by this greeter; replace privately then publish.
                pending = ROOT / (filename + '.new')
                flat.save(pending, format='PNG')
                os.chmod(pending, 0o644)
                os.replace(pending, ROOT / filename)
        else:
            raise ValueError('invalid operation')
        return dict(path=str(ROOT / name))
    finally:
        os.close(directory)


if __name__ == '__main__':
    try:
        if len(sys.argv) != 3:
            raise ValueError('arguments')
        print(json.dumps(handoff(*sys.argv[1:])))
    except (OSError, ValueError):
        sys.exit(1)
