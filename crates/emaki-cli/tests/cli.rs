use std::process::{Command, Output};

fn run(args: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_emaki"))
        .args(args)
        // Version and help must work without a desktop session or user profile.
        .env_clear()
        .output()
        .expect("CLI process should start")
}

#[test]
fn version_is_available_without_session_or_home() {
    for args in [["version"], ["--version"], ["-V"]] {
        let output = run(&args);
        assert!(output.status.success());
        assert_eq!(
            String::from_utf8(output.stdout).unwrap(),
            format!("emaki {}\n", emaki_core::VERSION)
        );
        assert!(output.stderr.is_empty());
    }
}

#[test]
fn help_only_advertises_implemented_commands() {
    for args in [vec!["help"], vec!["--help"], vec!["-h"]] {
        let output = run(&args);
        assert!(output.status.success());
        let text = String::from_utf8(output.stdout).unwrap();
        assert!(text.starts_with("Usage: emaki <command> [options]\n"));
        assert!(text.contains("version"));
        assert!(text.contains("state [--json]"));
        assert!(text.contains("map [--json]"));
        assert!(!text.contains("apply"));
        assert!(output.stderr.is_empty());
    }
}

#[test]
fn unsupported_commands_and_extra_arguments_fail_explicitly() {
    for args in [
        vec!["state", "--timeout-ms", "0"],
        vec!["state", "--timeout-ms", "10001"],
        vec!["state", "--timeout-ms", "oops"],
        vec!["state", "--timeout-ms"],
        vec!["state", "--json", "--json"],
        vec!["state", "--timeout-ms", "50", "--timeout-ms", "50"],
        vec!["map", "--timeout-ms", "100"],
        vec!["niri", "focus"],
        vec!["niri", "watch", "--json", "--json"],
        vec!["niri", "snapshot", "--timeout-ms", "0"],
        vec!["niri", "watch", "--timeout-ms", "10001"],
        vec!["niri", "watch", "--timeout-ms"],
        vec!["niri", "snapshot", "--app-id"],
        vec!["niri", "focus-window"],
        vec!["niri", "focus-workspace"],
        vec!["niri", "close-window"],
        vec!["niri", "move-window-to-workspace", "--id", "1"],
        vec!["niri", "move-window-to-workspace", "--workspace-id", "20"],
        vec!["niri", "focus-window", "--id", "-1"],
        vec!["niri", "focus-window", "--id", "+1"],
        vec!["niri", "focus-window", "--id", "1.0"],
        vec!["niri", "focus-window", "--id", "18446744073709551616"],
        vec!["niri", "focus-window", "--pid", "1"],
        vec!["niri", "focus-workspace", "--idx", "1"],
        vec!["niri", "close-window", "--id", "1", "--id", "2"],
        vec!["niri", "close-window", "--id", "1", "--workspace-id", "20"],
        vec!["niri", "switch-layout"],
        vec!["niri", "switch-layout", "--id", "1"],
        vec!["niri", "switch-layout", "--index", "256"],
        vec!["niri", "switch-layout", "--index", "next"],
        vec!["niri", "switch-layout", "--index", "-1"],
        vec!["niri", "switch-layout", "--index", "1", "--index", "1"],
        vec![
            "niri",
            "switch-layout",
            "--index",
            "1",
            "--workspace-id",
            "20",
        ],
        vec!["niri", "focus-window", "--id", "1", "--index", "0"],
        vec!["niri", "focus-window", "--id", "1", "--timeout-ms", "0"],
        vec!["niri", "focus-window", "--id", "1", "--json", "--json"],
        vec!["settings", "set"],
        vec!["settings", "set", "appearance.gaps"],
        vec!["settings", "get"],
        vec!["settings", "list", "--json", "--json"],
        vec![
            "settings",
            "set",
            "appearance.gaps",
            "4",
            "--timeout-ms",
            "0",
        ],
        vec!["settings", "list", "--profile-root"],
        vec!["settings", "undo"],
        vec!["apply"],
        vec!["--unknown"],
        vec!["version", "--json"],
        vec!["version", "extra", "argument"],
        vec!["--help", "extra"],
    ] {
        let output = run(&args);
        assert_eq!(output.status.code(), Some(2));
        assert!(output.stdout.is_empty());
        assert!(String::from_utf8(output.stderr).unwrap().contains("Usage:"));
    }
}

