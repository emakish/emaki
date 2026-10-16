#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Login handoff through real wrappers and claim helper, with an offline compositor.

Only installed paths, root-owned tree validation and the compositor/Qt process are
fixture boundaries. The compositor's one-shot environment function is compiled
from the packaged patch, so dropping its delivery fails this regression.
"""
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from PIL import Image
import reaper
reaper.guard()

ROOT = Path(__file__).resolve().parents[1]
RENDER = runpy.run_path(str(ROOT / 'scripts/render-paths'))
TOKEN = 'a' * 32


def function(source, name):
    start = source.index('fn ' + name + '(')
    opening = source.index('{', start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


class LoginHandoff(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='login-handoff-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.bin = self.base / 'bin'
        self.bin.mkdir()
        self.data = self.base / 'data'
        self.helper = self.data / 'shell/helpers/session-start.py'
        self.helper.parent.mkdir(parents=True)
        self.runtime = self.base / 'runtime'
        self.runtime.mkdir(mode=0o700)
        images = self.base / 'images'
        images.mkdir(mode=0o711)
        images.chmod(0o711)
        for suffix in ('', '-plate'):
            path = images / (TOKEN + suffix + '.png')
            Image.new('RGB', (16, 16), '#446688').save(path)
            path.chmod(0o644)
        source = (ROOT / 'shell/helpers/session-start.py').read_text()
        source = source.replace('Path("/var/lib/emaki-greeter/handoff")', 'Path(' + repr(str(images)) + ')')
        source = source.replace("pwd.getpwnam('greeter').pw_uid", 'os.geteuid()')
        self.helper.write_text(source)
        values = RENDER['paths'](prefix=str(self.base), datadir=str(self.data),
            system={'PYTHON': sys.executable, 'TRUSTED_PATH': str(self.bin), 'QS': str(self.bin / 'qs')})
        (self.bin / 'paths').write_text(''.join(f'EMAKI_{k}={v!r}\n' for k, v in values.items()))
        for name in ('niri-emaki-session', 'emaki-shell-trusted', 'emaki-autostart',
                     'emaki-session-import-environment'):
            self.script(name, RENDER['substitute']((ROOT / 'scripts' / name).read_text(), values))
        for name in ('python3', 'readlink', 'timeout', 'env'):
            (self.bin / name).symlink_to(shutil.which(name))
        for name in ('emaki-session-files', 'emaki-shell-health', 'emaki-qt-check'):
            self.script(name, '#!' + sys.executable + '\n')
        self.script('emaki', '#!/bin/sh\nexit 1\n')
        activation = """import os,json,sys,subprocess
from pathlib import Path
args = sys.argv[1:]
manager = Path(os.environ['MANAGER'])
values = json.loads(manager.read_text()) if manager.exists() else {}
if Path(sys.argv[0]).name == 'dbus-update-activation-environment':
    updates = dict(os.environ) if '--all' in args else dict(
        arg.split('=', 1) for arg in args if '=' in arg)
    activation = Path(str(manager) + '.dbus')
    state = json.loads(activation.read_text()) if activation.exists() else {}
    state.update(updates)
    activation.write_text(json.dumps(state))
    # dbus-broker forwards activation updates to the user manager.
    values.update(updates)
elif 'is-active' in args:
    sys.exit(1)
elif args == ['--user', '--wait', 'start', 'niri-emaki.service']:
    Path(str(manager) + '.service').write_text(json.dumps(values))
    result = subprocess.run(['/bin/bash', os.environ['WRAPPER'], '--compositor'],
                            env=values, timeout=15)
    Path(str(manager) + '.status').write_text(str(result.returncode))
    result.check_returncode()
    sys.exit(0)
elif args[:2] == ['--user', 'import-environment']:
    values.update({key: os.environ[key] for key in args[2:]})
elif args[:2] == ['--user', 'unset-environment']:
    for key in args[2:]:
        values.pop(key, None)
elif args[:2] == ['--user', 'set-environment']:
    values.update(arg.split('=', 1) for arg in args[2:])
