use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};
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
fn every_map_entry_names_one_of_three_zones_and_its_attributes() {
    let f = Fixture::new();
    let out = f.command().args(["map", "--json"]).output().unwrap();
    let map: Value = serde_json::from_slice(&out.stdout).unwrap();
    assert_eq!(map["schema_version"], 1);
    let entries = map["entries"].as_array().unwrap();
    let mut zones = BTreeSet::new();
    for entry in entries {
        for field in ["zone", "owner", "change_via", "on_update", "history"] {
            assert!(
                entry[field].as_str().is_some_and(|v| !v.is_empty()),
                "{field}: {entry}"
            );
        }
        zones.insert(entry["zone"].as_str().unwrap());
    }
    // The three zones of the 2026-09-23 decision; a fourth name is a bug, not a new zone.
    assert_eq!(zones, BTreeSet::from(["managed", "package", "yours"]));
    let page = entries.iter().find(|e| e["id"] == "zones_doc").unwrap();
    let page = page["path"].as_str().unwrap();
    assert!(page.ends_with("/share/doc/emaki/ZONES.md"), "{page}");
    let text = String::from_utf8(f.command().arg("map").output().unwrap().stdout).unwrap();
    assert!(text.contains(&format!(
        "Zones (package, managed, yours) are explained in {page:?}\n"
    )));
    let settings = text.split("\nemaki_settings: ").nth(1).unwrap();
    let settings = settings.split_once("\n  owner: ").unwrap();
    assert!(settings.0.ends_with("zone: managed; on update: never_touched; change via: emaki_settings; history: emaki_settings"));
    assert!(
        settings
            .1
            .contains("isolated_profile_only, not connected yet;")
    );
}

/// Path patterns that docs/ZONES.md lists under each zone's heading, `{a,b}` groups expanded.
fn zone_page() -> BTreeMap<&'static str, Vec<Vec<String>>> {
    let mut sections: BTreeMap<&str, String> = BTreeMap::new();
    let mut zone = None;
    for line in include_str!("../../../docs/ZONES.md").lines() {
        if let Some(title) = line.strip_prefix("## ") {
            zone = match title.split('.').next() {
                Some("1") => Some("package"),
                Some("2") => Some("managed"),
                Some("3") => Some("yours"),
                _ => None,
            };
        } else if let Some(zone) = zone {
            let section = sections.entry(zone).or_default();
            section.push_str(line);
            section.push('\n');
        }
    }
    fn expand(word: &str) -> Vec<String> {
        match (word.find('{'), word.find('}')) {
            (Some(open), Some(close)) if open < close => word[open + 1..close]
                .split(',')
                .flat_map(|choice| {
                    expand(&format!("{}{choice}{}", &word[..open], &word[close + 1..]))
                })
                .collect(),
            _ => vec![word.to_owned()],
        }
    }
    sections
        .into_iter()
        .map(|(zone, section)| {
            // A `{a,b}` group may wrap onto the next line: drop whitespace inside braces.
            let mut depth = 0;
            let joined: String = section
                .chars()
                .filter(|&c| {
                    match c {
                        '{' => depth += 1,
                        '}' => depth -= 1,
                        _ => {}
                    }
                    depth == 0 || !c.is_whitespace()
                })
                .collect();
            let patterns = joined
                .split_whitespace()
                .map(|word| {
                    word.trim_matches('`')
                        .trim_end_matches([',', '.', ';', ':', ')'])
                        .trim_matches('`')
                })
                .filter(|word| word.starts_with('/') || word.starts_with("~/"))
                .flat_map(expand)
                .map(|pattern| pattern.split('/').map(str::to_owned).collect())
                .collect();
            (zone, patterns)
        })
        .collect()
}

/// One path component against one pattern component; `*` matches any run of characters.
fn name_matches(pattern: &str, name: &str) -> bool {
    match pattern.split_once('*') {
        None => pattern == name,
        Some((head, rest)) => name.strip_prefix(head).is_some_and(|tail| {
            (0..=tail.len()).any(|i| tail.is_char_boundary(i) && name_matches(rest, &tail[i..]))
        }),
    }
}

