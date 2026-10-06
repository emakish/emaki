#!/usr/bin/env python3
"""Bounded argv-only operations. No shell, profiles, credentials or private logs."""
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

spec = importlib.util.spec_from_file_location('launcher_tools', Path(__file__).with_name('launcher-tools.py'))
shared = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shared)


def run(name, args):
    return shared.run([os.environ.get('EMAKI_' + name.upper().replace('-', '_'), name), *args], limit=16384).decode('utf-8', 'replace').strip()


UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
NMCLI_STATES = {3: 'timeout', 4: 'activation_failed', 5: 'deactivation_failed', 8: 'nm_not_running', 10: 'network_not_found', 65: 'password_required'}


def nmcli(args, timeout=30):
    """nmcli exit codes (man nmcli) become states; stderr is never returned to the shell."""
    argv = [os.environ.get('EMAKI_NMCLI', 'nmcli'), *args]
    try:
        done = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout, start_new_session=True, env=dict(os.environ, LC_ALL='C'))
    except subprocess.TimeoutExpired:
        raise shared.Refused('timeout')
    if done.returncode == 0:
        return done.stdout.decode('utf-8', 'replace')[:16384]
    if done.returncode == 4 and b'ecrets were required' in done.stderr:
        raise shared.Refused('wrong_password')
    raise shared.Refused(NMCLI_STATES.get(done.returncode, 'operation_failed'))


def vpn_list():
    rows = []
    for line in nmcli(['-t', '-f', 'NAME,TYPE,UUID,ACTIVE', 'connection', 'show'], timeout=8).splitlines():
        # Terse mode escapes ':' and '\' inside values with a backslash (man nmcli, --terse).
        parts = [p.replace('\\:', ':').replace('\\\\', '\\') for p in re.split(r'(?<!\\):', line)]
        if len(parts) != 4 or parts[1] not in ('vpn', 'wireguard') or not UUID.match(parts[2]): continue
        rows.append(dict(name=parts[0][:64], kind=parts[1], uuid=parts[2], active=parts[3] == 'yes'))
    return dict(state='ready', connections=rows[:20])


def vpn_set(r):
    uuid = r.get('uuid'); up = r.get('up')
    if not isinstance(uuid, str) or not UUID.match(uuid) or type(up) is not bool: return dict(state='invalid_request')
    nmcli(['--wait', '25', 'connection', 'up' if up else 'down', 'uuid', uuid])
    rows = vpn_list()['connections']
    row = next((v for v in rows if v['uuid'] == uuid), None)
    return dict(state='confirmed' if row and row['active'] == up else 'unconfirmed', connections=rows)


NM = 'org.freedesktop.NetworkManager'
NM_PATH = '/org/freedesktop/NetworkManager'
NM_DEVICE_TYPE_WIFI = 2  # nm-dbus-interface.h
NM_ACTIVE_ACTIVATED, NM_ACTIVE_DEACTIVATED = 2, 4  # NMActiveConnectionState
NM_REASON_NO_SECRETS = 9  # NMActiveConnectionStateReason: NM asked for secrets again = the PSK was rejected


def nm_call(bus, path, interface, method, args=None, timeout=10000):
    Gio, GLib = shared.gio()
    return bus.call_sync(NM, path, interface, method, args, None, Gio.DBusCallFlags.NONE, timeout, None)


def nm_property(bus, path, interface, name):
    _, GLib = shared.gio()
    return nm_call(bus, path, 'org.freedesktop.DBus.Properties', 'Get', GLib.Variant('(ss)', (interface, name)))[0]