manager.write_text(json.dumps(values))
"""
        for name in ('systemctl', 'dbus-update-activation-environment'):
            self.script(name, '#!' + sys.executable + '\n' + activation)
        self.script('qs', '#!' + sys.executable + '\nimport json,os\nfrom pathlib import Path\n'
                    'Path(os.environ["RESULT"]).write_text(json.dumps(dict(os.environ)))\n')
        self.env = dict(PATH=str(self.bin), HOME=str(self.base),
                        XDG_CONFIG_HOME=str(self.base / "config"),
                        XDG_DATA_HOME=str(self.base / "user-data"),
                        XDG_STATE_HOME=str(self.base / "state"),
                        XDG_CACHE_HOME=str(self.base / "cache"),
                        XDG_RUNTIME_DIR=str(self.runtime), RESULT=str(self.base / 'result'),
                        SHELL='', WRAPPER=str(self.bin / 'niri-emaki-session'))
        for key in ('WAYLAND_DISPLAY', 'DISPLAY', 'WAYLAND_SOCKET', 'EMAKI_LOGIN_HANDOFF',
                    'EMAKI_SESSION_START', 'EMAKI_SESSION_TAKEOVER', 'EMAKI_STARTUP_COVER'):
            self.env.pop(key, None)
        # The private socket and immutable generation are compositor/install boundaries.
        # All preparation commands and shell-token remain real child processes.
        self.launcher = self.bin / 'emaki-shell'
        shell = self.base / 'emaki-shell-real'
        shell.write_text(RENDER['substitute']((ROOT / 'scripts/emaki-shell').read_text(), values))
        self.launcher.write_text('#!' + sys.executable + '\n' + '''import os,runpy
from pathlib import Path
from unittest.mock import Mock,patch
tree=Mock()
tree.select.return_value=Path(GENERATION)
original_run = runpy.run_path
original_stat = Path.lstat
def installed_metadata(path):
    fields = list(original_stat(path))
    fields[4] = 0
    fields[0] &= ~0o022
    return os.stat_result(fields)
def run_helper(path, **kwargs):
    if path == HELPER:
        module = original_run(path)
        with patch.dict(module['launch'].__globals__, bootstrap=lambda:tree):
            return module['launch']()
    return original_run(path, **kwargs)
with patch.object(Path,'lstat',installed_metadata), \
        patch.object(runpy,'run_path',run_helper):
    original_run(SHELL, run_name='__main__')
'''.replace('HELPER', repr(str(self.bin / 'emaki-shell-trusted')))
   .replace('GENERATION', repr(str(self.data))).replace('SHELL', repr(str(shell))))
        self.launcher.chmod(0o700)
        self.autostart = self.base / 'autostart.py'
        self.autostart.write_text("""import os,socket
