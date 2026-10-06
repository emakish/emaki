#!/usr/bin/env python3
"""Cut a recorded install stream (emaki-install-cli --follow, NDJSON) into a transcript.

The job's own events are kept as recorded: every state, progress and terminal event, each
"installing <package>..." line of the package copy (one per package, what the copy bar
counts), and the last 100 log lines (what the window retains). Request ids and sequence
numbers are dropped; the mock worker numbers the frames again. The recording has no
hello, inventory or review: those rows are taken from erase-happy.json, and only the
confirmation's responses come from the recording.

Usage: cut-recording.py RECORDING.ndjson OUTPUT.json
"""
import json
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
EVENTS = ('state', 'progress', 'log', 'done', 'error', 'cancel_ack')
PACKAGE = re.compile(r'installing \S+\.\.\.')
RETAINED = 100


def cut(recording):
    frames = [json.loads(line) for line in Path(recording).read_text().splitlines() if line.strip()]
    events = [frame for frame in frames if frame.get('type') in EVENTS and frame.get('job_id')]
    job = events[0]['job_id']
    events = [frame for frame in events if frame['job_id'] == job]
    logs = [index for index, frame in enumerate(events) if frame['type'] == 'log']
    tail = set(logs[-RETAINED:])
    phase, kept = '', []
    for index, frame in enumerate(events):
        if frame['type'] in ('state', 'progress'):
            phase = frame['phase']
        if frame['type'] != 'log' or index in tail or (phase == 'copy_packages' and PACKAGE.fullmatch(frame['line'])):
            kept.append({key: value for key, value in frame.items() if key not in ('id', 'seq')})
    assert kept[-1]['type'] in ('done', 'error'), 'the recording does not end with the job'
    rows = json.loads((HERE / 'transcripts/erase-happy.json').read_text())
    assert [row['expect'] for row in rows] == ['hello', 'probe', 'plan', 'confirm']
    rows[3]['responses'] = [{'type': 'reply', 'ok': True, 'job_id': job}] + kept
    return rows


def main():
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    rows = cut(sys.argv[1])
    Path(sys.argv[2]).write_text(json.dumps(rows, indent=2, ensure_ascii=False) + '\n')
    print(f'{sys.argv[2]}: {len(rows[3]["responses"]) - 1} job events')


if __name__ == '__main__':
    main()
