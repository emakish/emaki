// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later

use std::fs;
use std::io::Write;
use std::os::unix::fs::PermissionsExt;
use std::process::{Command, Stdio};

#[test]
fn keyboard_menu_edits_resets_and_undoes_through_core() {
    let root = std::env::temp_dir().join(format!("emaki-settings-menu-{}", std::process::id()));
    fs::create_dir_all(&root).unwrap();
    fs::set_permissions(&root, fs::Permissions::from_mode(0o700)).unwrap();
    for dir in ["config", "state", "runtime", "bin"] {
        fs::create_dir_all(root.join(dir)).unwrap();
        fs::set_permissions(root.join(dir), fs::Permissions::from_mode(0o700)).unwrap();
    }
    let niri = root.join("bin/niri");
    fs::write(
        &niri,
        "#!/bin/sh\nif [ \"$1\" = --version ]; then echo 'niri 26.04 (test)'; fi\nexit 0\n",
    )
    .unwrap();
    fs::set_permissions(&niri, fs::Permissions::from_mode(0o700)).unwrap();
    let command = || {
        let mut c = Command::new(env!("CARGO_BIN_EXE_emaki"));
        c.arg("settings")
            .env("XDG_CONFIG_HOME", root.join("config"))
            .env("XDG_STATE_HOME", root.join("state"))
            .env("XDG_RUNTIME_DIR", root.join("runtime"))
            .env(
                "PATH",
                format!(
                    "{}:{}",
                    root.join("bin").display(),
                    std::env::var("PATH").unwrap()
                ),
            )
            .env_remove("NIRI_SOCKET")
            .env_remove("DBUS_SESSION_BUS_ADDRESS");
        c
    };
    let menu = |keys: &str| {
        let mut child = command()
            .arg("--profile-root")
            .arg(&root)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .unwrap();
        child
            .stdin
            .take()
            .unwrap()
            .write_all(keys.as_bytes())
            .unwrap();
        let out = child.wait_with_output().unwrap();
        assert!(out.status.success(), "{out:?}");
        assert!(out.stderr.is_empty(), "{out:?}");
        String::from_utf8(out.stdout).unwrap()
    };
    let text = menu("bad\nk\nj\n1\ne\n:cancel\ne\n21\nh\nq\n");
    assert!(text.contains("Unknown command"));
    assert!(text.contains("appearance.gaps = 21"), "{text}");
    let out = command()
        .args(["history", "--json", "--profile-root"])
        .arg(&root)
        .output()
        .unwrap();
    let history: serde_json::Value = serde_json::from_slice(&out.stdout).unwrap();
    let id = history["history"].as_array().unwrap().last().unwrap()["id"]
        .as_str()
        .unwrap();
    let text = menu(&format!("u\n{id}\nq\n"));
    assert!(text.contains("Change ID to undo"));
    assert!(
        !fs::read_to_string(root.join("config/emaki/settings.toml"))
            .unwrap()
            .contains("gaps = 21")
    );
    let text = menu("e\n22\nr\nq\n");
    assert!(text.contains("appearance.gaps = 22"));
    assert!(
        !fs::read_to_string(root.join("config/emaki/settings.toml"))
            .unwrap()
            .contains("gaps = 22")
    );
    assert!(menu("").contains("keyboard menu"));
    assert!(menu("e\n").contains("value>"));
    assert!(menu("u\n\nq\n").contains("Change ID"));
    let out = command()
        .args(["--json", "--profile-root"])
        .arg(&root)
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(2));
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn terminal_keys_cancel_edit_and_restore_terminal_modes() {
    // Exercise a real terminal: pipe fixtures cannot prove canonical mode,
    // immediate Escape delivery, ANSI focus indication or mode restoration.
    let output = Command::new("python3")
        .args(["-c", r#"
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import pathlib
import select
import subprocess
import sys
import tempfile
import termios
import time

with tempfile.TemporaryDirectory(prefix='emaki-settings-pty-') as directory:
    root = pathlib.Path(directory)
    for name in ('config', 'state', 'runtime', 'bin'):
        (root / name).mkdir(mode=0o700)
    niri = root / 'bin/niri'
    niri.write_text('#!/bin/sh\n# Copyright (C) 2026 Artur Yakymenko\n# SPDX-License-Identifier: GPL-3.0-or-later\nif [ "$1" = --version ]; then echo "niri 26.04 (test)"; fi\nexit 0\n')
    niri.chmod(0o700)
    env = dict(os.environ, XDG_CONFIG_HOME=str(root / 'config'),
               XDG_STATE_HOME=str(root / 'state'), XDG_RUNTIME_DIR=str(root / 'runtime'),
               PATH=str(root / 'bin') + ':' + os.environ['PATH'])
    env.pop('NIRI_SOCKET', None)
    env.pop('DBUS_SESSION_BUS_ADDRESS', None)

    class Menu:
        def __init__(self, profile=root):
            self.master, self.slave = os.openpty()
            self.before = termios.tcgetattr(self.slave)
            self.pending = b''
            self.transcript = b''
            self.child = subprocess.Popen(
                [sys.argv[1], 'settings', '--profile-root', str(profile)],
                stdin=self.slave, stdout=self.slave, stderr=subprocess.PIPE, env=env)

        def expect(self, expected):
            deadline = time.monotonic() + 5
            while expected not in self.pending:
                assert time.monotonic() < deadline, (expected, self.transcript)
                if select.select([self.master], [], [], 0.05)[0]:
                    block = os.read(self.master, 65536)
                    self.pending += block
                    self.transcript += block
            end = self.pending.index(expected) + len(expected)
            self.pending = self.pending[end:]

        def send(self, data):
            os.write(self.master, data)

        def finish(self, code=0):
            actual = self.child.wait(timeout=5)
            assert actual == code, (actual, code, self.transcript)
            assert termios.tcgetattr(self.slave) == self.before, 'terminal mode changed'
            assert self.child.stderr.read() == b''

        def close(self):
            if self.child.poll() is None:
                self.child.kill()
                self.child.wait()
            self.child.stderr.close()
            os.close(self.master)
            os.close(self.slave)

    menu = Menu()
    try:
        menu.expect(b'\x1b[7m>  1. appearance.gaps')
        menu.expect(b'settings> ')
        active = termios.tcgetattr(menu.slave)
        assert not active[3] & (termios.ICANON | termios.ECHO | termios.ISIG)
        menu.send(b'\x1b[B')
        menu.expect(b'\x1b[7m>  2.')
        menu.expect(b'settings> ')
        menu.send(b'\x1b[A')
        menu.expect(b'\x1b[7m>  1.')
        menu.expect(b'settings> ')
        menu.send(b'\t')
        menu.expect(b'\x1b[7m>  2.')
        menu.expect(b'settings> ')
        menu.send(b'\x1b[Z')
        menu.expect(b'\x1b[7m>  1.')
        menu.expect(b'settings> ')
        menu.send(b'\r')
        menu.expect(b'value> ')
        menu.send(b'21')
        menu.expect(b'value> 21')
        menu.send(b'\x1b')
        menu.expect(b'settings> ')
        assert not (root / 'config/emaki/settings.toml').exists(), 'cancel wrote settings'
        history = subprocess.check_output(
            [sys.argv[1], 'settings', 'history', '--json', '--profile-root', str(root)], env=env)
        import json
        assert json.loads(history)['history'] == [], history
        menu.send(b'\r')
        menu.expect(b'value> ')
        # Edit 21 into 31 using the cursor and delete keys, then submit.
        menu.send(b'21\x1b[D\x1b[D\x1b[3~3\r')
        menu.expect(b'appearance.gaps = 31')
        menu.expect(b'settings> ')
        assert 'gaps = 31' in (root / 'config/emaki/settings.toml').read_text()
        menu.send(b'u')
        menu.expect(b'Change ID to undo (empty cancels)> ')
        menu.send(b'\x1b')
        menu.expect(b'settings> ')
        started = time.monotonic()
        menu.send(b'\x1b')
        menu.finish()
        assert time.monotonic() - started < 0.5, 'Escape required another key'
    finally:
        menu.close()

    for quit_key in (b'\x03', b'\x04'):
        menu = Menu()
        try:
            menu.expect(b'settings> ')
            menu.send(b'\r')
            menu.expect(b'value> ')
            menu.send(quit_key)
            menu.finish()
        finally:
            menu.close()

    invalid = root / 'not-a-directory'
    invalid.write_text('invalid profile')
    menu = Menu(invalid)
    try:
        menu.finish(1)
    finally:
        menu.close()
"#, env!("CARGO_BIN_EXE_emaki")])
        .output()
        .expect("python3 is required for terminal integration tests");
    assert!(
        output.status.success(),
        "{}\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
}
