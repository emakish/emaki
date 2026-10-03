use emaki_core::niri::actions::{self, Command, Delivery, Outcome, Phase};
use emaki_core::niri::{ConnectionStatus, Model, Observation, Observer};
use serde_json::{Value, json};
use std::fs;
use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::{Path, PathBuf};
use std::sync::{
    Arc, Mutex,
    atomic::{AtomicBool, AtomicU64, Ordering},
};
use std::time::{Duration, Instant};

static COUNTER: AtomicU64 = AtomicU64::new(0);
const TIMEOUT: Duration = Duration::from_millis(300);

fn layout() -> Value {
    json!({"pos_in_scrolling_layout":[1,1],"tile_size":[700.0,600.0],
        "window_size":[696,596],"tile_pos_in_workspace_view":null,"window_offset_in_tile":[2.0,2.0]})
}
fn window(id: u64, workspace: u64, focus: bool) -> Value {
    json!({"id":id,"title":"SECRET_TITLE","app_id":"SECRET_APP","pid":1234,
        "workspace_id":workspace,"is_focused":focus,"is_floating":false,"is_urgent":false,
        "layout":layout(),"focus_timestamp":null})
}
fn workspace(id: u64, idx: u8, output: &str, focus: bool, window: Option<u64>) -> Value {
    json!({"id":id,"idx":idx,"name":"SECRET_NAME","output":output,"is_urgent":false,
        "is_active":idx==1,"is_focused":focus,"active_window_id":window})
}
fn monitor(name: &str) -> Value {
    json!({"name":name,"make":"SECRET_MAKE","model":"SECRET_MODEL","serial":"SECRET_SERIAL",
        "physical_size":null,"modes":[],"current_mode":null,"is_custom_mode":false,
        "vrr_supported":false,"vrr_enabled":false,"logical":null})
}
#[derive(Clone)]
struct Data {
    version: String,
    windows: Value,
    workspaces: Value,
    outputs: Value,
    overview_open: bool,
    layouts: Value,
    overview_reply: Option<Value>,
    initial_override: Option<String>,
    action_mode: ActionMode,
}
#[derive(Clone, Copy)]
enum ActionMode {
    Apply,
    Ignore,
    Delay,
    Reject,
    DropReply,
    DropConfirm,
    HangReply,
    HangConfirm,
    BadReply,
    GoneWindow,
    GoneWorkspace,
    GoneLayout,
}
impl Default for Data {
    fn default() -> Self {
        Self {
            version: "26.04 (fake)".into(),
            windows: json!([window(1, 10, true), window(2, 20, false)]),
            workspaces: json!([
                workspace(10, 1, "A", true, Some(1)),
                workspace(11, 2, "A", false, None),
                workspace(20, 1, "B", false, Some(2))
            ]),
            outputs: json!({"A":monitor("A"),"B":monitor("B")}),
            overview_open: false,
            layouts: json!({"names":["English (US)","Russian"],"current_idx":0}),
            overview_reply: None,
            initial_override: None,
            action_mode: ActionMode::Apply,
        }
    }
}
struct Fake {
    root: PathBuf,
    path: PathBuf,
    data: Arc<Mutex<Data>>,
    streams: Arc<Mutex<Vec<UnixStream>>>,
    stop: Arc<AtomicBool>,
    server: Option<std::thread::JoinHandle<()>>,
    actions: Arc<Mutex<Vec<Value>>>,
    connections: Arc<AtomicU64>,
}
impl Fake {
    fn new() -> Self {
        let root = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../.cache/tmp")
            .join(format!(
                "niri-{}-{}",
                std::process::id(),
                COUNTER.fetch_add(1, Ordering::Relaxed)
            ));
        fs::create_dir_all(&root).unwrap();
        let root = root.canonicalize().unwrap();
        let mut fake = Self {
            path: root.join("n.sock"),
            root,
            data: Arc::new(Mutex::new(Data::default())),
            streams: Arc::new(Mutex::new(Vec::new())),
            stop: Arc::new(AtomicBool::new(false)),
            server: None,
            actions: Arc::new(Mutex::new(Vec::new())),
            connections: Arc::new(AtomicU64::new(0)),
        };
        fake.start();
        fake
    }
    fn start(&mut self) {
        self.stop.store(false, Ordering::Relaxed);
        let listener = UnixListener::bind(&self.path).unwrap();
        listener.set_nonblocking(true).unwrap();
        let data = self.data.clone();
        let streams = self.streams.clone();
        let stop = self.stop.clone();
        let actions = self.actions.clone();
        let connections = self.connections.clone();
        self.server = Some(std::thread::spawn(move || {
            while !stop.load(Ordering::Relaxed) {
                let socket = match listener.accept() {
                    Ok((socket, _)) => socket,
                    Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                        std::thread::sleep(Duration::from_millis(2));
                        continue;
                    }
                    Err(e) => panic!("{e}"),
                };
                socket
                    .set_read_timeout(Some(Duration::from_secs(2)))
                    .unwrap();
                let mut reader = BufReader::new(socket);
                connections.fetch_add(1, Ordering::Relaxed);
                let mut action_seen = false;
                let mut pending = None;
                let mut stale_reads = 0;
                loop {
                    let mut line = String::new();
                    if !matches!(reader.read_line(&mut line), Ok(n) if n > 0) {
                        break;
                    }
                    if action_seen
                        && matches!(data.lock().unwrap().action_mode, ActionMode::DropConfirm)
                    {
                        break;
                    }
                    if action_seen
                        && matches!(data.lock().unwrap().action_mode, ActionMode::HangConfirm)
                    {
                        continue;
                    }
                    if pending.is_some()
                        && (line.trim() == "\"Windows\"" || line.trim() == "\"Workspaces\"")
                    {
                        if stale_reads == 0 {
                            *data.lock().unwrap() = pending.take().unwrap();
                        } else {
                            stale_reads -= 1;
                        }
                    }
                    let mut d = data.lock().unwrap().clone();
                    let response = match line.trim() {
                        "\"Version\"" => json!({"Ok":{"Version":d.version}}),
                        "\"Outputs\"" => json!({"Ok":{"Outputs":d.outputs}}),
                        "\"Windows\"" => json!({"Ok":{"Windows":d.windows}}),
                        "\"Workspaces\"" => json!({"Ok":{"Workspaces":d.workspaces}}),
                        "\"KeyboardLayouts\"" => json!({"Ok":{"KeyboardLayouts":d.layouts}}),
                        "\"OverviewState\"" => d.overview_reply.unwrap_or_else(
                            || json!({"Ok":{"OverviewState":{"is_open":d.overview_open}}}),
                        ),
                        "\"EventStream\"" => {
                            // Coalesced ACK and initial events exercise retained framing.
                            let bytes = d.initial_override.unwrap_or_else(|| {
                                format!(
                                    "{}\n{}\n{}\n{}\n{}\n",
                                    json!({"Ok":"Handled"}),
                                    json!({"WorkspacesChanged":{"workspaces":d.workspaces}}),
                                    json!({"WindowsChanged":{"windows":d.windows}}),
                                    json!({"KeyboardLayoutsChanged":{"keyboard_layouts":d.layouts}}),
                                    json!({"OverviewOpenedOrClosed":{"is_open":d.overview_open}})
                                )
                            });
                            if reader.get_mut().write_all(bytes.as_bytes()).is_ok() {
                                streams.lock().unwrap().push(reader.into_inner());
                            }
                            break;
                        }
                        request if request.starts_with("{\"Action\":") => {
                            let request: Value = serde_json::from_str(request).unwrap();
                            let action = request["Action"].clone();
                            actions.lock().unwrap().push(action.clone());
                            action_seen = true;
                            match d.action_mode {
                                ActionMode::Reject => json!({"Err":"SECRET_REJECTION"}),
                                ActionMode::HangReply => continue,
                                ActionMode::BadReply => {
                                    reader
                                        .get_mut()
                                        .write_all(b"SECRET invalid JSON\n")
                                        .unwrap();
                                    break;
                                }
                                mode => {
                                    if !matches!(mode, ActionMode::Ignore | ActionMode::DropConfirm)
                                    {
                                        perform(&mut d, &action);
                                        if matches!(mode, ActionMode::GoneWindow) {
                                            d.windows = json!([]);
                                        }
                                        if matches!(mode, ActionMode::GoneWorkspace) {
                                            d.workspaces = json!([]);
                                        }
                                        if matches!(mode, ActionMode::GoneLayout) {
                                            d.layouts =
                                                json!({"names":["English (US)"],"current_idx":0});
                                        }
                                        if matches!(mode, ActionMode::Delay) {
                                            pending = Some(d);
                                            stale_reads = 2;
                                        } else {
                                            *data.lock().unwrap() = d;
                                        }
                                    }
                                    if matches!(mode, ActionMode::DropReply) {
                                        break;
                                    }
                                    json!({"Ok":"Handled"})
                                }
                            }
                        }
                        other => panic!("unexpected request: {other}"),
                    };
                    if writeln!(reader.get_mut(), "{response}").is_err() {
                        break;
                    }
                }
            }
        }));
    }
    fn stop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        if let Some(server) = self.server.take() {
            server.join().unwrap();
        }
        self.streams.lock().unwrap().clear();
        if self.path.exists() {
            fs::remove_file(&self.path).unwrap();
        }
    }
    fn observer(&self) -> Observer {
        Observer::at(Some(self.path.clone()), TIMEOUT)
    }
    fn raw(&self, bytes: &[u8]) {
        let deadline = Instant::now() + Duration::from_secs(1);
        while self.streams.lock().unwrap().is_empty() {
            assert!(
                Instant::now() < deadline,
                "server has not retained subscription"
            );
            std::thread::sleep(Duration::from_millis(2));
        }
        let mut streams = self.streams.lock().unwrap();
        assert!(!streams.is_empty());
        streams.retain_mut(|s| s.write_all(bytes).is_ok());
    }
    fn event(&self, event: Value) {
        self.raw(format!("{event}\n").as_bytes());
    }
}
fn perform(data: &mut Data, action: &Value) {
    if let Some(cmd) = action.get("FocusWindow") {
        for w in data.windows.as_array_mut().unwrap() {
            w["is_focused"] = json!(w["id"] == cmd["id"]);
        }
    } else if let Some(cmd) = action.get("CloseWindow") {
        data.windows
            .as_array_mut()
            .unwrap()
            .retain(|w| w["id"] != cmd["id"]);
    } else if let Some(cmd) = action.get("FocusWorkspace") {
        assert!(cmd["reference"]["Id"].is_u64());
        for w in data.workspaces.as_array_mut().unwrap() {
            w["is_focused"] = json!(w["id"] == cmd["reference"]["Id"]);
        }
    } else if let Some(cmd) = action.get("MoveWindowToWorkspace") {
        assert!(cmd["window_id"].is_u64());
        assert!(cmd["reference"]["Id"].is_u64());
        for w in data.windows.as_array_mut().unwrap() {
            if w["id"] == cmd["window_id"] {
                w["workspace_id"] = cmd["reference"]["Id"].clone();
            }
        }
    } else if let Some(cmd) = action.get("SwitchLayout") {
        let index = cmd["layout"]["Index"].as_u64().expect("explicit index");
        data.layouts["current_idx"] = json!(index);
    } else {
        panic!("unexpected action: {action}");
    }
}
impl Drop for Fake {
    fn drop(&mut self) {
        self.stop();
        fs::remove_dir_all(&self.root).unwrap();
    }
}
fn until(observer: &mut Observer, predicate: impl Fn(&Observation) -> bool) -> Observation {
    let deadline = Instant::now() + Duration::from_secs(4);
    while Instant::now() < deadline {
        if let Some(view) = observer.poll()
            && predicate(&view)
        {
            return view;
        }
    }
    panic!("expected observation did not arrive");
}
fn model_until(observer: &mut Observer, predicate: impl Fn(&Model) -> bool) -> Observation {
    until(observer, |v| v.model.as_ref().is_some_and(&predicate))
}
fn ready(observer: &mut Observer) -> Observation {
    assert_eq!(
        observer.poll().unwrap().connection.status,
        ConnectionStatus::Connecting
    );
    until(observer, |v| {
        v.connection.status == ConnectionStatus::Connected
    })
}

