use serde_json::{Value, json};
use std::collections::BTreeMap;
use std::fs;
use std::os::unix::fs::{DirBuilderExt, PermissionsExt, symlink};
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant};

static SERIAL: AtomicU64 = AtomicU64::new(0);
// A write runs the validator three times. On a loaded machine (the suite itself runs
// tests in parallel) the product's 2 s default can run out before a validator that is
// not under test finishes; only the deadline test uses a short limit on purpose.
const SLOW_MACHINE_TIMEOUT_MS: &str = "10000";
// Waiting for a child process: generous, because these waits only end a stuck test.
const CHILD_DEADLINE: Duration = Duration::from_secs(60);
fn write_timeout(args: &[&str]) -> Vec<&'static str> {
    if matches!(args.first(), Some(&"set" | &"reset" | &"undo")) {
        vec!["--timeout-ms", SLOW_MACHINE_TIMEOUT_MS]
    } else {
        vec![]
    }
}
struct Fixture {
    root: PathBuf,
}
impl Fixture {
    fn new() -> Self {
        let root = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../.cache/tmp")
            .join(format!(
                "settings-{}-{}",
                std::process::id(),
                SERIAL.fetch_add(1, Ordering::Relaxed)
            ));
        fs::create_dir_all(&root).unwrap();
        fs::set_permissions(&root, fs::Permissions::from_mode(0o700)).unwrap();
        let root = root.canonicalize().unwrap();
        for dir in ["config", "state", "runtime", "bin"] {
            fs::DirBuilder::new()
                .mode(0o700)
                .create(root.join(dir))
                .unwrap();
        }
        Self { root }
    }
    fn command(&self) -> Command {
        let mut c = Command::new(env!("CARGO_BIN_EXE_emaki"));
        c.env("XDG_CONFIG_HOME", self.root.join("config"))
            .env("XDG_STATE_HOME", self.root.join("state"))
            .env("XDG_RUNTIME_DIR", self.root.join("runtime"))
            .env_remove("NIRI_SOCKET")
            .env_remove("DBUS_SESSION_BUS_ADDRESS");
        c
    }
    fn run(&self, args: &[&str]) -> (Output, Value) {
        let mut command = self.command();
        command.arg("settings").args(args).args([
            "--profile-root",
            self.root.to_str().unwrap(),
            "--json",
        ]);
        command.args(write_timeout(args));
        let out = command.output().unwrap();
        assert!(out.stderr.is_empty(), "{out:?}");
        let json = serde_json::from_slice(&out.stdout).unwrap_or_else(|_| panic!("{out:?}"));
        (out, json)
    }
    fn set(&self, key: &str, value: &str) -> Value {
        let (out, json) = self.run(&["set", key, value]);
        assert!(out.status.success(), "{json}");
        assert_eq!(json["session_applied"], false);
        json
    }
    fn source(&self) -> PathBuf {
        self.root.join("config/emaki/settings.toml")
    }
    fn manual(&self, contents: &str) {
        fs::create_dir_all(self.source().parent().unwrap()).unwrap();
        fs::write(self.source(), contents).unwrap();
    }
    fn fake(&self, mode: &str) -> Command {
        let script = format!(
            r#"#!/usr/bin/env python3
import sys, time
from pathlib import Path
mode = {mode:?}
if sys.argv[1:] == ['--version']:
    print('niri 26.05' if mode == 'version' else 'niri 26.04 (fake)')
    sys.exit(0)
assert sys.argv[1:3] == ['validate', '--config']
p = Path(sys.argv[3])
assert p.is_file()
assert p.name in ('niri.kdl', 'config.kdl')
if mode == 'timeout':
    time.sleep(5)
if mode == 'fragment' or (mode == 'combined' and p.name == 'config.kdl'):
    print('SECRET validator diagnostic', file=sys.stderr)
    sys.exit(1)
if mode == 'conflict':
    source = Path({source:?})
    source.write_text('schema_version = 1\n[appearance]\ngaps = 12\n')
if p.name == 'config.kdl':
    assert (p.parent/'package/default.kdl').is_file()
    assert (p.parent/'package/theme.kdl').is_file()
"#,
            source = self.source().to_str().unwrap()
        );
        let path = self.root.join("bin/niri");
        fs::write(&path, script).unwrap();
        fs::set_permissions(path, fs::Permissions::from_mode(0o700)).unwrap();
        let mut c = self.command();
        c.env(
            "PATH",
            format!(
                "{}:{}",
                self.root.join("bin").display(),
                std::env::var("PATH").unwrap()
            ),
        )
        .env("PYTHONDONTWRITEBYTECODE", "1");
        c
    }
    fn fake_set(&self, mode: &str) -> (Output, Value) {
        // The fake validator is a python script started three times; only the
        // "timeout" mode is about the deadline, the others must not race it.
        let timeout = if mode == "timeout" {
            "150"
        } else {
            SLOW_MACHINE_TIMEOUT_MS
        };
        let out = self
            .fake(mode)
            .args([
                "settings",
                "set",
                "appearance.gaps",
                "4",
                "--json",
                "--profile-root",
                self.root.to_str().unwrap(),
                "--timeout-ms",
                timeout,
            ])
            .output()
            .unwrap();
        let json = serde_json::from_slice(&out.stdout).unwrap();
        (out, json)
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.root).unwrap();
    }
}
fn tree(root: &Path) -> BTreeMap<PathBuf, Vec<u8>> {
    fn walk(root: &Path, path: &Path, result: &mut BTreeMap<PathBuf, Vec<u8>>) {
        for e in fs::read_dir(path).unwrap() {
            let p = e.unwrap().path();
            if p.is_dir() {
                result.insert(p.strip_prefix(root).unwrap().into(), vec![]);
                walk(root, &p, result);
            } else {
                result.insert(p.strip_prefix(root).unwrap().into(), fs::read(p).unwrap());
            }
        }
    }
    let mut result = BTreeMap::new();
    for dir in ["config", "state", "runtime"] {
        walk(root, &root.join(dir), &mut result);
    }
    result
}
fn row<'a>(view: &'a Value, key: &str) -> &'a Value {
    view["settings"]
        .as_array()
        .unwrap()
        .iter()
        .find(|v| v["key"] == key)
        .unwrap()
}

#[test]
fn isolated_settings_defaults_reads_and_xdg_guard_never_write() {
    let f = Fixture::new();
    let before = tree(&f.root);
    let (out, v) = f.run(&["list"]);
    assert!(out.status.success());
    let tokens: Value = serde_json::from_slice(
        &Command::new("python")
            .args([
                "-c",
                "import json,tomllib;print(json.dumps(tomllib.load(open('tokens.toml','rb'))))",
            ])
            .current_dir(Path::new(env!("CARGO_MANIFEST_DIR")).join("../.."))
            .output()
            .unwrap()
            .stdout,
    )
    .unwrap();
    assert_eq!(
        row(&v, "appearance.gaps")["value"],
        tokens["geometry"]["gaps"]
    );
    assert!(row(&v, "keybindings.toggle_window_floating")["value"].is_null());
    assert_eq!(v["generation_status"], "needs_generation");
    assert!(f.run(&["get", "appearance.gaps"]).0.status.success());
    let out = f
        .command()
        .env_remove("XDG_STATE_HOME")
        .args([
            "settings",
            "set",
            "appearance.gaps",
            "4",
            "--json",
            "--profile-root",
            f.root.to_str().unwrap(),
        ])
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(1));
    let result: Value = serde_json::from_slice(&out.stdout).unwrap();
    assert_eq!(result["reason"], "isolated_xdg_mismatch");
    assert_eq!(before, tree(&f.root));
}

#[test]
fn installed_settings_reads_create_no_files_and_preserve_personal_files() {
    let f = Fixture::new();
    let personal = f.root.join("config/niri/config.kdl");
    fs::create_dir_all(personal.parent().unwrap()).unwrap();
    fs::write(&personal, "// Personal configuration remains untouched.\n").unwrap();
    let personal_before = fs::read(&personal).unwrap();
    let before = tree(&f.root);
    for args in [
        vec!["settings", "list", "--json"],
        vec!["settings", "history", "--json"],
        vec!["settings", "get", "appearance.gaps", "--json"],
    ] {
        let out = f.command().args(args).output().unwrap();
        assert!(out.status.success(), "{out:?}");
        let result: Value = serde_json::from_slice(&out.stdout).unwrap();
        assert_eq!(result["session_applied"], false);
    }
    assert!(!f.source().exists());
    assert_eq!(fs::read(&personal).unwrap(), personal_before);
    assert_eq!(tree(&f.root), before);
    for args in [["settings", "list"], ["settings", "history"]] {
        let out = f.command().args(args).output().unwrap();
        assert!(out.status.success(), "{out:?}");
        assert!(
            !String::from_utf8(out.stdout)
                .unwrap()
                .contains("not connected")
        );
    }
    let help = f.command().args(["settings", "--help"]).output().unwrap();
    assert!(help.status.success());
    assert!(
        !String::from_utf8(help.stdout)
            .unwrap()
            .contains("not connected")
    );
}

