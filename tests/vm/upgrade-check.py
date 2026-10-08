#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Upgrade acceptance T1/T2/T3 on a fresh old install.

A person on 0.1.0 or 0.1.1 has exactly one way to update: type `sudo pacman -Syu`, the password,
and press Enter at every question. This check does that and nothing else, by key presses into a
kitty window on the guest's real desktop (QEMU monitor sendkey), screenshots every state a person
sees, then reads the result over SSH.

  T1  mirrorlist switched to its own `testing` line (the documented way to test): on 0.1.x the
      old GitHub address, pacman downloads the candidate from the real github.com testing
      release; from 0.2.0 on https://pkgs.emaki.sh/testing/$arch (t1_via).
  T2  the candidate is the testing snapshot; how it is reached depends on the mirror (t2_via):
      the bridge (stable already serves the candidate after `publish.sh promote --first`):
          nothing pacman reads is changed; github.com is answered by tests/vm/old-address-proxy.py
          with the files GitHub stable will hold (one test CA file and one /etc/hosts line in
          the guest); the following sync goes to the real https://pkgs.emaki.sh/stable/x86_64.
      every later release (stable moves only through the stamped promote): the mirrorlist's
          only Server line becomes https://pkgs.emaki.sh/testing/$arch, the address the
          moved machines' mirrorlist offers for testing; pacman upgrades through the real
          pkgs.emaki.sh Worker and its signed database.
  T1 and T2 from 0.3.0 on (the mirrorlist only includes /etc/emaki/channel): the documented
      channel switch (docs/updates.md, Channels) is the only way such a machine follows testing,
      so both runs swap the commented Include in /etc/emaki/channel, leave the mirrorlist alone
      and type `sudo pacman -Syyu` for the first upgrade, as documented (via channel-testing).
  T3  nothing changed at all: the real GitHub stable after `publish.sh github`.
  --rehearsal  T2 of the bridge with pkgs.emaki.sh also answered locally from a bucket
      directory; never acceptance (publish.sh stamp refuses its results).

Base disk: a fresh install (target.qcow2 + OVMF_VARS.4m.fd in --base) from the test ISO of the
start version with tests/vm/fixtures/plan-upgrade.json (online update off, no repo_server, so
the packaged mirrorlist is untouched). The test ISOs test6 (0.1.0) and test-0.1.1 carry the same
Emaki packages and closure as the released ISOs (checked by sha256 on 2026-10-05).
Each run works on a copy of the base; the base is never written.