def wifi_hidden(r):
    """Hidden network over the NetworkManager D-Bus API (AddAndActivateConnection):
    the password travels as a property inside the message, never as an argv of any process."""
    ssid = r.get('ssid'); password = r.get('password', '')
    if not isinstance(ssid, str) or not ssid.strip() or len(ssid.encode()) > 32 or '\n' in ssid: return dict(state='invalid_ssid')
    if not isinstance(password, str) or password and not 8 <= len(password) <= 63: return dict(state='invalid_password')
    Gio, GLib = shared.gio()
    ssid = ssid.strip()
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        devices = nm_property(bus, NM_PATH, NM, 'Devices')
        device = next((d for d in devices if nm_property(bus, d, NM + '.Device', 'DeviceType') == NM_DEVICE_TYPE_WIFI), None)
        if device is None: raise shared.Refused('no_wifi_device')
        settings = {
            'connection': {'type': GLib.Variant('s', '802-11-wireless'), 'id': GLib.Variant('s', ssid)},
            '802-11-wireless': {'ssid': GLib.Variant('ay', ssid.encode()), 'mode': GLib.Variant('s', 'infrastructure'), 'hidden': GLib.Variant('b', True)},
        }
        if password:
            settings['802-11-wireless-security'] = {'key-mgmt': GLib.Variant('s', 'wpa-psk'), 'psk': GLib.Variant('s', password)}
        # NM completes the rest (ipv4/ipv6 auto, interface) from the device; "/" = no specific AP (a hidden one is not listed).
        profile, active = nm_call(bus, NM_PATH, NM, 'AddAndActivateConnection', GLib.Variant('(a{sa{sv}}oo)', (settings, device, '/')), timeout=15000)
    except GLib.Error as e:
        name = Gio.dbus_error_get_remote_error(e) or ''
        if name in ('org.freedesktop.DBus.Error.ServiceUnknown', 'org.freedesktop.DBus.Error.NameHasNoOwner'): raise shared.Refused('nm_not_running')
        if name in ('org.freedesktop.DBus.Error.Timeout', 'org.freedesktop.DBus.Error.NoReply', 'org.freedesktop.DBus.Error.TimedOut'): raise shared.Refused('timeout')
        if not name: raise shared.Refused('nm_not_running')  # no bus at all (G_IO_ERROR while connecting)
        raise shared.Refused('activation_failed')
    outcome = {}
    loop = GLib.MainLoop()
    def on_state(_bus, _sender, _path, _iface, _signal, params):
        state, reason = params.unpack()
        if state == NM_ACTIVE_ACTIVATED: outcome['state'] = 'confirmed'
        elif state == NM_ACTIVE_DEACTIVATED: outcome['state'] = 'wrong_password' if reason == NM_REASON_NO_SECRETS else 'activation_failed'
        if outcome: loop.quit()
    sub = bus.signal_subscribe(NM, NM + '.Connection.Active', 'StateChanged', active, None, Gio.DBusSignalFlags.NONE, on_state)
    try:
        if nm_property(bus, active, NM + '.Connection.Active', 'State') == NM_ACTIVE_ACTIVATED: outcome['state'] = 'confirmed'
    except GLib.Error:
        pass
    if not outcome:
        GLib.timeout_add(30000, loop.quit)
        loop.run()
    bus.signal_unsubscribe(sub)
    state = outcome.get('state', 'timeout')
    if state != 'confirmed':
        # A rejected profile would otherwise stay in NM with its password; nmcli leaves it, this does not.
        try: nm_call(bus, profile, NM + '.Settings.Connection', 'Delete')
        except GLib.Error: pass
    return dict(state=state)


def portal_open():
    Gio, _ = shared.gio()
    handler = Gio.AppInfo.get_default_for_uri_scheme('http')
    if not handler: raise shared.Refused('no_handler')
    if not shared.app_scope.gio_launch(handler, uris=[PORTAL_URL]): raise shared.Refused('open_failed')
    return dict(state='requested')


PORTAL_URL = 'http://nmcheck.gnome.org/'


def lock(prepare_sleep=False):
    """Wait for compositor lock acknowledgement and full pour; process liveness is insufficient."""
    argv = [os.environ.get('EMAKI_LOCK', 'emaki-lock'), '--wait' if prepare_sleep else '--confirm']
    try:
        # The locker applies its own qs default; its supervisor and hyprlock
        # fallback must retain the session's allocator configuration.
        done = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=21, start_new_session=True,
                              env=shared.app_scope.application_environment())
    except subprocess.TimeoutExpired:
        return dict(state='lock_failed')
    return dict(state='locked' if done.returncode == 0 else 'lock_failed')


def brightness():
    line = run('brightnessctl', ['--class=backlight', '--machine-readable']).splitlines()[0]
    fields = line.split(',')
    percent = int(fields[3].rstrip('%'))
    if not 0 <= percent <= 100:
        raise ValueError()
    return dict(state='ready', percent=percent)


# The names emaki-sleep-guard writes: its policies, and end-session-failed when
# end-session could not end the session.
SLEEP_LOCK_POLICIES = ('sleep', 'sleep-relock', 'end-session', 'end-session-failed', 'stay-awake')