#[test]
fn initial_stream_is_complete_private_and_keeps_ids_separate_from_indices() {
    let fake = Fake::new();
    let mut observer = fake.observer();
    let view = ready(&mut observer);
    let model = view.model.as_ref().unwrap();
    assert_eq!(model.windows.len(), 2);
    assert_eq!(model.workspaces[&10].idx, 1);
    assert_eq!(model.workspaces[&20].idx, 1);
    assert_eq!(model.focused_output.as_deref(), Some("A"));
    assert!(!model.links_pending);
    assert!(!model.overview_open);
    let text = serde_json::to_string(&view).unwrap();
    for marker in ["SECRET", "app_id", "title", "pid", "/home/", "serial"] {
        assert!(!text.contains(marker));
    }
    assert_eq!(view.generation, 1);
    // Private-only changes do not produce model changes or retain private strings.
    fake.event(json!({"WindowOpenedOrChanged":{"window":window(1,10,true)}}));
    assert!(observer.poll().is_none());
    fake.event(json!({"ScreenshotCaptured":{"path":"/SECRET/private/screenshot.png"}}));
    assert!(observer.poll().is_none());
}

#[test]
fn missing_initial_snapshot_and_rejected_subscription_never_publish_partial_model() {
    for (initial, reason) in [
        (
            "{\"Ok\":\"Handled\"}\n{\"WorkspacesChanged\":{\"workspaces\":[]}}\n",
            "initial_state_timeout",
        ),
        (
            "{\"Ok\":\"Handled\"}\n{\"WorkspacesChanged\":{\"workspaces\":[]}}\n{\"WindowsChanged\":{\"windows\":[]}}\n",
            "initial_state_timeout",
        ),
        (
            "{\"Ok\":\"Handled\"}\n{\"WorkspacesChanged\":{\"workspaces\":[]}}\n{\"WindowsChanged\":{\"windows\":[]}}\n{\"KeyboardLayoutsChanged\":{\"keyboard_layouts\":{\"names\":[\"English (US)\"],\"current_idx\":0}}}\n",
            "initial_state_timeout",
        ),
        ("{\"Err\":\"SECRET\"}\n", "request_rejected"),
    ] {
        let fake = Fake::new();
        fake.data.lock().unwrap().initial_override = Some(initial.into());
        let mut observer = fake.observer();
        let start = Instant::now();
        let lost = until(&mut observer, |v| {
            v.connection.status == ConnectionStatus::Disconnected
        });
        assert_eq!(lost.connection.reason, reason);
        assert!(lost.model.is_none());
        assert_eq!(lost.generation, 0);
        assert!(start.elapsed() < Duration::from_secs(2));
        assert!(!serde_json::to_string(&lost).unwrap().contains("SECRET"));
    }
}