#[test]
fn valid_explicit_commands_have_single_line_rejection_without_a_session() {
    for args in [
        vec![
            "niri",
            "focus-window",
            "--id",
            "18446744073709551615",
            "--json",
        ],
        vec!["niri", "focus-workspace", "--id", "20", "--json"],
        vec![
            "niri",
            "move-window-to-workspace",
            "--workspace-id",
            "20",
            "--json",
            "--id",
            "1",
        ],
        vec!["niri", "close-window", "--id", "0", "--json"],
        vec!["niri", "switch-layout", "--index", "255", "--json"],
    ] {
        let result = run(&args);
        assert_eq!(result.status.code(), Some(1));
        assert!(result.stderr.is_empty());
        assert_eq!(result.stdout.last(), Some(&b'\n'));
        assert_eq!(
            result.stdout.iter().filter(|&&byte| byte == b'\n').count(),
            1
        );
        let view: serde_json::Value = serde_json::from_slice(&result.stdout).unwrap();
        assert_eq!(view["schema_version"], 1);
        assert_eq!(view["outcome"], "rejected");
        assert_eq!(view["delivery"], "not_sent");
        assert_eq!(view["reason"], "socket_not_configured");
        if args[1] == "focus-window" {
            assert_eq!(view["command"]["id"].as_u64(), Some(u64::MAX));
        }
        if args[1] == "switch-layout" {
            assert_eq!(view["command"]["index"].as_u64(), Some(255));
        }
    }
}

#[test]
fn niri_snapshot_without_socket_is_explicit_and_help_does_not_connect() {
    let result = run(&["niri", "snapshot", "--json"]);
    assert_eq!(result.status.code(), Some(1));
    let view: serde_json::Value = serde_json::from_slice(&result.stdout).unwrap();
    assert_eq!(view["schema_version"], 1);
    assert_eq!(view["connection"]["status"], "disconnected");
    assert_eq!(view["connection"]["reason"], "socket_not_configured");
    assert!(view["model"].is_null());
    assert!(result.stderr.is_empty());
    for args in [
        vec!["niri", "--help"],
        vec!["niri", "watch", "--help"],
        vec!["niri", "snapshot", "--help"],
    ] {
        let result = run(&args);
        assert!(result.status.success());
        assert!(String::from_utf8(result.stdout).unwrap().contains("26.04"));
    }
}

#[test]
fn niri_watch_flushes_status_lines_before_exit() {
    use std::io::{BufRead, BufReader};
    use std::process::Stdio;
    let mut child = Command::new(env!("CARGO_BIN_EXE_emaki"))
        .args(["niri", "watch", "--json"])
        .env_clear()
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .unwrap();
    let stdout = child.stdout.take().unwrap();
    let (sender, receiver) = std::sync::mpsc::channel();
    let reader = std::thread::spawn(move || {
        let lines: Result<Vec<_>, _> = BufReader::new(stdout).lines().take(2).collect();
        let _ = sender.send(lines);
    });
    let result = receiver.recv_timeout(std::time::Duration::from_secs(2));
    // Clean up even when flush/assertions fail: never leave a watch in the session.
    child.kill().unwrap();
    child.wait().unwrap();
    reader.join().unwrap();
    let lines = result.unwrap().unwrap();
    assert_eq!(lines.len(), 2);
    let first: serde_json::Value = serde_json::from_str(&lines[0]).unwrap();
    let second: serde_json::Value = serde_json::from_str(&lines[1]).unwrap();
    assert_eq!(first["connection"]["status"], "connecting");
    assert_eq!(second["connection"]["status"], "disconnected");
    assert_eq!(second["connection"]["reason"], "socket_not_configured");
    assert!(second["model"].is_null());
}

#[cfg(unix)]
#[test]
fn non_utf8_argument_is_a_usage_error_not_a_panic() {
    use std::ffi::OsString;
    use std::os::unix::ffi::OsStringExt;

    let output = Command::new(env!("CARGO_BIN_EXE_emaki"))
        .arg(OsString::from_vec(vec![0xff]))
        .env_clear()
        .output()
        .expect("CLI process should start");
    assert_eq!(output.status.code(), Some(2));
    assert!(output.stdout.is_empty());
}