#[test]
fn installed_settings_without_a_session_leave_the_source_absent() {
    let f = Fixture::new();
    let out = f
        .fake("ok")
        .args([
            "settings",
            "set",
            "appearance.gaps",
            "4",
            "--json",
            "--timeout-ms",
            SLOW_MACHINE_TIMEOUT_MS,
        ])
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(1));
    let result: Value = serde_json::from_slice(&out.stdout).unwrap();
    assert_eq!(result["session_applied"], false);
    assert_eq!(result["reason"], "niri_socket_missing", "{result}");
    assert!(!f.source().exists());
    assert!(
        !f.root
            .join("state/emaki/history/live-pending.json")
            .exists()
    );
    let runtime = f.root.join("runtime/emaki-settings");
    if runtime.exists() {
        assert_eq!(fs::read_dir(runtime).unwrap().count(), 0);
    }
}

#[test]
fn concurrent_installed_writers_preserve_both_values_and_history_across_processes() {
    let f = Fixture::new();
    let shell = f.root.join("bin/emaki-shell");
    fs::write(
        &shell,
        r#"#!/usr/bin/env python3
import json, sys, time
assert sys.argv[1:4] == ['call', 'settings', 'apply']
assert isinstance(json.loads(sys.argv[4])['rows'], list)
time.sleep(0.1)
print('applied')
"#,
    )
    .unwrap();
    fs::set_permissions(&shell, fs::Permissions::from_mode(0o700)).unwrap();
    let mut first_command = f.fake("ok");
    let mut second_command = f.command();
    second_command.env(
        "PATH",
        format!(
            "{}:{}",
            f.root.join("bin").display(),
            std::env::var("PATH").unwrap()
        ),
    );
    let mut children = vec![];
    for (command, key, value) in [
        (&mut first_command, "bar.autohide", "true"),
        (&mut second_command, "dock.on", "false"),
    ] {
        children.push(
            command
                .args([
                    "settings",
                    "set",
                    key,
                    value,
                    "--json",
                    "--timeout-ms",
                    SLOW_MACHINE_TIMEOUT_MS,
                ])
                .stdout(std::process::Stdio::piped())
                .stderr(std::process::Stdio::piped())
                .spawn()
                .unwrap(),
        );
    }
    for mut child in children {
        let deadline = Instant::now() + CHILD_DEADLINE;
        while child.try_wait().unwrap().is_none() {
            if Instant::now() >= deadline {
                let _ = child.kill();
                let _ = child.wait();
                panic!("concurrent settings writer did not exit");
            }
            std::thread::sleep(Duration::from_millis(5));
        }
        let out = child.wait_with_output().unwrap();
        assert!(out.status.success(), "{out:?}");
        let result: Value = serde_json::from_slice(&out.stdout).unwrap();
        assert_eq!(result["session_applied"], true, "{result}");
    }
    let out = f
        .command()
        .args(["settings", "list", "--json"])
        .output()
        .unwrap();
    assert!(out.status.success(), "{out:?}");
    let result: Value = serde_json::from_slice(&out.stdout).unwrap();
    assert_eq!(row(&result, "bar.autohide")["value"], true);
    assert_eq!(row(&result, "dock.on")["value"], false);
    let out = f
        .command()
        .args(["settings", "history", "--json"])
        .output()
        .unwrap();
    assert!(out.status.success(), "{out:?}");
    let result: Value = serde_json::from_slice(&out.stdout).unwrap();
    let history = result["history"].as_array().unwrap();
    assert_eq!(history.len(), 2, "{result}");
    let keys: std::collections::BTreeSet<_> = history
        .iter()
        .map(|entry| entry["changes"][0]["key"].as_str().unwrap())
        .collect();
    assert_eq!(keys, ["bar.autohide", "dock.on"].into_iter().collect());
}

fn installed_run(f: &Fixture, args: &[&str]) -> (Output, Value) {
    let out = f
        .fake("ok")
        .env("XDG_CURRENT_DESKTOP", "niri")
        .args(["settings"])
        .args(args)
        .arg("--json")
        .args(write_timeout(args))
        .output()
        .unwrap();
    let reply = serde_json::from_slice(&out.stdout).unwrap_or_else(|_| panic!("{out:?}"));
    (out, reply)
}
fn installed_helper(f: &Fixture, name: &str, body: &str) {
    let path = f.root.join("bin").join(name);
    fs::write(&path, format!("#!/usr/bin/env python3\n{body}")).unwrap();
    fs::set_permissions(path, fs::Permissions::from_mode(0o700)).unwrap();
}

#[test]
fn installed_mime_requires_the_session_desktop_before_writing_associations() {
    for desktop in ["", "other", "niri-other"] {
        let f = Fixture::new();
        installed_helper(&f, "emaki-terminal", "pass\n");
        installed_helper(&f, "gio", "print('Default application: test.desktop')\n");
        for key in ["defaults.browser", "defaults.files"] {
            let out = f
                .fake("ok")
                .env("XDG_CURRENT_DESKTOP", desktop)
                .args([
                    "settings",
                    "set",
                    key,
                    "test.desktop",
                    "--json",
                    "--timeout-ms",
                    SLOW_MACHINE_TIMEOUT_MS,
                ])
                .output()
                .unwrap();
            let reply: Value = serde_json::from_slice(&out.stdout).unwrap();
            assert!(!out.status.success(), "{desktop}: {reply}");
            assert_eq!(reply["reason"], "desktop_defaults_unavailable");
            assert!(!f.source().exists());
            assert!(!f.root.join("config/niri-mimeapps.list").exists());
            assert!(
                !f.root
                    .join("state/emaki/history/live-pending.json")
                    .exists()
            );
        }
    }
}

#[test]
fn installed_wallpaper_cannot_commit_without_the_compositor_reload() {
    let f = Fixture::new();
    let picture = f.root.join("picture.png");
    fs::write(&picture, b"image").unwrap();
    let calls = f.root.join("wallpaper-calls");
    installed_helper(
        &f,
        "emaki-settings-wallpaper",
        &format!(
            "import sys\nfrom pathlib import Path\np = Path({calls:?})\np.write_text((p.read_text() if p.exists() else '') + sys.argv[1] + '\\n')\nif sys.argv[1] == 'snapshot': print('{{}}')\n",
            calls = calls.to_str().unwrap(),
        ),
    );
    // An absent, short endpoint proves that image mode must contact the compositor;
    // accepting only the wallpaper helper would incorrectly commit this change.
    let missing = format!(
        "/tmp/emaki-missing-{}-{}",
        std::process::id(),
        SERIAL.fetch_add(1, Ordering::Relaxed)
    );
    let out = f
        .fake("ok")
        .env("NIRI_SOCKET", &missing)
        .args([
            "settings",
            "set",
            "appearance.wallpaper",
            picture.to_str().unwrap(),
            "--json",
            "--timeout-ms",
            SLOW_MACHINE_TIMEOUT_MS,
        ])
        .output()
        .unwrap();
    let reply: Value = serde_json::from_slice(&out.stdout).unwrap();
    assert!(!out.status.success(), "{reply}");
    assert_eq!(reply["session_applied"], false);
    assert!(!f.source().exists());
    assert!(
        !fs::read_to_string(calls)
            .unwrap()
            .lines()
            .any(|op| op == "apply")
    );
}

fn assert_no_transaction_candidate(f: &Fixture) {
    assert!(!f.root.join("state/emaki/history/pending.json").exists());
    for relative in tree(&f.root).keys() {
        assert!(
            !relative.to_string_lossy().ends_with(".tmp"),
            "{relative:?}"
        );
    }
}

