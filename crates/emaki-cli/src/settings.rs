use crate::{bad_arguments, json_output, output};
use emaki_core::settings::{Operation, run as settings};
use std::path::Path;
use std::process::{Command, ExitCode, Stdio};
use std::time::Duration;

#[path = "settings_tui.rs"]
mod tui;

const HELP: &str = "Usage: emaki settings [page | command] [options]
Without a command, open settings inside the running shell launcher.
Use a page name (for example, wifi) to open that page.
  list                       List settings, current values, defaults and sources
  get KEY                    Read one setting
  set KEY VALUE              Validate, save and apply a setting
  reset KEY                  Restore the package default
  history                    List persisted changes
  undo ID                    Restore changed keys, refusing later-key conflicts
Options:
  --text                     Open the keyboard settings menu instead
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
    if let [page] = args {
        if PAGES.contains(page) {
            return open_launcher_settings(page);
        }
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
    let (mut root, mut json, mut timeout, mut text) = (None, false, None, false);
    let mut args = tail.iter();
    while let Some(arg) = args.next() {
        match *arg {
            "--text" if !text && operation.is_none() => text = true,
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
        if !text {
            if root.is_some() || timeout.is_some() {
                return bad_arguments();
            }
            return open_launcher_settings("");
        }
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

const PAGES: &[&str] = &[
    "panel",
    "windows",
    "wifi",
    "bluetooth",
    "sound",
    "displays",
    "battery",
    "keyboard",
    "mouse",
    "network",
    "notifications",
    "wallpaper",
    "region",
    "apps",
    "about",
    "updates",
    "lock",
];

fn open_launcher_settings(page: &str) -> ExitCode {
    // A successful IPC process can still mean an unavailable method. Require the
    // launcher's structured acknowledgement, with one bounded request and no retry.
    let reply = Command::new("timeout")
        .args([
            "--kill-after=1s",
            "3s",
            "emaki-shell",
            "call",
            "settings",
            "open",
            page,
        ])
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output();
    let opened = reply
        .ok()
        .filter(|reply| reply.status.success())
        .is_some_and(|reply| {
            serde_json::from_slice::<serde_json::Value>(&reply.stdout)
                .is_ok_and(|value| value["schema_version"] == 1 && value["status"] == "opened")
        });
    if opened {
        ExitCode::SUCCESS
    } else {
        output(
            "settings: launcher unavailable; use emaki settings --text for the keyboard menu.\n",
            1,
        )
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn page_names_match_the_shell_registry() {
        let registry = include_str!("../../../shell/SettingsPageRegistry.qml");
        let pages: Vec<_> = registry
            .lines()
            .filter_map(|line| line.trim().strip_prefix(r#"pageId: ""#))
            .map(|page| page.trim_end_matches('"'))
            .collect();
        assert_eq!(super::PAGES, pages);
    }
}
