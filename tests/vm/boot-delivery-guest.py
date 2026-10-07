#!/usr/bin/env -S python3 -B
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Destructive acceptance operations restricted to a disposable QEMU guest."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
import time

# Also protect direct interpreter invocation and every refresh subprocess.
sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'


def run(argv, *, check=True):
    result = subprocess.run(argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    with (OUT / 'commands.log').open('a') as log:
        log.write(f'$ {argv!r}\n{result.stdout}\nexit={result.returncode}\n')
    if check and result.returncode:
        raise RuntimeError(f'Command failed: {argv!r}; see commands.log')
    return result


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inventory(roots):
    result = {}
    for root in roots:
        for path in sorted(root.rglob('*')):
            if path.is_symlink():
                result[str(path)] = {'symlink': os.readlink(path)}
            elif path.is_file():
                result[str(path)] = {'bytes': path.stat().st_size, 'sha256': digest(path)}
            elif path.is_dir():
                result[str(path)] = {'directory': True}
    return result


def save(name, data):
    (OUT / name).write_text(json.dumps(data, indent=2) + '\n')


def state():
    return json.loads((ESP / 'EFI/Emaki/boot-state.json').read_text())


def boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def trial():
    result = run(['grub-editenv', str(ESP / 'EFI/Emaki/trial.env'), 'list'])
    return dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)


def scenario_prepare(name):
    assert not (OUT / f'{name}.json').exists(), 'Use a fresh evidence directory for each scenario'
    refresh()
    current = state()
    assert current['newest'] != current['good'], (
        'Install changed loader inputs before preparing a trial scenario')
    data = {'prepared_boot_id': boot_id(), 'candidate': current['newest'],
            'good': current['good'], 'fallback': digest(ESP / 'EFI/BOOT/BOOTX64.EFI'),
            'trial': trial(), 'boots': []}
    assert data['trial']['emaki_candidate'] == data['candidate']
    assert data['trial']['emaki_trial'] == '0', 'Fresh candidate did not receive two trials'
    save(f'{name}.json', data)
    return data


def scenario_boot(name):
    data = json.loads((OUT / f'{name}.json').read_text())
    current_id = boot_id()
    assert current_id != data['prepared_boot_id'], 'Reboot before collecting this scenario'
    assert all(row['boot_id'] != current_id for row in data['boots']), 'Boot already collected'
    kernel()
    run(['journalctl', '-b', '-u', 'emaki-boot-complete.service', '--no-pager'])
    row = {'boot_id': current_id, 'cmdline': Path('/proc/cmdline').read_text().strip(),
           'state': state(), 'trial': trial(),
           'fallback': digest(ESP / 'EFI/BOOT/BOOTX64.EFI')}
    data['boots'].append(row)
    # Save observations before assertions so a failed boot is still useful evidence.
    save(f'{name}.json', data)
    return data, row


def fallback_prepare():
    scenario_prepare('fallback-only')
    print('Cold boot only EFI/BOOT/BOOTX64.EFI, then run fallback-check; allow at most two boots.')


def fallback_check():
    data, row = scenario_boot('fallback-only')
    assert len(data['boots']) <= 2, 'Candidate was not promoted within two fallback boots'
    source = row['trial'].get('emaki_trial_source', '').lower().replace('\\', '/')
    promoted = row['state']['good'] == data['candidate']
    if promoted:
        assert '/efi/boot' in source, 'Trial did not record fallback firmware origin'
        assert re.search(r'(?:^|\s)emaki\.generation=' + data['candidate'] + r'(?=\s|$)', row['cmdline'])
        collect()
        print('PASS: fallback-only boot promoted the candidate; preserve firmware-path evidence too.')
    else:
        assert len(data['boots']) == 1, 'Second fallback boot still did not promote the candidate'
        assert row['fallback'] == data['fallback'], 'Unconfirmed fallback image changed'
        print('First fallback boot did not promote. Cold boot the same fallback path once more, then fallback-check.')


def corrupt_check():
    data, row = scenario_boot('corrupt')
    number = len(data['boots'])
    assert number <= 3, 'Corruption scenario requires exactly three cold boots'
    assert row['state']['good'] == data['good'], 'Broken candidate replaced the confirmed generation'
    assert row['fallback'] == data['fallback'], 'Broken candidate replaced the fallback image'
    assert re.search(r'(?:^|\s)emaki\.generation=' + data['good'] + r'(?=\s|$)', row['cmdline']), 'Old generation did not boot'
    assert row['trial']['emaki_candidate'] == data['candidate']
    assert row['trial']['emaki_trial'] == str(min(number, 2)), 'Trial counter did not stop at two attempts'
    source = row['trial'].get('emaki_trial_source', '').lower().replace('\\', '/')
    assert '/efi/boot' in source, 'Trial did not originate through the fallback firmware path'
    collect()
    if number == 3:
        print('PASS: two corrupt candidate trials, then old generation boot with no third trial.')
    else:
        print(f'Cold boot {number}/3 collected. Cold boot the fallback path again, then corrupt-check.')