#[test]
fn failed_shell_compensation_does_not_block_reads_or_a_later_retry() {
    let f = Fixture::new();
    installed_helper(&f, "emaki-shell", "print('applied')\n");
    let (out, reply) = installed_run(&f, &["set", "dock.on", "false"]);
    assert!(out.status.success(), "{reply}");
    let source = fs::read(f.source()).unwrap();
    let history = installed_run(&f, &["history"]).1["history"].clone();
    installed_helper(&f, "emaki-shell", "import sys\nsys.exit(1)\n");
    let (out, reply) = installed_run(&f, &["set", "bar.autohide", "true"]);
    assert!(!out.status.success(), "{reply}");
    assert_eq!(fs::read(f.source()).unwrap(), source);
    assert_no_transaction_candidate(&f);
    let pending = f.root.join("state/emaki/history/live-pending.json");
    assert!(pending.exists());
    for args in [vec!["list"], vec!["get", "dock.on"], vec!["history"]] {
        let (out, reply) = installed_run(&f, &args);
        assert!(out.status.success(), "{reply}");
        assert_eq!(reply["session_applied"], false);
    }
    assert_eq!(installed_run(&f, &["history"]).1["history"], history);
    assert!(
        pending.exists(),
        "An absent session must retain compensation intent"
    );
    installed_helper(&f, "emaki-shell", "print('applied')\n");
    let out = f
        .fake("ok")
        .env("WAYLAND_DISPLAY", "settings-test-session")
        .args([
            "settings",
            "set",
            "bar.autohide",
            "true",
            "--json",
            "--timeout-ms",
            SLOW_MACHINE_TIMEOUT_MS,
        ])
        .output()
        .unwrap();
    let reply: Value = serde_json::from_slice(&out.stdout).unwrap();
    assert!(out.status.success(), "{reply}");
    assert_eq!(row(&reply, "dock.on")["value"], false);
    assert_eq!(row(&reply, "bar.autohide")["value"], true);
    assert!(!pending.exists());
    assert_no_transaction_candidate(&f);
}

#[test]
fn session_start_after_failed_apply_preserves_the_persons_later_edit() {
    let f = Fixture::new();
    installed_helper(&f, "emaki-shell", "print('applied')\n");
    let (out, reply) = installed_run(&f, &["set", "dock.on", "false"]);
    assert!(out.status.success(), "{reply}");
    installed_helper(&f, "emaki-shell", "import sys\nsys.exit(1)\n");
    let (out, reply) = installed_run(&f, &["set", "bar.autohide", "true"]);
    assert!(!out.status.success(), "{reply}");
    let personal = "schema_version = 1\n[appearance]\ngaps = 12\n[dock]\non = false\n";
    f.manual(personal);
    let out = f
        .fake("ok")
        .args(["settings", "session-config"])
        .output()
        .unwrap();
    assert!(out.status.success(), "{out:?}");
    assert_eq!(fs::read_to_string(f.source()).unwrap(), personal);
    assert!(
        !out.stderr.is_empty(),
        "The preserved hand edit needs a recovery notice"
    );
    let wrapper = PathBuf::from(String::from_utf8(out.stdout).unwrap().trim());
    assert!(
        fs::read_to_string(wrapper)
            .unwrap()
            .contains("settings-manual.kdl")
    );
    assert!(
        fs::read_to_string(f.root.join("runtime/emaki-settings/settings-manual.kdl"))
            .unwrap()
            .contains("gaps 12")
    );
    assert_no_transaction_candidate(&f);
    let (out, reply) = installed_run(&f, &["list"]);
    assert!(out.status.success(), "{reply}");
    assert_eq!(row(&reply, "appearance.gaps")["value"], 12);
    assert_eq!(row(&reply, "dock.on")["value"], false);
    let again = f
        .fake("ok")
        .args(["settings", "session-config"])
        .output()
        .unwrap();
    assert!(again.status.success(), "{again:?}");
    assert!(
        again.stderr.is_empty(),
        "Recovery notice must not repeat: {again:?}"
    );
}

#[test]
fn installed_shell_keys_apply_and_restore_previous_rows_on_unconfirmed_reply() {
    for (key, value, changed) in [
        ("bar.autohide", "true", "false"),
        ("bar.overview_workspaces", "false", "true"),
        ("dock.on", "false", "true"),
        ("dock.auto_hide", "false", "true"),
    ] {
        let f = Fixture::new();
        installed_helper(&f, "emaki-shell", "print('applied')\n");
        let (out, reply) = installed_run(&f, &["set", key, value]);
        assert!(out.status.success(), "{reply}");
        assert_eq!(reply["session_applied"], true);
        let before = fs::read(f.source()).unwrap();
        let history = installed_run(&f, &["history"]).1["history"].clone();
        let calls = f.root.join("shell-calls");
        installed_helper(
            &f,
            "emaki-shell",
            &format!(
                r#"import json, sys
from pathlib import Path
p = Path({calls:?})
rows = json.loads(sys.argv[4])['rows']
previous = p.read_text() if p.exists() else ''
p.write_text(previous + json.dumps(rows) + '\n')
print('applied' if previous else 'unconfirmed')
"#,
                calls = calls.to_str().unwrap()
            ),
        );
        let (out, reply) = installed_run(&f, &["set", key, changed]);
        assert!(!out.status.success(), "{reply}");
        assert_eq!(reply["reason"], "shell_apply_unconfirmed");
        assert_eq!(reply["session_applied"], false);
        assert_eq!(fs::read(f.source()).unwrap(), before);
        assert_eq!(installed_run(&f, &["history"]).1["history"], history);
        let calls: Vec<Value> = fs::read_to_string(calls)
            .unwrap()
            .lines()
            .map(|line| serde_json::from_str(line).unwrap())
            .collect();
        assert_eq!(calls.len(), 2);
        for (rows, expected) in [(&calls[0], changed == "true"), (&calls[1], value == "true")] {
            let applied = rows
                .as_array()
                .unwrap()
                .iter()
                .find(|row| row["key"] == key)
                .unwrap();
            assert_eq!(applied["value"], expected);
        }
    }
}

#[test]
fn installed_application_defaults_publish_owned_mime_and_reject_unavailable_apps() {
    let f = Fixture::new();
    installed_helper(
        &f,
        "emaki-terminal",
        "import sys\nassert sys.argv[1] in ('--check', '--check-terminal')\nsys.exit(1 if sys.argv[2] == 'missing.desktop' else 0)\n",
    );
    installed_helper(
        &f,
        "gio",
        "import sys\nassert sys.argv[1] == 'mime'\nprint('Default application: test.desktop')\n",
    );
    for key in ["defaults.terminal", "defaults.browser", "defaults.files"] {
        let (out, reply) = installed_run(&f, &["set", key, "test.desktop"]);
        assert!(out.status.success(), "{reply}");
        assert_eq!(reply["session_applied"], true);
    }
    let mime = f.root.join("state/emaki/defaults/mimeapps.list");
    let contents = fs::read_to_string(&mime).unwrap();
    assert!(contents.starts_with("# Emaki managed defaults;"));
    for association in [
        "x-scheme-handler/http=test.desktop",
        "x-scheme-handler/https=test.desktop",
        "text/html=test.desktop",
        "inode/directory=test.desktop",
    ] {
        assert!(contents.contains(association), "{contents}");
    }
    let before = fs::read(f.source()).unwrap();
    let history = installed_run(&f, &["history"]).1["history"].clone();
    for key in ["defaults.terminal", "defaults.browser", "defaults.files"] {
        let (out, reply) = installed_run(&f, &["set", key, "missing.desktop"]);
        assert!(!out.status.success(), "{reply}");
        assert_eq!(reply["reason"], "application_unavailable");
        assert_eq!(fs::read(f.source()).unwrap(), before);
        assert_eq!(fs::read_to_string(&mime).unwrap(), contents);
        assert_eq!(installed_run(&f, &["history"]).1["history"], history);
    }
}

#[test]
fn installed_mime_confirmation_failure_rolls_back_source_and_associations() {
    for key in ["defaults.browser", "defaults.files"] {
        let f = Fixture::new();
        installed_helper(
            &f,
            "emaki-terminal",
            "import sys\nassert sys.argv[1] in ('--check', '--check-terminal')\n",
        );
        installed_helper(&f, "gio", "print('Default application: first.desktop')\n");
        let (out, reply) = installed_run(&f, &["set", key, "first.desktop"]);
        assert!(out.status.success(), "{reply}");
        let source = fs::read(f.source()).unwrap();
        let mime = f.root.join("state/emaki/defaults/mimeapps.list");
        let associations = fs::read(&mime).unwrap();
        let history = installed_run(&f, &["history"]).1["history"].clone();
        let (out, reply) = installed_run(&f, &["set", key, "second.desktop"]);
        assert!(!out.status.success(), "{reply}");
        assert_eq!(reply["reason"], "default_application_unconfirmed");
        assert_eq!(fs::read(f.source()).unwrap(), source);
        assert_eq!(fs::read(&mime).unwrap(), associations);
        assert_eq!(installed_run(&f, &["history"]).1["history"], history);
    }
}

