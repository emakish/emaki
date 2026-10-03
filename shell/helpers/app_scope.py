"""Launch-only children leave the shell cgroup; protocol helpers stay with qs."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

WARNING = 'emaki: app scope unavailable; launching without cgroup isolation'
# Keep diagnostics separate from helpers' private JSON pipes and muted app output.
DIAGNOSTIC = os.dup(2)
SETUP_TIMEOUT = 1
LAUNCH_TIMEOUT = 5
WORKER_TIMEOUT = LAUNCH_TIMEOUT + 1
MALLOC_MARKER = 'EMAKI_SHELL_ORIGINAL_MALLOC_CONF'


def application_environment():
    """Undo only emaki-shell's allocator default before leaving the shell."""
    environment = dict(os.environ)
    original = environment.pop(MALLOC_MARKER, None)
    if original == '0':
        environment.pop('MALLOC_CONF', None)
    elif original is not None and original.startswith('1'):
        environment['MALLOC_CONF'] = original[1:]
    return environment


def unit_name(app_id):
    # Escape each component (including hyphens), preserving desktop-ID reversibility.
    raw = (app_id or 'application').encode()
    component = ''.join(chr(c) if c in b'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_:.'
                        else f'\\x{c:02x}' for c in raw)
    if component.startswith('.'):
        component = r'\x2e' + component[1:]
    if len(component) > 180:
        component = 'application'  # Bound the complete unit name to 255 bytes.
    return f'app-emaki-{component}-{uuid.uuid4().hex}.scope'


def run(app_id, request):
    """Wait for launch acceptance, never for the resulting application's lifetime."""
    payload = json.dumps(request).encode()
    environment = application_environment()
    with tempfile.TemporaryDirectory(prefix='app-') as directory:
        marker = Path(directory) / 'started'
        pending = Path(directory) / 'pending'
        pending.touch()
        worker = [sys.executable, '-B', str(Path(__file__).resolve()), str(marker)]
        command = ['systemd-run', '--user', '--scope', '--slice=app.slice',
                   '--unit=' + unit_name(app_id), '--quiet', '--collect',
                   '--property=BindsTo=graphical-session.target',
                   '--property=PartOf=graphical-session.target',
                   '--property=After=graphical-session.target',
                   '--expand-environment=no', '--', *worker]
        try:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, start_new_session=True, env=environment)
            try:
                process.communicate(payload, timeout=SETUP_TIMEOUT)
            except subprocess.TimeoutExpired:
                pending.unlink(missing_ok=True)
                if marker.exists():
                    # A request already handed to a launcher may still complete. Do
                    # not kill it, retry it, or tell the user that it failed.
                    try:
                        process.communicate(timeout=WORKER_TIMEOUT)
                    except subprocess.TimeoutExpired:
                        return 0  # Requested, not proof of an opened window.
                else:
                    process.kill()
                    process.wait()
            result = process.returncode
        except OSError:
            result = None
        # Revoke a slow scope's ticket before deciding to fall back. Atomic rename
        # in the worker either already claimed it or can no longer launch anything.
        pending.unlink(missing_ok=True)
        # The worker acknowledges before doing anything with the application. An app
        # error (or a timeout after acceptance) must not launch a second copy.
        if marker.exists():
            return result if result is not None and result >= 0 else 0
        os.write(DIAGNOSTIC, (WARNING + '\n').encode())
        process = subprocess.Popen([*worker[:-1], '-'], stdin=subprocess.PIPE,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   start_new_session=True, env=environment)
        try:
            process.communicate(payload, timeout=WORKER_TIMEOUT)
            return max(0, process.returncode)
        except subprocess.TimeoutExpired:
            return 0  # The fallback may still complete too; never offer a false retry.


def gio_launch(handler, *, files=None, uris=None, keyfile=None, app_id=None):
    request = dict(kind='gio', files=files, uris=uris)
    if keyfile is not None:
        request['keyfile'] = keyfile.to_data()[0]
    else:
        request['desktop'] = handler.get_filename()
    ident = app_id or handler.get_id() or 'application'
    if app_id is None and ident.endswith('.desktop'):
        ident = ident[:-8]
    return run(ident, request) == 0


def copy(argv, payload):
    # wl-copy forks the selection owner: it must inherit the app scope, not qs's unit.
    return run('wl-copy', dict(kind='exec', argv=argv,
                              input=base64.b64encode(payload).decode('ascii'))) == 0


def worker(marker):
    request = json.loads(sys.stdin.buffer.read())
    # Redirect before GIO/exec: applications must not keep a shell protocol pipe open.
    with open(os.devnull, 'r+') as sink:
        for fd in (0, 1, 2):
            os.dup2(sink.fileno(), fd)
    if marker != '-':
        try:
            Path(marker).with_name('pending').rename(marker)
        except FileNotFoundError:
            return 1
    if request['kind'] == 'exec':
        # Exit status is the launcher's error channel. All application output goes
        # to /dev/null; no pipe or deleted temporary file survives in its fd table.
        data = base64.b64decode(request['input']) if 'input' in request else None
        child = subprocess.Popen(request['argv'], stdin=subprocess.PIPE if data is not None else None)
        try:
            child.communicate(data, timeout=LAUNCH_TIMEOUT)
            return max(0, child.returncode)
        except subprocess.TimeoutExpired:
            return 0  # Already requested; leave the launcher alive to finish.
    import gi
    gi.require_version('Gio', '2.0')
    from gi.repository import Gio, GLib
    if 'keyfile' in request:
        key = GLib.KeyFile()
        key.load_from_data(request['keyfile'], len(request['keyfile'].encode()), GLib.KeyFileFlags.NONE)
        handler = Gio.DesktopAppInfo.new_from_keyfile(key)
    else:
        handler = Gio.DesktopAppInfo.new_from_filename(request['desktop'])
    if not handler:
        return 1
    uris = request['uris']
    if uris is None:
        uris = [Gio.File.new_for_path(p).get_uri() for p in request['files'] or []]
    loop = GLib.MainLoop()
    cancellable = Gio.Cancellable()
    outcome = []

    def completed(app, result):
        try:
            accepted = app.launch_uris_finish(result)
        except GLib.Error as error:
            # A cancelled/timed-out D-Bus call may already have reached its owner.
            accepted = (error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED)
                        or error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.TIMED_OUT)
                        or error.matches(Gio.dbus_error_quark(), Gio.DBusError.NO_REPLY)
                        or error.matches(Gio.dbus_error_quark(), Gio.DBusError.TIMEOUT))
        if not outcome:
            outcome.append(0 if accepted else 1)
        loop.quit()

    def expired():
        outcome.append(0)  # Requested; activation can still finish on the bus.
        cancellable.cancel()
        loop.quit()
        return GLib.SOURCE_REMOVE

    deadline = GLib.timeout_add(int(LAUNCH_TIMEOUT * 1000), expired)
    handler.launch_uris_async(uris, None, cancellable, completed)
    if not outcome:
        loop.run()
    if not cancellable.is_cancelled():
        GLib.source_remove(deadline)
    return outcome[0]


if __name__ == '__main__':
    if sys.argv[1] == '--exec':
        sys.exit(run(sys.argv[2], dict(kind='exec', argv=sys.argv[3:])))
    sys.exit(worker(sys.argv[1]))
