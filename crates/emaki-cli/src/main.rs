mod settings;

use std::env;
use std::io::{self, Write};
use std::os::unix::process::CommandExt;
use std::path::PathBuf;
use std::process::Command;
use std::process::ExitCode;
use std::time::Duration;

const HELP: &str = "Usage: emaki [command]

Desktop help:
  Open apps: press Super + D.
  Getting started: open Welcome to Emaki from the app list.
  All shortcuts: press Super + Shift + /.
  Your own shortcuts override Emaki defaults, including new ones (niri bindings).

Commands:
  emaki                         Show system information (fastfetch)
  emaki settings [page]         Open settings, optionally at a page
  emaki --version               Show the Emaki version
  emaki help                    Show this help
  emaki help --internal         Show diagnostic commands

Super is the Windows key, or Command on a Mac keyboard.";

const INTERNAL_HELP: &str = "Usage: emaki <command> [options]

Commands:
  version                       Print the Emaki build version
  map [--json]                  Expected paths and ownership; no filesystem writes
  state [--json] [--timeout-ms N]
                                Read session state; default 1000 ms per component
  niri snapshot [--json] [--timeout-ms N]
                                Initial window/workspace/output model (niri 26.04)
  niri watch [--json] [--timeout-ms N]
                                Observe changes; reconnect with a fresh model
  niri focus-window --id N       Focus a window by niri ID
  niri focus-workspace --id N    Focus a workspace by niri ID, never index
  niri move-window-to-workspace --id N --workspace-id M
                                Move explicit window; do not follow focus
  niri close-window --id N       Request closing an explicit window

Options:
  -h, --help                    Print help
  -V, --version                 Print the Emaki build version
  --timeout-ms N                State/niri: 10..10000 ms; default 1000

Exit codes:
  0  Success; all state components available / all map paths resolved
  1  Partial observation, unresolved paths or rejected niri command; valid JSON
  2  Invalid arguments, serialization failure or output error
  3  Niri command sent/possibly sent, but result not confirmed; never auto-retried

State statuses: available, absent, denied, incomplete, timeout, error.
Available means the documented probe succeeded, not full service health.
Read-only queries never create directories or activate D-Bus services.
State omits window titles, app IDs, SSIDs, credentials and personal paths.
Map intentionally shows resolved paths; planned entries need not exist.";

const NIRI_HELP: &str = "Usage: emaki niri <command> [options]
  snapshot | watch [--json] [--timeout-ms N]
  focus-window --id N [--json] [--timeout-ms N]
  focus-workspace --id N [--json] [--timeout-ms N]
  move-window-to-workspace --id N --workspace-id M [--json] [--timeout-ms N]
  close-window --id N [--json] [--timeout-ms N]
  switch-layout --index N [--json] [--timeout-ms N]
niri IPC 26.04 only. Separate observation/action JSON schemas, each version 1.
Snapshot/watch are read-only. Actions require explicit decimal u64 niri IDs.
Switch-layout addresses a configured keyboard layout by its decimal index (0..255), never next/prev.
Actions check fresh target state, send once, and confirm a postcondition on the same socket.
Move uses focus=false. Close is a request to the app, never force-kill.
Action outcomes: confirmed=0, rejected=1, unconfirmed=3; invalid arguments/output=2.
Action --json writes one compact JSON object followed by a newline, for every outcome.
Delivery is not_sent/sent/unknown, independently of Handled acknowledgement.
Action timeout covers preflight, send, reply and confirmation together; no retry.
Watch emits complete JSON objects, one per changed model/connection; flushes each line.
Windows/workspaces come from EventStream; outputs are polled every 1000 ms.
Each reconnect replaces the entire model and increments generation; old IDs expire.
Disconnected/incompatible: model=null, explicit reason; watch retries every 500 ms.
No titles, app IDs, PIDs, workspace names, monitor serials or socket paths in output.
Snapshot has exit code 0 connected, 1 unavailable, 2 bad arguments/output error.
Watch runs until interrupted (Ctrl+C); failures are observations, not silent exit.
--timeout-ms: 10..10000, bounds startup/requests/partial lines, not idle watch duration.";

