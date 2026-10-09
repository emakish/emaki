use crate::{bad_arguments, json_output, output};
use emaki_core::settings::{Operation, run as settings};
use std::path::Path;
use std::process::ExitCode;
use std::time::Duration;

#[path = "settings_tui.rs"]
mod tui;

const HELP: &str = "Usage: emaki settings [command] [options]
Without a command, open the keyboard settings menu.
  list                       List settings, current values, defaults and sources
  get KEY                    Read one setting
  set KEY VALUE              Validate, save and apply a setting
  reset KEY                  Restore the package default
  history                    List persisted changes
  undo ID                    Restore changed keys, refusing later-key conflicts
Options:
  --json                     Print a command reply as JSON (not for the menu)
  --profile-root PATH        Use an isolated test profile with matching XDG paths
  --timeout-ms N             Write/menu validation deadline: 10..10000 ms (default 2000)
The default store belongs to the logged-in person in Emaki's managed settings zone.
Personal niri configuration is never edited. Application failures are reported by
the core; session_applied describes the result, not just whether a value was saved.
Use list for each key's type, allowed values and application details.
Exit codes: 0 success, 1 rejected/conflict, 2 arguments/output error,
3 committed with incomplete durability/history; the next call recovers.";

pub(super) fn run(args: &[&str]) -> ExitCode {
    if args == ["session-config"] {
        return match emaki_core::settings::session_config(Duration::from_secs(2)) {
            Ok(path) => output(&format!("{}\n", path.display()), 0),
            Err(reason) => output(&format!("settings: {reason}\n"), 1),
        };
    }
    if args == ["--help"] || args == ["-h"] {
        return output(&format!("{HELP}\n"), 0);
    }
    let (operation, tail) = match args {
        ["list", tail @ ..] => (Some(Operation::List), tail),
        ["history", tail @ ..] => (Some(Operation::History), tail),
        ["undo", id, tail @ ..] if !id.starts_with('-') => (Some(Operation::Undo { id }), tail),
        ["get", key, tail @ ..] if !key.starts_with('-') => (Some(Operation::Get { key }), tail),
        ["reset", key, tail @ ..] if !key.starts_with('-') => {
            (Some(Operation::Reset { key }), tail)
        }
        ["set", key, value, tail @ ..] if !key.starts_with('-') => {
            (Some(Operation::Set { key, value }), tail)
        }
        [] => (None, args),
        [flag, ..] if flag.starts_with("--") => (None, args),
        _ => return bad_arguments(),
    };
    let (mut root, mut json, mut timeout) = (None, false, None);
    let mut args = tail.iter();
    while let Some(arg) = args.next() {
        match *arg {
            "--profile-root" if root.is_none() => {
                let Some(path) = args.next() else {
                    return bad_arguments();
                };
                root = Some(Path::new(path));
            }
            "--json" if !json && operation.is_some() => json = true,
            "--timeout-ms"
                if timeout.is_none()
                    && matches!(
                        operation,
                        None | Some(
                            Operation::Set { .. }
                                | Operation::Reset { .. }
                                | Operation::Undo { .. }
                        )
                    ) =>
            {
                let Some(ms) = args
                    .next()
                    .and_then(|s| s.parse::<u64>().ok())
                    .filter(|v| (10..=10000).contains(v))
                else {
                    return bad_arguments();
                };
                timeout = Some(ms);
            }
            _ => return bad_arguments(),
        }
    }
    let Some(operation) = operation else {
        return tui::run(root, Duration::from_millis(timeout.unwrap_or(2000)));
    };
    let reply = settings(
        operation,
        root,
        Duration::from_millis(timeout.unwrap_or(2000)),
    );
    if json {
        json_output(serde_json::to_string_pretty(&reply), reply.exit_code())
    } else {
        output(&reply.human(), reply.exit_code())
    }
}
