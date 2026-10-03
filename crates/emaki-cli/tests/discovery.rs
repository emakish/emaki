use serde_json::{Value, json};
use std::collections::BTreeMap;
use std::fs;
use std::io::{BufRead, BufReader, Write};
use std::os::unix::{fs::PermissionsExt, net::UnixListener};
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant};

static COUNTER: AtomicU64 = AtomicU64::new(0);

struct Fixture {
    root: PathBuf,
}
impl Fixture {
    fn new() -> Self {
        // Test writes are always inside this checkout, including without TMPDIR.
        let root = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../.cache/tmp")
            .join(format!(
                "discovery-{}-{}",
                std::process::id(),
                COUNTER.fetch_add(1, Ordering::Relaxed)
            ));
        fs::create_dir_all(root.join("bin")).unwrap();
        let root = root.canonicalize().unwrap();
        for (name, source) in [
            ("busctl", include_str!("fixtures/busctl.py")),
            ("pw-cli", include_str!("fixtures/pw-cli.py")),
        ] {
            let file = root.join("bin").join(name);
            fs::write(&file, source).unwrap();
            fs::set_permissions(file, fs::Permissions::from_mode(0o700)).unwrap();
        }
        Self { root }
    }

    fn command(&self) -> Command {
        let mut command = Command::new(env!("CARGO_BIN_EXE_emaki"));
        command
            .env_clear()
            .env(
                "PATH",
                format!(
                    "{}:{}",
                    self.root.join("bin").display(),
                    std::env::var("PATH").unwrap()
                ),
            )
            .env("PYTHONDONTWRITEBYTECODE", "1")
            .env("HOME", self.root.join("home"))
            .env("XDG_CONFIG_HOME", self.root.join("config"))
            .env("XDG_STATE_HOME", self.root.join("state"))
            .env("XDG_RUNTIME_DIR", self.root.join("runtime"))
            .env("NIRI_SOCKET", self.root.join("niri.sock"));
        command
    }

    fn state(&self, mode: &str) -> (Output, Value) {
        self.state_within(mode, 500)
    }

    fn state_within(&self, mode: &str, timeout_ms: u32) -> (Output, Value) {
        let timeout = timeout_ms.to_string();
        let output = self
            .command()
            .env("FAKE_DBUS", mode)
            .args(["state", "--json", "--timeout-ms", &timeout])
            .output()
            .unwrap();
        let json = serde_json::from_slice(&output.stdout).unwrap_or_else(|_| panic!("{output:?}"));
        assert!(output.stderr.is_empty(), "{output:?}");
        (output, json)
    }

    fn server(&self, responses: Vec<Vec<u8>>) -> std::thread::JoinHandle<()> {
        let listener = UnixListener::bind(self.root.join("niri.sock")).unwrap();
        listener.set_nonblocking(true).unwrap();
        std::thread::spawn(move || {
            let deadline = Instant::now() + Duration::from_secs(5);
            let stream = loop {
                match listener.accept() {
                    Ok((stream, _)) => break stream,
                    Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                        assert!(Instant::now() < deadline, "test client did not connect");
                        std::thread::sleep(Duration::from_millis(2));
                    }
                    Err(e) => panic!("{e}"),
                }
            };
            stream
                .set_read_timeout(Some(Duration::from_secs(3)))
                .unwrap();
            let mut reader = BufReader::new(stream);
            for (request, response) in ["Version", "Workspaces", "Windows"]
                .into_iter()
                .zip(responses)
            {
                let mut line = String::new();
                reader.read_line(&mut line).unwrap();
                assert_eq!(line, format!("\"{request}\"\n"));
                if response == b"HANG" {
                    std::thread::sleep(Duration::from_millis(700));
                    break;
                }
                reader.get_mut().write_all(&response).unwrap();
            }
        })
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.root).unwrap();
    }
}

fn component<'a>(state: &'a Value, id: &str) -> &'a Value {
    state["components"]
        .as_array()
        .unwrap()
        .iter()
        .find(|c| c["id"] == id)
        .unwrap()
}