/// Runs `emaki niri snapshot` against a fixture niri: it answers the four requests, then
/// writes `events` on the subscription. The stream stays open until the client exits, so a
/// missing initial event ends in the client's timeout rather than in a closed connection.
fn niri_snapshot_against(
    name: &str,
    query: bool,
    events: Vec<serde_json::Value>,
    timeout_ms: &str,
) -> Output {
    use serde_json::json;
    use std::io::{BufRead, BufReader, Write};
    use std::os::unix::net::UnixListener;
    use std::time::{Duration, Instant};
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../.cache/tmp")
        .join(format!("{name}-{}", std::process::id()));
    std::fs::create_dir_all(&root).unwrap();
    let path = root.join("n.sock");
    let listener = UnixListener::bind(&path).unwrap();
    listener.set_nonblocking(true).unwrap();
    let (exited, client_exited) = std::sync::mpsc::channel::<()>();
    let server = std::thread::spawn(move || {
        let deadline = Instant::now() + Duration::from_secs(3);
        let stream = loop {
            match listener.accept() {
                Ok((stream, _)) => break stream,
                Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                    assert!(Instant::now() < deadline, "snapshot never connected");
                    std::thread::sleep(Duration::from_millis(2));
                }
                Err(e) => panic!("{e}"),
            }
        };
        stream
            .set_read_timeout(Some(Duration::from_secs(2)))
            .unwrap();
        let mut reader = BufReader::new(stream);
        for (expected, reply) in [
            ("Version", json!({"Ok":{"Version":"26.04 (fixture)"}})),
            ("Outputs", json!({"Ok":{"Outputs":{}}})),
            (
                "OverviewState",
                json!({"Ok":{"OverviewState":{"is_open":query}}}),
            ),
            ("EventStream", json!({"Ok":"Handled"})),
        ] {
            let mut line = String::new();
            reader.read_line(&mut line).unwrap();
            assert_eq!(serde_json::from_str::<String>(&line).unwrap(), expected);
            writeln!(reader.get_mut(), "{reply}").unwrap();
        }
        for value in events {
            writeln!(reader.get_mut(), "{value}").unwrap();
        }
        let _ = client_exited.recv();
    });
    let result = Command::new(env!("CARGO_BIN_EXE_emaki"))
        .args(["niri", "snapshot", "--json", "--timeout-ms", timeout_ms])
        .env_clear()
        .env("NIRI_SOCKET", &path)
        .output()
        .unwrap();
    let _ = exited.send(());
    server.join().unwrap();
    std::fs::remove_file(path).unwrap();
    std::fs::remove_dir(root).unwrap();
    result
}

/// The initial events before the casts, in niri 26.04's order apart from the first two.
fn niri_initial_events_without_casts(overview: bool) -> Vec<serde_json::Value> {
    use serde_json::json;
    vec![
        json!({"WindowsChanged":{"windows":[]}}),
        json!({"WorkspacesChanged":{"workspaces":[]}}),
        json!({"KeyboardLayoutsChanged":{"keyboard_layouts":{"names":["English (US)"],"current_idx":0}}}),
        json!({"OverviewOpenedOrClosed":{"is_open":overview}}),
    ]
}

#[test]
fn niri_snapshot_serializes_overview_from_query_and_newer_initial_event() {
    use serde_json::json;
    for (query, event) in [(false, false), (true, true), (false, true), (true, false)] {
        let mut events = niri_initial_events_without_casts(event);
        events.push(json!({"ConfigLoaded":{"failed":false}}));
        events.push(
            json!({"CastsChanged":{"casts":[{"stream_id":42,"session_id":7,
            "kind":"PipeWire","target":{"Nothing":{}},"is_dynamic_target":false,
            "is_active":true,"pid":4321,"pw_node_id":99}]}}),
        );
        let result = niri_snapshot_against("overview-snapshot", query, events, "1000");
        assert!(result.status.success(), "{result:?}");
        assert!(result.stderr.is_empty());
        let view: serde_json::Value = serde_json::from_slice(&result.stdout).unwrap();
        assert_eq!(view["model"]["overview_open"], event);
        assert_eq!(view["connection"]["status"], "connected");
        // Casts come only from the initial CastsChanged, which niri sends last.
        assert_eq!(view["model"]["casts"]["42"]["stream_id"], 42, "{view}");
    }
}

#[test]
fn niri_snapshot_waits_for_the_initial_casts_event() {
    // Without the initial CastsChanged a snapshot cannot tell "no casts" from "not read yet".
    let events = niri_initial_events_without_casts(false);
    let result = niri_snapshot_against("snapshot-no-casts", false, events, "300");
    assert_eq!(result.status.code(), Some(1), "{result:?}");
    let view: serde_json::Value = serde_json::from_slice(&result.stdout).unwrap();
    assert_eq!(view["connection"]["reason"], "initial_state_timeout");
    assert!(view["model"].is_null(), "{view}");
}