#[test]
fn failed_output_poll_invalidates_model_and_recovers_with_new_generation() {
    let fake = Fake::new();
    let mut observer = fake.observer();
    ready(&mut observer);
    fake.data.lock().unwrap().outputs = Value::Null;
    let lost = until(&mut observer, |v| {
        v.connection.status == ConnectionStatus::Disconnected
    });
    assert_eq!(lost.connection.reason, "invalid_response");
    assert!(lost.model.is_none());
    fake.data.lock().unwrap().outputs = json!({"A":monitor("A"),"B":monitor("B")});
    let again = until(&mut observer, |v| {
        v.connection.status == ConnectionStatus::Connected
    });
    assert_eq!(again.generation, 2);
}

#[test]
fn events_update_open_close_move_focus_dynamic_workspaces_and_monitor_inventory() {
    let fake = Fake::new();
    let mut observer = fake.observer();
    ready(&mut observer);
    fake.event(json!({"WorkspaceActiveWindowChanged":{"workspace_id":11,"active_window_id":3}}));
    assert!(
        model_until(&mut observer, |m| m.links_pending)
            .model
            .unwrap()
            .links_pending
    );
    fake.event(json!({"WindowOpenedOrChanged":{"window":window(3,11,false)}}));
    model_until(&mut observer, |m| {
        m.windows.contains_key(&3) && !m.links_pending
    });
    fake.event(json!({"WorkspaceActivated":{"id":11,"focused":true}}));
    let view = model_until(&mut observer, |m| m.workspaces[&11].is_focused);
    let m = view.model.unwrap();
    assert!(!m.workspaces[&10].is_active);
    assert!(m.workspaces[&20].is_active);
    fake.event(json!({"WindowFocusChanged":{"id":3}}));
    let m = model_until(&mut observer, |m| m.windows[&3].is_focused)
        .model
        .unwrap();
    assert!(!m.windows[&1].is_focused);
    fake.event(json!({"WindowOpenedOrChanged":{"window":window(3,20,true)}}));
    model_until(&mut observer, |m| m.windows[&3].workspace_id == Some(20));
    let ws = json!([
        workspace(10, 2, "B", false, Some(1)),
        workspace(20, 1, "B", true, Some(3))
    ]);
    fake.data.lock().unwrap().outputs = json!({"B":monitor("B")});
    fake.event(json!({"WorkspacesChanged":{"workspaces":ws}}));
    let m = model_until(&mut observer, |m| !m.workspaces.contains_key(&11))
        .model
        .unwrap();
    assert_eq!(m.workspaces[&10].output.as_deref(), Some("B"));
    assert_eq!(m.workspaces[&10].idx, 2);
    assert_eq!(m.focused_output.as_deref(), Some("B"));
    model_until(&mut observer, |m| {
        m.outputs.len() == 1 && m.outputs.contains_key("B")
    });
    let mut changed = layout();
    changed["window_size"] = json!([300, 400]);
    fake.event(json!({"WindowLayoutsChanged":{"changes":[[3,changed]]}}));
    model_until(&mut observer, |m| {
        m.windows[&3].layout.window_size == (300, 400)
    });
    fake.event(json!({"WindowClosed":{"id":3}}));
    assert!(
        model_until(&mut observer, |m| !m.windows.contains_key(&3))
            .model
            .unwrap()
            .links_pending
    );
    fake.event(json!({"WorkspaceActiveWindowChanged":{"workspace_id":20,"active_window_id":2}}));
    model_until(&mut observer, |m| !m.links_pending);
}