def sleep_lock_flags(session_start):
    """Take the flags emaki-sleep-guard leaves when it could not confirm the lock before
    sleep; each holds the guard's policy name and is read once, then deleted. The
    persistent flag (end-session) belongs to the next login, so only a starting shell
    takes it, and only when it runs in another session than the one the flag names (the
    guard records the boot and niri's socket): a shell restarted within the same session
    leaves it. A flag without them (an older guard) is taken at any start."""
    runtime = os.environ.get('XDG_RUNTIME_DIR')
    paths = [Path(runtime) / 'emaki-sleep-lock-failed'] if runtime else []
    persistent = None
    if session_start:
        state = os.environ.get('XDG_STATE_HOME') or os.path.join(os.environ.get('HOME', '/'), '.local/state')
        persistent = Path(state) / 'emaki' / 'sleep-lock-failed'
        paths.insert(0, persistent)
    try:
        boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    except OSError:
        boot = ''
    policies = []
    for path in paths:
        # Rename first: a flag is taken by one reader, and a new one written meanwhile stays.
        taken = path.with_name(f'.{path.name}.{os.getpid()}')
        try:
            os.rename(path, taken)
        except FileNotFoundError:
            continue
        try:
            descriptor = os.open(taken, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
            with os.fdopen(descriptor, 'rb') as flag:
                lines = flag.read(1024).decode('utf-8', 'replace').splitlines() or ['']
            if path == persistent:
                recorded = dict(line.split('=', 1) for line in lines[1:] if '=' in line)
                if recorded.get('niri') and recorded['niri'] == os.environ.get('NIRI_SOCKET') and recorded.get('boot') == boot:
                    # Put back (unless a newer flag arrived meanwhile) for the next session.
                    try:
                        os.link(taken, path)
                    except OSError:
                        pass
                    continue
            name = lines[0].strip()
        except OSError:
            name = ''
        finally:
            try:
                taken.unlink()
            except OSError:
                pass
        policies.append(name if name in SLEEP_LOCK_POLICIES else 'unknown')
    return dict(state='ready', policies=policies)


def can_hibernate():
    reply = json.loads(run('busctl', ['--system', '--json=short', 'call',
        'org.freedesktop.login1', '/org/freedesktop/login1',
        'org.freedesktop.login1.Manager', 'CanHibernate']))
    return reply.get('type') == 's' and reply.get('data') == ['yes']


def operation(r):
    op = r.get('op')
    if op == 'brightness-read': return brightness()
    if op == 'brightness-set':
        value = r.get('value')
        if type(value) is not int or not 0 <= value <= 100: return dict(state='invalid_brightness')
        run('brightnessctl', ['--class=backlight', 'set', f'{value}%'])
        result = brightness()
        result['state'] = 'confirmed' if abs(result['percent']-value) <= 1 else 'unconfirmed'
        return result
    if op == 'profiles-read':
        profiles = [line.strip().lstrip('*').strip()[:-1] for line in run('powerprofilesctl', ['list']).splitlines() if line.strip().endswith(':')]
        profiles = [p for p in profiles if p in ('power-saver', 'balanced', 'performance')]
        current = run('powerprofilesctl', ['get'])
        return dict(state='ready', profiles=profiles, current=current if current in profiles else '')
    if op == 'profile-set':
        value = r.get('value')
        if value not in ('power-saver', 'balanced', 'performance'): return dict(state='invalid_profile')
        run('powerprofilesctl', ['set', value])
        current = run('powerprofilesctl', ['get'])
        return dict(state='confirmed' if current == value else 'unconfirmed', current=current)
    if op == 'vpn-list': return vpn_list()
    if op == 'vpn-set': return vpn_set(r)
    if op == 'wifi-hidden': return wifi_hidden(r)
    if op == 'portal-open': return portal_open()
    if op == 'lock': return lock()
    if op == 'night-light-check':
        # Presence only; the shell starts wlsunset itself (long-lived, guarded by its stdin).
        return dict(state='ready', installed=shutil.which(os.environ.get('EMAKI_WLSUNSET') or 'wlsunset') is not None)
    if op == 'hibernate-read': return dict(state='ready', available=can_hibernate())
    if op == 'sleep-lock-flags':
        if type(r.get('session_start')) is not bool: return dict(state='invalid_request')
        return sleep_lock_flags(r['session_start'])
    if op == 'session':
        value = r.get('value')
        if value not in ('reboot', 'poweroff', 'suspend', 'logout', 'hibernate') or r.get('confirmed') is not True: return dict(state='confirmation_required')
        if value == 'hibernate' and not can_hibernate(): return dict(state='hibernate_unavailable')
        if value in ('suspend', 'hibernate'):
            # Never sleep before the compositor confirms the lock and the pour completes.
            locked = lock(prepare_sleep=True)
            if locked['state'] != 'locked': return locked
        if value == 'logout':
            run('niri', ['msg', 'action', 'quit', '--skip-confirmation'])
        else:
            run('systemctl', [value])
        return dict(state='requested')  # systemctl accepted; NOT proof that a session ended.
    return dict(state='unsupported')


def main():
    try:
        line = sys.stdin.buffer.readline(4097)
        if len(line) > 4096: raise ValueError()
        result = operation(json.loads(line))
    except FileNotFoundError: result = dict(state='helper_missing')
    except shared.Refused as e: result = dict(state=str(e))
    except (ValueError, IndexError, KeyError, TypeError, OSError): result = dict(state='unavailable')
    print(json.dumps(dict(schema_version=1, **result), separators=(',', ':')))


if __name__ == '__main__': main()