#[test]
fn installed_keyboard_changes_refuse_machine_settings_without_publishing() {
    let f = Fixture::new();
    for (key, value) in [
        ("keyboard.layouts", "us,de"),
        ("keyboard.switch_key", "Alt+Shift"),
    ] {
        let (out, reply) = installed_run(&f, &["set", key, value]);
        assert!(!out.status.success(), "{reply}");
        assert_eq!(reply["reason"], "keyboard_requires_machine_settings");
        assert!(!f.source().exists());
        assert_eq!(installed_run(&f, &["history"]).1["history"], json!([]));
    }
}

#[test]
fn installed_application_defaults_preserve_personal_mime_file() {
    for filename in ["niri-mimeapps.list", "mimeapps.list"] {
        let f = Fixture::new();
        installed_helper(&f, "emaki-terminal", "pass\n");
        let mime = f.root.join("config").join(filename);
        let personal = "[Default Applications]\nx-scheme-handler/http=personal.desktop;\nx-scheme-handler/https=personal.desktop;\ntext/html=personal.desktop;\ninode/directory=personal.desktop;\n";
        fs::write(&mime, personal).unwrap();
        installed_helper(
            &f,
            "gio",
            &format!(
                "import configparser, sys\np = configparser.ConfigParser(interpolation=None)\np.read({mime:?})\nprint('Default application: ' + p['Default Applications'][sys.argv[2]].split(';')[0])\n",
                mime = mime.to_str().unwrap(),
            ),
        );
        for key in ["defaults.browser", "defaults.files"] {
            let (out, reply) = installed_run(&f, &["set", key, "test.desktop"]);
            assert!(out.status.success(), "{reply}");
            assert_eq!(row(&reply, key)["value"], "test.desktop");
            assert_eq!(fs::read_to_string(&mime).unwrap(), personal);
            assert!(
                fs::read_to_string(f.root.join("state/emaki/defaults/mimeapps.list"))
                    .unwrap()
                    .contains("test.desktop")
            );
        }
    }
}

#[test]
fn installed_wallpaper_failure_restores_snapshot_and_previous_source() {
    let f = Fixture::new();
    let first = f.root.join("first.png");
    let second = f.root.join("second.png");
    fs::write(&first, b"image").unwrap();
    fs::write(&second, b"image").unwrap();
    let calls = f.root.join("wallpaper-calls");
    let rejected = f.root.join("reject-wallpaper");
    installed_helper(
        &f,
        "emaki-settings-wallpaper",
        &format!(
            r#"import json, sys
from pathlib import Path
p = Path({calls:?})
p.write_text((p.read_text() if p.exists() else '') + json.dumps(sys.argv[1:]) + '\n')
if sys.argv[1] == 'snapshot':
    print('{{"screen":"previous.png"}}')
if sys.argv[1] == 'apply' and Path({rejected:?}).exists():
    sys.exit(1)
"#,
            calls = calls.to_str().unwrap(),
            rejected = rejected.to_str().unwrap()
        ),
    );
    let (out, reply) = installed_run(
        &f,
        &["set", "appearance.wallpaper", first.to_str().unwrap()],
    );
    assert!(out.status.success(), "{reply}");
    assert_eq!(reply["session_applied"], true);
    let before = fs::read(f.source()).unwrap();
    let history = installed_run(&f, &["history"]).1["history"].clone();
    fs::write(rejected, "").unwrap();
    let (out, reply) = installed_run(
        &f,
        &["set", "appearance.wallpaper", second.to_str().unwrap()],
    );
    assert!(!out.status.success(), "{reply}");
    assert_eq!(reply["reason"], "wallpaper_apply_failed");
    assert_eq!(fs::read(f.source()).unwrap(), before);
    assert_eq!(installed_run(&f, &["history"]).1["history"], history);
    let calls: Vec<Value> = fs::read_to_string(calls)
        .unwrap()
        .lines()
        .map(|line| serde_json::from_str(line).unwrap())
        .collect();
    assert_eq!(calls.len(), 5);
    assert_eq!(calls[3], json!(["apply", second.to_str().unwrap()]));
    assert_eq!(calls[4][0], "restore");
    let restored: Value = serde_json::from_str(calls[4][1].as_str().unwrap()).unwrap();
    assert_eq!(restored, json!({"screen": "previous.png"}));
}

#[test]
fn real_niri_validates_two_settings_generations_are_immutable_and_noop_is_read_only() {
    let f = Fixture::new();
    let a = f.set("appearance.gaps", "4");
    let first = PathBuf::from(a["generation_path"].as_str().unwrap());
    let first_bytes = fs::read(first.join("niri.kdl")).unwrap();
    assert_eq!(a["generation_status"], "prepared");
    // Comments may contain personal material; only typed fields enter a new generation.
    fs::write(
        f.source(),
        format!(
            "# SECRET manual comment\n{}",
            fs::read_to_string(f.source()).unwrap()
        ),
    )
    .unwrap();
    let b = f.set("keybindings.toggle_window_floating", "Shift+Mod+V");
    assert_ne!(a["generation"], b["generation"]);
    assert_eq!(row(&b, "appearance.gaps")["value"], 4);
    assert_eq!(
        row(&b, "keybindings.toggle_window_floating")["value"],
        "Mod+Shift+V"
    );
    let generation = PathBuf::from(b["generation_path"].as_str().unwrap());
    let fragment = fs::read_to_string(generation.join("niri.kdl")).unwrap();
    assert!(
        fragment.contains("gaps 4") && fragment.contains("Mod+Shift+V { toggle-window-floating; }")
    );
    assert_eq!(fs::read(first.join("niri.kdl")).unwrap(), first_bytes);
    assert_eq!(
        fs::read(f.source()).unwrap(),
        fs::read(generation.join("settings.toml")).unwrap()
    );
    assert!(
        tree(&f.root)
            .values()
            .all(|bytes| !String::from_utf8_lossy(bytes).contains("SECRET"))
    );
    assert_eq!(
        fs::metadata(f.source()).unwrap().permissions().mode() & 0o777,
        0o600
    );
    assert_eq!(
        fs::metadata(&generation).unwrap().permissions().mode() & 0o777,
        0o700
    );
    let before = tree(&f.root);
    assert_eq!(
        f.set("keybindings.toggle_window_floating", "Mod+Shift+V")["status"],
        "unchanged"
    );
    assert_eq!(before, tree(&f.root));
    assert!(
        fs::read_dir(f.root.join("runtime"))
            .unwrap()
            .next()
            .is_none()
    );
    assert!(!f.root.join("config/niri").exists());
}

#[test]
fn invalid_values_and_unknown_settings_preserve_every_existing_byte() {
    let f = Fixture::new();
    f.set("appearance.gaps", "4");
    let before = tree(&f.root);
    for (key, value) in [
        ("appearance.gaps", "-1"),
        ("appearance.gaps", "65"),
        ("appearance.gaps", "1.5"),
        ("appearance.gaps", "SECRET"),
        ("appearance.soft_full", "Soft"),
        ("keybindings.toggle_window_floating", "V"),
        ("keybindings.toggle_window_floating", "Mod+Mod+V"),
        (
            "keybindings.toggle_window_floating",
            "Mod+V\"; spawn SECRET",
        ),
        (
            "keybindings.toggle_window_floating",
            "Mod+EmakiNotARealKeysym",
        ),
    ] {
        let (out, v) = f.run(&["set", key, value]);
        assert_eq!(out.status.code(), Some(1), "{v}");
        assert!(!String::from_utf8(out.stdout).unwrap().contains("SECRET"));
        assert_eq!(before, tree(&f.root), "{key} {value}");
    }
}

#[test]
fn invalid_manual_toml_unknown_secrets_and_generation_paths_are_not_copied_or_logged() {
    let f = Fixture::new();
    for contents in [
        "SECRET broken toml",
        "schema_version=2",
        "schema_version=1\npassword='SECRET'",
        "schema_version=1\n[appearance]\ngaps=1\nunknown='SECRET'",
        "schema_version=1\ngeneration='../../SECRET'",
        "schema_version=1\n[appearance]\ngaps=true",
    ] {
        f.manual(contents);
        let before = tree(&f.root);
        let (out, v) = f.run(&["set", "appearance.gaps", "4"]);
        assert_eq!(out.status.code(), Some(1), "{v}");
        assert!(!String::from_utf8(out.stdout).unwrap().contains("SECRET"));
        assert_eq!(before, tree(&f.root));
    }
}

