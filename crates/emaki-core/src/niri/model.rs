//! Public model deliberately excludes titles, app IDs, PIDs, workspace names and serials.
use crate::state::{Failure, Status};
use niri_ipc::{Event, KeyboardLayouts, LogicalOutput, Mode, WindowLayout};
use serde::Serialize;
use std::collections::BTreeMap;

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct Window {
    pub id: u64,
    pub workspace_id: Option<u64>,
    pub is_focused: bool,
    pub is_floating: bool,
    pub is_urgent: bool,
    pub layout: WindowLayout,
}
impl From<niri_ipc::Window> for Window {
    fn from(w: niri_ipc::Window) -> Self {
        Self {
            id: w.id,
            workspace_id: w.workspace_id,
            is_focused: w.is_focused,
            is_floating: w.is_floating,
            is_urgent: w.is_urgent,
            layout: w.layout,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct Workspace {
    pub id: u64,
    pub idx: u8,
    pub output: Option<String>,
    pub is_active: bool,
    pub is_focused: bool,
    pub is_urgent: bool,
    pub active_window_id: Option<u64>,
}
impl From<niri_ipc::Workspace> for Workspace {
    fn from(w: niri_ipc::Workspace) -> Self {
        Self {
            id: w.id,
            idx: w.idx,
            output: w.output,
            is_active: w.is_active,
            is_focused: w.is_focused,
            is_urgent: w.is_urgent,
            active_window_id: w.active_window_id,
        }
    }
}

/// Screencast facts only: no consumer PID, no window title behind the target ID.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct Cast {
    pub stream_id: u64,
    pub session_id: u64,
    pub kind: &'static str,
    pub target: &'static str,
    pub output: Option<String>,
    pub window_id: Option<u64>,
    pub is_active: bool,
    /// The consumer is the process that launched this core — the shell capturing the
    /// screen for its own glass. Compared here so the PID itself never leaves the core.
    pub by_parent: bool,
}
impl From<niri_ipc::Cast> for Cast {
    fn from(c: niri_ipc::Cast) -> Self {
        let (target, output, window_id) = match c.target {
            niri_ipc::CastTarget::Nothing {} => ("nothing", None, None),
            niri_ipc::CastTarget::Output { name } => ("output", Some(name), None),
            niri_ipc::CastTarget::Window { id } => ("window", None, Some(id)),
        };
        Self {
            stream_id: c.stream_id,
            session_id: c.session_id,
            kind: match c.kind {
                niri_ipc::CastKind::PipeWire => "pipewire",
                niri_ipc::CastKind::WlrScreencopy => "wlr_screencopy",
            },
            target,
            output,
            window_id,
            is_active: c.is_active,
            by_parent: c.pid.is_some_and(|pid| {
                u32::try_from(pid).ok() == Some(std::os::unix::process::parent_id())
            }),
        }
    }
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct Output {
    pub name: String,
    pub logical: Option<LogicalOutput>,
    pub current_mode: Option<Mode>,
    pub vrr_enabled: bool,
}
impl Output {
    pub(crate) fn from_ipc(o: niri_ipc::Output) -> Result<Self, Failure> {
        let current_mode = o
            .current_mode
            .map(|i| o.modes.get(i).copied().ok_or_else(invalid))
            .transpose()?;
        Ok(Self {
            name: o.name,
            logical: o.logical,
            current_mode,
            vrr_enabled: o.vrr_enabled,
        })
    }
}

#[derive(Clone, Debug, Default, PartialEq, Serialize)]
pub struct Model {
    pub windows: BTreeMap<u64, Window>,
    pub workspaces: BTreeMap<u64, Workspace>,
    pub outputs: BTreeMap<String, Output>,
    pub focused_output: Option<String>,
    pub keyboard_layouts: Option<KeyboardLayouts>,
    /// Screencasts by stream ID; 26.04 replicates them on subscription (CastsState).
    pub casts: BTreeMap<u64, Cast>,
    pub overview_open: bool,
    /// Niri updates different state parts separately. Keep unresolved IDs visible.
    pub links_pending: bool,
}

fn invalid() -> Failure {
    Failure::new(Status::Incomplete, "inconsistent_event")
}

impl Model {
    pub(crate) fn refresh_links(&mut self) {
        self.focused_output = self
            .workspaces
            .values()
            .find(|w| w.is_focused)
            .and_then(|w| w.output.clone());
        self.links_pending = self.windows.values().any(|w| {
            w.workspace_id
                .is_some_and(|id| !self.workspaces.contains_key(&id))
        }) || self.workspaces.values().any(|w| {
            w.output
                .as_ref()
                .is_some_and(|o| !self.outputs.contains_key(o))
                || w.active_window_id
                    .is_some_and(|id| !self.windows.contains_key(&id))
        });
    }

    pub(crate) fn apply(&mut self, event: Event) -> Result<(), Failure> {
        match event {
            Event::WorkspacesChanged { workspaces } => {
                let count = workspaces.len();
                self.workspaces = workspaces.into_iter().map(|w| (w.id, w.into())).collect();
                if self.workspaces.len() != count || self.workspaces.values().any(|w| w.idx == 0) {
                    return Err(invalid());
                }
            }
            Event::WindowsChanged { windows } => {
                let count = windows.len();
                self.windows = windows.into_iter().map(|w| (w.id, w.into())).collect();
                if self.windows.len() != count {
                    return Err(invalid());
                }
            }
            Event::WindowOpenedOrChanged { window } => {
                if window.is_focused {
                    for w in self.windows.values_mut() {
                        w.is_focused = false;
                    }
                }
                self.windows.insert(window.id, window.into());
            }
            Event::WindowClosed { id } => {
                self.windows.remove(&id).ok_or_else(invalid)?;
            }
            Event::WindowFocusChanged { id } => {
                for w in self.windows.values_mut() {
                    w.is_focused = Some(w.id) == id;
                }
            }
            Event::WorkspaceActivated { id, focused } => {
                let output = self.workspaces.get(&id).ok_or_else(invalid)?.output.clone();
                for w in self.workspaces.values_mut() {
                    if w.output == output {
                        w.is_active = w.id == id;
                    }
                    if focused {
                        w.is_focused = w.id == id;
                    }
                }
            }
            Event::WorkspaceActiveWindowChanged {
                workspace_id,
                active_window_id,
            } => {
                self.workspaces
                    .get_mut(&workspace_id)
                    .ok_or_else(invalid)?
                    .active_window_id = active_window_id;
            }
            Event::WorkspaceUrgencyChanged { id, urgent } => {
                self.workspaces.get_mut(&id).ok_or_else(invalid)?.is_urgent = urgent;
            }
            Event::WindowUrgencyChanged { id, urgent } => {
                self.windows.get_mut(&id).ok_or_else(invalid)?.is_urgent = urgent;
            }
            Event::WindowLayoutsChanged { changes } => {
                for (id, layout) in changes {
                    self.windows.get_mut(&id).ok_or_else(invalid)?.layout = layout;
                }
            }
            Event::KeyboardLayoutsChanged { keyboard_layouts } => {
                if keyboard_layouts.names.is_empty()
                    || usize::from(keyboard_layouts.current_idx) >= keyboard_layouts.names.len()
                {
                    return Err(invalid());
                }
                self.keyboard_layouts = Some(keyboard_layouts);
            }
            Event::KeyboardLayoutSwitched { idx } => {
                let layouts = self.keyboard_layouts.as_mut().ok_or_else(invalid)?;
                if usize::from(idx) >= layouts.names.len() {
                    return Err(invalid());
                }
                layouts.current_idx = idx;
            }
            Event::OverviewOpenedOrClosed { is_open } => self.overview_open = is_open,
            Event::CastsChanged { casts } => {
                let count = casts.len();
                self.casts = casts.into_iter().map(|c| (c.stream_id, c.into())).collect();
                if self.casts.len() != count {
                    return Err(invalid());
                }
            }
            Event::CastStartedOrChanged { cast } => {
                self.casts.insert(cast.stream_id, cast.into());
            }
            Event::CastStopped { stream_id } => {
                self.casts.remove(&stream_id).ok_or_else(invalid)?;
            }
            // Known 26.04 events outside this deliberately limited model.
            Event::WindowFocusTimestampChanged { .. }
            | Event::ConfigLoaded { .. }
            | Event::ScreenshotCaptured { .. } => {}
        }
        self.refresh_links();
        Ok(())
    }
}
