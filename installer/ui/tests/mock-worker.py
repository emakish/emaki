#!/usr/bin/env python3
"""Serve synthetic recorded v1 transcripts, with no worker/device imports.

Default: a Unix socket in a new temporary directory, printed to stdout.
--stdio --screen is the bind-free renderer transport for restricted sandboxes.
No request payloads are printed or saved; passwords are discarded immediately.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
from datetime import datetime
from zoneinfo import ZoneInfo

TRANSCRIPTS = Path(__file__).with_name('transcripts')
# evdev.lst names of the fixtures' layouts, as the planner's review line prints them.
KEYBOARD_NAMES = {'us': 'English (US)', 'ru': 'Russian', 'de': 'German', 'cz': 'Czech'}


def transcript(name):
    """A transcript's rows; {"base": NAME, "confirm": [...]} is NAME's rows with other job events."""
    rows = json.loads((TRANSCRIPTS / (name + '.json')).read_text())
    if isinstance(rows, dict):
        base = transcript(rows['base'])
        assert base[-1]['expect'] == 'confirm', name
        base[-1] = dict(base[-1], responses=rows['confirm'])
        rows = base
    return rows


def console_chars():
    """latin_layouts.CONSOLE_CHARS, a data-only module, as the worker's hello sends it. The
    renderer copies the window to a temporary directory and names the checkout."""
    checkout = Path(os.environ.get('EMAKI_INSTALLER_CHECKOUT') or Path(__file__).resolve().parents[3])
    if str(checkout / 'installer') not in sys.path:
        sys.path.insert(0, str(checkout / 'installer'))
    from emaki_installer.latin_layouts import CONSOLE_CHARS
    return {name: list(record) for name, record in CONSOLE_CHARS.items()}


class Replay:
    def __init__(self, scenario='erase-happy', screen=None):
        self.rows = transcript(scenario)
        self.recorded = json.loads((TRANSCRIPTS / 'render.json').read_text())
        self.screen = screen
        if (screen or '').startswith('alongside') or screen == 'encryption-alongside':
            alongside = json.loads((TRANSCRIPTS / 'alongside.json').read_text())
            self.recorded['inventory'] = alongside[1]['responses'][0]
            self.recorded['plan_ack'] = alongside[2]['responses'][0]
        if screen == 'manual-empty':
            # The system disk has a GPT and no partitions yet.
            inventory = copy.deepcopy(self.recorded['inventory'])
            inventory['disks'][0].update(partition_table='gpt', partitions=[])
            self.recorded['inventory'] = inventory
        if screen == 'manual-by-id':
            # The system disk has a stable by-id name that differs from its device path, as on
            # real hardware; every probe answers with it (Open GParted probes again first).
            inventory = copy.deepcopy(self.recorded['inventory'])
            inventory['disks'][0]['id'] = '/dev/disk/by-id/virtio-emaki-target'
            self.recorded['inventory'] = inventory
        if screen == 'disk-mbr':
            # The system disk carries an MBR (dos) partition table.
            inventory = copy.deepcopy(self.recorded['inventory'])
            inventory['disks'][0]['partition_table'] = 'dos'
            self.recorded['inventory'] = inventory
        if screen == 'welcome-bios':
            self.recorded['inventory'] = dict(self.recorded['inventory'], uefi=False, uefi_bits=None)
        self.index = 0
        self.seq = 0
        self.disconnect = False

    def respond(self, request):
        kind = request['type']
        assert isinstance(request.get('id'), str) and request['id']
        if kind == 'set_timezone' and self.screen:
            now = datetime.now(ZoneInfo(request['timezone']))
            responses = [dict(type='reply', ok=True, timezone=request['timezone'],
                              unix_ms=int(now.timestamp() * 1000),
                              offset_seconds=int(now.utcoffset().total_seconds()), abbreviation=now.tzname())]
        elif self.screen:
            names = {'hello': ['hello'], 'probe': ['inventory'], 'plan': ['plan_ack'],
                     'confirm': ['reply', 'state'] + (['done'] if self.screen.startswith('done') else ['error'] if self.screen.startswith('error') else []),
                     'save_log': ['reply'], 'reboot': ['reply']}.get(kind, [])
            responses = [self.recorded[name] for name in names]
            if kind == 'hello':
                # The real worker's hello carries the console table (protocol.py).
                responses = [dict(responses[0], console_chars=console_chars())]
            if kind == 'confirm' and self.screen == 'done-warning':
                # The worker's PARTIAL_UPDATE warning (installer/emaki_installer/worker.py).
                responses[-1] = dict(responses[-1], warnings=[
                    'The online update stopped part-way; some packages may be newer than others. '
                    'Run `sudo pacman -Syu` after the first login.'])
            if kind == 'confirm' and self.screen == 'install-signatures':
                # The preflight's count (worker.py Worker.activity), before the first state event.
                responses = [responses[0], dict(type='progress', job_id=responses[0].get('job_id', 'fixture-job'),
                                                phase='prepare_disk', phase_pct=0, total_pct=0, indeterminate=True,
                                                activity=dict(name='signatures', done=312, total=871))]
            if kind == 'confirm' and self.screen == 'install-updates':
                # The update's downloads (worker.py DownloadCount): 93 % before the update phase.
                responses = [responses[0], dict(type='progress', job_id=responses[0].get('job_id', 'fixture-job'),
                                                phase='update', phase_pct=0, total_pct=93, indeterminate=True,
                                                activity=dict(name='downloads', done=37, total=144))]
            if kind == 'confirm' and self.screen == 'done-no-package-lists':
                # The worker's NO_PACKAGE_LISTS warning: installed offline, no sync databases.
                responses[-1] = dict(responses[-1], warnings=['Run `sudo pacman -Syu` once you are online.'])
            if kind == 'confirm' and self.screen.startswith('error'):
                responses[-1:-1] = self.recorded['logs']
            if kind == 'plan' and self.screen == 'plan-errors':
                responses = [dict(self.recorded['plan_ack'], token=None, summary=[], warnings=[], errors=[dict(code='manual_layout', msg='Exactly one / and one /efi are required.')])]
        else:
            row = self.rows[self.index]
            assert row['expect'] == kind, f'Expected {row["expect"]}, received {kind}'
            for key, value in row.get('fields', {}).items():
                assert request.get(key) == value, 'Unexpected ' + key
            self.index += 1
            responses = row['responses']
        if kind == 'plan':
            config = request['config']
            assert config['user']['password']
            config['user']['password'] = ''
            assert config['encryption'] in ('none', 'account', 'separate')
            config.pop('disk_password', None)
            assert config.get('software', 'rich') in ('rich', 'minimal')
            responses = copy.deepcopy(responses)
            for response in responses:
                if response['type'] == 'plan_ack' and not response.get('errors'):
                    response['summary'] = [line for line in response.get('summary', []) if not line.startswith(('Software:', 'Keyboard:'))]
                    # The planner's keyboard line (render.keyboard_summary), after the account line.
                    names = [KEYBOARD_NAMES.get(layout, layout) for layout in config.get('layouts') or ['us']]
                    keyboard = (f'Keyboard: {names[0]}.' if len(names) == 1 else
                                f"Keyboard: {names[0]} (default), {', '.join(names[1:])}. Super+Space switches.")
                    account = next((i for i, line in enumerate(response['summary']) if line.startswith('Account:')), None)
                    response['summary'].insert(len(response['summary']) if account is None else account + 1, keyboard)
                    response['summary'].append('Encryption: off.' if config['encryption'] == 'none' else 'Encryption: LUKS2 root, account password.')
                    response['summary'].append('Hibernation: reserve 4.00 GiB, equal to RAM.' if config.get('hibernation') else 'Hibernation: off.')
                    response['summary'].append('Software: Rich — desktop apps, office, email, media and utilities.'
                                               if config.get('software', 'rich') == 'rich'
                                               else 'Software: Minimal — Dolphin, Firefox and kitty.')
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
