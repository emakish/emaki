#!/usr/bin/env python3
"""Minimal RFB client: full raw framebuffer grabs from a unix-socket VNC server -> PNG.

QEMU's VNC server reads the real scan-out of virtio-vga-gl with egl-headless, which QMP
screendump cannot do once the guest kernel owns the display (GOTCHAS: "no surface").
"""
import os
import socket
import struct
import sys
import time

from PIL import Image


def rd(s, n):
    b = bytearray()
    while len(b) < n:
        p = s.recv(n - len(b))
        if not p:
            raise RuntimeError('vnc eof')
        b.extend(p)
    return bytes(b)


class Client:
    """One RFB 3.8 connection, kept open so back-to-back grabs skip the handshake."""

    def __init__(self, sock_path, timeout=15):
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(timeout)
        s.connect(os.path.realpath(sock_path))
        rd(s, 12)
        s.sendall(b'RFB 003.008\n')
        n = rd(s, 1)[0]
        types = rd(s, n)
        if 1 not in types:
            raise RuntimeError(f'vnc server offers no "none" security type: {list(types)}')
        s.sendall(b'\x01')
        if struct.unpack('>I', rd(s, 4))[0] != 0:
            raise RuntimeError('vnc security handshake failed')
        s.sendall(b'\x01')  # shared
        self.width, self.height = struct.unpack('>HH', rd(s, 4))
        rd(s, 16)
        nl = struct.unpack('>I', rd(s, 4))[0]
        rd(s, nl)
        # 32bpp, depth 24, little endian, truecolor, shifts r16 g8 b0
        s.sendall(struct.pack('>BxxxBBBBHHHBBBxxx', 0, 32, 24, 0, 1, 255, 255, 255, 16, 8, 0))
        # RichCursor keeps the pointer out of raw scan-out pixels.
        s.sendall(struct.pack('>BxHii', 2, 2, 0, -239))
        self.sock = s

    def frame(self):
        """Request and read one complete (non-incremental) frame."""
        s, w, h = self.sock, self.width, self.height
        s.sendall(struct.pack('>BBHHHH', 3, 0, 0, 0, w, h))
        img = Image.new('RGB', (w, h))
        got = 0
        while got < w * h:
            t = rd(s, 1)[0]
            if t == 0:
                rd(s, 1)
                nr = struct.unpack('>H', rd(s, 2))[0]
                for _ in range(nr):
                    x, y, rw, rh, enc = struct.unpack('>HHHHi', rd(s, 12))
                    if enc == -239:
                        # Cursor pixels and one-bit transparency mask are separate.
                        rd(s, rw * rh * 4 + ((rw + 7) // 8) * rh)
                        continue
                    if enc != 0:
                        raise RuntimeError(f'unexpected vnc encoding {enc}')
                    data = rd(s, rw * rh * 4)
                    img.paste(Image.frombuffer('RGB', (rw, rh), data, 'raw', 'BGRX', 0, 1), (x, y))
                    got += rw * rh
            elif t == 2:
                pass
            elif t == 3:
                rd(s, 3)
                length = struct.unpack('>I', rd(s, 4))[0]
                rd(s, length)
            elif t == 1:
                rd(s, 1)
                nc = struct.unpack('>xxH', rd(s, 4))[0]
                rd(s, nc * 6)
            else:
                raise RuntimeError(f'unexpected vnc message {t}')
        return img

    def close(self):
        self.sock.close()


def grab(sock_path, out):
    client = Client(sock_path)
    try:
        img = client.frame()
    finally:
        client.close()
    img.save(out)
    return img.size


if __name__ == '__main__':
    start = time.monotonic()
    print(grab(sys.argv[1], sys.argv[2]), f'{time.monotonic() - start:.3f}s')
