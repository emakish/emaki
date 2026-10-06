//! Niri 26.04 observations and explicit ID-addressed commands.
pub mod actions;
mod model;
mod stream;

use crate::state::{Failure, Status};
use crate::transport::{connect, exchange, remaining, send};
pub use model::{Model, Output, Window, Workspace};
use niri_ipc::{Event, Reply, Response};
use serde::Serialize;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};
use stream::Lines;

pub const IPC_RELEASE: &str = "26.04";
pub const SCHEMA_VERSION: u32 = 1;
const OUTPUT_POLL: Duration = Duration::from_secs(1);
const RETRY: Duration = Duration::from_millis(500);
const TICK: Duration = Duration::from_millis(100);

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ConnectionStatus {
    Connecting,
    Connected,
    Disconnected,
    Incompatible,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct Connection {
    pub status: ConnectionStatus,
    pub reason: &'static str,
    pub failure: Option<Status>,
}
impl Connection {
    fn connected() -> Self {
        Self {
            status: ConnectionStatus::Connected,
            reason: "synchronized",
            failure: None,
        }
    }
    fn failed(error: Failure) -> Self {
        Self {
            status: if error.reason == "unsupported_version" {
                ConnectionStatus::Incompatible
            } else {
                ConnectionStatus::Disconnected
            },
            reason: error.reason,
            failure: Some(error.status),
        }
    }
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct Observation {
    pub schema_version: u32,
    pub ipc_release: &'static str,
    /// Monotonic only within this observer. Emitted lines are complete replacements.
    pub sequence: u64,
    /// IDs are valid only within this successful subscription generation.
    pub generation: u64,
    pub connection: Connection,
    pub model: Option<Model>,
    pub window_workspace_source: &'static str,
    pub output_source: &'static str,
    pub output_poll_ms: u64,
}
impl Observation {
    fn new(connection: Connection, model: Option<Model>, generation: u64) -> Self {
        Self {
            schema_version: SCHEMA_VERSION,
            ipc_release: IPC_RELEASE,
            sequence: 1,
            generation,
            connection,
            model,
            window_workspace_source: "niri:EventStream",
            output_source: "niri:Outputs",
            output_poll_ms: OUTPUT_POLL.as_millis() as u64,
        }
    }
    pub fn exit_code(&self) -> u8 {
        u8::from(self.connection.status != ConnectionStatus::Connected)
    }
    pub fn human(&self) -> String {
        // Debug/JSON escaping prevents output connector names from injecting terminal controls.
        let mut text = format!(
            "niri {:?}: {}; generation={}, sequence={}\n",
            self.connection.status, self.connection.reason, self.generation, self.sequence
        );
        if let Some(m) = &self.model {
            text.push_str(&format!(
                "  outputs={} workspaces={} windows={} focused_output={:?} links_pending={} overview_open={}\n",
                m.outputs.len(),
                m.workspaces.len(),
                m.windows.len(),
                m.focused_output,
                m.links_pending,
                m.overview_open
            ));
            for w in m.workspaces.values() {
                text.push_str(&format!(
                    "  workspace id={} idx={} output={:?} active={} focused={} window={:?}\n",
                    w.id, w.idx, w.output, w.is_active, w.is_focused, w.active_window_id
                ));
            }
            for w in m.windows.values() {
                text.push_str(&format!(
                    "  window id={} workspace={:?} focused={} floating={} urgent={}\n",
                    w.id, w.workspace_id, w.is_focused, w.is_floating, w.is_urgent
                ));
            }
        }
        text
    }
}

fn endpoint() -> Option<PathBuf> {
    std::env::var_os("NIRI_SOCKET")
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
}
fn missing_endpoint() -> Failure {
    Failure::new(Status::Absent, "socket_not_configured")
}
fn malformed() -> Failure {
    Failure::new(Status::Incomplete, "invalid_response")
}
fn reply(bytes: &[u8]) -> Result<Response, Failure> {
    serde_json::from_slice::<Reply>(bytes)
        .map_err(|_| malformed())?
        .map_err(|_| Failure::new(Status::Error, "request_rejected"))
}
fn parse_event(bytes: &[u8]) -> Result<Event, Failure> {
    // Do not print serde errors: these can include private values from niri.
    let value: serde_json::Value = serde_json::from_slice(bytes).map_err(|_| malformed())?;
    serde_json::from_value(value).map_err(|_| Failure::new(Status::Incomplete, "unsupported_event"))
}
fn check_version(
    socket: &mut std::os::unix::net::UnixStream,
    deadline: Instant,
) -> Result<(), Failure> {
    let Response::Version(version) = reply(&exchange(socket, b"\"Version\"\n", deadline)?)? else {
        return Err(malformed());
    };
    if version.split_whitespace().next() != Some(IPC_RELEASE) {
        return Err(Failure::new(Status::Error, "unsupported_version"));
    }
    Ok(())
}
fn read_outputs(
    stream: &mut std::os::unix::net::UnixStream,
    deadline: Instant,
) -> Result<std::collections::BTreeMap<String, Output>, Failure> {
    let Response::Outputs(outputs) = reply(&exchange(stream, b"\"Outputs\"\n", deadline)?)? else {
        return Err(malformed());
    };
    outputs
        .into_iter()
        .map(|(key, value)| {
            if key != value.name {
                return Err(malformed());
            }
            Ok((key, Output::from_ipc(value)?))
        })
        .collect()
}

struct Session {
    lines: Lines,
    model: Model,
    next_outputs: Instant,
}
impl Session {
    /// `with_casts`: also wait for the initial `CastsChanged` (26.04 sends it last). Only the
    /// one-shot snapshot needs it; a watching session receives it as its next event.
    fn open(path: &Path, timeout: Duration, with_casts: bool) -> Result<Self, Failure> {
        let deadline = Instant::now() + timeout;
        let mut socket = connect(path, deadline)?;
        check_version(&mut socket, deadline)?;
        let mut model = Model {
            outputs: read_outputs(&mut socket, deadline)?,
            ..Model::default()
        };
        let Response::OverviewState(overview) =
            reply(&exchange(&mut socket, b"\"OverviewState\"\n", deadline)?)?
        else {
            return Err(malformed());
        };
        model.overview_open = overview.is_open;
        send(&mut socket, b"\"EventStream\"\n", deadline)?;
        let mut lines = Lines::new(socket, timeout);
        let ack = lines
            .next(deadline)?
            .ok_or(Failure::new(Status::Timeout, "initial_state_timeout"))?;
        if !matches!(reply(&ack)?, Response::Handled) {
            return Err(malformed());
        }
        let (mut windows, mut workspaces, mut overview) = (false, false, false);
        let mut casts = !with_casts;
        // 26.04 replicates overview on subscription too. Wait for that newer
        // value: the overview may change between OverviewState and EventStream.
        while !windows || !workspaces || !overview || !casts || model.keyboard_layouts.is_none() {
            remaining(deadline)?;
            let bytes = lines
                .next(deadline)?
                .ok_or(Failure::new(Status::Timeout, "initial_state_timeout"))?;
            let event = parse_event(&bytes)?;
            windows |= matches!(event, Event::WindowsChanged { .. });
            workspaces |= matches!(event, Event::WorkspacesChanged { .. });
            overview |= matches!(event, Event::OverviewOpenedOrClosed { .. });
            casts |= matches!(event, Event::CastsChanged { .. });
            model.apply(event)?;
        }
        Ok(Self {
            lines,
            model,
            next_outputs: Instant::now() + OUTPUT_POLL,
        })
    }

    fn advance(&mut self, path: &Path, timeout: Duration) -> Result<(), Failure> {
        // 26.04 has no OutputsChanged event. Poll separately; do not pretend this
        // and the event-stream workspace state form one atomic monitor snapshot.
        if Instant::now() >= self.next_outputs {
            let deadline = Instant::now() + timeout;
            let mut socket = connect(path, deadline)?;
            self.model.outputs = read_outputs(&mut socket, deadline)?;
            self.model.refresh_links();
            self.next_outputs = Instant::now() + OUTPUT_POLL;
        }
        let deadline = (Instant::now() + TICK).min(self.next_outputs);
        if let Some(bytes) = self.lines.next(deadline)? {
            self.model.apply(parse_event(&bytes)?)?;
        }
        Ok(())
    }
}

/// Bounded one-shot initial state from the subscription, not separate Windows/Workspaces queries.
pub fn snapshot(timeout: Duration) -> Observation {
    snapshot_at(endpoint(), timeout)
}
fn snapshot_at(path: Option<PathBuf>, timeout: Duration) -> Observation {
    let result = path
        .as_deref()
        .ok_or_else(missing_endpoint)
        .and_then(|p| Session::open(p, timeout, true));
    match result {
        Ok(s) => Observation::new(Connection::connected(), Some(s.model), 1),
        Err(e) => Observation::new(Connection::failed(e), None, 0),
    }
}

/// Poll from a single consumer; no background threads, unbounded queues or filesystem writes.
/// Each call may wait for a tick, or up to the configured timeout during resynchronization.
pub struct Observer {
    path: Option<PathBuf>,
    timeout: Duration,
    session: Option<Session>,
    next_retry: Instant,
    last: Observation,
    initial: bool,
}
impl Observer {
    pub fn new(timeout: Duration) -> Self {
        Self::at(endpoint(), timeout)
    }
    pub fn at(path: Option<PathBuf>, timeout: Duration) -> Self {
        Self {
            path,
            timeout,
            session: None,
            next_retry: Instant::now(),
            initial: true,
            last: Observation::new(
                Connection {
                    status: ConnectionStatus::Connecting,
                    reason: "starting",
                    failure: None,
                },
                None,
                0,
            ),
        }
    }
    pub fn poll(&mut self) -> Option<Observation> {
        if self.initial {
            self.initial = false;
            return Some(self.last.clone());
        }
        let result = if let Some(session) = &mut self.session {
            // A live session always has its endpoint. Avoid exposing its path in errors/output.
            match self.path.as_deref() {
                Some(path) => session.advance(path, self.timeout),
                None => Err(missing_endpoint()),
            }
        } else {
            if Instant::now() < self.next_retry {
                std::thread::sleep(
                    self.next_retry
                        .saturating_duration_since(Instant::now())
                        .min(TICK),
                );
                return None;
            }
            match self
                .path
                .as_deref()
                .ok_or_else(missing_endpoint)
                .and_then(|p| Session::open(p, self.timeout, false))
            {
                Ok(session) => {
                    self.session = Some(session);
                    self.last.generation = self.last.generation.saturating_add(1);
                    Ok(())
                }
                Err(e) => Err(e),
            }
        };
        let (connection, model) = match result {
            Ok(()) => (
                Connection::connected(),
                self.session.as_ref().map(|s| s.model.clone()),
            ),
            Err(e) => {
                self.session = None; // Old IDs/data must never masquerade as current state.
                self.next_retry = Instant::now() + RETRY;
                (Connection::failed(e), None)
            }
        };
        if self.last.connection == connection && self.last.model == model {
            return None;
        }
        self.last.sequence = self.last.sequence.saturating_add(1);
        self.last.connection = connection;
        self.last.model = model;
        Some(self.last.clone())
    }
}