#[test]
fn disconnected_socket_reappears_with_fresh_generation_not_old_ids() {
    let mut fake = Fake::new();
    let mut observer = fake.observer();
    ready(&mut observer);
    fake.stop();
    let lost = until(&mut observer, |v| {
        v.connection.status == ConnectionStatus::Disconnected
    });
    assert!(lost.model.is_none());
    assert_eq!(lost.connection.reason, "connection_closed");
    until(&mut observer, |v| v.connection.reason == "endpoint_missing");
    {
        let mut d = fake.data.lock().unwrap();
        d.windows = json!([window(99, 77, true)]);
        d.workspaces = json!([workspace(77, 1, "B", true, Some(99))]);
    }
    fake.start();
    let again = until(&mut observer, |v| {
        v.connection.status == ConnectionStatus::Connected
    });
    assert_eq!(again.generation, 2);
    let m = again.model.unwrap();
    assert_eq!(m.windows.keys().copied().collect::<Vec<_>>(), vec![99]);
    assert_eq!(m.workspaces.keys().copied().collect::<Vec<_>>(), vec![77]);
}

#[test]
fn unknown_malformed_and_truncated_events_invalidate_model_then_resynchronize() {
    for (bytes, reason) in [
        (b"{\"NewFutureEvent\":{}}\n".as_slice(), "unsupported_event"),
        (b"not JSON SECRET\n".as_slice(), "invalid_response"),
        (
            b"{\"WindowClosed\":{\"id\":999}}\n".as_slice(),
            "inconsistent_event",
        ),
        (b"{\"WindowClosed\":".as_slice(), "partial_line_timeout"),
    ] {
        let fake = Fake::new();
        let mut observer = fake.observer();
        ready(&mut observer);
        fake.raw(bytes);
        let lost = until(&mut observer, |v| {
            v.connection.status == ConnectionStatus::Disconnected
        });
        assert_eq!(lost.connection.reason, reason);
        assert!(lost.model.is_none());
        assert!(!serde_json::to_string(&lost).unwrap().contains("SECRET"));
        let again = until(&mut observer, |v| {
            v.connection.status == ConnectionStatus::Connected
        });
        assert_eq!(again.generation, 2);
        assert_eq!(again.model.unwrap().windows.len(), 2);
    }
}

