#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Host side of upgrade acceptance T2: answer the old address an installed 0.1.x system reads,

  https://github.com/emakish/packages/releases/download/stable/<file>

from a directory holding what GitHub stable will hold (written by
`packaging/publish.sh --github local:DIR github --tag testing`, then DIR/testing). TLS uses a
throwaway CA made here; the guest trusts it through one file in
/etc/ca-certificates/trust-source/anchors/ and reaches this server through one /etc/hosts line
and QEMU's guestfwd (tests/vm/upgrade-check.py does both). With --pkgs-root it also answers
pkgs.emaki.sh from a local bucket the way the pointer Worker does: a REHEARSAL only, whose
results publish.sh refuses as acceptance.
"""
import argparse
import functools
import http.server
import importlib.util
import json
from pathlib import Path
import ssl
import subprocess
import sys
import threading

HERE = Path(__file__).resolve().parent
GITHUB_PREFIX = '/emakish/packages/releases/download/stable/'
spec = importlib.util.spec_from_file_location('pointer_server', HERE.parent / 'pointer-server.py')
pointer_server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pointer_server)


def make_certificates(directory, hosts):
    """A CA valid for 7 days and one server certificate for the given host names."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    ca_key, ca_cert = directory / 'ca.key', directory / 'ca.crt'
    key, csr, cert = directory / 'server.key', directory / 'server.csr', directory / 'server.crt'
    if not ca_cert.exists():
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256',
                        '-nodes', '-keyout', ca_key, '-out', ca_cert, '-days', '7',
                        '-subj', '/CN=Emaki upgrade-check test CA',
                        '-addext', 'basicConstraints=critical,CA:TRUE',
                        '-addext', 'keyUsage=critical,keyCertSign,cRLSign'], check=True, capture_output=True)
    extensions = directory / 'server.ext'
    extensions.write_text('subjectAltName=' + ','.join(f'DNS:{h}' for h in hosts) +
                          '\nextendedKeyUsage=serverAuth\nbasicConstraints=CA:FALSE\n')
    subprocess.run(['openssl', 'req', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256', '-nodes',
                    '-keyout', key, '-out', csr, '-subj', f'/CN={hosts[0]}'], check=True, capture_output=True)
    subprocess.run(['openssl', 'x509', '-req', '-in', csr, '-CA', ca_cert, '-CAkey', ca_key, '-CAcreateserial',
                    '-out', cert, '-days', '7', '-extfile', extensions], check=True, capture_output=True)
    return ca_cert, cert, key


class Handler(pointer_server.Handler):
    github_dir = None
    pkgs_root = None

    def answer(self, head):
        host = (self.headers.get('Host') or '').split(':')[0].lower()
        path = self.path.split('?', 1)[0]
        if host == 'github.com':
            name = path[len(GITHUB_PREFIX):] if path.startswith(GITHUB_PREFIX) else ''
            target = Path(self.github_dir) / name
            if not name or '/' in name or name.startswith('.') or not target.is_file():
                self.send_error(404)
                return
            self.serve_file(target, head)
        elif host == 'pkgs.emaki.sh' and self.pkgs_root:
            self.directory = str(self.pkgs_root)
            super().answer(head)
        else:
            self.send_error(404)

    def serve_file(self, target, head):
        data = target.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', 'application/octet-stream')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Last-Modified', self.date_time_string(int(target.stat().st_mtime)))
        self.end_headers()
        if not head:
            self.wfile.write(data)


def serve(github_dir, certificates, port=0, pkgs_root=None, log=None):
    ca, cert, key = certificates
    bound = type('Bound', (Handler,), {'github_dir': str(github_dir), 'log_path': log, 'flat': False,
                                       'pkgs_root': str(pkgs_root) if pkgs_root else None})
    handler = functools.partial(bound, directory=str(pkgs_root or github_dir))
    server = http.server.ThreadingHTTPServer(('127.0.0.1', port), handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--github-dir', required=True, help='files GitHub stable will hold')
    parser.add_argument('--certs', required=True, help='directory for the test CA and server certificate')
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--pkgs-root', help='REHEARSAL: also answer pkgs.emaki.sh from this local bucket')
    parser.add_argument('--log')
    parser.add_argument('--port-file')
    args = parser.parse_args()
    hosts = ['github.com'] + (['pkgs.emaki.sh'] if args.pkgs_root else [])
    server = serve(args.github_dir, make_certificates(args.certs, hosts), args.port, args.pkgs_root, args.log)
    if args.port_file:
        Path(args.port_file).write_text(str(server.server_address[1]))
    print(json.dumps({'port': server.server_address[1], 'hosts': hosts}), flush=True)
    threading.Event().wait()


if __name__ == '__main__':
    sys.exit(main())
