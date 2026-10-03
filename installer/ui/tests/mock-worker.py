#!/usr/bin/env python3
"""Serve synthetic recorded v1 transcripts, with no worker/device imports.

Default: a Unix socket in a new temporary directory, printed to stdout.
--stdio --screen is the bind-free renderer transport for restricted sandboxes.
No request payloads are printed or saved; passwords are discarded immediately.
"""
import argparse
import copy
import json
from pathlib import Path
import socket
import sys
import tempfile
import time

TRANSCRIPTS = Path(__file__).with_name('transcripts')


class Replay:
    def __init__(self, scenario='erase-happy', screen=None):
        self.rows = json.loads((TRANSCRIPTS / (scenario + '.json')).read_text())
        self.recorded = json.loads((TRANSCRIPTS / 'render.json').read_text())
        self.screen = screen
        self.index = 0
        self.seq = 0
        self.disconnect = False

    def respond(self, request):
        kind = request['type']
        assert isinstance(request.get('id'), str) and request['id']
        if self.screen:
            names = {'hello': ['hello'], 'probe': ['inventory'], 'plan': ['plan_ack'],
                     'confirm': ['reply', 'state'] + (['done'] if self.screen == 'done' else ['error'] if self.screen == 'error' else []),
                     'save_log': ['reply'], 'reboot': ['reply']}.get(kind, [])
            responses = [self.recorded[name] for name in names]
            if kind == 'plan' and self.screen == 'plan-errors':
                responses = [dict(self.recorded['plan_ack'], token=None, summary=[], warnings=[], errors=[dict(code='manual_layout', msg='Exactly one / and one /efi are required.')])]
        else:
            row = self.rows[self.index]
            assert row['expect'] == kind, f'Expected {row["expect"]}, received {kind}'
            self.index += 1
            responses = row['responses']
        if kind == 'plan':
            config = request['config']
            assert config['user']['password']
            config['user']['password'] = ''
        result = []
        for response in responses:
            self.seq += 1
            message = copy.deepcopy(response)
            event = message['type'] in ('state', 'progress', 'log', 'error', 'done', 'cancel_ack')
            message.update(id='' if event else request['id'], seq=self.seq)
            if message['type'] == 'reply': message['for_id'] = request['id']
            result.append(message)
        if not self.screen and self.index < len(self.rows) and self.rows[self.index]['expect'] == 'disconnect':
            self.index += 1
            self.disconnect = True
        return result


def serve_connection(reader, writer, replay, delay, stdio=False):
    while True:
        frame = reader.readline(65537)
        if not frame or len(frame) > 65536 or not frame.endswith(b'\n'): return
        request = json.loads(frame)
        for message in replay.respond(request):
            writer.write((json.dumps(message) + '\n').encode())
            writer.flush()
            if delay: time.sleep(delay)
        if replay.disconnect:
            replay.disconnect = False
            if not stdio: return
            writer.write(b'{"type":"transport_disconnected"}\n')
            writer.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', default='erase-happy', choices=[p.stem for p in TRANSCRIPTS.glob('*.json') if p.stem != 'render'])
    parser.add_argument('--socket', type=Path)
    parser.add_argument('--stdio', action='store_true')
    parser.add_argument('--screen')
    parser.add_argument('--delay', type=float, default=0.15)
    args = parser.parse_args()
    replay = Replay(args.scenario, args.screen)
    if args.stdio:
        serve_connection(sys.stdin.buffer, sys.stdout.buffer, replay, args.delay, stdio=True)
        return
    with tempfile.TemporaryDirectory(prefix='emaki-mock-') as temporary:
        path = args.socket or Path(temporary) / 'worker.sock'
        with socket.socket(socket.AF_UNIX) as server:
            server.bind(str(path))
            server.listen(1)
            print(path, flush=True)
            while True:
                conn, _ = server.accept()
                with conn, conn.makefile('rb') as reader, conn.makefile('wb') as writer:
                    serve_connection(reader, writer, replay, args.delay)


if __name__ == '__main__':
    main()