#[test]
fn split_line_waits_for_completion_and_partial_eof_is_explicit() {
    let fake = Fake::new();
    let mut observer = fake.observer();
    ready(&mut observer);
    fake.raw(b"{\"WindowFocusChanged\":");
    assert!(observer.poll().is_none());
    fake.raw(b"{\"id\":2}}\n{\"WorkspaceActivated\":{\"id\":20,\"focused\":true}}\n");
    model_until(&mut observer, |m| m.windows[&2].is_focused);
    model_until(&mut observer, |m| m.focused_output.as_deref() == Some("B"));
    fake.raw(b"{\"partial\":");
    fake.streams.lock().unwrap().clear();
    assert_eq!(
        until(&mut observer, |v| v.model.is_none())
            .connection
            .reason,
        "partial_line_closed"
    );
}

#[test]
fn incompatible_release_is_explicit_and_never_publishes_a_model() {
    let fake = Fake::new();
    fake.data.lock().unwrap().version = "26.05 SECRET".into();
    let mut observer = fake.observer();
    let view = until(&mut observer, |v| {
        v.connection.status == ConnectionStatus::Incompatible
    });
    assert_eq!(view.connection.reason, "unsupported_version");
    assert!(view.model.is_none());
    assert_eq!(view.generation, 0);
    assert!(!serde_json::to_string(&view).unwrap().contains("SECRET"));
}

fn execute(fake: &Fake, command: Command) -> actions::ActionResult {
    actions::execute_at(Some(&fake.path), command, TIMEOUT)
}

#[test]
fn commands_use_explicit_niri_ids_and_confirm_actual_postconditions() {
    for (command, wire) in [
        (
            Command::FocusWindow { id: 2 },
            json!({"FocusWindow":{"id":2}}),
        ),
        (
            Command::FocusWorkspace { id: 20 },
            json!({"FocusWorkspace":{"reference":{"Id":20}}}),
        ),
        (
            Command::MoveWindowToWorkspace {
                window_id: 1,
                workspace_id: 20,
            },
            json!({"MoveWindowToWorkspace":{"window_id":1,"reference":{"Id":20},"focus":false}}),
        ),
        (
            Command::CloseWindow { id: 1 },
            json!({"CloseWindow":{"id":1}}),
        ),
        (
            Command::SwitchLayout { index: 1 },
            json!({"SwitchLayout":{"layout":{"Index":1}}}),
        ),
    ] {
        let fake = Fake::new();
        let result = execute(&fake, command);
        assert_eq!(result.outcome, Outcome::Confirmed, "{result:?}");
        assert_eq!(result.delivery, Delivery::Sent);
        assert!(result.acknowledged);
        assert_eq!(result.phase, Phase::Complete);
        assert_eq!(result.exit_code(), 0);
        assert_ne!(result.before, result.after);
        assert_eq!(*fake.actions.lock().unwrap(), vec![wire]);
        assert_eq!(fake.connections.load(Ordering::Relaxed), 1);
        let json = serde_json::to_string(&result).unwrap();
        for private in ["SECRET", "app_id", "title", "pid", "/home/"] {
            assert!(!json.contains(private));
        }
    }
}

