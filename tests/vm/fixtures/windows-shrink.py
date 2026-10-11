#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise the current resize path only on the disposable Windows fixture.

Reads an alongside config from stdin. With --resize, shrink at the maximum
verified offer (NTFS minimum plus the Windows reserve, rounded up to MiB).
Without it, stop after proving a forced dry-run refusal caused no disk writes.
The target must have no unrelated writer during these checks.
"""
import argparse
import json
import os
from pathlib import Path
import runpy
import sys

from emaki_installer.arch_backend import resize_partition
from emaki_installer import inventory as alongside
from emaki_installer.errors import Code, InstallError
from emaki_installer.inventory import Inventory
from emaki_installer.planner import make_plan

fixture = runpy.run_path(str(Path(__file__).with_name('windows-refusals.py')))
ProbeRunner = fixture['ProbeRunner']


class ResizeRunner(ProbeRunner):
    def __init__(self, refuse=False):
        self.refuse = refuse
        self.saw_dry_run = False

    def run(self, argv, **kwargs):
        if argv[0] == 'ntfsresize' and '--no-action' in argv and '--size' in argv:
            self.saw_dry_run = True
            if self.refuse:
                # Exercise the backend's output validation before changed() or
                # the real ntfsresize invocation can write anything.
                return 'ERROR: forced read-only dry-run failure for preservation test.\n'
        return super().run(argv, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resize', action='store_true')
    args = parser.parse_args()
    fixture['guest_guard']()
    config = json.load(sys.stdin)
    assert config['mode'] == 'alongside' and config['disk_id'] == fixture['DISK']
    assert config['partition_id'] == fixture['NTFS']
    Path('/run/emaki-installer').mkdir(mode=0o700, exist_ok=True)
    inventory = Inventory(ProbeRunner()).probe()
    disk = next(d for d in inventory['disks'] if d['id'] == config['disk_id'])
    windows = next(p for p in disk['partitions'] if p['id'] == config['partition_id'])
    offer = windows['shrink']
    if args.resize:
        config['shrink_bytes'] = offer['max_free_bytes']
    plan = make_plan(config, inventory)
    os.sync()
    before = fixture['digest']()
    writes = fixture['write_counters']()
    changed = []
    runner = ResizeRunner(refuse=True)
    try:
        resize_partition(plan, runner, lambda: changed.append(True))
    except InstallError as error:
        assert error.code == Code.SHRINK_BOUNDS, error
    else:
        raise AssertionError('Forced dry-run failure was accepted')
    assert runner.saw_dry_run and not changed
    assert fixture['digest']() == before, 'Dry-run refusal changed sampled disk bytes'
    assert fixture['write_counters']() == writes, 'Dry-run refusal wrote to the target disk'
    print('PASS: dry-run refusal; whole-device write counters and sampled bytes unchanged.', file=sys.stderr)
    if args.resize:
        retained = windows['size_bytes'] - config['shrink_bytes']
        assert offer['min_bytes'] + alongside.WINDOWS_RESERVE <= retained
        assert retained < offer['min_bytes'] + alongside.WINDOWS_RESERVE + alongside.MIB
        runner = ResizeRunner()
        resize_partition(plan, runner, lambda: changed.append(True))
        assert runner.saw_dry_run and changed == [True]
        assert fixture['write_counters']() != writes
        print('PASS: real shrink at verified minimum plus Windows reserve.', file=sys.stderr)
    print(json.dumps({'shrink_bytes': config['shrink_bytes'], 'resized': args.resize}))


if __name__ == '__main__':
    main()
