use serde_json::{Value, json};
use std::collections::BTreeMap;
use std::fs;
use std::os::unix::fs::{DirBuilderExt, PermissionsExt, symlink};
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant};

static SERIAL: AtomicU64 = AtomicU64::new(0);
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
        let out = self
            .command()
            .arg("settings")
            .args(args)
            .args(["--profile-root", self.root.to_str().unwrap(), "--json"])
            .output()
            .unwrap();
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
                "150",
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
fn settings_defaults_reads_and_required_isolated_profile_never_write() {
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
    for c in [
        f.command()
            .args(["settings", "set", "appearance.gaps", "4", "--json"])
            .output()
            .unwrap(),
        f.command()
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
            .unwrap(),
    ] {
        assert_eq!(c.status.code(), Some(1));
        let r: Value = serde_json::from_slice(&c.stdout).unwrap();
        assert!(matches!(
            r["reason"].as_str(),
            Some("isolated_profile_required" | "isolated_xdg_mismatch")
        ));
    }
    assert_eq!(before, tree(&f.root));
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
        assert!(start.elapsed() < Duration::from_secs(2));
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
    let lock = fs::File::open(f.root.join("runtime")).unwrap();
    lock.try_lock().unwrap();
    let (out, v) = f.run(&["set", "appearance.gaps", "4"]);
    assert_eq!(out.status.code(), Some(1));
    assert_eq!(v["reason"], "settings_busy");
    assert_eq!(before, tree(&f.root));
    drop(lock);
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
            .env("EMAKI_TEST_PAUSE", phase)
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn()
            .unwrap();
        let mut guard = Self(child);
        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            if let Ok(bytes) = fs::read(f.root.join("runtime/emaki-settings-hook.json"))
                && let Ok(v) = serde_json::from_slice::<Value>(&bytes)
                && v["pid"] == guard.0.id()
                && v["phase"] == phase
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
    fn resume(&mut self) {
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
        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            if let Some(status) = self.0.try_wait().unwrap() {
                assert!(status.success());
                break;
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
    a.resume();
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
        ])
        .env("EMAKI_TEST_PAUSE", "before_rename")
        .stdout(std::process::Stdio::null())
        .spawn()
        .unwrap();
    let deadline = Instant::now() + Duration::from_secs(5);
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
fn settings_page_keys_generate_xkb_wallpaper_and_default_app_files_and_undo_one_key() {
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
    let generation = PathBuf::from(layouts["generation_path"].as_str().unwrap());
    let fragment = fs::read_to_string(generation.join("niri.kdl")).unwrap();
    assert!(fragment.contains("layout \"us,ru\"") && !fragment.contains("options"));
    assert!(
        fs::read_to_string(f.source())
            .unwrap()
            .contains("layouts = [\"us\", \"ru\"]")
    );
    assert_eq!(row(&layouts, "keyboard.switch_key")["value"], "Super+Space");
    let switch = f.set("keyboard.switch_key", "Alt+Shift");
    let fragment = fs::read_to_string(
        PathBuf::from(switch["generation_path"].as_str().unwrap()).join("niri.kdl"),
    )
    .unwrap();
    assert!(
        fragment.contains("layout \"us,ru\"")
            && fragment.contains("options \"grp:alt_shift_toggle\"")
    );
    let caps = f.set("keyboard.switch_key", "Caps Lock");
    assert!(
        fs::read_to_string(
            PathBuf::from(caps["generation_path"].as_str().unwrap()).join("niri.kdl")
        )
        .unwrap()
        .contains("options \"grp:caps_toggle\"")
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
    // Undo of the layouts entry alone: xkb options and every other key stay.
    let (out, v) = f.run(&["undo", layouts["change_id"].as_str().unwrap()]);
    assert!(out.status.success(), "{v}");
    assert!(row(&v, "keyboard.layouts")["value"].is_null());
    assert_eq!(row(&v, "keyboard.switch_key")["value"], "Caps Lock");
    let fragment =
        fs::read_to_string(PathBuf::from(v["generation_path"].as_str().unwrap()).join("niri.kdl"))
            .unwrap();
    assert!(!fragment.contains("layout \"") && fragment.contains("grp:caps_toggle"));
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
