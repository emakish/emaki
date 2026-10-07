#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Serve a local bucket directory the way pkgs.emaki.sh does: R2 behind the pointer Worker.

<channel>/x86_64/emaki.{db,files}[.sig] -> 302 into snap/<channel>/<id>/ named by
pointers/<channel> (same rule as packaging/mirror/pointer-worker.js); everything else is a
static file with Last-Modified and If-Modified-Since, as the bucket answers. --flat serves the
directory as it is (the old in-place layout), for the self-check that must go red.
Each request is appended to --log as one JSON line.
"""
import argparse
from functools import partial
import http.server
import json
from pathlib import Path
import re
import sys
import threading

ROUTE = re.compile(r'^/(stable|testing)/x86_64/(emaki\.(?:db|files)(?:\.sig)?|SOURCES(?:\.json)?)$')
ISO_ROUTE = re.compile(r'^/iso/(\d+\.\d+\.\d+)/(SOURCES-ISO\.txt|ARCH-SOURCES\.json|MISSING-SOURCES\.json)$')
SOURCE_SNAPSHOT_ID = re.compile(r'^[a-f0-9]{64}$')
SNAPSHOT_ID = re.compile(r'^\d{8}T\d{6}Z$')


def route(root, path):
    """(status, location or None) for a pointer path, or None to serve the file."""
    image = ISO_ROUTE.fullmatch(path)
    if image:
        pointer = Path(root) / 'iso' / image.group(1) / 'source-pointer'
        try:
            snap_id = pointer.read_text().strip()
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError):
            return 503, None
        if not SOURCE_SNAPSHOT_ID.fullmatch(snap_id):
            return 503, None
        return 302, f'/iso/{image.group(1)}/source-snapshots/{snap_id}/{image.group(2)}'
    match = ROUTE.match(path)
    if not match:
        return None
    pointer = Path(root) / 'pointers' / match.group(1)
    try:
        snap_id = pointer.read_text().strip()
    except OSError:
        snap_id = ''
    if not SNAPSHOT_ID.match(snap_id):
        return 503, None
    return 302, f'/snap/{match.group(1)}/{snap_id}/{match.group(2)}'


class Handler(http.server.SimpleHTTPRequestHandler):
    flat = False
    log_path = None
    lock = threading.Lock()

    def log_message(self, format, *args):
        pass

    def record(self, status):
        if self.log_path:
            line = json.dumps({'method': self.command, 'path': self.path, 'status': status,
                               'ims': self.headers.get('If-Modified-Since')})
            with self.lock, open(self.log_path, 'a') as log:
                log.write(line + '\n')

    def send_response(self, code, message=None):
        self.record(code)
        super().send_response(code, message)

    def end_headers(self):
        if not self.flat and ISO_ROUTE.fullmatch(self.path.split('?', 1)[0]):
            self.send_header('X-Emaki-Source-Pointer', '1')
        super().end_headers()

    def answer(self, head):
        if self.headers.get('User-Agent', '').startswith('Python-urllib/'):
            self.send_error(403)
            return
        path = self.path.split('?', 1)[0]
        if path.startswith('/.') or '/.' in path or '..' in path.split('/'):
            self.send_error(404)
            return
        routed = None if self.flat else route(self.directory, path)
        if routed:
            status, location = routed
            self.send_response(status)
            if location:
                self.send_header('Location', location)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if not head and self.headers.get('Range') and self.ranged(path):
            return
        if head:
            super().do_HEAD()
        else:
            super().do_GET()

    def ranged(self, path):
        """One byte range, as R2 answers it (resumed downloads, the ISO read-back)."""
        match = re.fullmatch(r'bytes=(\d+)-(\d*)', self.headers['Range'].strip())
        target = Path(self.translate_path(path))
        if not match or not target.is_file():
            return False
        size = target.stat().st_size
        start = int(match.group(1))
        end = min(int(match.group(2)) if match.group(2) else size - 1, size - 1)
        if start > end:
            self.send_response(416)
            self.send_header('Content-Range', f'bytes */{size}')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return True
        with open(target, 'rb') as stream:
            stream.seek(start)
            data = stream.read(end - start + 1)
        self.send_response(206)
        self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Last-Modified', self.date_time_string(int(target.stat().st_mtime)))
        self.end_headers()
        self.wfile.write(data)
        return True

    def do_GET(self):
        self.answer(False)

    def do_HEAD(self):
        self.answer(True)

    def list_directory(self, path):
        self.send_error(404)  # R2 public buckets do not list
        return None


def serve(root, port=0, flat=False, log=None):
    handler = partial(type('BoundHandler', (Handler,), {'flat': flat, 'log_path': log}), directory=str(root))
    server = http.server.ThreadingHTTPServer(('127.0.0.1', port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('root')
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--flat', action='store_true')
    parser.add_argument('--log')
    parser.add_argument('--port-file', help='write the bound port here once listening')
    args = parser.parse_args()
    server = serve(args.root, args.port, args.flat, args.log)
    if args.port_file:
        Path(args.port_file).write_text(str(server.server_address[1]))
    print(f'serving {args.root} on http://127.0.0.1:{server.server_address[1]}', flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.shutdown()
    return 0


if __name__ == '__main__':
    sys.exit(main())