fn good_responses() -> Vec<Vec<u8>> {
    vec![
        b"{\"Ok\":{\"Version\":\"26.04\"}}\n".to_vec(),
        b"{\"Ok\":{\"Workspaces\":[{\"id\":1,\"name\":\"SECRET_WORKSPACE\"}]}}\n".to_vec(),
        b"{\"Ok\":{\"Windows\":[{\"id\":4,\"title\":\"SECRET_TITLE\",\"app_id\":\"SECRET_APP\"}]}}\n".to_vec(),
    ]
}

#[test]
fn successful_sources_have_versioned_private_output() {
    let f = Fixture::new();
    let server = f.server(good_responses());
    // A generous deadline: the fake busctl is a Python script and the WirePlumber probe runs it
    // three times in a row, which took over 500 ms on the CI runner. Timeouts have their own tests.
    let (out, state) = f.state_within("ok", 5000);
    server.join().unwrap();
    assert_eq!(out.status.code(), Some(0), "{state:#}");
    assert_eq!(state["schema_version"], 1);
    assert_eq!(state["components"].as_array().unwrap().len(), 9);
    assert_eq!(
        component(&state, "niri")["data"],
        json!({"version":"26.04", "workspace_count":1, "window_count":1})
    );
    assert_eq!(
        component(&state, "power_profiles")["data"]["active_profile"],
        "performance"
    );
    let text = String::from_utf8(out.stdout).unwrap();
    for private in [
        "SECRET",
        "title",
        "app_id",
        "SSID",
        "/home/",
        f.root.to_str().unwrap(),
    ] {
        assert!(!text.contains(private), "leaked {private}");
    }
}

#[test]
fn absent_denied_truncated_and_timeout_dbus_are_distinct() {
    let f = Fixture::new();
    for (mode, status, reason) in [
        ("absent", "absent", "name_has_no_owner"),
        ("denied", "denied", "access_denied"),
        ("truncated", "incomplete", "invalid_response"),
        ("disconnected", "incomplete", "connection_closed"),
        ("timeout", "timeout", "source_timeout"),
        ("unknown_failure", "error", "helper_failed"),
    ] {
        let (out, state) = f.state(mode);
        assert_eq!(out.status.code(), Some(1));
        let c = component(&state, "networkmanager");
        assert_eq!(c["status"], status, "mode {mode}: {c}");
        assert_eq!(c["reason"], reason, "mode {mode}: {c}");
        assert!(!String::from_utf8(out.stdout).unwrap().contains("SECRET"));
    }
}

#[test]
fn helper_deadline_and_output_limit_are_enforced() {
    let f = Fixture::new();
    for (mode, expected) in [("hang", "timeout"), ("flood", "incomplete")] {
        let start = Instant::now();
        let (out, state) = f.state(mode);
        assert_eq!(out.status.code(), Some(1));
        assert!(start.elapsed() < Duration::from_secs(3));
        assert_eq!(component(&state, "bluez")["status"], expected);
    }
}

#[test]
fn niri_missing_reset_partial_malformed_and_slow_replies() {
    for (reply, status, reason) in [
        (Vec::new(), "incomplete", "connection_closed"),
        (b"{\"Ok\":".to_vec(), "incomplete", "connection_closed"),
        (b"garbage\n".to_vec(), "incomplete", "invalid_response"),
        (
            b"{\"Ok\":{\"Version\":null}}\n".to_vec(),
            "incomplete",
            "invalid_response",
        ),
        (b"HANG".to_vec(), "timeout", "deadline_exceeded"),
    ] {
        let f = Fixture::new();
        let server = f.server(vec![reply]);
        let (out, state) = f.state("ok");
        server.join().unwrap();
        assert_eq!(out.status.code(), Some(1));
        let niri = component(&state, "niri");
        assert_eq!(niri["status"], status, "{niri}");
        assert_eq!(niri["reason"], reason);
    }
    let f = Fixture::new();
    let (_, state) = f.state("ok");
    assert_eq!(component(&state, "niri")["status"], "absent");
}