server,client=socket.socketpair()
client.set_inheritable(True)
os.environ['WAYLAND_SOCKET']=str(client.fileno())
os.execv(PYTHON, [PYTHON, '-I', AUTOSTART, 'shell', LAUNCHER])
""".replace('PYTHON', repr(sys.executable))
   .replace('AUTOSTART', repr(str(self.bin / 'emaki-autostart')))
   .replace('LAUNCHER', repr(str(self.launcher))))

    def script(self, name, content):
        path = self.bin / name
        path.write_text(content)
        path.chmod(0o700)

    def marker(self, age=0):
        return json.dumps(dict(v=1, token=TOKEN, createdMs=time.time()*1000-age,
                               idleMode='lake', clock=4, introStart=1))

    def compile_compositor(self):
        patch = (ROOT / 'packaging/niri-emaki/0010-trusted-authentication-prompt.patch').read_text()
        cover_patch = (ROOT / 'packaging/niri-emaki/0002-keep-greeter-frame-and-cover-session-startup.patch').read_text()
        self.assertIn('env::remove_var("EMAKI_STARTUP_COVER")', cover_patch)
        self.assertIn('std::env::remove_var("EMAKI_SESSION_TAKEOVER")', cover_patch)
        source = '\n'.join(line[1:] for line in patch.splitlines() if line.startswith('+') and not line.startswith('+++'))
        delivery = function(source, 'configure_login_handoff') + '\n' + function(source, 'spawn_with_login_handoff')
        capture = function(source, 'take_login_handoff')
        # Pin integration as well as the extracted behavior: capture precedes
        # backend construction and ordinary spawns; only Supervisor owns it.
        main_patch = patch.split('diff --git a/src/main.rs b/src/main.rs', 1)[1].split('diff --git ', 1)[0]
        self.assertLess(main_patch.index('take_login_handoff();'), main_patch.index('startup_cover_path'))
        self.assertIn('start(&mut state, shell.command, login_handoff)', main_patch)
        self.assertLess(main_patch.index('start(&mut state, shell.command, login_handoff)'),
                        main_patch.index('spawn(cli.command, None)'))
        self.assertIn('login_handoff: Option<OsString>', source)
        self.assertIn('        login_handoff,', source)
        self.assertIn('spawn_with_login_handoff(&mut process, &mut self.login_handoff);', source)
        driver = '''use std::ffi::OsString;
use std::process::{Child, Command};
use std::io;
DELIVERY
CAPTURE
fn main() {
 std::fs::write(format!("{}/niri-marker", BASE), std::env::var("EMAKI_LOGIN_HANDOFF").unwrap_or_default()).unwrap();
 let mut handoff = take_login_handoff();
 if handoff.is_some() {
  assert_eq!(std::env::var("EMAKI_SESSION_TAKEOVER").as_deref(), Ok("1"));
 }
 std::env::remove_var("EMAKI_STARTUP_COVER");
 std::env::remove_var("EMAKI_SESSION_TAKEOVER");
 for label in ["first", "restart"] {
  let mut process = Command::new(PYTHON);
  process.args(["-I", LAUNCHER]);
  process.env("RESULT", format!("{}/{}", BASE, label));
  let mut child = spawn_with_login_handoff(&mut process, &mut handoff).unwrap();
  assert!(child.wait().unwrap().success());
 }
 let mut ordinary = Command::new(PYTHON);
 ordinary.args(["-I", PROBE]);
 ordinary.env("RESULT", format!("{}/client", BASE));
 assert!(ordinary.status().unwrap().success());
}
'''.replace('DELIVERY', delivery).replace('CAPTURE', capture).replace('PYTHON', json.dumps(sys.executable))
        driver = driver.replace('LAUNCHER', json.dumps(str(self.autostart))).replace('BASE', json.dumps(str(self.base)))
        driver = driver.replace('PROBE', json.dumps(str(self.bin / 'qs')))
        rust = self.base / 'compositor.rs'
        rust.write_text(driver)
        subprocess.run(['rustc', '--edition=2021', str(rust), '-o', str(self.bin / 'niri-emaki')],
                       check=True, capture_output=True, text=True, timeout=30)

    def run_login(self, marker):
        self.compile_compositor()
        env = dict(self.env, MANAGER=str(self.base / 'manager'))
        if marker is not None:
            env['EMAKI_LOGIN_HANDOFF'] = marker
        subprocess.run(['/bin/bash', str(self.bin / 'niri-emaki-session')],
                       env=env, check=True, timeout=20)
        self.assertEqual((self.base / 'manager.status').read_text(), '0')
        service = json.loads((self.base / 'manager.service').read_text())
        self.assertEqual(service.get('EMAKI_LOGIN_HANDOFF'), marker)
        self.assertEqual((self.base / 'niri-marker').read_text(), marker or '')
        self.assertNotIn('EMAKI_LOGIN_HANDOFF', json.loads((self.base / 'manager').read_text()))
        activation = json.loads((self.base / 'manager.dbus').read_text())
        self.assertNotIn('EMAKI_LOGIN_HANDOFF', activation)
        return [json.loads((self.base / name).read_text()) for name in ('first', 'restart', 'client')]

    def test_fresh_login_claims_once_and_never_reaches_later_client(self):
        first, restart, client = self.run_login(self.marker())
        state = json.loads((self.runtime / 'emaki-session-start' / (TOKEN + '.json')).read_text())
        self.assertTrue(state['compositorClaimed'])
        self.assertEqual(first.get('EMAKI_SESSION_START'), TOKEN)
        self.assertEqual(first.get('EMAKI_SESSION_SKIP_INTRO'), '1')
        self.assertTrue(state['shellClaimed'])
        manager = json.loads((self.base / 'manager').read_text())
        self.assertNotIn('EMAKI_LOGIN_HANDOFF', manager)
        activation = json.loads((self.base / 'manager.dbus').read_text())
        self.assertNotIn('EMAKI_LOGIN_HANDOFF', activation)
        for env in (first, restart, client):
            self.assertNotIn('EMAKI_LOGIN_HANDOFF', env)
            self.assertNotIn('EMAKI_STARTUP_COVER', env)
            self.assertNotIn('EMAKI_SESSION_TAKEOVER', env)
        for env in (restart, client):
            self.assertNotIn('EMAKI_SESSION_START', env)
            self.assertNotIn('EMAKI_SESSION_SKIP_INTRO', env)
        # Shell application launch snapshots must not retain the private marker.
        self.assertNotIn('EMAKI_LOGIN_HANDOFF', json.loads(first['EMAKI_SHELL_SESSION_ENV']))

    def test_claimed_login_cannot_be_replayed(self):
        marker = self.marker()
        env = dict(self.env, EMAKI_LOGIN_HANDOFF=marker)
        for operation in ('prepare', 'shell-token'):
            result = subprocess.run([sys.executable, '-I', str(self.helper), operation],
                                    env=env, check=True, capture_output=True, text=True, timeout=5)
            self.assertTrue(result.stdout.strip())
        for env in self.run_login(marker):
            self.assertNotIn('EMAKI_SESSION_START', env)

    def test_stale_login_never_claims(self):
        for env in self.run_login(self.marker(age=60000)):
            self.assertNotIn('EMAKI_SESSION_START', env)
        self.assertFalse((self.runtime / 'emaki-session-start' / (TOKEN + '.json')).exists())

    def test_unset_login_never_claims(self):
        for env in self.run_login(None):
            self.assertNotIn('EMAKI_SESSION_START', env)

    def test_empty_login_never_claims(self):
        for env in self.run_login(''):
            self.assertNotIn('EMAKI_SESSION_START', env)


if __name__ == '__main__':
    unittest.main()
