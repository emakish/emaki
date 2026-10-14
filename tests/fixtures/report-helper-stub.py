#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
# A stand-in for emaki-report with the F8RC contract. It records each call and never touches the
# network. Mode (profile/mode): ok | big | fail | bad | badurl | huge | ... (others behave as ok).
# calls.jsonl gets argv[1:] per call; stdin.jsonl gets, per url call, the stdin byte length and the
# longest argument of this process and of its parent (the document must come on stdin, not argv).
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote

LIMIT = 8000  # Like the real helper: a longer issue address is not handed to the browser.
profile = Path(os.environ['REPORT_PROFILE'])
mode = (profile / 'mode').read_text().strip() if (profile / 'mode').exists() else 'ok'
with (profile / 'calls.jsonl').open('a') as log:
    log.write(json.dumps(sys.argv[1:]) + '\n')
args = sys.argv[1:]
if args[:1] == ['compose']:
    if mode == 'fail':
        sys.exit(1)
    if mode == 'bad':
        print('{"version": 2}')
        sys.exit(0)
    part = args[args.index('--part') + 1]
    print(json.dumps({'version': 1, 'part': part, 'items': [
        {'id': 'emaki', 'label': 'Emaki version', 'text': 'Emaki 0.8.0', 'default': True},
        {'id': 'kernel', 'label': 'Linux kernel', 'text': '7.2.7-arch1-1', 'default': True},
        {'id': 'hardware', 'label': 'Hardware', 'text': 'CPU: Example\nGPU: Example', 'default': False},
        {'id': 'logs', 'label': 'Recent logs of ' + part,
         'text': 'x' * 150000 if mode == 'huge' else 'line one\nline two', 'default': True},
    ], 'removed': {'user': 2, 'host': 1, 'email': 0, 'ip': 1, 'mac': 0, 'serial': 0, 'secret': 1}}))
elif args[:1] == ['url']:
    data = sys.stdin.buffer.read()
    try:
        parent = Path(f'/proc/{os.getppid()}/cmdline').read_bytes().split(b'\0')
    except OSError:
        parent = []
    with (profile / 'stdin.jsonl').open('a') as log:
        log.write(json.dumps({'stdin': len(data), 'argv_max': max(len(a.encode()) for a in sys.argv),
                              'parent_argv_max': max((len(a) for a in parent), default=0)}) + '\n')
    report = json.loads(data)
    what = args[args.index('--what') + 1]
    chosen = [i for i in args[args.index('--items') + 1].split(',') if i]
    body = '## What happened\n' + what + '\n' + ''.join(
        f'\n## {item["label"]}\n{item["text"]}\n' for item in report['items'] if item['id'] in chosen)
    url = ('https://github.com/emakish/emaki/issues/new?title=' + quote('Problem: ' + report['part'])
           + '&body=' + quote(body))
    if mode == 'big' or len(url.encode()) > LIMIT:
        print(json.dumps({'too_big': True, 'body': body}))
    elif mode == 'badurl':
        print(json.dumps({'url': 'javascript:alert(1)'}))
    else:
        print(json.dumps({'url': url}))
else:
    sys.exit(2)