fn output(text: &str, code: u8) -> ExitCode {
    if io::stdout().lock().write_all(text.as_bytes()).is_err() {
        ExitCode::from(2)
    } else {
        ExitCode::from(code)
    }
}

fn bad_arguments() -> ExitCode {
    eprintln!("emaki: invalid arguments\n\n{HELP}");
    ExitCode::from(2)
}

fn update_channel() -> String {
    let Ok(result) = Command::new("emaki-update-channel").output() else {
        return "unknown".into();
    };
    let text = String::from_utf8_lossy(&result.stdout);
    match text.trim() {
        "stable" | "testing" | "custom" | "mixed" | "disabled" if result.status.success() => {
            text.trim().into()
        }
        _ => "unknown".into(),
    }
}

fn fetch() -> ExitCode {
    let data = option_env!("EMAKI_DATADIR").map_or_else(
        || PathBuf::from(option_env!("EMAKI_PREFIX").unwrap_or("/usr")).join("share/emaki"),
        PathBuf::from,
    );
    let error = Command::new("fastfetch")
        .arg("--config")
        .arg(data.join("fetch/config.jsonc"))
        .exec();
    if error.kind() == io::ErrorKind::NotFound {
        eprintln!("emaki: fastfetch is not installed or is not on PATH.\n\n{HELP}");
    } else {
        eprintln!("emaki: cannot start fastfetch: {error}\n\n{HELP}");
    }
    ExitCode::from(1)
}

fn main() -> ExitCode {
    let args: Result<Vec<_>, _> = env::args_os()
        .skip(1)
        .map(|arg| arg.into_string())
        .collect();
    let Ok(args) = args else {
        return bad_arguments();
    };
    let args: Vec<_> = args.iter().map(String::as_str).collect();
    match args.as_slice() {
        ["version" | "--version" | "-V"] => output(
            &format!(
                "emaki {} [channel: {}]\n",
                emaki_core::VERSION,
                update_channel()
            ),
            0,
        ),
        [] => fetch(),
        ["help" | "--help" | "-h"] => output(&format!("{HELP}\n"), 0),
        ["help", "--internal"] | ["map" | "state", "--help" | "-h"] => {
            output(&format!("{INTERNAL_HELP}\n"), 0)
        }
        ["settings", tail @ ..] => settings::run(tail),
        ["map"] => {
            let map = emaki_core::map::collect();
            output(&map.human(), map.exit_code())
        }
        ["map", "--json"] => {
            let map = emaki_core::map::collect();
            json_output(serde_json::to_string_pretty(&map), map.exit_code())
        }
        ["niri", "--help" | "-h"]
        | [
            "niri",
            "snapshot"
            | "watch"
            | "focus-window"
            | "focus-workspace"
            | "move-window-to-workspace"
            | "close-window"
            | "switch-layout",
            "--help" | "-h",
        ] => output(&format!("{NIRI_HELP}\n"), 0),
        [
            "niri",
            command @ ("focus-window"
            | "focus-workspace"
            | "move-window-to-workspace"
            | "close-window"
            | "switch-layout"),
            tail @ ..,
        ] => {
            let Some((command, json, timeout)) = action_options(command, tail) else {
                return bad_arguments();
            };
            let result = emaki_core::niri::actions::execute(command, timeout);
            if json {
                json_output(serde_json::to_string(&result), result.exit_code())
            } else {
                output(&result.human(), result.exit_code())
            }
        }
        ["niri", command @ ("snapshot" | "watch"), tail @ ..] => {
            let Some((json, timeout)) = read_options(tail) else {
                return bad_arguments();
            };
            if *command == "snapshot" {
                let state = emaki_core::niri::snapshot(timeout);
                if json {
                    json_output(serde_json::to_string_pretty(&state), state.exit_code())
                } else {
                    output(&state.human(), state.exit_code())
                }
            } else {
                watch_niri(json, timeout)
            }
        }
        ["state", tail @ ..] => {
            let Some((json, timeout)) = read_options(tail) else {
                return bad_arguments();
            };
            let state = emaki_core::state::collect(timeout);
            if json {
                json_output(serde_json::to_string_pretty(&state), state.exit_code())
            } else {
                output(&state.human(), state.exit_code())
            }
        }
        _ => bad_arguments(),
    }
}