#[test]
fn absent_ids_do_not_send_any_action_even_when_idx_or_pid_matches() {
    for (command, reason) in [
        (Command::FocusWindow { id: 1234 }, "window_not_found"), // real PID in fake, not a window ID
        (Command::CloseWindow { id: 999 }, "window_not_found"),
        (
            Command::MoveWindowToWorkspace {
                window_id: 999,
                workspace_id: 20,
            },
            "window_not_found",
        ),
        (
            Command::MoveWindowToWorkspace {
                window_id: 1,
                workspace_id: 1,
            },
            "workspace_not_found",
        ), // idx 1 exists
        (Command::FocusWorkspace { id: 1 }, "workspace_not_found"),
        (Command::SwitchLayout { index: 2 }, "layout_not_found"), // two layouts: 0 and 1
    ] {
        let fake = Fake::new();
        let result = execute(&fake, command);
        assert_eq!(result.outcome, Outcome::Rejected);
        assert_eq!(result.delivery, Delivery::NotSent);
        assert_eq!(result.phase, Phase::Preflight);
        assert_eq!(result.reason, reason);
        assert_eq!(result.exit_code(), 1);
        assert!(fake.actions.lock().unwrap().is_empty());
    }
}

#[test]
fn handled_without_effect_is_unconfirmed_for_all_five_commands() {
    for command in [
        Command::FocusWindow { id: 2 },
        Command::CloseWindow { id: 1 },
        Command::FocusWorkspace { id: 20 },
        Command::MoveWindowToWorkspace {
            window_id: 1,
            workspace_id: 20,
        },
        Command::SwitchLayout { index: 1 },
    ] {
        let fake = Fake::new();
        fake.data.lock().unwrap().action_mode = ActionMode::Ignore;
        let start = Instant::now();
        let result = execute(&fake, command);
        assert_eq!(result.outcome, Outcome::Unconfirmed);
        assert_eq!(result.delivery, Delivery::Sent);
        assert!(result.acknowledged);
        assert_eq!(result.reason, "confirmation_timeout");
        assert_eq!(result.exit_code(), 3);
        assert!(start.elapsed() < Duration::from_secs(2));
        assert_eq!(fake.actions.lock().unwrap().len(), 1);
        assert_eq!(fake.connections.load(Ordering::Relaxed), 1);
    }
}

#[test]
fn delayed_result_and_already_satisfied_result_need_no_event_but_do_need_fresh_read() {
    let fake = Fake::new();
    fake.data.lock().unwrap().action_mode = ActionMode::Delay;
    let start = Instant::now();
    let result = execute(&fake, Command::CloseWindow { id: 1 });
    assert_eq!(result.outcome, Outcome::Confirmed);
    assert_eq!(result.after.unwrap().window_exists, Some(false));
    assert!(start.elapsed() >= Duration::from_millis(40));
    assert_eq!(fake.actions.lock().unwrap().len(), 1);
    let fake = Fake::new();
    fake.data.lock().unwrap().action_mode = ActionMode::Ignore;
    let result = execute(&fake, Command::FocusWindow { id: 1 }); // already focused
    assert_eq!(result.outcome, Outcome::Confirmed);
    assert!(result.after.is_some());
    assert_eq!(result.before, result.after);
    assert_eq!(fake.actions.lock().unwrap().len(), 1);
}

