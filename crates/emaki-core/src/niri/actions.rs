//! One explicit action, fresh preflight and postcondition on the same socket.
//! Never reconnect/retry a mutation: delivery may be uncertain after a transport failure.
use super::{IPC_RELEASE, check_version, endpoint, malformed, missing_endpoint, reply};
use crate::state::{Failure, Status};
use crate::transport::{connect, exchange, read_reply, remaining, send};
use niri_ipc::{Action, LayoutSwitchTarget, Request, Response, WorkspaceReferenceArg};
use serde::Serialize;
use std::os::unix::net::UnixStream;
use std::path::Path;
use std::time::{Duration, Instant};

/// No variant permits implicit focused-window, index, name or PID addressing.
/// Keyboard layouts have no IDs in niri 26.04: the explicit configured index is their address.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(tag = "action", rename_all = "snake_case")]
pub enum Command {
    FocusWindow { id: u64 },
    MoveWindowToWorkspace { window_id: u64, workspace_id: u64 },
    FocusWorkspace { id: u64 },
    CloseWindow { id: u64 },
    SwitchLayout { index: u8 },
}
impl Command {
    pub fn name(self) -> &'static str {
        match self {
            Self::FocusWindow { .. } => "focus-window",
            Self::MoveWindowToWorkspace { .. } => "move-window-to-workspace",
            Self::FocusWorkspace { .. } => "focus-workspace",
            Self::CloseWindow { .. } => "close-window",
            Self::SwitchLayout { .. } => "switch-layout",
        }
    }
    fn window_id(self) -> Option<u64> {
        match self {
            Self::FocusWindow { id } | Self::CloseWindow { id } => Some(id),
            Self::MoveWindowToWorkspace { window_id, .. } => Some(window_id),
            Self::FocusWorkspace { .. } | Self::SwitchLayout { .. } => None,
        }
    }
    fn layout_index(self) -> Option<u8> {
        match self {
            Self::SwitchLayout { index } => Some(index),
            _ => None,
        }
    }
    fn workspace_id(self) -> Option<u64> {
        match self {
            Self::FocusWorkspace { id } => Some(id),
            Self::MoveWindowToWorkspace { workspace_id, .. } => Some(workspace_id),
            _ => None,
        }
    }
    fn wire(self) -> Action {
        match self {
            Self::FocusWindow { id } => Action::FocusWindow { id },
            Self::CloseWindow { id } => Action::CloseWindow { id: Some(id) },
            Self::FocusWorkspace { id } => Action::FocusWorkspace {
                reference: WorkspaceReferenceArg::Id(id),
            },
            Self::MoveWindowToWorkspace {
                window_id,
                workspace_id,
            } => Action::MoveWindowToWorkspace {
                window_id: Some(window_id),
                reference: WorkspaceReferenceArg::Id(workspace_id),
                focus: false,
            },
            Self::SwitchLayout { index } => Action::SwitchLayout {
                layout: LayoutSwitchTarget::Index(index),
            },
        }
    }
    fn satisfied(self, state: &TargetState) -> bool {
        match self {
            Self::FocusWindow { .. } => state.window_focused == Some(true),
            Self::CloseWindow { .. } => state.window_exists == Some(false),
            Self::FocusWorkspace { .. } => state.workspace_focused == Some(true),
            Self::MoveWindowToWorkspace { workspace_id, .. } => {
                state.window_exists == Some(true)
                    && state.workspace_exists == Some(true)
                    && state.window_workspace_id == Some(workspace_id)
            }
            Self::SwitchLayout { .. } => state.layout_active == Some(true),
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Outcome {
    Confirmed,
    Unconfirmed,
    Rejected,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Delivery {
    NotSent,
    Sent,
    Unknown,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Phase {
    Preflight,
    Send,
    Reply,
    Confirm,
    Complete,
}

/// Only relevant target facts, never raw windows or error strings from the server.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize)]
pub struct TargetState {
    pub window_exists: Option<bool>,
    pub window_focused: Option<bool>,
    pub window_workspace_id: Option<u64>,
    pub workspace_exists: Option<bool>,
    pub workspace_focused: Option<bool>,
    pub layout_exists: Option<bool>,
    pub layout_active: Option<bool>,
}
#[derive(Clone, Debug, Serialize)]
pub struct ActionResult {
    pub schema_version: u32,
    pub ipc_release: &'static str,
    pub command: Command,
    pub outcome: Outcome,
    pub delivery: Delivery,
    pub acknowledged: bool,
    pub phase: Phase,
    pub reason: &'static str,
    pub failure: Option<Status>,
    pub before: Option<TargetState>,
    pub after: Option<TargetState>,
}
impl ActionResult {
    pub fn exit_code(&self) -> u8 {
        match self.outcome {
            Outcome::Confirmed => 0,
            Outcome::Rejected => 1,
            Outcome::Unconfirmed => 3,
        }
    }
    pub fn human(&self) -> String {
        format!(
            "niri {:?}: {:?} ({})\n  delivery={:?}, acknowledged={}, phase={:?}\n  before={:?}\n  after={:?}\n",
            self.command,
            self.outcome,
            self.reason,
            self.delivery,
            self.acknowledged,
            self.phase,
            self.before,
            self.after
        )
    }
}

fn target_state(
    socket: &mut UnixStream,
    command: Command,
    deadline: Instant,
) -> Result<TargetState, Failure> {
    let mut state = TargetState::default();
    if let Some(id) = command.workspace_id() {
        let Response::Workspaces(workspaces) =
            reply(&exchange(socket, b"\"Workspaces\"\n", deadline)?)?
        else {
            return Err(malformed());
        };
        let mut matching = workspaces.into_iter().filter(|w| w.id == id);
        let workspace = matching.next();
        if matching.next().is_some() {
            return Err(malformed());
        }
        state.workspace_exists = Some(workspace.is_some());
        state.workspace_focused = workspace.map(|w| w.is_focused);
    }
    if let Some(id) = command.window_id() {
        let Response::Windows(windows) = reply(&exchange(socket, b"\"Windows\"\n", deadline)?)?
        else {
            return Err(malformed());
        };
        let mut matching = windows.into_iter().filter(|w| w.id == id);
        let window = matching.next();
        if matching.next().is_some() {
            return Err(malformed());
        }
        state.window_exists = Some(window.is_some());
        state.window_focused = window.as_ref().map(|w| w.is_focused);
        state.window_workspace_id = window.and_then(|w| w.workspace_id);
    }
    if let Some(index) = command.layout_index() {
        let Response::KeyboardLayouts(layouts) =
            reply(&exchange(socket, b"\"KeyboardLayouts\"\n", deadline)?)?
        else {
            return Err(malformed());
        };
        let exists = usize::from(index) < layouts.names.len();
        state.layout_exists = Some(exists);
        state.layout_active = exists.then_some(layouts.current_idx == index);
    }
    remaining(deadline)?;
    Ok(state)
}

/// The timeout bounds preflight + one send + acknowledgement + confirmation together.
pub fn execute(command: Command, timeout: Duration) -> ActionResult {
    execute_at(endpoint().as_deref(), command, timeout)
}
pub fn execute_at(path: Option<&Path>, command: Command, timeout: Duration) -> ActionResult {
    let mut result = ActionResult {
        schema_version: 1,
        ipc_release: IPC_RELEASE,
        command,
        outcome: Outcome::Rejected,
        delivery: Delivery::NotSent,
        acknowledged: false,
        phase: Phase::Preflight,
        reason: "not_started",
        failure: None,
        before: None,
        after: None,
    };
    if let Err(e) = run(path, timeout, &mut result) {
        result.reason = if result.phase == Phase::Confirm && e.status == Status::Timeout {
            "confirmation_timeout"
        } else {
            e.reason
        };
        result.failure = Some(e.status);
    }
    result
}

fn run(path: Option<&Path>, timeout: Duration, result: &mut ActionResult) -> Result<(), Failure> {
    let deadline = Instant::now() + timeout;
    let mut socket = connect(path.ok_or_else(missing_endpoint)?, deadline)?;
    check_version(&mut socket, deadline)?;
    let before = target_state(&mut socket, result.command, deadline)?;
    result.before = Some(before.clone());
    if before.window_exists == Some(false) {
        return Err(Failure::new(Status::Absent, "window_not_found"));
    }
    if before.workspace_exists == Some(false) {
        return Err(Failure::new(Status::Absent, "workspace_not_found"));
    }
    if before.layout_exists == Some(false) {
        return Err(Failure::new(Status::Absent, "layout_not_found"));
    }
    let mut bytes = serde_json::to_vec(&Request::Action(result.command.wire()))
        .map_err(|_| Failure::new(Status::Error, "serialization_failed"))?;
    bytes.push(b'\n');
    remaining(deadline)?;

    // Once writing starts, a failure may have delivered bytes. Do not report a
    // rejection or retry automatically merely because the response was lost.
    result.phase = Phase::Send;
    result.outcome = Outcome::Unconfirmed;
    result.delivery = Delivery::Unknown;
    send(&mut socket, &bytes, deadline)?;
    result.delivery = Delivery::Sent;
    result.phase = Phase::Reply;
    let response = reply(&read_reply(&mut socket, deadline)?);
    match response {
        Ok(Response::Handled) => result.acknowledged = true,
        Ok(_) => return Err(malformed()),
        Err(e) => {
            if e.reason == "request_rejected" {
                result.outcome = Outcome::Rejected;
            }
            return Err(e);
        }
    }

    result.phase = Phase::Confirm;
    loop {
        let after = target_state(&mut socket, result.command, deadline)?;
        result.after = Some(after.clone());
        if result.command.satisfied(&after) {
            result.outcome = Outcome::Confirmed;
            result.phase = Phase::Complete;
            result.reason = "postcondition_observed";
            return Ok(());
        }
        if after.window_exists == Some(false) {
            return Err(Failure::new(Status::Absent, "window_disappeared"));
        }
        if after.workspace_exists == Some(false) {
            return Err(Failure::new(Status::Absent, "workspace_disappeared"));
        }
        if after.layout_exists == Some(false) {
            return Err(Failure::new(Status::Absent, "layout_disappeared"));
        }
        std::thread::sleep(remaining(deadline)?.min(Duration::from_millis(20)));
    }
}