#[test]
fn validator_rejections_timeouts_and_missing_helper_leave_no_published_generation() {
    for (mode, reason) in [
        ("fragment", "fragment_validation_failed"),
        ("combined", "combined_validation_failed"),
        ("version", "unsupported_validator_version"),
        ("timeout", "deadline_exceeded"),
    ] {
        let f = Fixture::new();
        let before = tree(&f.root);
        let start = Instant::now();
        let (out, v) = f.fake_set(mode);
        assert_eq!(out.status.code(), Some(1), "{v}");
        assert_eq!(v["reason"], reason);
        if mode == "timeout" {
            // The fake sleeps 5 s; the 150 ms deadline must cut it short.
            assert!(start.elapsed() < Duration::from_secs(4));
        }
        assert!(!String::from_utf8(out.stdout).unwrap().contains("SECRET"));
        assert_eq!(before, tree(&f.root));
    }
    let f = Fixture::new();
    let before = tree(&f.root);
    let out = f
        .command()
        .env("PATH", "/nonexistent")
        .args([
            "settings",
            "set",
            "appearance.gaps",
            "4",
            "--json",
            "--profile-root",
            f.root.to_str().unwrap(),
        ])
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(1));
    let v: Value = serde_json::from_slice(&out.stdout).unwrap();
    assert_eq!(v["reason"], "helper_missing");
    assert_eq!(before, tree(&f.root));
}

#[test]
fn symlinks_hardlinks_and_unsafe_paths_cannot_redirect_settings_writes() {
    let f = Fixture::new();
    let outside = f.root.join("sentinel");
    fs::write(&outside, "SECRET sentinel").unwrap();
    fs::create_dir_all(f.source().parent().unwrap()).unwrap();
    for linked in [true, false] {
        if linked {
            symlink(&outside, f.source()).unwrap();
        } else {
            fs::hard_link(&outside, f.source()).unwrap();
        }
        let (out, v) = f.run(&["set", "appearance.gaps", "4"]);
        assert_eq!(out.status.code(), Some(1), "{v}");
        assert_eq!(fs::read_to_string(&outside).unwrap(), "SECRET sentinel");
        fs::remove_file(f.source()).unwrap();
    }
    fs::remove_dir(f.root.join("state")).unwrap();
    symlink(f.root.join("config"), f.root.join("state")).unwrap();
    assert_eq!(
        f.run(&["set", "appearance.gaps", "4"]).1["reason"],
        "symlink_refused"
    );
}

#[test]
fn manual_change_is_visible_as_unprepared_and_a_late_edit_is_not_overwritten() {
    let f = Fixture::new();
    f.set("keybindings.toggle_window_floating", "Mod+Shift+V");
    let source = fs::read_to_string(f.source()).unwrap();
    fs::write(f.source(), source + "\n[appearance]\ngaps=8\n").unwrap();
    let (out, v) = f.run(&["get", "appearance.gaps"]);
    assert!(out.status.success());
    assert_eq!(row(&v, "appearance.gaps")["value"], 8);
    assert_eq!(v["generation_status"], "needs_generation");
    let v = f.set("appearance.gaps", "8");
    assert_eq!(v["status"], "committed");
    assert_eq!(
        row(&v, "keybindings.toggle_window_floating")["value"],
        "Mod+Shift+V"
    );
    let (out, v) = f.fake_set("conflict");
    assert_eq!(out.status.code(), Some(1));
    assert_eq!(v["reason"], "settings_conflict");
    assert!(
        fs::read_to_string(f.source())
            .unwrap()
            .contains("gaps = 12")
    );
    assert!(
        fs::read_dir(f.root.join("runtime"))
            .unwrap()
            .next()
            .is_none()
    );
}

#[test]
fn busy_writer_is_rejected_and_released_lock_allows_next_write() {
    let f = Fixture::new();
    let before = tree(&f.root);
    // The lock is held by a separate process, not by a descriptor of this test process:
    // other tests fork children all the time, and a child forked while such a descriptor
    // is open keeps the flock alive until it execs, after the test has dropped it.
    let mut holder = Command::new("python")
        .args([
            "-c",
            "import fcntl,os,sys\n\
             fd=os.open(sys.argv[1],os.O_RDONLY)\n\
             fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)\n\
             print('locked',flush=True)\n\
             sys.stdin.read()",
            f.root.join("runtime").to_str().unwrap(),
        ])
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .spawn()
        .unwrap();
    let mut line = String::new();
    std::io::BufRead::read_line(
        &mut std::io::BufReader::new(holder.stdout.take().unwrap()),
        &mut line,
    )
    .unwrap();
    assert_eq!(line, "locked\n");
    let (out, v) = f.run(&["set", "appearance.gaps", "4"]);
    assert_eq!(out.status.code(), Some(1));
    assert_eq!(v["reason"], "settings_busy");
    assert_eq!(before, tree(&f.root));
    drop(holder.stdin.take());
    assert!(holder.wait().unwrap().success());
    assert_eq!(f.set("appearance.gaps", "4")["status"], "committed");
}

#[test]
fn semantic_undo_restores_only_appearance_and_keeps_hotkey() {
    let f = Fixture::new();
    let a = f.set("appearance.gaps", "4");
    f.set("keybindings.toggle_window_floating", "Mod+Shift+V");
    let (out, v) = f.run(&["undo", a["change_id"].as_str().unwrap()]);
    assert!(out.status.success(), "{v}");
    assert_eq!(row(&v, "appearance.gaps")["value"], 2);
    assert!(row(&v, "appearance.gaps")["override_value"].is_null());
    assert_eq!(
        row(&v, "keybindings.toggle_window_floating")["value"],
        "Mod+Shift+V"
    );
    let (_, h) = f.run(&["history"]);
    let entries = h["history"].as_array().unwrap();
    assert_eq!(entries.len(), 3);
    assert_eq!(entries[2]["kind"], "undo");
    assert_eq!(entries[2]["undo_of"], a["change_id"]);
    assert_eq!(entries[2]["changes"].as_array().unwrap().len(), 1);
    assert_eq!(entries[2]["changes"][0]["key"], "appearance.gaps");
    let before = tree(&f.root);
    assert_eq!(
        f.run(&["undo", a["change_id"].as_str().unwrap()]).1["reason"],
        "undo_conflict"
    );
    assert_eq!(before, tree(&f.root));
}

#[test]
fn reset_returns_any_key_to_the_package_default_through_the_journal() {
    let f = Fixture::new();
    let default_gaps = row(&f.run(&["list"]).1, "appearance.gaps")["default"].clone();
    f.set("appearance.gaps", "4");
    f.set("keybindings.toggle_window_floating", "Mod+Shift+V");
    f.set("dock.on", "false");
    // An empty value is refused for these two keys; reset is the way back.
    let (out, v) = f.run(&["reset", "appearance.gaps"]);
    assert!(out.status.success(), "{v}");
    assert_eq!(v["status"], "committed");
    assert!(row(&v, "appearance.gaps")["override_value"].is_null());
    assert_eq!(row(&v, "appearance.gaps")["value"], default_gaps);
    assert_eq!(
        row(&v, "keybindings.toggle_window_floating")["value"],
        "Mod+Shift+V"
    );
    assert!(!fs::read_to_string(f.source()).unwrap().contains("gaps"));
    let (out, v) = f.run(&["reset", "keybindings.toggle_window_floating"]);
    assert!(out.status.success(), "{v}");
    let fragment =
        fs::read_to_string(PathBuf::from(v["generation_path"].as_str().unwrap()).join("niri.kdl"))
            .unwrap();
    assert!(!fragment.contains("toggle-window-floating"));
    let (_, h) = f.run(&["history"]);
    let entries = h["history"].as_array().unwrap();
    assert_eq!(entries.len(), 5);
    for (entry, key, before) in [
        (&entries[3], "appearance.gaps", json!(4)),
        (
            &entries[4],
            "keybindings.toggle_window_floating",
            json!("Mod+Shift+V"),
        ),
    ] {
        assert_eq!(entry["kind"], "set");
        assert_eq!(
            entry["changes"],
            json!([{"key": key, "before": before, "after": null}])
        );
    }
    // Undo of a reset brings the override back.
    let (out, v) = f.run(&["undo", entries[3]["id"].as_str().unwrap()]);
    assert!(out.status.success(), "{v}");
    assert_eq!(row(&v, "appearance.gaps")["value"], 4);
    // Nothing to reset: unchanged, nothing written.
    let before = tree(&f.root);
    assert_eq!(f.run(&["reset", "bar.autohide"]).1["status"], "unchanged");
    assert_eq!(before, tree(&f.root));
    let (out, v) = f.run(&["reset", "dock.pinned"]);
    assert_eq!(out.status.code(), Some(1));
    assert_eq!(v["reason"], "unknown_setting");
    assert_eq!(before, tree(&f.root));
    for args in [
        &["settings", "reset"][..],
        &["settings", "reset", "--json"][..],
    ] {
        assert_eq!(
            f.command().args(args).output().unwrap().status.code(),
            Some(2)
        );
    }
}