/// `**` matches any number of components, including none.
fn glob(pattern: &[String], path: &[&str]) -> bool {
    match pattern.split_first() {
        None => path.is_empty(),
        Some((first, rest)) if first == "**" => {
            glob(rest, path) || (!path.is_empty() && glob(pattern, &path[1..]))
        }
        Some((first, rest)) => path
            .split_first()
            .is_some_and(|(name, tail)| name_matches(first, name) && glob(rest, tail)),
    }
}

/// The path is listed itself, or is a directory above a listed path (`/etc/xdg`).
fn listed(pattern: &[String], path: &[&str]) -> bool {
    glob(pattern, path)
        || (path.len() < pattern.len()
            && path
                .iter()
                .zip(pattern)
                .all(|(name, p)| p != "**" && name_matches(p, name)))
}

/// The page names the packaged locations; a probe build elsewhere has nothing to compare.
fn built_for_default_locations() -> bool {
    let locations = [
        (option_env!("EMAKI_PREFIX"), "/usr"),
        (option_env!("EMAKI_DATADIR"), "/usr/share/emaki"),
        (option_env!("EMAKI_SYSCONFDIR"), "/etc"),
    ];
    let default = !locations
        .iter()
        .any(|(value, default)| value.is_some_and(|v| v != *default));
    if !default {
        eprintln!("skipped: built for non-default locations {locations:?}");
    }
    default
}

fn zone_map() -> Value {
    let out = Command::new(env!("CARGO_BIN_EXE_emaki"))
        .env_clear()
        .env("HOME", "/zones-home")
        .args(["map", "--json"])
        .output()
        .unwrap();
    serde_json::from_slice(&out.stdout).unwrap()
}

/// The paths of the page's list under "Files in /etc that pacman protects".
fn protected_on_page() -> BTreeSet<String> {
    let page = include_str!("../../../docs/ZONES.md");
    let start = page
        .find("Files in /etc that pacman protects")
        .expect("the zone page lists the files pacman protects");
    let mut paths = BTreeSet::new();
    let mut in_list = false;
    for line in page[start..].lines() {
        in_list |= line.starts_with("- ");
        if in_list && line.trim().is_empty() {
            break;
        }
        if in_list {
            paths.extend(
                line.split_whitespace()
                    .map(|word| word.trim_end_matches(','))
                    .filter(|word| word.starts_with("/etc/"))
                    .map(str::to_owned),
            );
        }
    }
    paths
}

/// The files the Emaki packages mark `backup=` in packaging/*/PKGBUILD.
fn backup_files() -> BTreeSet<String> {
    let packaging = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../packaging");
    let mut files = BTreeSet::new();
    for entry in fs::read_dir(packaging).unwrap() {
        let Ok(recipe) = fs::read_to_string(entry.unwrap().path().join("PKGBUILD")) else {
            continue;
        };
        let Some(start) = recipe.find("\nbackup=(") else {
            continue;
        };
        let list = &recipe[start + "\nbackup=(".len()..];
        files.extend(
            list[..list.find(')').unwrap()]
                .split_whitespace()
                .map(|word| format!("/{}", word.trim_matches(['\'', '"']))),
        );
    }
    files
}

#[test]
fn every_protected_etc_file_is_on_the_zone_page_and_in_the_map() {
    // The other direction of every_map_path_sits_in_its_own_zone_on_the_zone_page: a file a
    // package protects in /etc must reach the page and `emaki map`, and neither may name a
    // file no package protects.
    if !built_for_default_locations() {
        return;
    }
    let packaged = backup_files();
    assert!(packaged.len() >= 10, "{packaged:?}");
    assert_eq!(
        protected_on_page(),
        packaged,
        "docs/ZONES.md against the backup= arrays"
    );
    let mapped: BTreeSet<String> = zone_map()["entries"]
        .as_array()
        .unwrap()
        .iter()
        // Emaki's own files; `system_xdg` is the /etc/xdg directory all packages share.
        .filter(|entry| {
            entry["on_update"] == "replaced_unless_edited" && entry["owner"] == "emaki_package"
        })
        .map(|entry| entry["path"].as_str().unwrap().to_owned())
        .collect();
    assert_eq!(mapped, packaged, "emaki map against the backup= arrays");
}

