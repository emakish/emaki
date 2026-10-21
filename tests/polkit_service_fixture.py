# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Load the production authorization service and surface on an isolated headless Wayland server."""
from pathlib import Path
import shutil
import socket
import subprocess
import time


def exercise_headless(root, profile, env, qs):
    """Load the real shell type graph even when IPC socket tests cannot start."""
    shutil.copyfile(root / 'shell/shell.qml', profile / 'qml/shell.qml')
    isolated = dict(env, EMAKI_SHELL_HEADLESS='1', EMAKI_SHELL_BORDER='invalid',
                    EMAKI_SHELL_TEST_WIDTH='1280', EMAKI_SHELL_TEST_HEIGHT='720',
                    EMAKI_SHELL_NOTIFICATIONS='0', EMAKI_SHELL_TRAY='0', EMAKI_BIN='')
    result = subprocess.run([qs, '-p', str(profile / 'qml'), '--no-color'],
                            env=isolated, capture_output=True, text=True, timeout=15)
    output = result.stdout + result.stderr
    assert result.returncode == 1 and 'choose EMAKI_SHELL_BORDER' in output, output
    assert 'No PanelWindow backend' not in output and 'Failed to load configuration' not in output, output
    print('Production headless shell type graph loaded without a PanelWindow backend')


def exercise_offscreen_service(root, profile, env, qs):
    """Instantiate the production service with no compositor; runs wherever qs runs.

    The type graph check above never creates PolkitPromptService (shell.qml keeps it behind a
    LazyLoader that headless mode leaves inactive), and the Wayland load below needs sway.
    """
    shutil.copyfile(root / 'tests/fixtures/polkit-service-offscreen.qml', profile / 'qml/shell.qml')
    result = subprocess.run([qs, '-p', str(profile / 'qml'), '--no-color'], env=env,
                            capture_output=True, text=True, timeout=30)
    output = result.stdout + result.stderr
    assert result.returncode == 0 and 'POLKIT_OFFSCREEN_OK' in output, output
    assert not any(value in output for value in (
        'POLKIT_OFFSCREEN_FAIL', 'ReferenceError', 'TypeError', 'Failed to load configuration')), output
    print('Production polkit service, native listener, adapter, controller and lock state loaded offscreen')


def exercise_service(root, profile, runtime, env, qs):
    """Use the caller's throwaway profile; never connect to a desktop or real bus."""
    try:
        with socket.socket(socket.AF_UNIX) as probe:
            probe.bind(str(Path(runtime) / 'service-probe.sock'))
    except PermissionError:
        print('BLOCKED: production polkit load requires Unix sockets')
        return False
    sway = shutil.which('sway')
    if not sway:
        print('BLOCKED: production polkit load requires sway with its headless backend')
        return False
    if Path('/run/emaki-sudo/broker.sock').exists():
        print('BLOCKED: production authorization load must not contact a host sudo broker')
        return False
    sudo_build = profile / 'sudo-build'
    build = subprocess.run(['make', 'build-sudo', 'SUDO_BUILD=' + str(sudo_build)], cwd=root, capture_output=True,
                           text=True, timeout=180)
    assert build.returncode == 0, build.stdout + build.stderr
    isolated = dict(env, QT_QPA_PLATFORM='wayland', WLR_BACKENDS='headless',
                    WLR_RENDERER='pixman', WLR_LIBINPUT_NO_DEVICES='1',
                    QML_IMPORT_PATH=str(sudo_build / 'qml') + ':' + str(root / '.cache/i18n-native/qml'),
                    DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(profile / 'no-session.sock'),
                    DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(profile / 'no-system.sock'))
    for key in ('DISPLAY', 'NIRI_SOCKET', 'WAYLAND_SOCKET', 'WAYLAND_DISPLAY', 'SWAYSOCK', 'SESSION_MANAGER'):
        isolated.pop(key, None)
    config = profile / 'sway-test.conf'
    config.write_text('output * mode 1280x720\n')
    compositor_log = profile / 'sway-output.log'
    command = [sway, '--config', str(config)]
    with compositor_log.open('w') as log:
        compositor = subprocess.Popen(command, env=isolated, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 15
            while not any(path.is_socket() for path in Path(runtime).glob('wayland-*')):
                assert compositor.poll() is None and time.monotonic() < deadline, (
                    compositor_log.read_text())
                time.sleep(.05)
            sockets = [path for path in Path(runtime).glob('wayland-*') if path.is_socket()]
            assert len(sockets) == 1, sockets
            isolated['WAYLAND_DISPLAY'] = sockets[0].name
            shutil.copyfile(root / 'tests/fixtures/polkit-service.qml', profile / 'qml/shell.qml')
            result = subprocess.run([qs, '-p', str(profile / 'qml'), '--no-color'],
                                    env=isolated, capture_output=True, text=True, timeout=30)
            output = result.stdout + result.stderr
            assert result.returncode == 0 and 'POLKIT_SERVICE_OK' in output, output
            assert not any(value in output for value in (
                'ERROR', 'FAIL!', 'ReferenceError', 'TypeError', 'unavailable', 'Failed to load configuration'
            )), output
            print('Production polkit and sudo services, surfaces and native adapters loaded on isolated Wayland')
            return True
        finally:
            compositor.terminate()
            try:
                compositor.wait(timeout=3)
            except subprocess.TimeoutExpired:
                compositor.kill()
                compositor.wait(timeout=3)