#[test]
fn later_edit_conflicts_even_when_value_returns_to_the_same_value() {
    let f = Fixture::new();
    let a = f.set("appearance.gaps", "4");
    f.set("appearance.gaps", "8");
    let last = f.set("appearance.gaps", "4");
    let before = tree(&f.root);
    let (out, v) = f.run(&["undo", a["change_id"].as_str().unwrap()]);
    assert_eq!(out.status.code(), Some(1));
    assert_eq!(v["reason"], "undo_conflict");
    assert_eq!(v["conflicts"][0]["actual_change"], last["change_id"]);
    assert_eq!(v["conflicts"][0]["actual_value"], 4);
    assert_eq!(before, tree(&f.root));
}

#[test]
fn manual_edit_is_recorded_once_on_get_and_is_independently_undoable() {
    let f = Fixture::new();
    let a = f.set("appearance.gaps", "4");
    f.set("keybindings.toggle_window_floating", "Mod+Shift+V");
    let source = fs::read_to_string(f.source())
        .unwrap()
        .replace("gaps = 4", "gaps = 9");
    fs::write(f.source(), format!("# SECRET manual comment\n{source}")).unwrap();
    let (_, get) = f.run(&["get", "appearance.gaps"]);
    assert_eq!(get["manual_changes_recorded"].as_array().unwrap().len(), 1);
    assert_eq!(get["generation_status"], "needs_generation");
    let manual = get["manual_changes_recorded"][0].as_str().unwrap();
    let (_, history) = f.run(&["history"]);
    assert_eq!(history["history"].as_array().unwrap().len(), 3);
    assert_eq!(history["history"][2]["kind"], "manual");
    assert_eq!(history["history"][2]["changes"][0]["before"], 4);
    assert_eq!(history["history"][2]["changes"][0]["after"], 9);
    for file in fs::read_dir(f.root.join("state/emaki/history")).unwrap() {
        assert!(
            !fs::read_to_string(file.unwrap().path())
                .unwrap()
                .contains("SECRET")
        );
    }
    assert_eq!(
        f.run(&["undo", a["change_id"].as_str().unwrap()]).1["reason"],
        "undo_conflict"
    );
    let (out, v) = f.run(&["undo", manual]);
    assert!(out.status.success(), "{v}");
    assert_eq!(row(&v, "appearance.gaps")["value"], 4);
    assert_eq!(
        row(&v, "keybindings.toggle_window_floating")["value"],
        "Mod+Shift+V"
    );
    // A pre-existing profile has no known prior state. Import is explicitly not undoable.
    let legacy = Fixture::new();
    legacy.manual("schema_version=1\n[appearance]\ngaps=6\n");
    let (_, h) = legacy.run(&["history"]);
    assert_eq!(h["history"][0]["kind"], "import");
    assert_eq!(
        legacy
            .run(&["undo", h["history"][0]["id"].as_str().unwrap()])
            .1["reason"],
        "entry_not_undoable"
    );
}

#[test]
fn corrupted_history_is_refused_without_exposing_raw_data_or_overwriting_settings() {
    let f = Fixture::new();
    f.set("appearance.gaps", "4");
    let path = fs::read_dir(f.root.join("state/emaki/history"))
        .unwrap()
        .next()
        .unwrap()
        .unwrap()
        .path();
    fs::write(path, "SECRET malformed history").unwrap();
    let before = tree(&f.root);
    let (out, v) = f.run(&["history"]);
    assert_eq!(out.status.code(), Some(1));
    assert_eq!(v["reason"], "history_corrupt");
    assert!(!String::from_utf8(out.stdout).unwrap().contains("SECRET"));
    assert_eq!(before, tree(&f.root));
}

