#!/usr/bin/env python3
"""Private test helper; no access to real niri or user directories."""
import json
import os
from pathlib import Path
import sys
import time

profile = Path(os.environ['EMAKI_SHELL_FIXTURE'])
if sys.argv[1:] == ['niri', 'watch', '--json']:
    (profile / 'core.pid').write_text(str(os.getpid()))
    parent = os.getppid()
    last = None
    while True:
        # Exit together with the shell under test: an orphaned fixture must not linger.
        if os.getppid() != parent:
            sys.exit(0)
        value = (profile / 'observation.json').read_text()
        if value != last:
            print(value, flush=True)
            last = value
        time.sleep(.025)
else:
    with (profile / 'actions.jsonl').open('a') as log:
        log.write(json.dumps(sys.argv[1:]) + '\n')
    print(json.dumps({'outcome': 'confirmed', 'reason': 'postcondition_satisfied'}))