def recover():
    kernel()
    save('recovery-before.json', inventory([Path('/boot/emaki'), ESP]))
    run(['journalctl', '-b', '-u', 'emaki-boot-complete.service', '--no-pager'])
    for number in range(1, 4):
        refresh()
        current = state()
        assert not (ESP / 'EFI/Emaki/boot-intent.json').exists(), 'Completed refresh left transaction intent'
        assert (Path('/boot/emaki') / current['newest']).is_dir(), 'Recovered state refers to a missing generation'
        assert (ESP / f"EFI/Emaki/loader-{current['newest']}.efi").is_file(), 'Recovered candidate image is missing'
        assert digest(ESP / 'EFI/BOOT/BOOTX64.EFI') == current['fallback_sha256']
        save(f'recovery-{number}.json', {'state': current, 'trial': trial(),
                                      'files': inventory([Path('/boot/emaki'), ESP])})
    print('Three refreshes succeeded after the interrupted publication. Reboot and collect to prove promotion.')


def kernel():
    cmdline = Path('/proc/cmdline').read_text().strip()
    release = os.uname().release
    assert (Path('/usr/lib/modules') / release).is_dir(), 'Running kernel has no installed modules'
    expected = OUT / 'expected-kernel'
    if expected.exists():
        assert release == expected.read_text().strip(), 'The upgraded kernel did not boot'
    save('kernel.json', {'cmdline': cmdline, 'uname': release,
                        'modules': sorted(p.name for p in Path('/usr/lib/modules').iterdir()),
                        'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip()})
    save('esp.json', inventory([ESP]))


def collect():
    kernel()
    cmdline = Path('/proc/cmdline').read_text().strip()
    release = os.uname().release
    tags = re.findall(r'(?:^|\s)emaki\.generation=([0-9a-f]{32})(?=\s|$)', cmdline)
    assert len(tags) == 1, 'Missing or ambiguous generation tag'
    current = state()
    assert current['good'] == tags[0], 'Boot completion did not mark this generation good'
    fallback = ESP / 'EFI/BOOT/BOOTX64.EFI'
    assert digest(fallback) == current['fallback_sha256'], 'Fallback promotion checksum mismatch'
    save('boot.json', {'cmdline': cmdline, 'uname': release, 'state': current,
                      'modules': sorted(p.name for p in Path('/usr/lib/modules').iterdir()),
                      'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip()})
    save('esp.json', inventory([ESP]))
    run(['findmnt', '--json'])
    run(['journalctl', '-b', '-u', 'emaki-boot-complete.service', '--no-pager'])
    run(['systemctl', 'is-active', 'multi-user.target'])


def refresh():
    run(['emaki-boot-refresh'])


def install(packages):
    assert packages, 'Pass candidate packages after the install action'
    run(['pacman', '-Q'])
    save('before-install-esp.json', inventory([ESP]))
    run(['pacman', '-U', '--noconfirm', *packages])
    assert state()['newest'], 'Candidate did not publish a generation'
    save('after-install-esp.json', inventory([ESP]))
    print('Candidate installed. Reboot this guest, then run collect with the same --out.')


def bounded():
    collect()
    initial = state()
    good = initial['good']
    fallback = digest(ESP / 'EFI/BOOT/BOOTX64.EFI')
    rows = []
    for number in range(1, 46):
        refresh()
        current = state()
        assert current['good'] == good, 'An unbooted refresh replaced the good generation'
        assert current['newest'] == initial['newest'], 'Unchanged loader inputs created a new candidate'
        assert digest(ESP / 'EFI/BOOT/BOOTX64.EFI') == fallback, 'Refresh overwrote fallback'
        directories = {p.name for p in Path('/boot/emaki').iterdir() if p.is_dir()}
        assert directories == {good, current['newest']}, f'Unexpected generations: {directories}'
        files = inventory([Path('/boot/emaki'), ESP])
        assert not any('.emaki-old-' in name for name in files), 'Rollback sidecars leaked'
        assert not any(re.search(r'/boot/emaki/[^/]+/(?:vmlinuz|initramfs)', name)
                       for name in files), 'Frozen kernel artifacts remain'
        rows.append({'refresh': number, 'state': current,
                     'bytes': sum(item.get('bytes', 0) for item in files.values())})
        save('bounded.json', rows)
    sizes = [row['bytes'] for row in rows]
    assert max(sizes) - min(sizes) < 1024 * 1024, 'Storage grew by at least 1 MiB'


def refusal(packages):
    collect()
    assert len(packages) == 1, 'Pass exactly one newer linux package'
    name, version = run(['pacman', '-Qp', packages[0]]).stdout.strip().split()
    assert name == 'linux', 'The upgrade fixture must be the linux package'
    old_version = run(['pacman', '-Q', 'linux']).stdout.strip().split()[1]
    assert int(run(['vercmp', version, old_version]).stdout.strip()) > 0, 'Kernel must be newer'
    baseline = inventory([ESP])
    module = Path('/usr/lib/grub/x86_64-efi/normal.mod')
    hidden = OUT / 'normal.mod.saved'
    assert not hidden.exists(), 'Previous interrupted refusal requires manual module restoration'
    shutil.copy2(module, hidden)
    module.unlink()
    try:
        # No cascading removals: a dependency conflict is a failed fixture, never forced.
        run(['pacman', '-R', '--noconfirm', 'linux-lts'])
        run(['pacman', '-U', '--noconfirm', packages[0]])
        result = run(['emaki-boot-refresh'], check=False)
        assert result.returncode != 0, 'Missing required module did not refuse the refresh'
        assert inventory([ESP]) == baseline, 'Refused refresh changed the ESP'
        releases = [p.parent.name for p in Path('/usr/lib/modules').glob('*/pkgbase')
                    if p.read_text().strip() == 'linux']
        assert len(releases) == 1, 'Cannot identify the installed linux kernel release'
        (OUT / 'expected-kernel').write_text(releases[0] + '\n')
        save('refusal-esp.json', inventory([ESP]))
    finally:
        shutil.copy2(hidden, module)
        hidden.unlink()
    print('Refused refresh captured after kernel upgrade. Reboot, then run collect with the same --out.')


def negative():
    before = inventory([Path('/boot'), ESP, Path('/etc/default')])
    save('negative-before.json', before)
    result = run(['emaki-boot-refresh', '--hook'], check=False)
    after = inventory([Path('/boot'), ESP, Path('/etc/default')])
    save('negative-after.json', after)
    assert result.returncode == 0, 'An expected refusal failed the package hook'
    assert before == after, 'Refusal changed boot files'
    lines = result.stdout.strip().splitlines()
    assert len(lines) == 1 and lines[0].strip(), 'Expected one plain refusal line'
    assert 'warning' not in result.stdout.lower(), 'Refusal included a warning'
    assert 'resolve' not in result.stdout.lower(), 'Refusal included repair instructions'


def corrupt():
    collect()
    data = scenario_prepare('corrupt')
    candidate = ESP / f"EFI/Emaki/loader-{data['candidate']}.efi"
    assert candidate.is_file(), 'Candidate EFI path is missing'
    candidate.write_bytes(b'corrupted EFI image\n')
    os.sync()
    assert digest(ESP / 'EFI/BOOT/BOOTX64.EFI') == data['fallback']
    print('Candidate corrupted. Cold boot EFI/BOOT/BOOTX64.EFI three times; run corrupt-check after each boot.')


def stall(number):
    # Observe the selected parent rename immediately before strace delays that syscall.
    # strace follows only this process: generator child renames are not publication stops.
    marker = OUT / 'rename-ready.json'
    marker.unlink(missing_ok=True)
    count = 0
    renames = []
    def wrap(function):
        def rename(source, target, *args, **kwargs):
            nonlocal count
            count += 1
            renames.append({'number': count, 'source': str(source), 'target': str(target)})
            save('publication-renames.json', renames)
            if count == number:
                save('rename-ready.json', {'number': number, 'source': str(source),
                     'target': str(target), 'time': time.time(), 'pid': os.getpid()})
            return function(source, target, *args, **kwargs)
        return rename
    os.replace = wrap(os.replace)
    os.rename = wrap(os.rename)
    sys.argv = ['/usr/bin/emaki-boot-refresh']
    runpy.run_path(sys.argv[0], run_name='__main__')


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--disposable-vm', action='store_true', required=True)
parser.add_argument('--out', type=Path, required=True)
parser.add_argument('--esp', type=Path, default=Path('/efi'))
parser.add_argument('--rename', type=int, help='Positive parent rename number for stall')
parser.add_argument('action', choices=['install', 'collect', 'kernel', 'bounded', 'refusal', 'negative',
                    'corrupt', 'corrupt-check', 'fallback-prepare', 'fallback-check', 'recover', 'trace', 'stall'])
parser.add_argument('packages', nargs='*')
args = parser.parse_args()
assert os.geteuid() == 0, 'Run inside the disposable guest as root'
virt = subprocess.run(['systemd-detect-virt', '--vm'], text=True, capture_output=True)
assert virt.returncode == 0 and virt.stdout.strip() in ('qemu', 'kvm'), 'Only disposable QEMU guests are supported'
OUT = args.out.resolve()
assert '/.cache/evidence/' in str(OUT) and not str(OUT).startswith('/tmp/'), 'Evidence belongs under a worktree .cache/evidence directory'
OUT.mkdir(parents=True, exist_ok=True)
ESP = args.esp.resolve()
if args.action == 'stall':
    assert args.rename and args.rename > 0, 'Positive --rename is required for stall'
    stall(args.rename)
elif args.action == 'trace':
    stall(None)
elif args.action in ('install', 'refusal'):
    globals()[args.action](args.packages)
else:
    globals()[args.action.replace('-', '_')]()