fn action_options(
    command: &str,
    tail: &[&str],
) -> Option<(emaki_core::niri::actions::Command, bool, Duration)> {
    use emaki_core::niri::actions::Command;
    let (mut id, mut workspace, mut index) = (None, None, None);
    let mut common = Vec::new();
    let mut options = tail.iter();
    while let Some(option) = options.next() {
        match *option {
            "--id" if id.is_none() => id = Some(decimal_id(options.next()?)?),
            "--index" if index.is_none() => {
                index = Some(u8::try_from(decimal_id(options.next()?)?).ok()?)
            }
            "--workspace-id" if workspace.is_none() => {
                workspace = Some(decimal_id(options.next()?)?)
            }
            "--json" => common.push(*option),
            "--timeout-ms" => {
                common.push(*option);
                common.push(*options.next()?);
            }
            _ => return None,
        }
    }
    let (json, timeout) = read_options(&common)?;
    if command == "switch-layout" {
        if id.is_some() || workspace.is_some() {
            return None;
        }
        return Some((Command::SwitchLayout { index: index? }, json, timeout));
    }
    if index.is_some() {
        return None;
    }
    let id = id?;
    let action = match command {
        "focus-window" if workspace.is_none() => Command::FocusWindow { id },
        "close-window" if workspace.is_none() => Command::CloseWindow { id },
        "focus-workspace" if workspace.is_none() => Command::FocusWorkspace { id },
        "move-window-to-workspace" => Command::MoveWindowToWorkspace {
            window_id: id,
            workspace_id: workspace?,
        },
        _ => return None,
    };
    Some((action, json, timeout))
}

fn decimal_id(value: &str) -> Option<u64> {
    if value.is_empty() || !value.bytes().all(|c| c.is_ascii_digit()) {
        return None;
    }
    value.parse().ok()
}

fn read_options(tail: &[&str]) -> Option<(bool, Duration)> {
    let (mut json, mut timeout) = (false, None);
    let mut options = tail.iter();
    while let Some(option) = options.next() {
        match *option {
            "--json" if !json => json = true,
            "--timeout-ms" if timeout.is_none() => {
                timeout = Some(
                    options
                        .next()?
                        .parse::<u64>()
                        .ok()
                        .filter(|n| (10..=10000).contains(n))?,
                );
            }
            _ => return None,
        }
    }
    Some((json, Duration::from_millis(timeout.unwrap_or(1000))))
}

fn watch_niri(json: bool, timeout: Duration) -> ExitCode {
    let mut observer = emaki_core::niri::Observer::new(timeout);
    let stdout = io::stdout();
    let mut writer = stdout.lock();
    loop {
        let Some(state) = observer.poll() else {
            continue;
        };
        let text = if json {
            match serde_json::to_string(&state) {
                Ok(text) => text,
                Err(_) => return ExitCode::from(2),
            }
        } else {
            state.human().lines().collect::<Vec<_>>().join(" | ")
        };
        if writeln!(writer, "{text}")
            .and_then(|()| writer.flush())
            .is_err()
        {
            return ExitCode::from(2);
        }
    }
}

fn json_output(result: Result<String, serde_json::Error>, code: u8) -> ExitCode {
    match result {
        Ok(text) => output(&format!("{text}\n"), code),
        Err(_) => {
            eprintln!("emaki: cannot serialize response");
            ExitCode::from(2)
        }
    }
}

#[cfg(test)]
mod help_tests {
    use super::HELP;

    #[test]
    fn everyday_help_explains_desktop_entry_points() {
        assert!(HELP.contains("Welcome to Emaki"));
        assert!(HELP.contains("Super + D"));
        assert!(HELP.contains("emaki help --internal"));
        for internal in [
            "settings list|get|set",
            "map [--json]",
            "niri snapshot",
            "State statuses:",
        ] {
            assert!(
                !HELP.contains(internal),
                "internal command leaked: {internal}"
            );
        }
    }
}