#[test]
fn every_etc_file_of_emaki_config_is_on_the_zone_page() {
    // expected-files.list is emaki-config's file list; package() fails on any difference.
    if !built_for_default_locations() {
        return;
    }
    let page = zone_page();
    let mut checked = 0;
    for path in include_str!("../../../packaging/emaki-config/expected-files.list")
        .lines()
        .filter(|path| path.starts_with("/etc/"))
    {
        let parts: Vec<&str> = path.split('/').collect();
        assert!(
            page["package"].iter().any(|pattern| glob(pattern, &parts)),
            "{path} is not in the package zone of docs/ZONES.md"
        );
        checked += 1;
    }
    assert!(checked >= 10, "only {checked} files compared");
}

#[test]
fn the_snapshot_boot_hook_is_a_file_of_emaki_config() {
    // emaki-config ships the hook in mkinitcpio's own directory; the installer writes no
    // copy into /etc/initcpio, so the map names exactly the packaged files, in zone 1.
    if !built_for_default_locations() {
        return;
    }
    let shipped: BTreeSet<String> =
        include_str!("../../../packaging/emaki-config/expected-files.list")
            .lines()
            .filter(|path| path.contains("/initcpio/"))
            .map(str::to_owned)
            .collect();
    assert_eq!(shipped.len(), 2, "{shipped:?}");
    let map = zone_map();
    let mut mapped = BTreeSet::new();
    for entry in map["entries"].as_array().unwrap() {
        let Some(path) = entry["path"].as_str().filter(|p| p.contains("/initcpio/")) else {
            continue;
        };
        assert_eq!(
            (entry["zone"].as_str(), entry["owner"].as_str()),
            (Some("package"), Some("emaki_package")),
            "{entry}"
        );
        mapped.insert(path.to_owned());
    }
    assert_eq!(
        mapped, shipped,
        "emaki map against emaki-config's file list"
    );
}

#[test]
fn every_map_path_sits_in_its_own_zone_on_the_zone_page() {
    if !built_for_default_locations() {
        return;
    }
    let page = zone_page();
    assert_eq!(
        page.keys().copied().collect::<Vec<_>>(),
        ["managed", "package", "yours"]
    );
    assert!(
        page.values().all(|patterns| patterns.len() >= 3),
        "{page:?}"
    );
    let out = Command::new(env!("CARGO_BIN_EXE_emaki"))
        .env_clear()
        .env("HOME", "/zones-home")
        .args(["map", "--json"])
        .output()
        .unwrap();
    let map: Value = serde_json::from_slice(&out.stdout).unwrap();
    let mut checked = 0;
    for entry in map["entries"].as_array().unwrap() {
        // Planned locations do not exist yet, so the page does not describe them.
        if entry["lifecycle"] == "planned" {
            continue;
        }
        let path = entry["path"].as_str().unwrap();
        let path = path
            .strip_prefix("/zones-home/")
            .map_or(path.to_owned(), |rest| format!("~/{rest}"));
        let parts: Vec<&str> = path.split('/').collect();
        let zones: Vec<&str> = page
            .iter()
            .filter(|(_, patterns)| patterns.iter().any(|p| listed(p, &parts)))
            .map(|(zone, _)| *zone)
            .collect();
        assert_eq!(zones, [entry["zone"].as_str().unwrap()], "{path}");
        checked += 1;
    }
    assert!(checked >= 40, "only {checked} entries compared");
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