#[test]
fn rejection_lost_reply_and_failed_confirmation_have_distinct_outcomes_without_retry() {
    for (mode, outcome, ack, phase, reason) in [
        (
            ActionMode::Reject,
            Outcome::Rejected,
            false,
            Phase::Reply,
            "request_rejected",
        ),
        (
            ActionMode::DropReply,
            Outcome::Unconfirmed,
            false,
            Phase::Reply,
            "connection_closed",
        ),
        (
            ActionMode::BadReply,
            Outcome::Unconfirmed,
            false,
            Phase::Reply,
            "invalid_response",
        ),
        (
            ActionMode::DropConfirm,
            Outcome::Unconfirmed,
            true,
            Phase::Confirm,
            "connection_closed",
        ),
        (
            ActionMode::GoneWindow,
            Outcome::Unconfirmed,
            true,
            Phase::Confirm,
            "window_disappeared",
        ),
        (
            ActionMode::HangReply,
            Outcome::Unconfirmed,
            false,
            Phase::Reply,
            "deadline_exceeded",
        ),
        (
            ActionMode::HangConfirm,
            Outcome::Unconfirmed,
            true,
            Phase::Confirm,
            "confirmation_timeout",
        ),
    ] {
        let fake = Fake::new();
        fake.data.lock().unwrap().action_mode = mode;
        let result = execute(&fake, Command::FocusWindow { id: 2 });
        assert_eq!(result.outcome, outcome, "{result:?}");
        assert_eq!(result.delivery, Delivery::Sent);
        assert_eq!(result.acknowledged, ack);
        assert_eq!(result.phase, phase);
        assert_eq!(result.reason, reason);
        assert_eq!(fake.actions.lock().unwrap().len(), 1);
        assert_eq!(fake.connections.load(Ordering::Relaxed), 1);
        assert!(!serde_json::to_string(&result).unwrap().contains("SECRET"));
    }
    let fake = Fake::new();
    fake.data.lock().unwrap().action_mode = ActionMode::GoneWorkspace;
    let result = execute(&fake, Command::FocusWorkspace { id: 20 });
    assert_eq!(result.outcome, Outcome::Unconfirmed);
    assert_eq!(result.reason, "workspace_disappeared");
    // Layout list shrinks after Handled: the index no longer addresses anything.
    let fake = Fake::new();
    fake.data.lock().unwrap().action_mode = ActionMode::GoneLayout;
    let result = execute(&fake, Command::SwitchLayout { index: 1 });
    assert_eq!(result.outcome, Outcome::Unconfirmed);
    assert_eq!(result.reason, "layout_disappeared");
    assert!(!serde_json::to_string(&result).unwrap().contains("Russian"));
}

#[test]
fn full_u64_id_is_preserved_and_zero_is_not_an_implicit_current_window() {
    for id in [0, u64::MAX] {
        let fake = Fake::new();
        fake.data.lock().unwrap().windows = json!([window(id, 10, false)]);
        let result = execute(&fake, Command::FocusWindow { id });
        assert_eq!(result.outcome, Outcome::Confirmed);
        assert_eq!(
            fake.actions.lock().unwrap()[0],
            json!({"FocusWindow":{"id":id}})
        );
    }
}

#[test]
fn keyboard_layouts_initialize_switch_replace_and_reject_invalid_index() {
    let fake = Fake::new();
    let mut observer = fake.observer();
    let view = ready(&mut observer);
    assert_eq!(view.model.unwrap().keyboard_layouts.unwrap().current_idx, 0);
    fake.event(json!({"KeyboardLayoutSwitched":{"idx":1}}));
    model_until(&mut observer, |m| {
        m.keyboard_layouts.as_ref().unwrap().current_idx == 1
    });
    fake.event(json!({"KeyboardLayoutsChanged":{"keyboard_layouts":{"names":["English (US)"],"current_idx":0}}}));
    model_until(&mut observer, |m| {
        m.keyboard_layouts.as_ref().unwrap().names.len() == 1
    });
    fake.event(json!({"KeyboardLayoutSwitched":{"idx":2}}));
    let lost = until(&mut observer, |v| {
        v.connection.status == ConnectionStatus::Disconnected
    });
    assert_eq!(lost.connection.reason, "inconsistent_event");
    assert!(lost.model.is_none());
    let again = until(&mut observer, |v| {
        v.connection.status == ConnectionStatus::Connected
    });
    assert_eq!(again.generation, 2);
    assert_eq!(
        again.model.unwrap().keyboard_layouts.unwrap().names.len(),
        2
    );
}

fn cast(stream: u64, target: Value, active: bool) -> Value {
    json!({"stream_id":stream,"session_id":7,"kind":"PipeWire","target":target,
        "is_dynamic_target":false,"is_active":active,"pid":4321,"pw_node_id":99})
}

#[test]
fn cast_by_the_parent_process_is_marked_without_exposing_pid() {
    // The shell launches the core; its own screencopy for the glass must not light the
    // privacy pill, a capture by anyone else must.
    let parent = std::os::unix::process::parent_id();
    let fake = Fake::new();
    let mut observer = fake.observer();
    ready(&mut observer);
    let mut own = cast(1, json!({"Output":{"name":"A"}}), true);
    own["kind"] = json!("WlrScreencopy");
    own["pid"] = json!(parent);
    fake.event(
        json!({"CastsChanged":{"casts":[own, cast(2, json!({"Output":{"name":"A"}}), true)]}}),
    );
    let view = model_until(&mut observer, |m| m.casts.len() == 2);
    let casts = &view.model.as_ref().unwrap().casts;
    assert!(casts[&1].by_parent && !casts[&2].by_parent);
    let json = serde_json::to_string(&view).unwrap();
    assert!(!json.contains("\"pid\"") && !json.contains("4321"));
}

