#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stall one parent publication rename, kill a disposable QEMU VM, inspect its FAT."""
import argparse
import json
import os
from pathlib import Path
import shlex
import select
import signal
import socket
import subprocess
import time


def wait_qemu_exit(pidfd, timeout=30):
    # A zombie leader can still have live worker threads holding the image lock.
    # A process pidfd becomes readable only after the whole thread group exits.
    poller = select.poll()
    poller.register(pidfd, select.POLLIN)
    events = poller.poll(round(timeout * 1000))
    if not any(event & select.POLLIN for _, event in events):
        raise RuntimeError('QEMU still owns the image')


def convert_after_exit(pidfd, disk, raw):
    wait_qemu_exit(pidfd)
    subprocess.run(['qemu-img', 'convert', '-f', 'qcow2', '-O', 'raw',
                    str(disk), str(raw)], check=True)


def trace_command(args):
    return ['env', 'PYTHONDONTWRITEBYTECODE=1', 'strace', '-e', 'trace=rename', '-e',
            f'inject=rename:delay_{args.phase}=120s:when={args.rename}',
            '-o', args.guest_out.rstrip('/') + '/strace.log',
            'python3', '-B', args.guest_helper, '--disposable-vm', '--out', args.guest_out,
            '--rename', str(args.rename), 'stall']


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--out', type=Path, required=True)
parser.add_argument('--disk', type=Path, required=True, help='Disposable qcow2 image; VM must have no other writers')
parser.add_argument('--qemu-pid', type=int, required=True)
parser.add_argument('--qmp', type=Path, required=True)
parser.add_argument('--vm-name', required=True, help='Expected QEMU -name value')
parser.add_argument('--ssh', required=True, help='JSON argv array, e.g. ["ssh","-p","2222","root@127.0.0.1"]')
parser.add_argument('--guest-helper', required=True)
parser.add_argument('--guest-out', required=True)
parser.add_argument('--rename', type=int, required=True, help='Positive rename number from the guest trace action')
parser.add_argument('--phase', choices=['enter', 'exit'], default='enter',
                    help='Cut before or after the selected rename executes')
args = parser.parse_args()
assert args.rename > 0, 'The rename number must be positive'
out = args.out.resolve()
assert '/.cache/evidence/' in str(out) and not str(out).startswith('/tmp/')
assert '/.cache/evidence/' in str(args.disk.resolve()), 'Use a disposable disk under .cache/evidence'
assert '/.cache/evidence/' in args.guest_out
out.mkdir(parents=True, exist_ok=True)
ssh = json.loads(args.ssh)
assert isinstance(ssh, list) and ssh and all(isinstance(item, str) for item in ssh)
qemu_pidfd = os.pidfd_open(args.qemu_pid)
cmdline = Path(f'/proc/{args.qemu_pid}/cmdline').read_bytes().split(b'\0')
assert b'qemu-system-' in cmdline[0], 'PID is not a QEMU system emulator'
assert any(str(args.disk.resolve()).encode() in item for item in cmdline), 'PID does not use the specified disposable disk'
# A name check through the supplied QMP endpoint catches accidental socket selection.
with socket.socket(socket.AF_UNIX) as connection:
    connection.settimeout(10)
    connection.connect(str(args.qmp))
    stream = connection.makefile('rwb', buffering=0)
    json.loads(stream.readline())
    for request in ({'execute': 'qmp_capabilities'}, {'execute': 'query-name'}):
        stream.write((json.dumps(request) + '\n').encode())
        while True:
            response = json.loads(stream.readline())
            if 'return' in response or 'error' in response:
                break
        assert 'error' not in response, response
    assert response['return']['name'] == args.vm_name, 'QMP VM name mismatch'


def remote(argv, check=True):
    return subprocess.run([*ssh, shlex.join(argv)], text=True, capture_output=True, check=check, timeout=15)