Writes <run dir>/result.json; `packaging/publish.sh stamp RESULT...` turns green results for
T1 and T2 from 0.1.0 and 0.1.1 at both sizes into the stamp that promotion needs. It has not
been run yet: the first run is a separate VM job.
"""
import argparse
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import socket_runtime
ROOT = HERE.parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


monitor = load('monitor', HERE / 'iso-monitor.py')
publish = load('publish', ROOT / 'packaging/mirror/publish.py')

GITHUB = 'https://github.com/emakish/packages/releases/download'
PKGS_TESTING = 'Server = https://pkgs.emaki.sh/testing/$arch'
# How each run reaches the candidate; `edited`: the run itself changed the mirrorlist.
VIAS = {'github-testing': {'edited': True}, 'old-address': {'edited': False},
        'pkgs-testing': {'edited': True}, 'pkgs-testing-swap': {'edited': True},
        'channel-testing': {'edited': True}, 'github-stable': {'edited': False}}
# From 0.3.0 on the mirrorlist only includes the channel selector, which includes one of these.
CHANNEL_INCLUDE = 'Include = /etc/emaki/channel'
SELECTOR = 'Include = /usr/share/emaki/mirrors/{}.conf'
GUESTFWD_IP = '10.0.2.100'
HTOP = 'https://archive.archlinux.org/packages/h/htop/htop-3.5.3-1-x86_64.pkg.tar.zst'
KDL_MARK = '// emaki-upgrade-check: a line the person added'
WALLET = ('emaki-upgrade-check', 'marker')

# Real Secret Service/portal calls, including the binary fixture used by the
# KWallet 6.30 persistence probe. A lookup alone cannot identify the provider.
WALLET_PROBE = r'''
import os
from pathlib import Path
import select
import sys
import time
from gi.repository import Gio, GLib
bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
name = 'org.freedesktop.secrets'
root = '/org/freedesktop/secrets'
service = 'org.freedesktop.Secret.Service'
app = 'org.emaki.UpgradeCheck'
raw = bytes(range(128, 192))
def call(destination, path, interface, method, signature=None, args=()):
    return bus.call_sync(destination, path, interface, method,
                         GLib.Variant(signature, args) if signature else None,
                         None, Gio.DBusCallFlags.NONE, 10000, None).unpack()
if sys.argv[1] == 'seed':
    session = call(name, root, service, 'OpenSession', '(sv)',
                   ('plain', GLib.Variant('s', '')))[1]
    collection = call(name, root, service, 'ReadAlias', '(s)', ('default',))[0]
    assert collection != '/', 'No default collection'
    props = {'org.freedesktop.Secret.Item.Label': GLib.Variant('s', 'Upgrade portal key'),
             'org.freedesktop.Secret.Item.Attributes': GLib.Variant('a{ss}', {
                 'app_id': app, 'xdg:schema': 'org.freedesktop.portal.Secret'})}
    item, prompt = call(name, collection, 'org.freedesktop.Secret.Collection',
                        'CreateItem', '(a{sv}(oayays)b)',
                        (props, (session, b'', raw, 'application/octet-stream'), False))
    assert prompt == '/', 'Portal fixture needs an unlocked collection'
    secret = call(name, item, 'org.freedesktop.Secret.Item', 'GetSecret', '(o)', (session,))[0]
    assert bytes(secret[2]) == raw, 'Portal fixture was not stored exactly'
    print('portal fixture stored: 64 bytes')
elif sys.argv[1] == 'owner':
    dbus = 'org.freedesktop.DBus'
    pid = call(dbus, '/org/freedesktop/DBus', dbus,
               'GetConnectionUnixProcessID', '(s)', (name,))[0]
    assert Path(os.readlink('/proc/' + str(pid) + '/exe')).name == 'ksecretd'
    owner = call(dbus, '/org/freedesktop/DBus', dbus, 'GetNameOwner', '(s)', (name,))[0]
    for other in ('org.kde.ksecretd', 'org.freedesktop.impl.portal.desktop.kwallet'):
        assert call(dbus, '/org/freedesktop/DBus', dbus, 'GetNameOwner', '(s)', (other,))[0] == owner
    print('ksecretd owns Secret Service and the Secret portal')
else:
    read_fd, write_fd = os.pipe()
    fds = Gio.UnixFDList.new()
    index = fds.append(write_fd)
    os.close(write_fd)
    reply, _ = bus.call_with_unix_fd_list_sync(
        'org.freedesktop.impl.portal.desktop.kwallet', '/org/freedesktop/portal/desktop',
        'org.freedesktop.impl.portal.Secret', 'RetrieveSecret',
        GLib.Variant('(osha{sv})', ('/org/freedesktop/portal/desktop/request/1_1/upgrade',
                                  app, index, {})),
        None, Gio.DBusCallFlags.NONE, 10000, fds, None)
    fds = None
    assert reply.unpack()[0] == 0, 'Portal request failed'
    actual = b''
    deadline = time.monotonic() + 10
    while True:
        remaining = deadline - time.monotonic()
        assert remaining > 0 and select.select([read_fd], [], [], remaining)[0], 'Portal key read timed out'
        part = os.read(read_fd, 4096)
        if not part:
            break
        actual += part
        assert len(actual) <= len(raw), 'Portal key has an unexpected length'
    os.close(read_fd)
    assert actual == raw, 'Portal key changed across the upgrade'
    print('portal key retained: 64 bytes')
'''


def wallet_probe(guest, mode):
    return guest.desktop('timeout 30 python3 -IB -c ' + shlex.quote(WALLET_PROBE)
                         + ' ' + shlex.quote(mode), check=False)
SIZES = {'1920x1080': (1920, 1080, 1), '2560x1600': (2560, 1600, 2)}
CHANNEL_SCREEN = ('cat /etc/pacman.d/emaki-mirrorlist /etc/emaki/channel\n'
                  'pacman-conf --repo emaki Server\n'
                  'emaki-update-channel\n')

# QEMU monitor `sendkey` names for every character this check types.
KEYS = {' ': 'spc', '-': 'minus', '_': 'shift-minus', '/': 'slash', '.': 'dot', ':': 'shift-semicolon',
        '=': 'equal', '$': 'shift-4', "'": 'apostrophe', '"': 'shift-apostrophe', ',': 'comma',
        '|': 'shift-backslash', '>': 'shift-dot', '~': 'shift-grave', '\n': 'ret'}


def sendkeys(text):
    """The monitor commands that type text on a US keyboard."""
    keys = []
    for char in text:
        if char.isascii() and (char.islower() or char.isdigit()):
            keys.append(char)
        elif char.isascii() and char.isupper():
            keys.append('shift-' + char.lower())
        elif char in KEYS:
            keys.append(KEYS[char])
        else:
            raise ValueError(f'no key for {char!r}')
    return ['sendkey ' + key for key in keys]


def fetch(url):
    """The object at url, or None when it does not exist (HTTP 404, or no such file:// path)."""
    try:
        # Cloudflare answers Python's default User-Agent with 403; publish.py names itself too.
        request = urllib.request.Request(url, headers={'User-Agent': 'emaki-publish'})
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise
    except urllib.error.URLError as error:
        if isinstance(error.reason, FileNotFoundError):
            return None
        raise


def snapshot_manifest(mirror, channel):
    """(snapshot id, MANIFEST bytes) the channel's pointer names, or (None, None)."""
    pointer = fetch(f'{mirror}/pointers/{channel}')
    if pointer is None:
        return None, None
    snap = pointer.decode().strip()
    manifest = fetch(f'{mirror}/snap/{channel}/{snap}/MANIFEST')
    if manifest is None:
        raise SystemExit(f'BAD: {mirror}/snap/{channel}/{snap}/MANIFEST is missing')
    return snap, manifest


def t1_via(start):
    """How T1 reaches the candidate: the start's packaged mirrorlist with its two Server lines
    swapped. 0.1.x name the old GitHub address; 0.2.0 and later name https://pkgs.emaki.sh, so
    their T1 never reads GitHub and its candidate is the testing snapshot itself."""
    version = start.removesuffix('-release').removesuffix('-full')
    return 'github-testing' if tuple(map(int, version.split('.'))) < (0, 2, 0) else 'pkgs-testing-swap'


def t2_via(mirror):
    """How T2 reaches the candidate in testing. For the bridge, `promote --first` has put it
    behind pkgs.emaki.sh/stable as well: T2 is the unedited old address (GitHub stable answered
    by old-address-proxy.py), whose upgraded mirrorlist then syncs pkgs.emaki.sh/stable. After
    the bridge, stable moves only through the stamped promote, so the candidate is not in
    stable while it is tested: T2 is the upgrade through https://pkgs.emaki.sh/testing."""
    _, testing = snapshot_manifest(mirror, 'testing')
    _, stable = snapshot_manifest(mirror, 'stable')
    if testing is None:
        raise SystemExit('BAD: testing serves nothing; publish the candidate to testing first')
    if stable is None:
        raise SystemExit('BAD: stable serves nothing; T2 of the bridge needs `publish.sh promote --first` first')
    return 'old-address' if stable == testing else 'pkgs-testing'


def selector_via(guest, via):
    """From 0.3.0 on the start's mirrorlist only includes /etc/emaki/channel: its T1 and T2 follow
    testing the one documented way, through the selector (channel-testing). Older starts keep via."""
    if via not in ('pkgs-testing-swap', 'pkgs-testing'):
        return via
    mirrorlist = guest.run('cat /etc/pacman.d/emaki-mirrorlist')
    active = [line.split('#', 1)[0].strip() for line in mirrorlist.splitlines() if line.split('#', 1)[0].strip()]
    return 'channel-testing' if active == [CHANNEL_INCLUDE] else via


def candidate(mirror, channel, github_db=None):
    """The candidate this run tests: the mirror snapshot's MANIFEST. The database pacman will read
    must be byte for byte that snapshot's emaki.db: the old address's copy when github_db is
    given, else the snapshot itself."""
    snap, manifest = snapshot_manifest(mirror, channel)
    if snap is None:
        raise SystemExit(f'BAD: {channel} serves nothing')
    entries = publish.parse_manifest(manifest)
    if github_db is None:
        served = fetch(f'{mirror}/snap/{channel}/{snap}/emaki.db') or b''
        if hashlib.sha256(served).hexdigest() != entries.get('emaki.db'):
            raise SystemExit(f'BAD: snap/{channel}/{snap}/emaki.db does not match its MANIFEST')
    else:
        served = github_db
        if hashlib.sha256(github_db).hexdigest() != entries.get('emaki.db'):
            raise SystemExit(f'BAD: the old address does not serve the database of {channel} {snap}; '
                             'run publish.sh github first')
    versions = {e['name']: e['version'] for e in publish.db_entries(served).values()}
    return {'snapshot': snap, 'manifest_sha256': hashlib.sha256(manifest).hexdigest(), 'versions': versions,
            'db_sha256': entries['emaki.db']}


class Guest:
    def __init__(self, vm, port, user, password, key):
        self.vm, self.user, self.password = vm, user, password
        # One multiplexed connection: the upgrade replaces openssh (0.1.1 → 0.2.0: 10.5p1 → 10.6p1), and
        # until sshd restarts every new connection is closed (2026-10-06); the master made before the
        # transaction keeps working. It dies with the restart, and the next command opens a new one.
        self.ssh = ['ssh', '-F', '/dev/null', '-p', str(port), '-i', str(key), '-o', 'BatchMode=yes',
                    '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=accept-new',
                    '-o', f'UserKnownHostsFile={vm / "known_hosts"}', '-o', 'ConnectTimeout=3',
                    '-o', 'ControlMaster=auto',
                    # A socket path must stay under 108 bytes; the run directory's can be longer.
                    '-o', f'ControlPath=/tmp/emaki-uc-{hashlib.sha256(str(vm).encode()).hexdigest()[:16]}',
                    '-o', 'ControlPersist=yes', '-o', 'ServerAliveInterval=5', '-o', 'ServerAliveCountMax=3',
                    f'{user}@127.0.0.1']

    def run(self, command, data=b'', root=False, check=True, timeout=900):
        if root:
            command = 'sudo -k -S -p "" -- sh -ec ' + shlex.quote(command)
            data = (self.password + '\n').encode() + data
        result = subprocess.run(self.ssh + [command], input=data, capture_output=True, timeout=timeout)
        if check and result.returncode:
            raise RuntimeError(f'{command}: {result.returncode}\n{result.stdout.decode()}\n{result.stderr.decode()}')
        return result.stdout.decode() if check else result

    def wait(self, seconds=300):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if subprocess.run(self.ssh + ['true'], capture_output=True).returncode == 0:
                return
            time.sleep(3)
        raise RuntimeError('SSH did not come up')

    SESSION = ("export XDG_RUNTIME_DIR=/run/user/$(id -u); export DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus; "
               "export NIRI_SOCKET=$(find $XDG_RUNTIME_DIR -maxdepth 1 -type s -name 'niri.*.sock' | head -1); "
               "export WAYLAND_DISPLAY=$(basename $(find $XDG_RUNTIME_DIR -maxdepth 1 -type s -name 'wayland-*' | head -1)); ")

    def desktop(self, command, check=True):
        return self.run('sh -ec ' + shlex.quote(self.SESSION + command), check=check)

    def login(self):
        self.run('cat > /tmp/upgrade-guest-login.py', (HERE / 'guest-login.py').read_bytes())
        self.run('python3 /tmp/upgrade-guest-login.py ' + self.user + ' niri-emaki-session',
                 self.password.encode(), root=True)
        for _ in range(60):
            if self.desktop('niri msg -j windows >/dev/null && echo ok', check=False).returncode == 0:
                return
            time.sleep(2)
        raise RuntimeError('the desktop did not start')

    def screenshot(self, name):
        self.run('cat > /tmp/upgrade-guest-shot.py', (HERE / 'iso-guest-shot.py').read_bytes())
        image = self.run('python3 /tmp/upgrade-guest-shot.py', root=True, check=False)
        if image.returncode == 0:
            (self.vm / f'{name}.png').write_bytes(image.stdout)
        else:
            monitor.command(self.vm, f'screendump {self.vm / (name + ".png")} -f png')
        path = self.vm / (name + '.png')
        print(f'SHOT: {path} (not judged); HUMAN REVIEW REQUIRED', flush=True)

    def type(self, text):
        for command in sendkeys(text):
            monitor.command(self.vm, command)
            time.sleep(0.04)

    def process(self, name):
        return self.run(f'pgrep -x {name} | head -1', check=False).stdout.decode().strip()

    def waiting_on_terminal(self, pid, stdin_only=True):
        """True while the process is blocked in read() (syscall 0 on x86_64): pacman reads its
        answers from fd 0, sudo reads the password from /dev/tty on another descriptor."""
        state = self.run(f'cat /proc/{pid}/syscall /proc/{pid}/wchan 2>/dev/null || true', root=True,
                         check=False).stdout.decode().split()
        if len(state) >= 2 and state[0] == '0':
            return state[1] == '0x0' or not stdin_only
        return bool(state) and state[-1] in ('n_tty_read', 'wait_woken') and len(state) < 2


def open_terminal(guest, size):
    width, height, scale = SIZES[size]
    if scale != 1:
        output = json.loads(guest.desktop('niri msg -j outputs'))
        guest.desktop(f'niri msg output {shlex.quote(next(iter(output)))} scale {scale}')
    before = {w['id'] for w in json.loads(guest.desktop('niri msg -j windows'))}
    guest.desktop('niri msg action spawn -- kitty')
    for _ in range(30):
        new = [w for w in json.loads(guest.desktop('niri msg -j windows')) if w['id'] not in before]
        if new:
            guest.desktop(f'niri msg action focus-window --id {new[0]["id"]}')
            time.sleep(1.5)
            return
        time.sleep(1)
    raise RuntimeError('kitty did not open')


def answer_sudo(guest):
    """Type the password only when sudo, not pacman, is the one waiting."""
    for _ in range(10):
        time.sleep(1)
        if guest.process('pacman'):
            return
        sudo = guest.process('sudo')
        if sudo and guest.waiting_on_terminal(sudo, stdin_only=False):
            guest.type(guest.password + '\n')
            return


def upgrade_in_terminal(guest, label, timeout=3600, command='sudo pacman -Syu'):
    """`sudo pacman -Syu`, the password, then Enter at every question and nothing else."""
    guest.type(command + '\n')
    answer_sudo(guest)
    deadline = time.monotonic() + timeout
    questions = 0
    while time.monotonic() < deadline:
        pid = guest.process('pacman')
        if not pid:
            time.sleep(2)
            if not guest.process('pacman'):
                break
            continue
        if guest.waiting_on_terminal(pid):
            questions += 1
            guest.screenshot(f'{label}-question-{questions}')  # B1: the list and the question
            monitor.command(guest.vm, 'sendkey ret')
            time.sleep(3)
        else:
            time.sleep(2)
    else:
        guest.screenshot(f'{label}-timeout')
        raise RuntimeError('pacman did not finish within the timeout')
    time.sleep(2)
    guest.screenshot(f'{label}-finished')  # B2
    return questions


def prepare_old_state(guest, via):
    """State an upgrade must not lose: a line in config.kdl, a package the person installed, a
    unit they disabled, an entry in their wallet."""
    guest.run(f"mkdir -p ~/.config/niri && touch ~/.config/niri/config.kdl && printf '%s\\n' {shlex.quote(KDL_MARK)} >> ~/.config/niri/config.kdl")
    guest.run(f'pacman -U --noconfirm {HTOP}', root=True)
    guest.run('systemctl disable bluetooth.service', root=True)
    # A fresh 0.1.1 asks gnome-keyring's prompter to unlock the login keyring and no window
    # appears (2026-10-06, kr-debug); timeout ends the request, and the prompt closes with it.
    stored = guest.desktop(f'printf %s upgrade-secret | timeout 30 secret-tool store --label=upgrade-check '
                           f'{WALLET[0]} {WALLET[1]}', check=False)
    if stored.returncode:
        guest.screenshot('wallet-store-failed')
    (guest.vm / 'wallet-setup.txt').write_text(f'exit {stored.returncode}\n{stored.stderr.decode()}')
    portal = wallet_probe(guest, 'seed')
    (guest.vm / 'portal-setup.txt').write_text(
        f'exit {portal.returncode}\n{portal.stdout.decode()}{portal.stderr.decode()}')
    if portal.returncode:
        # A fresh 0.1.x names a login collection in its default alias that was never created, so
        # neither secret-tool nor the portal seed can store anything: nothing can be lost there.
        missing = b'Object does not exist at path' in portal.stdout + portal.stderr
        if not (stored.returncode and missing):
            raise RuntimeError('The old portal key fixture could not be stored; migration acceptance cannot continue')
    if via in ('github-testing', 'pkgs-testing-swap'):
        # The documented way to follow testing: swap the two Server lines. 0.1.x end in
        # /stable (GitHub), 0.2.0 in /stable/$arch (pkgs.emaki.sh).
        guest.run(r"sed -i -e 's|^Server = \(.*\)/stable\(/\$arch\)\{0,1\}$|# Server = \1/stable\2|' "
                  r"-e 's|^# Server = \(.*\)/testing\(/\$arch\)\{0,1\}$|Server = \1/testing\2|' "
                  "/etc/pacman.d/emaki-mirrorlist", root=True)
    elif via == 'pkgs-testing':
        # After the bridge: the candidate is served by pkgs.emaki.sh/testing only.
        guest.run("sed -i -e 's|^Server = |# Server = |' /etc/pacman.d/emaki-mirrorlist && "
                  f"printf '%s\\n' {shlex.quote(PKGS_TESTING)} >> /etc/pacman.d/emaki-mirrorlist", root=True)
    elif via == 'channel-testing':
        # From 0.3.0 on (docs/updates.md, Channels): swap which Include of /etc/emaki/channel is
        # commented; the packaged mirrorlist, which only includes the selector, stays untouched.
        mirrorlist = guest.run('cat /etc/pacman.d/emaki-mirrorlist')
        guest.run(r"sed -i -e 's|^Include = /usr/share/emaki/mirrors/stable\.conf$|# &|' "
                  r"-e 's|^# \(Include = /usr/share/emaki/mirrors/testing\.conf\)$|\1|' /etc/emaki/channel",
                  root=True)
        selector = guest.run('cat /etc/emaki/channel')

        def active(text):
            return [line.split('#', 1)[0].strip() for line in text.splitlines() if line.split('#', 1)[0].strip()]

        if active(selector) != [SELECTOR.format('testing')]:
            raise RuntimeError(f'the channel selector was not switched to testing:\n{selector}')
        after = guest.run('cat /etc/pacman.d/emaki-mirrorlist')
        if after != mirrorlist or active(after) != [CHANNEL_INCLUDE]:
            raise RuntimeError(f'the mirrorlist changed or does not include the selector:\n{after}')
        return guest.run('cat /etc/pacman.d/emaki-mirrorlist /etc/emaki/channel; pacman -Q', root=False)
    if VIAS[via]['edited']:
        # A switch that matched nothing would upgrade from stable and only fail much later.
        mirrorlist = guest.run('cat /etc/pacman.d/emaki-mirrorlist')
        active = [line.split('#', 1)[0].strip() for line in mirrorlist.splitlines()
                  if line.split('#', 1)[0].strip()]
        if len(active) != 1 or not re.fullmatch(r'Server = \S+/testing(/\$arch)?', active[0]):
            raise RuntimeError(f'the mirrorlist was not switched to testing:\n{mirrorlist}')
    return guest.run('cat /etc/pacman.d/emaki-mirrorlist; pacman -Q', root=False)


def redirect_old_address(guest, certs):
    """T2 only: the guest trusts the test CA and resolves github.com to the guestfwd address."""
    guest.run('install -Dm644 /dev/stdin /etc/ca-certificates/trust-source/anchors/emaki-upgrade-check.crt',
              (certs / 'ca.crt').read_bytes(), root=True)
    guest.run('update-ca-trust', root=True)
    names = 'github.com pkgs.emaki.sh' if (certs / 'rehearsal').exists() else 'github.com'
    guest.run(f"printf '%s %s\\n' {GUESTFWD_IP} {shlex.quote(names)} >> /etc/hosts", root=True)


def check_start_version(guest, start, cand):
    installed = guest.run('pacman -Q emaki').split()
    expected = start.removesuffix('-release').removesuffix('-full')
    if len(installed) != 2 or installed[0] != 'emaki':
        raise RuntimeError('Cannot read the starting version [pacman].')
    version = installed[1]
    upstream = version.split(':', 1)[-1].rsplit('-', 1)[0]
    if upstream != expected:
        raise RuntimeError(f'Starting version differs [pacman]: expected {expected}, found {version}.')
    if version == cand['versions'].get('emaki'):
        raise RuntimeError('The starting version is already the candidate [pacman].')
    return version


def personal_config(guest):
    result = guest.run('cat ~/.config/niri/config.kdl', check=False)
    if result.returncode:
        raise RuntimeError('Cannot read the personal configuration [config.kdl].')
    return result.stdout


def checks_after_upgrade(guest, via, cand, before_log, start, before_version, before_config):
    result = {}

    def check(name, ok, detail=''):
        result[name] = {'ok': bool(ok), 'detail': str(detail)[-2000:]}
        print(('OK  ' if ok else 'BAD ') + name + (f': {detail}' if detail and not ok else ''), flush=True)

    check('Personal configuration unchanged after upgrade [config.kdl]',
          personal_config(guest) == before_config)
    installed = dict(line.split()[:2] for line in guest.run('pacman -Q').splitlines())
    check('emaki is the candidate', installed.get('emaki') == cand['versions'].get('emaki'),
          f'installed {installed.get("emaki")}, candidate {cand["versions"].get("emaki")}')
    log = guest.run('cat /var/log/pacman.log', root=True)
    tail = log[len(before_log):] if log.startswith(before_log) else ''
    check('Previous log retained [pacman]', log.startswith(before_log))
    transition = f"[ALPM] upgraded emaki ({before_version} -> {cand['versions'].get('emaki')})"
    upgraded = tail.find(transition)
    completed = tail.find('[ALPM] transaction completed', upgraded) if upgraded >= 0 else -1
    check('Candidate upgrade completed [pacman]', completed > upgraded >= 0, tail[-800:])
    # 0.1.0/0.1.1 keep Arch's portal: their [emaki] follows [extra], so pacman never offers the
    # fork's replaces=, and nothing requires the fork by name (tests/test-packaging.py,
    # PortalResolutionTests). Installs that already have the fork move to the candidate's.
    portals = ' '.join(f'{k} {v}' for k, v in installed.items() if 'portal' in k)
    if start in ('0.1.0', '0.1.1'):
        check("Arch's portal kept, fork not pulled in", 'xdg-desktop-portal-gnome' in installed
              and 'xdg-desktop-portal-gnome-emaki' not in installed, portals)
    elif 'xdg-desktop-portal-gnome-emaki' in cand['versions']:
        check('portal fork is the candidate', 'xdg-desktop-portal-gnome' not in installed
              and installed.get('xdg-desktop-portal-gnome-emaki') == cand['versions']['xdg-desktop-portal-gnome-emaki'],
              portals)
    expected = 'testing' if VIAS[via]['edited'] else 'stable'
    mirrorlist = guest.run('cat /etc/pacman.d/emaki-mirrorlist')
    selector = guest.run('cat /etc/emaki/channel')

    def entries(text):
        return [line.split('#', 1)[0].strip() for line in text.splitlines()
                if line.split('#', 1)[0].strip()]

    check('channel selector connected [pacman]', entries(mirrorlist) == ['Include = /etc/emaki/channel'],
          mirrorlist)
    check('selected channel preserved [update channel]',
          entries(selector) == [f'Include = /usr/share/emaki/mirrors/{expected}.conf'], selector)
    check('no pending mirror list [pacnew]',
          guest.run('test ! -e /etc/pacman.d/emaki-mirrorlist.pacnew', check=False).returncode == 0)
    servers = guest.run('pacman-conf --repo emaki Server').splitlines()
    check('effective server matches channel [pacman]',
          servers == [f'https://pkgs.emaki.sh/{expected}/x86_64'], '\n'.join(servers))
    channel = guest.run('emaki-update-channel').strip()
    check('reported channel matches server [update channel]', channel == expected, channel)
    # As root: 0.3.0 ships /etc/sudoers.d/10-emaki-wheel (0440 in a 0750 directory), which
    # pacman -Qk run by the person counts as a missing file (Permission denied).
    files = guest.run('pacman -Qk emaki-config', root=True, check=False)
    check('emaki-config files all present', files.returncode == 0, files.stdout.decode() + files.stderr.decode())
    return result


def checks_after_restart(guest, result, cand, before_config):
    def check(name, ok, detail=''):
        result[name] = {'ok': bool(ok), 'detail': str(detail)[-2000:]}
        print(('OK  ' if ok else 'BAD ') + name + (f': {detail}' if detail and not ok else ''), flush=True)

    check('shell running after restart', guest.desktop('systemctl --user is-active emaki-shell.service',
                                                       check=False).returncode == 0)
    check('Personal configuration unchanged after restart [config.kdl]',
          personal_config(guest) == before_config)
    check('htop kept', guest.run('pacman -Q htop', check=False).returncode == 0)
    check('bluetooth stays disabled', guest.run('systemctl is-enabled bluetooth.service', check=False)
          .stdout.decode().strip() == 'disabled')
    owner = wallet_probe(guest, 'owner')
    check('ksecretd owns Secret Service', owner.returncode == 0,
          owner.stdout.decode() + owner.stderr.decode())
    portal_setup = guest.vm / 'portal-setup.txt'
    if portal_setup.is_file() and portal_setup.read_text().startswith('exit 0\n'):
        portal = wallet_probe(guest, 'verify')
        check('portal master key kept', portal.returncode == 0,
              portal.stdout.decode() + portal.stderr.decode())
    elif portal_setup.is_file():
        # prepare_old_state continued only when the start version has no usable default collection.
        detail = portal_setup.read_text()
        result['portal not checked'] = {'ok': True, 'detail': f'the start version could not store a key: {detail}'}
        print(f'--  portal not checked: the start version could not store a key ({detail.splitlines()[0]})',
              flush=True)
    else:
        check('portal master key kept', False, 'No successful pre-upgrade portal fixture record')
    setup = (guest.vm / 'wallet-setup.txt').read_text()
    if setup.startswith('exit 0\n'):
        wallet = guest.desktop(f'timeout 30 secret-tool lookup {WALLET[0]} {WALLET[1]}', check=False)
        check('wallet entry kept', wallet.returncode == 0 and wallet.stdout.decode() == 'upgrade-secret',
              f'lookup: {wallet.stderr.decode()} / before the upgrade the store gave: {setup}')
    else:
        # Nothing was stored, so nothing can be lost; the start version's own defect stays on record.
        result['wallet not checked'] = {'ok': True, 'detail': f'the start version could not store a secret: {setup}'}
        print(f'--  wallet not checked: the start version could not store a secret ({setup.splitlines()[0]})',
              flush=True)
    second = guest.run('pacman -Syu --noconfirm', root=True, check=False)
    output = second.stdout.decode() + second.stderr.decode()
    check('second pacman -Syu: nothing to do', second.returncode == 0 and 'there is nothing to do' in output, output)
    synced = guest.run('sha256sum /var/lib/pacman/sync/emaki.db', check=False).stdout.decode().split()[:1]
    check('second sync reads the candidate database',
          bool(synced) and synced[0] == cand.get('db_sha256'), synced)


def qemu_command(vm, port, size, guestfwd):
    width, height, _ = SIZES[size]
    network = f'user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:{port}-:22'
    if guestfwd:
        network += f',guestfwd=tcp:{GUESTFWD_IP}:443-cmd:python3 {HERE / "tcp-relay.py"} 127.0.0.1 {guestfwd}'
    # The arguments of tests/vm/run-iso.sh --no-cd, plus guestfwd and the panel size.
    return ['qemu-system-x86_64', '-machine', 'q35', '-enable-kvm', '-cpu', 'host', '-smp', '6', '-m', '6G',
            '-drive', 'if=pflash,format=raw,readonly=on,file=/usr/share/edk2/x64/OVMF_CODE.4m.fd',
            '-drive', f'if=pflash,format=raw,file={vm / "OVMF_VARS.4m.fd"}',
            '-drive', f'file={vm / "target.qcow2"},if=none,id=target,format=qcow2,discard=unmap',
            '-device', 'virtio-blk-pci,drive=target,serial=emaki-target', '-boot', 'order=c',
            '-nic', network, '-device', f'virtio-vga-gl,max_outputs=1,xres={width},yres={height}',
            '-display', 'egl-headless,rendernode=/dev/dri/renderD128',
            '-monitor', f'unix:{socket_runtime.runtime(vm) / "mon.sock"},server=on,wait=off',
            '-serial', f'file:{vm / "serial.log"}', '-pidfile', str(vm / 'qemu.pid')]


def visual_evidence(vm):
    """Name and bind each unjudged capture, including partial failed runs."""
    frames = [{'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
               'judgment': 'NOT TESTED'} for path in sorted(vm.glob('*.png'))]
    return {'status': 'NOT TESTED', 'frames': frames,
            'reason': 'HUMAN REVIEW REQUIRED' if frames else 'No frames captured'}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    parser.add_argument('--run', required=True, choices=('T1', 'T2', 'T3'))
    parser.add_argument('--start', required=True, help='0.1.0, 0.1.1, 0.1.2-release, 0.1.2-full, 0.2.0 or 0.3.0')
    parser.add_argument('--base', required=True, help='directory with target.qcow2 and OVMF_VARS.4m.fd of a fresh install')
    parser.add_argument('--vm-dir', default=os.environ.get('VMDIR'), help='this job\'s directory (default $VMDIR)')
    parser.add_argument('--ssh-port', type=int, default=2261)
    parser.add_argument('--size', choices=SIZES, default='1920x1080')
    parser.add_argument('--mirror', default='https://pkgs.emaki.sh',
                        help='where the candidate MANIFEST is read; file://BUCKET for a rehearsal')
    parser.add_argument('--github-dir', help='T2 of the bridge: files GitHub stable will hold')
    parser.add_argument('--rehearsal-bucket', help='T2 rehearsal of the bridge: also answer pkgs.emaki.sh from '
                                                   'this bucket')
    parser.add_argument('--fixture', default=str(HERE / 'fixtures/plan-upgrade.json'))
    parser.add_argument('--key', default=str(Path.home() / 'VMs/emaki-vm/id_vm'))
    parser.add_argument('--dry-run', action='store_true', help='check the candidate and print the plan; no VM')
    args = parser.parse_args()
    if args.ssh_port == 2222:
        raise SystemExit('BAD: port 2222 belongs to the build VM')
    rehearsal = bool(args.rehearsal_bucket)
    if rehearsal and args.run != 'T2':
        raise SystemExit('BAD: --rehearsal-bucket is a T2 rehearsal')
    if args.run == 'T1':
        via = t1_via(args.start)
    elif args.run == 'T3':
        via = 'github-stable'
    else:
        via = t2_via(args.mirror)
    if via == 'old-address' and not args.github_dir:
        raise SystemExit('BAD: T2 of the bridge needs --github-dir (publish.sh --github local:DIR github --tag '
                         'testing; DIR/testing)')
    if via == 'pkgs-testing' and (args.github_dir or rehearsal):
        raise SystemExit('BAD: stable does not serve the candidate, so this is a release after the bridge: '
                         'T2 upgrades through https://pkgs.emaki.sh/testing and takes no --github-dir or '
                         '--rehearsal-bucket (for the bridge itself, run publish.sh promote --first first)')

    if via == 'old-address':
        github_db = (Path(args.github_dir) / 'emaki.db').read_bytes()
    elif via in ('pkgs-testing', 'pkgs-testing-swap'):
        github_db = None
    else:
        request = urllib.request.Request(f'{GITHUB}/{"stable" if args.run == "T3" else "testing"}/emaki.db',
                                         headers={'User-Agent': 'emaki-publish'})
        with urllib.request.urlopen(request, timeout=60) as response:
            github_db = response.read()
    cand = candidate(args.mirror, 'stable' if args.run == 'T3' else 'testing', github_db)
    print(f'candidate: MANIFEST {cand["manifest_sha256"]}, emaki {cand["versions"].get("emaki")}', flush=True)
    fixture = json.loads(Path(args.fixture).read_text())
    user, password = fixture['user']['login'], fixture['user']['password']
    if args.dry_run:
        print(f'would run {args.run} ({via}) from {args.start} on a copy of {args.base}, size {args.size}; '
              f'type: {" ".join(sendkeys("sudo pacman -Syu"))} …', flush=True)
        return 0

    if not args.vm_dir:
        raise SystemExit('BAD: --vm-dir or VMDIR is required (the job\'s own VM directory)')
    vm = Path(args.vm_dir).resolve() / time.strftime(f'upgrade-{args.run}-{args.start}-{args.size}-%Y%m%d-%H%M%S')
    vm.mkdir(parents=True, mode=0o700)
    for name in ('target.qcow2', 'OVMF_VARS.4m.fd'):
        subprocess.run(['cp', '--reflink=auto', '--sparse=always', str(Path(args.base) / name), str(vm / name)],
                       check=True)
    key = vm / 'id_vm'
    shutil.copyfile(args.key, key)
    key.chmod(0o600)
    proxy = None
    proxy_port = None
    if via == 'old-address':
        certs = vm / 'certs'
        certs.mkdir()
        if rehearsal:
            (certs / 'rehearsal').touch()
        command = [sys.executable, str(HERE / 'old-address-proxy.py'), '--github-dir', args.github_dir,
                   '--certs', str(certs), '--port-file', str(vm / 'proxy-port'), '--log', str(vm / 'proxy.log')]
        if rehearsal:
            command += ['--pkgs-root', args.rehearsal_bucket]
        proxy = subprocess.Popen(command, stdout=subprocess.DEVNULL)
        for _ in range(50):
            if (vm / 'proxy-port').exists():
                break
            time.sleep(0.2)
        proxy_port = int((vm / 'proxy-port').read_text())
    log = (vm / 'qemu.log').open('w')
    qemu = subprocess.Popen(qemu_command(vm, args.ssh_port, args.size, proxy_port), stdout=log,
                            stderr=subprocess.STDOUT)
    guest = Guest(vm, args.ssh_port, user, password, key)
    result = {'run': args.run, 'start': args.start, 'size': args.size, 'rehearsal': rehearsal, 'via': via,
              'manifest_sha256': cand['manifest_sha256'], 'snapshot': cand['snapshot'], 'passed': False,
              'evidence': str(vm)}
    try:
        guest.wait()
        guest.login()
        before_version = check_start_version(guest, args.start, cand)
        result['start_package_version'] = before_version
        via = result['via'] = selector_via(guest, via)
        before = prepare_old_state(guest, via)
        (vm / 'before.txt').write_text(before)
        before_config = personal_config(guest)
        (vm / 'config-before.kdl').write_bytes(before_config)
        if via == 'old-address':
            redirect_old_address(guest, vm / 'certs')
        before_log = guest.run('cat /var/log/pacman.log', root=True)
        (vm / 'pacman-before.log').write_text(before_log)
        guest.screenshot('desktop-before')
        open_terminal(guest, args.size)
        # After a selector switch the documented first update is -Syyu: the two channels'
        # databases are not in time order.
        result['questions'] = upgrade_in_terminal(
            guest, 'upgrade', command='sudo pacman -Syyu' if via == 'channel-testing' else 'sudo pacman -Syu')
        checks = checks_after_upgrade(guest, via, cand, before_log, args.start, before_version, before_config)
        guest.type(CHANNEL_SCREEN)
        time.sleep(1)
        guest.screenshot('mirrorlist')  # B3 / B5
        upgrade_in_terminal(guest, 'second')
        guest.run('systemctl reboot', root=True, check=False)
        time.sleep(10)
        guest.wait()
        guest.login()
        time.sleep(8)
        guest.screenshot('desktop-after-restart')  # B4
        open_terminal(guest, args.size)
        guest.type('pacman -Q emaki\n')
        time.sleep(1)
        guest.screenshot('version-after-restart')
        checks_after_restart(guest, checks, cand, before_config)
        result['checks'] = checks
        result['passed'] = all(c['ok'] for c in checks.values())
        result['arch'] = dict(line.split()[:2] for line in guest.run('pacman -Q qt6-base niri pacman',
                                                                       check=False).stdout.decode().splitlines())
    except Exception as error:  # the run is red, with the reason recorded
        result['error'] = f'{type(error).__name__}: {error}'
        print(f'BAD: {result["error"]}', flush=True)
    finally:
        result['finished'] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')
        result['visual_assessment'] = visual_evidence(vm)
        (vm / 'result.json').write_text(json.dumps(result, indent=1, sort_keys=True) + '\n')
        if qemu.poll() is None:
            try:
                monitor.command(vm, 'quit')
            except (OSError, RuntimeError):
                qemu.kill()
        qemu.wait(timeout=60)
        if proxy:
            proxy.terminate()
        log.close()
    for frame in result['visual_assessment']['frames']:
        print(f'HUMAN REVIEW REQUIRED: {frame["path"]}', flush=True)
    print(('FUNCTIONAL PASS' if result['passed'] else 'FAIL') + f': {vm / "result.json"}; '
          'visual assessment NOT TESTED (see frame list above)', flush=True)
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    os.umask(0o077)
    sys.exit(main())