#[test]
fn casts_replace_change_stop_and_never_expose_pid() {
    let fake = Fake::new();
    let mut observer = fake.observer();
    assert!(ready(&mut observer).model.unwrap().casts.is_empty());
    fake.event(json!({"CastsChanged":{"casts":[cast(1, json!({"Output":{"name":"A"}}), true)]}}));
    let view = model_until(&mut observer, |m| m.casts.len() == 1);
    let first = &view.model.as_ref().unwrap().casts[&1];
    assert_eq!(
        (first.target, first.output.as_deref(), first.is_active),
        ("output", Some("A"), true)
    );
    let json = serde_json::to_string(&view).unwrap();
    assert!(!json.contains("pid") && !json.contains("4321") && !json.contains("SECRET"));
    fake.event(json!({"CastStartedOrChanged":{"cast":cast(2, json!({"Window":{"id":1}}), false)}}));
    let view = model_until(&mut observer, |m| m.casts.len() == 2);
    let second = &view.model.as_ref().unwrap().casts[&2];
    assert_eq!(
        (second.target, second.window_id, second.is_active),
        ("window", Some(1), false)
    );
    fake.event(json!({"CastStartedOrChanged":{"cast":cast(2, json!({"Nothing":{}}), true)}}));
    model_until(&mut observer, |m| {
        m.casts[&2].target == "nothing" && m.casts[&2].is_active
    });
    fake.event(json!({"CastStopped":{"stream_id":1}}));
    model_until(&mut observer, |m| {
        m.casts.len() == 1 && m.casts.contains_key(&2)
    });
    // A stop for an unknown stream is inconsistent, like a close of an unknown window.
    fake.event(json!({"CastStopped":{"stream_id":1}}));
    let lost = until(&mut observer, |v| {
        v.connection.status == ConnectionStatus::Disconnected
    });
    assert_eq!(lost.connection.reason, "inconsistent_event");
    let again = until(&mut observer, |v| {
        v.connection.status == ConnectionStatus::Connected
    });
    assert!(again.model.unwrap().casts.is_empty());
}

#[test]
fn overview_initial_state_events_and_reconnection_are_authoritative() {
    let mut fake = Fake::new();
    {
        let mut data = fake.data.lock().unwrap();
        // Overview opened between the query and subscription: never publish false.
        data.overview_reply = Some(json!({"Ok":{"OverviewState":{"is_open":false}}}));
        data.overview_open = true;
    }
    let mut observer = fake.observer();
    let initial = ready(&mut observer);
    assert!(initial.model.as_ref().unwrap().overview_open);
    assert_eq!(
        serde_json::to_value(&initial).unwrap()["model"]["overview_open"],
        true
    );
    assert!(initial.human().contains("overview_open=true"));
    fake.event(json!({"OverviewOpenedOrClosed":{"is_open":false}}));
    let closed = model_until(&mut observer, |m| !m.overview_open);
    assert!(closed.sequence > initial.sequence);
    fake.event(json!({"OverviewOpenedOrClosed":{"is_open":false}}));
    assert!(observer.poll().is_none()); // Duplicate state does not re-trigger consumers.
    fake.event(json!({"OverviewOpenedOrClosed":{"is_open":true}}));
    model_until(&mut observer, |m| m.overview_open);
    fake.stop();
    let lost = until(&mut observer, |v| {
        v.connection.status == ConnectionStatus::Disconnected
    });
    assert!(lost.model.is_none());
    {
        let mut data = fake.data.lock().unwrap();
        data.overview_reply = None;
        data.overview_open = false;
    }
    fake.start();
    let restored = model_until(&mut observer, |m| !m.overview_open);
    assert_eq!(restored.generation, 2);
}

#[test]
fn invalid_overview_query_or_event_never_publishes_guessed_state() {
    for response in [
        json!({"Ok":"Handled"}),
        json!({"Ok":{"OverviewState":{"is_open":"SECRET"}}}),
    ] {
        let fake = Fake::new();
        fake.data.lock().unwrap().overview_reply = Some(response);
        let mut observer = fake.observer();
        let lost = until(&mut observer, |v| {
            v.connection.status == ConnectionStatus::Disconnected
        });
        assert_eq!(lost.connection.reason, "invalid_response");
        assert!(lost.model.is_none());
        assert!(!serde_json::to_string(&lost).unwrap().contains("SECRET"));
    }
    let fake = Fake::new();
    let mut observer = fake.observer();
    ready(&mut observer);
    fake.event(json!({"OverviewOpenedOrClosed":{"is_open":null}}));
    let lost = until(&mut observer, |v| {
        v.connection.status == ConnectionStatus::Disconnected
    });
    assert_eq!(lost.connection.reason, "unsupported_event");
    assert!(lost.model.is_none());
}
