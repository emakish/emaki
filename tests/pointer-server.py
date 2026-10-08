#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Serve a local bucket directory the way pkgs.emaki.sh does: R2 behind the pointer Worker.

<channel>/x86_64/emaki.{db,files}[.sig] -> 302 into snap/<channel>/<id>/ named by
pointers/<channel> (same rule as packaging/mirror/pointer-worker.js); everything else is a
static file with Last-Modified and If-Modified-Since, as the bucket answers. The image
(iso/<version>/emaki-<version>-x86_64.iso) is answered as by the Worker: X-Emaki-Image: 1 on
every answer, a strong ETag, and If-Range honoured. --flat serves the directory as it is (the
old in-place layout), for the self-check that must go red.
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
IMAGE_ROUTE = re.compile(r'^/iso/(\d+\.\d+\.\d+)/emaki-\1-x86_64\.iso$')
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
    status = None
    image_worker = True  # the image is answered as by the Worker: X-Emaki-Image, If-Range

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
        self.status = code
        super().send_response(code, message)

    def end_headers(self):
        path = self.path.split('?', 1)[0]
        if not self.flat and ISO_ROUTE.fullmatch(path):
            self.send_header('X-Emaki-Source-Pointer', '1')
        tag = self.image_etag(path)
        if tag and self.status in (200, 206, 304):
            self.send_header('ETag', tag)
            self.send_header('Accept-Ranges', 'bytes')
        # Every Worker answer for the image is marked, a missing image included; the 403 for a
        # default client identity comes from the edge in front of the Worker.
        if (not self.flat and self.image_worker and IMAGE_ROUTE.fullmatch(path)
                and self.status != 403):
            self.send_header('X-Emaki-Image', '1')
        super().end_headers()

    def image_etag(self, path):
        """The image's strong ETag (the Worker sends R2's; here from mtime and size), or None."""
        if self.flat or not IMAGE_ROUTE.fullmatch(path):
            return None
        try:
            info = Path(self.translate_path(path)).stat()
        except OSError:
            return None
        return f'"{info.st_mtime_ns:x}-{info.st_size:x}"'

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
        """One byte range a-b, a- or -n, as R2 and the Worker answer it (resumed downloads, the
        ISO read-back); False serves the whole file. On the image a stale If-Range (neither its
        ETag nor its Last-Modified) gets the whole file, as from the Worker."""
        match = re.fullmatch(r'bytes=(\d*)-(\d*)', self.headers['Range'].strip())
        target = Path(self.translate_path(path))
        if not match or match.group(0) == 'bytes=-' or not target.is_file():
            return False
        size = target.stat().st_size
        modified = self.date_time_string(int(target.stat().st_mtime))
        tag = self.image_etag(path)
        validator = self.headers.get('If-Range')
        if (tag and self.image_worker and validator is not None
                and validator.strip() not in (tag, modified)):
            return False
        first, last = match.groups()
        if first:
            start = int(first)
            if last and int(last) < start:
                return False
            end = min(int(last) if last else size - 1, size - 1)
        else:
            suffix = int(last)
            start, end = max(0, size - suffix), (size - 1 if suffix else -1)
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
        self.send_header('Last-Modified', modified)
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


class Server(http.server.ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], ConnectionError):
            return  # the client stopped reading, as a refused read-back of a whole image does
        super().handle_error(request, client_address)


def serve(root, port=0, flat=False, log=None, base=None):
    """base: a Handler subclass, for a test that imitates another server."""
    handler = partial(type('BoundHandler', (base or Handler,), {'flat': flat, 'log_path': log}),
                      directory=str(root))
    server = Server(('127.0.0.1', port), handler)
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