#[test]
fn partial_niri_keeps_only_successful_fields() {
    let f = Fixture::new();
    let server = f.server(vec![
        good_responses()[0].clone(),
        b"{\"Ok\":{\"Workspaces\":{}}}\n".to_vec(),
    ]);
    let (_, state) = f.state("ok");
    server.join().unwrap();
    let niri = component(&state, "niri");
    assert_eq!(niri["status"], "incomplete");
    assert_eq!(niri["data"], json!({"version":"26.04"}));
}

fn snapshot(root: &Path) -> BTreeMap<PathBuf, Vec<u8>> {
    let mut result = BTreeMap::new();
    for entry in fs::read_dir(root).unwrap() {
        let path = entry.unwrap().path();
        if path.is_dir() {
            result.insert(path.clone(), Vec::new());
            result.extend(snapshot(&path));
        } else {
            result.insert(path.clone(), fs::read(&path).unwrap());
        }
    }
    result
}

#[test]
fn map_and_state_do_not_write_or_create_any_profile_files() {
    let f = Fixture::new();
    fs::create_dir(f.root.join("config")).unwrap();
    fs::write(f.root.join("config/untouched"), b"SENTINEL\0\xff").unwrap();
    let before = snapshot(&f.root);
    for _ in 0..2 {
        for args in [
            vec!["version"],
            vec!["map"],
            vec!["map", "--json"],
            vec!["state"],
            vec!["state", "--json"],
            vec!["niri", "snapshot"],
            vec!["niri", "snapshot", "--json"],
        ] {
            let out = f.command().args(args).output().unwrap();
            assert!(matches!(out.status.code(), Some(0 | 1)));
            assert!(out.stderr.is_empty());
            assert_eq!(snapshot(&f.root), before);
        }
    }
}

#[test]
fn map_resolves_xdg_and_marks_planned_paths_without_creating_them() {
    let f = Fixture::new();
    let out = f.command().args(["map", "--json"]).output().unwrap();
    assert_eq!(out.status.code(), Some(0));
    let map: Value = serde_json::from_slice(&out.stdout).unwrap();
    assert_eq!(map["schema_version"], 1);
    let entries = map["entries"].as_array().unwrap();
    let find = |id| entries.iter().find(|e| e["id"] == id).unwrap();
    assert_eq!(
        find("personal_niri")["path"],
        json!(f.root.join("config/niri/config.kdl"))
    );
    assert_eq!(
        find("managed_generations")["lifecycle"],
        "isolated_profile_only"
    );
    assert_eq!(find("history")["lifecycle"], "isolated_profile_only");
    assert_eq!(find("adapters")["lifecycle"], "planned");
    assert!(!f.root.join("state").exists());
    let out = f
        .command()
        .env("XDG_CONFIG_HOME", "relative")
        .env_remove("XDG_RUNTIME_DIR")
        .args(["map", "--json"])
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(1));
    let map: Value = serde_json::from_slice(&out.stdout).unwrap();
    let entries = map["entries"].as_array().unwrap();
    assert_eq!(
        entries.iter().find(|e| e["id"] == "personal_niri").unwrap()["path"],
        json!(f.root.join("home/.config/niri/config.kdl"))
    );
    assert!(entries.iter().find(|e| e["id"] == "runtime").unwrap()["path"].is_null());
}

#[test]
fn no_environment_is_reported_not_invented() {
    let out = Command::new(env!("CARGO_BIN_EXE_emaki"))
        .env_clear()
        .env("PATH", "/nonexistent")
        .args(["state", "--json"])
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(1));
    let state: Value = serde_json::from_slice(&out.stdout).unwrap();
    assert_eq!(component(&state, "niri")["reason"], "socket_not_configured");
    assert_eq!(component(&state, "bluez")["reason"], "helper_missing");
    let out = Command::new(env!("CARGO_BIN_EXE_emaki"))
        .env_clear()
        .args(["map", "--json"])
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(1));
    assert!(!String::from_utf8(out.stdout).unwrap().contains("/home/"));
}