marker = args.guest_out.rstrip('/') + '/rename-ready.json'
remote(['mkdir', '-p', args.guest_out])
remote(['rm', '-f', marker])
command = trace_command(args)
with (out / 'guest-command.log').open('w') as logfile:
    traced = subprocess.Popen([*ssh, shlex.join(command)], stdout=logfile, stderr=subprocess.STDOUT)
    deadline = time.monotonic() + 180
    evidence = None
    try:
        while time.monotonic() < deadline:
            if traced.poll() is not None:
                raise RuntimeError('Refresh exited before requested rename; this stop point was not reached')
            observed = remote(['cat', marker], check=False)
            if observed.returncode == 0:
                try:
                    evidence = json.loads(observed.stdout)
                except json.JSONDecodeError:
                    time.sleep(0.1)
                    continue
                assert evidence['number'] == args.rename
                evidence['phase'] = args.phase
                # The wrapper writes the marker just before the rename. Require the tracee
                # to be stopped by ptrace during the injected delay, not merely marker creation.
                time.sleep(1)
                status = remote(['cat', f"/proc/{evidence['pid']}/status"]).stdout
                syscall = remote(['cat', f"/proc/{evidence['pid']}/syscall"]).stdout
                assert 't (tracing stop)' in status, 'Tracee is not in the injected delay'
                assert syscall.split()[0] == '82', 'Expected x86_64 rename syscall at the stop point'
                assert not any(line == 'TracerPid:\t0' for line in status.splitlines())
                (out / 'tracee-status.txt').write_text(status)
                (out / 'tracee-syscall.txt').write_text(syscall)
                trace = remote(['cat', args.guest_out.rstrip('/') + '/strace.log']).stdout
                (out / 'strace.log').write_text(trace)
                renames = remote(['cat', args.guest_out.rstrip('/') + '/publication-renames.json']).stdout
                (out / 'publication-renames.json').write_text(renames)
                (out / 'cut.json').write_text(json.dumps(evidence, indent=2) + '\n')
                signal.pidfd_send_signal(qemu_pidfd, signal.SIGKILL)
                break
            time.sleep(0.25)
        else:
            raise RuntimeError('Timed out before requested rename; VM was not killed')
    finally:
        if traced.poll() is None:
            traced.terminate()
        try:
            traced.wait(timeout=5)
        except subprocess.TimeoutExpired:
            traced.kill()
            traced.wait()
assert evidence is not None
raw = out / 'after-cut.raw'
try:
    convert_after_exit(qemu_pidfd, args.disk, raw)
finally:
    os.close(qemu_pidfd)
partition_result = subprocess.run(['sfdisk', '--json', str(raw)], text=True, capture_output=True, check=True)
(out / 'partitions.json').write_text(partition_result.stdout)
table = json.loads(partition_result.stdout)['partitiontable']
partitions = [p for p in table['partitions']
              if p.get('type', '').lower() == 'c12a7328-f81f-11d2-ba4b-00a0c93ec93b']
assert len(partitions) == 1, 'Expected exactly one EFI system partition'
partition = partitions[0]
sector = table.get('sectorsize', 512)
fat = out / 'esp-after-cut.img'
with raw.open('rb') as source, fat.open('wb') as destination:
    source.seek(partition['start'] * sector)
    remaining = partition['size'] * sector
    while remaining:
        block = source.read(min(remaining, 1024 * 1024))
        assert block, 'Truncated disk image'
        destination.write(block)
        remaining -= len(block)
result = subprocess.run(['fsck.fat', '-n', str(fat)], text=True, capture_output=True)
(out / 'fsck.fat.txt').write_text(result.stdout + result.stderr + f'\nexit={result.returncode}\n')
assert result.returncode in (0, 1), 'FAT inspection failed; read fsck.fat.txt'
print('Power cut and read-only FAT inspection captured. No bootability verdict yet.')
print('Preserve this disk. Boot separate copies through primary and fallback EFI paths; collect each result.')
print('On each booted copy run the guest recover action, reboot through that same path, then collect.')