// The child writes its marker first and stops itself after that. A SIGCONT sent in
// between is lost and the child then stays stopped, so wait for the stopped state.
#[cfg(feature = "test-hooks")]
fn stopped(pid: u32) -> bool {
    fs::read_to_string(format!("/proc/{pid}/stat")).is_ok_and(|stat| {
        stat.rsplit_once(')')
            .and_then(|(_, rest)| rest.split_whitespace().next())
            == Some("T")
    })
}
#[cfg(feature = "test-hooks")]
struct Stopped(std::process::Child);
#[cfg(feature = "test-hooks")]
impl Stopped {
    fn launch(f: &Fixture, args: &[&str], phase: &str) -> Self {
        let child = f
            .command()
            .args(["settings"])
            .args(args)
            .args(["--profile-root", f.root.to_str().unwrap(), "--json"])
            .args(write_timeout(args))
            .env("EMAKI_TEST_PAUSE", phase)
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn()
            .unwrap();
        let mut guard = Self(child);
        let deadline = Instant::now() + CHILD_DEADLINE;
        loop {
            if let Ok(bytes) = fs::read(f.root.join("runtime/emaki-settings-hook.json"))
                && let Ok(v) = serde_json::from_slice::<Value>(&bytes)
                && v["pid"] == guard.0.id()
                && v["phase"] == phase
                && stopped(guard.0.id())
            {
                break;
            }
            assert!(
                guard.0.try_wait().unwrap().is_none(),
                "child exited before checkpoint {phase}"
            );
            assert!(Instant::now() < deadline, "checkpoint timeout: {phase}");
            std::thread::sleep(Duration::from_millis(5));
        }
        guard
    }
    fn kill(&mut self) {
        self.0.kill().unwrap();
        self.0.wait().unwrap();
    }
    fn resume(&mut self) -> Option<i32> {
        assert!(
            Command::new("python")
                .args([
                    "-c",
                    "import os,signal,sys;os.kill(int(sys.argv[1]),signal.SIGCONT)",
                    &self.0.id().to_string()
                ])
                .status()
                .unwrap()
                .success()
        );
        let deadline = Instant::now() + CHILD_DEADLINE;
        loop {
            if let Some(status) = self.0.try_wait().unwrap() {
                return status.code();
            }
            assert!(Instant::now() < deadline, "resumed child did not exit");
            std::thread::sleep(Duration::from_millis(5));
        }
    }
}
#[cfg(feature = "test-hooks")]
impl Drop for Stopped {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

#[cfg(feature = "test-hooks")]
#[test]
fn sigkill_at_six_commit_points_recovers_once_and_cleans_only_candidates() {
    for phase in [
        "after_stage",
        "after_validation",
        "after_generation",
        "before_rename",
        "after_rename",
        "after_history",
    ] {
        let f = Fixture::new();
        let initial = f.set("appearance.gaps", "3");
        let first = PathBuf::from(initial["generation_path"].as_str().unwrap());
        let bytes = fs::read(first.join("niri.kdl")).unwrap();
        let mut child = Stopped::launch(&f, &["set", "appearance.gaps", "7"], phase);
        child.kill();
        let (out, h) = f.run(&["history"]);
        assert!(out.status.success(), "phase={phase}: {h}");
        let committed = matches!(phase, "after_rename" | "after_history");
        assert_eq!(
            row(&h, "appearance.gaps")["value"],
            if committed { 7 } else { 3 }
        );
        assert_eq!(
            h["history"].as_array().unwrap().len(),
            if committed { 2 } else { 1 }
        );
        assert_eq!(h["generation_status"], "prepared");
        assert!(!f.root.join("state/emaki/history/pending.json").exists());
        assert!(
            fs::read_dir(f.root.join("runtime"))
                .unwrap()
                .next()
                .is_none()
        );
        assert_eq!(
            fs::read_dir(f.root.join("state/emaki/generations"))
                .unwrap()
                .count(),
            if committed { 2 } else { 1 }
        );
        assert_eq!(fs::read(first.join("niri.kdl")).unwrap(), bytes);
        assert_eq!(
            fs::read_dir(f.root.join("config/emaki")).unwrap().count(),
            1
        );
        let after = tree(&f.root);
        assert_eq!(f.run(&["history"]).1["history"], h["history"]);
        assert_eq!(after, tree(&f.root));
    }
}

#[cfg(feature = "test-hooks")]
#[test]
fn two_clients_do_not_lose_keys_and_recovery_can_itself_be_killed() {
    let f = Fixture::new();
    let mut a = Stopped::launch(&f, &["set", "appearance.gaps", "4"], "before_rename");
    let (out, b) = f.run(&["set", "keybindings.toggle_window_floating", "Mod+Shift+V"]);
    assert_eq!(out.status.code(), Some(1));
    assert_eq!(b["reason"], "settings_busy");
    assert_eq!(a.resume(), Some(0));
    let b = f.set("keybindings.toggle_window_floating", "Mod+Shift+V");
    assert_eq!(row(&b, "appearance.gaps")["value"], 4);
    let mut a = Stopped::launch(&f, &["set", "appearance.gaps", "8"], "after_rename");
    a.kill();
    let mut recovery = Stopped::launch(&f, &["history"], "recovery_after_history");
    recovery.kill();
    let (_, h) = f.run(&["history"]);
    assert_eq!(h["history"].as_array().unwrap().len(), 3);
    assert_eq!(row(&h, "appearance.gaps")["value"], 8);
    assert_eq!(
        row(&h, "keybindings.toggle_window_floating")["value"],
        "Mod+Shift+V"
    );
    assert!(!f.root.join("state/emaki/history/pending.json").exists());
}

#[cfg(feature = "test-hooks")]
#[test]
fn write_refused_after_its_generation_was_written_leaves_the_profile_usable() {
    let f = Fixture::new();
    let initial = f.set("appearance.gaps", "3");
    // The generation of the new value is on disk; the source changes before the rename.
    let mut child = Stopped::launch(&f, &["set", "appearance.gaps", "7"], "before_rename");
    let edited = fs::read_to_string(f.source())
        .unwrap()
        .replace("gaps = 3", "gaps = 9");
    fs::write(f.source(), &edited).unwrap();
    assert_eq!(child.resume(), Some(1), "settings_conflict expected");
    assert!(!f.root.join("state/emaki/history/pending.json").exists());
    assert_eq!(
        fs::read_dir(f.root.join("state/emaki/generations"))
            .unwrap()
            .map(|e| e.unwrap().file_name())
            .collect::<Vec<_>>(),
        [initial["generation"].as_str().unwrap()]
    );
    assert_eq!(fs::read_to_string(f.source()).unwrap(), edited);
    let (out, v) = f.run(&["list"]);
    assert!(out.status.success(), "{v}");
    assert_eq!(row(&v, "appearance.gaps")["value"], 9);
    assert_eq!(f.set("appearance.gaps", "5")["status"], "committed");
}

#[cfg(not(feature = "test-hooks"))]
#[test]
fn production_build_cannot_enable_fault_hooks_by_environment() {
    let f = Fixture::new();
    let mut child = f
        .command()
        .args([
            "settings",
            "set",
            "appearance.gaps",
            "4",
            "--json",
            "--profile-root",
            f.root.to_str().unwrap(),
            "--timeout-ms",
            SLOW_MACHINE_TIMEOUT_MS,
        ])
        .env("EMAKI_TEST_PAUSE", "before_rename")
        .stdout(std::process::Stdio::null())
        .spawn()
        .unwrap();
    let deadline = Instant::now() + CHILD_DEADLINE;
    loop {
        if let Some(status) = child.try_wait().unwrap() {
            assert!(status.success());
            break;
        }
        if Instant::now() >= deadline {
            child.kill().unwrap();
            child.wait().unwrap();
            panic!("normal build enabled a test hook");
        }
        std::thread::sleep(Duration::from_millis(5));
    }
    assert!(!f.root.join("runtime/emaki-settings-hook.json").exists());
}

#[cfg(feature = "test-hooks")]
#[test]
fn first_write_recovery_and_manual_edit_after_committed_crash_are_distinct() {
    for phase in ["before_rename", "after_rename"] {
        let f = Fixture::new();
        let mut child = Stopped::launch(&f, &["set", "appearance.gaps", "7"], phase);
        child.kill();
        let (out, h) = f.run(&["history"]);
        assert!(out.status.success(), "{h}");
        assert_eq!(
            h["history"].as_array().unwrap().len(),
            if phase == "after_rename" { 1 } else { 0 }
        );
        assert_eq!(
            row(&h, "appearance.gaps")["value"],
            if phase == "after_rename" { 7 } else { 2 }
        );
        assert!(!f.root.join("state/emaki/history/pending.json").exists());
        assert!(
            fs::read_dir(f.root.join("runtime"))
                .unwrap()
                .next()
                .is_none()
        );
    }
    let f = Fixture::new();
    f.set("appearance.gaps", "3");
    let mut child = Stopped::launch(&f, &["set", "appearance.gaps", "7"], "after_rename");
    child.kill();
    fs::write(
        f.source(),
        fs::read_to_string(f.source())
            .unwrap()
            .replace("gaps = 7", "gaps = 9"),
    )
    .unwrap();
    let (out, h) = f.run(&["history"]);
    assert!(out.status.success(), "{h}");
    let entries = h["history"].as_array().unwrap();
    assert_eq!(entries.len(), 3);
    assert_eq!(entries[1]["kind"], "set");
    assert_eq!(entries[1]["changes"][0]["after"], 7);
    assert_eq!(entries[2]["kind"], "manual");
    assert_eq!(entries[2]["changes"][0]["after"], 9);
    assert_eq!(h["generation_status"], "needs_generation");
}

#[test]
fn settings_page_keys_generate_wallpaper_and_default_app_files_never_xkb_and_undo_one_key() {
    let f = Fixture::new();
    let before = tree(&f.root);
    let picture = f.root.join("bin/fixture.png");
    fs::write(&picture, b"\x89PNG\r\n\x1a\nnot really").unwrap();
    for (key, value, reason) in [
        ("keyboard.layouts", "us,zzqq", "unknown_layout"),
        ("keyboard.layouts", "us,us", "invalid_layouts"),
        ("keyboard.layouts", "US", "invalid_layouts"),
        ("keyboard.layouts", "us,ru,ua,cz,de", "invalid_layouts"),
        ("keyboard.layouts", "us\"; spawn SECRET", "invalid_layouts"),
        ("keyboard.switch_key", "Ctrl+Shift", "invalid_switch_key"),
        ("appearance.wallpaper", "relative.png", "invalid_wallpaper"),
        (
            "appearance.wallpaper",
            "/nonexistent/SECRET.png",
            "wallpaper_missing",
        ),
        ("defaults.browser", "firefox", "invalid_desktop_id"),
        (
            "defaults.browser",
            "../SECRET.desktop",
            "invalid_desktop_id",
        ),
        (
            "defaults.terminal",
            "kitty.desktop\nSECRET",
            "invalid_desktop_id",
        ),
        ("bar.autohide", "yes", "invalid_boolean"),
        ("dock.pinned", "kitty", "unknown_setting"),
    ] {
        let (out, v) = f.run(&["set", key, value]);
        assert_eq!(out.status.code(), Some(1), "{key} {value}: {v}");
        assert_eq!(v["reason"], reason, "{key} {value}");
        assert!(!String::from_utf8(out.stdout).unwrap().contains("SECRET"));
        assert_eq!(before, tree(&f.root), "{key} {value}");
    }
    // A picture path containing a quote never reaches TOML/KDL.
    let quoted = f.root.join("bin/quo\"te.png");
    fs::write(&quoted, b"x").unwrap();
    assert_eq!(
        f.run(&["set", "appearance.wallpaper", quoted.to_str().unwrap()])
            .1["reason"],
        "invalid_wallpaper"
    );
    let layouts = f.set("keyboard.layouts", "us,ru");
    assert_eq!(
        row(&layouts, "keyboard.layouts")["value"],
        json!(["us", "ru"])
    );
    // Layouts live in /etc/vconsole.conf behind localed: the keyboard keys are kept in
    // settings.toml, and no generated niri file gets an xkb section or a grp: option.
    let no_xkb = |reply: &Value| {
        let generation = PathBuf::from(reply["generation_path"].as_str().unwrap());
        let fragment = fs::read_to_string(generation.join("niri.kdl")).unwrap();
        assert!(
            !fragment.contains("xkb") && !fragment.contains("grp:"),
            "{fragment}"
        );
    };
    no_xkb(&layouts);
    assert!(
        fs::read_to_string(f.source())
            .unwrap()
            .contains("layouts = [\"us\", \"ru\"]")
    );
    assert_eq!(row(&layouts, "keyboard.switch_key")["value"], "Super+Space");
    let switch = f.set("keyboard.switch_key", "Alt+Shift");
    no_xkb(&switch);
    assert_eq!(row(&switch, "keyboard.switch_key")["value"], "Alt+Shift");
    let caps = f.set("keyboard.switch_key", "Caps Lock");
    no_xkb(&caps);
    assert!(
        fs::read_to_string(f.source())
            .unwrap()
            .contains("switch_key = \"Caps Lock\"")
    );
    let wall = f.set("appearance.wallpaper", picture.to_str().unwrap());
    let generation = PathBuf::from(wall["generation_path"].as_str().unwrap());
    assert_eq!(
        fs::read_to_string(generation.join("wpaperd.toml")).unwrap(),
        format!(
            "# Generated by Emaki. Edit settings.toml, not this file.\n[default]\npath = \"{}\"\n",
            picture.display()
        )
    );
    assert_eq!(wall["generation_status"], "prepared");
    f.set("defaults.browser", "fixture-web.desktop");
    let files = f.set("defaults.files", "org.gnome.Nautilus.desktop");
    let generation = PathBuf::from(files["generation_path"].as_str().unwrap());
    assert_eq!(
        fs::read_to_string(generation.join("mimeapps.list")).unwrap(),
        "[Default Applications]\nx-scheme-handler/http=fixture-web.desktop\nx-scheme-handler/https=fixture-web.desktop\ntext/html=fixture-web.desktop\ninode/directory=org.gnome.Nautilus.desktop\n"
    );
    assert!(!generation.join("xdg-terminals.list").exists());
    let term = f.set("defaults.terminal", "kitty.desktop");
    assert_eq!(
        fs::read_to_string(
            PathBuf::from(term["generation_path"].as_str().unwrap()).join("xdg-terminals.list")
        )
        .unwrap(),
        "kitty.desktop\n"
    );
    let bar = f.set("bar.autohide", "true");
    assert_eq!(row(&bar, "bar.autohide")["value"], true);
    assert_eq!(row(&bar, "bar.overview_workspaces")["value"], true);
    assert!(row(&bar, "bar.overview_workspaces")["override_value"].is_null());
    let dock = f.set("dock.on", "false");
    assert!(
        fs::read_to_string(f.source())
            .unwrap()
            .contains("[dock]\non = false\n")
    );
    // The whole document round-trips through TOML with every key intact.
    let (out, v) = f.run(&["list"]);
    assert!(out.status.success());
    assert_eq!(v["generation_status"], "prepared");
    assert_eq!(row(&v, "keyboard.layouts")["value"], json!(["us", "ru"]));
    assert_eq!(
        row(&v, "appearance.wallpaper")["value"],
        picture.to_str().unwrap()
    );
    assert_eq!(row(&v, "defaults.terminal")["value"], "kitty.desktop");
    assert_eq!(row(&v, "dock.on")["value"], false);
    // Undo of the layouts entry alone: every other key stays.
    let (out, v) = f.run(&["undo", layouts["change_id"].as_str().unwrap()]);
    assert!(out.status.success(), "{v}");
    assert!(row(&v, "keyboard.layouts")["value"].is_null());
    assert_eq!(row(&v, "keyboard.switch_key")["value"], "Caps Lock");
    no_xkb(&v);
    // Undo of a picture whose file has since disappeared still restores the key.
    fs::remove_file(&picture).unwrap();
    let (_, h) = f.run(&["history"]);
    let removed = f.set("appearance.wallpaper", "");
    assert!(row(&removed, "appearance.wallpaper")["value"].is_null());
    let (out, v) = f.run(&["undo", removed["change_id"].as_str().unwrap()]);
    assert!(out.status.success(), "{v}");
    assert_eq!(
        row(&v, "appearance.wallpaper")["value"],
        picture.to_str().unwrap()
    );
    assert_eq!(
        h["history"].as_array().unwrap().len() + 2,
        f.run(&["history"]).1["history"].as_array().unwrap().len()
    );
    // Only known generation files: a stray file blocks cleanup, never deletes blindly.
    let (out, undo_dock) = f.run(&["undo", dock["change_id"].as_str().unwrap()]);
    assert!(out.status.success(), "{undo_dock}");
    assert_eq!(row(&undo_dock, "dock.on")["value"], true);
}

#[test]
fn installed_first_use_preserves_legacy_dock_choices_once() {
    let f = Fixture::new();
    fs::create_dir_all(f.root.join("state/emaki")).unwrap();
    let dock = f.root.join("state/emaki/dock.json");
    let legacy = br#"{"version":1,"on":false,"auto_hide":false,"pinned":["test.desktop"]}"#;
    fs::write(&dock, legacy).unwrap();
    let before = tree(&f.root);
    let (out, reply) = installed_run(&f, &["list"]);
    assert!(out.status.success(), "{reply}");
    assert_eq!(row(&reply, "dock.on")["value"], false);
    assert_eq!(row(&reply, "dock.auto_hide")["value"], false);
    assert_eq!(fs::read(&dock).unwrap(), legacy);
    assert_eq!(tree(&f.root), before);
    assert!(!f.source().exists());
    assert_eq!(installed_run(&f, &["history"]).1["history"], json!([]));
    installed_helper(&f, "emaki-shell", "print('applied')\n");
    let (out, reply) = installed_run(&f, &["set", "bar.autohide", "true"]);
    assert!(out.status.success(), "{reply}");
    assert_eq!(
        fs::metadata(f.source()).unwrap().permissions().mode() & 0o777,
        0o600
    );
    let history = installed_run(&f, &["history"]).1;
    assert_eq!(history["history"].as_array().unwrap().len(), 2);
    assert!(
        history["history"]
            .as_array()
            .unwrap()
            .iter()
            .any(|r| r["kind"] == "import")
    );
    fs::write(&dock, br#"{"version":1,"on":true,"auto_hide":true}"#).unwrap();
    assert_eq!(
        row(&installed_run(&f, &["list"]).1, "dock.on")["value"],
        false
    );
    fs::remove_file(f.source()).unwrap();
    assert_eq!(
        row(&installed_run(&f, &["list"]).1, "dock.on")["value"],
        true
    );
    assert!(!f.source().exists());
}

#[test]
fn installed_legacy_defaults_remain_inherited_after_first_mutation() {
    let f = Fixture::new();
    fs::create_dir_all(f.root.join("state/emaki")).unwrap();
    fs::write(
        f.root.join("state/emaki/dock.json"),
        br#"{"version":1,"on":true,"auto_hide":true,"pinned":["test.desktop"]}"#,
    )
    .unwrap();
    installed_helper(&f, "emaki-shell", "print('applied')\n");
    let (out, reply) = installed_run(&f, &["set", "bar.autohide", "true"]);
    assert!(out.status.success(), "{reply}");
    let source = fs::read_to_string(f.source()).unwrap();
    assert!(!source.contains("[dock]"), "{source}");
    assert!(row(&reply, "dock.on")["override_value"].is_null());
    assert!(row(&reply, "dock.auto_hide")["override_value"].is_null());
}

#[test]
fn installed_reset_of_unset_key_is_not_reported_as_applied() {
    let f = Fixture::new();
    let (out, reply) = installed_run(&f, &["reset", "appearance.gaps"]);
    assert!(out.status.success(), "{reply}");
    assert_eq!(reply["status"], "unchanged");
    assert_eq!(reply["session_applied"], false);
    assert!(reply["change_id"].is_null());
    assert!(!f.source().exists());
}

#[test]
fn login_keeps_valid_overrides_when_recovery_metadata_is_unreadable() {
    let f = Fixture::new();
    f.manual("schema_version=1\n[appearance]\ngaps=12\n");
    let history = f.root.join("state/emaki/history");
    fs::create_dir_all(&history).unwrap();
    fs::write(history.join("live-pending.json"), "invalid pending record").unwrap();
    let out = f
        .fake("ok")
        .args(["settings", "session-config"])
        .output()
        .unwrap();
    assert!(out.status.success(), "{out:?}");
    assert!(String::from_utf8_lossy(&out.stderr).contains("using your current managed overrides"));
    let wrapper = fs::read_to_string(String::from_utf8(out.stdout).unwrap().trim()).unwrap();
    assert!(wrapper.contains("settings-manual.kdl"));
    assert!(
        fs::read_to_string(f.root.join("runtime/emaki-settings/settings-manual.kdl"))
            .unwrap()
            .contains("gaps 12")
    );
    assert!(history.join("live-pending.json").exists());
}

#[test]
fn failed_first_change_preserves_migrated_legacy_deviations() {
    let f = Fixture::new();
    fs::create_dir_all(f.root.join("state/emaki")).unwrap();
    fs::write(
        f.root.join("state/emaki/dock.json"),
        r#"{"version":1,"on":false,"auto_hide":true}"#,
    )
    .unwrap();
    installed_helper(&f, "emaki-shell", "raise SystemExit(1)\n");
    let (out, _) = installed_run(&f, &["set", "bar.autohide", "true"]);
    assert!(!out.status.success());
    let (out, reply) = installed_run(&f, &["list"]);
    assert!(out.status.success(), "{reply}");
    assert_eq!(row(&reply, "dock.on")["value"], false);
    assert!(row(&reply, "dock.auto_hide")["override_value"].is_null());
}
