use crate::{bad_arguments, json_output, output};
use emaki_core::settings::{Operation, run as settings};
use std::path::Path;
use std::process::ExitCode;
use std::time::Duration;

const HELP: &str = "Managed settings are not connected to your session yet.
These commands work only in a separate test profile and change nothing you use.

Usage: emaki settings <command> --profile-root ABSOLUTE_PATH [--json]
  list                       List all settings, defaults, sources and schema
  get KEY                    Read one effective setting
  history                    List semantic changes; observe manual edits/recover first
  undo ID                    Restore only changed keys, refusing later-key conflicts
  set KEY VALUE              Validate and prepare a generation; never live reload
  reset KEY                  Remove the override; the key inherits the package default
Keys (an empty VALUE removes the override, except for the first two: use reset):
  appearance.gaps            Integer 0..64; default from packaged tokens.toml
  keybindings.toggle_window_floating
                             Mod + optional Ctrl/Alt/Shift + XKB keysym; default unset
  appearance.wallpaper       Absolute path of an existing image -> generation/wpaperd.toml
  keyboard.layouts           1..4 XKB layout codes, comma-separated (us,ru), checked
                             against the installed XKB rules; stored in settings.toml only
  keyboard.switch_key        Super+Space | Alt+Shift | Caps Lock; stored in settings.toml only
                             (Super+Space is the packaged Mod+Space bind)
  defaults.terminal          Desktop entry id -> generation/xdg-terminals.list
  defaults.browser           Desktop entry id -> generation/mimeapps.list (http/https/html)
  defaults.files             Desktop entry id -> generation/mimeapps.list (inode/directory)
  bar.autohide, bar.overview_workspaces, dock.on, dock.auto_hide
                             true | false; read by the shell from settings.toml
Isolated profile (required for reads as well as writes):
  --profile-root PATH        Existing isolated directory owned by this user
  XDG_CONFIG_HOME            Must be PATH/config (existing directory)
  XDG_STATE_HOME             Must be PATH/state (existing directory)
  XDG_RUNTIME_DIR            Must be PATH/runtime (existing directory)
  No HOME fallback, symlink redirection, system installation or live niri include.
Options:
  --json                     JSON schema_version=1 (no raw validator diagnostics)
  --timeout-ms N             Set/reset/undo: 10..10000 ms for all validator calls; default 2000
Validation:
  niri 26.04: generated fragment, then fragment over packaged default.kdl/theme.kdl.
  Source settings.toml is replaced atomically only after successful validation.
  Comments are not preserved when canonicalizing TOML; unknown keys are rejected.
  generation selects the prepared derived files; no current symlink or second pointer.
  A binding can override a package binding. Personal niri is not read or validated.
Exit codes:
  0  Read / committed / unchanged; session_applied is always false
  1  Rejected or undo conflict; source settings were not committed
     Manual observation/recovery metadata may have been recorded
  2  Invalid CLI arguments or output error (output may fail after a commit)
  3  Source committed, history/fsync incomplete; next settings call recovers
Settings reads may record manual changes and recover/clean an interrupted transaction.
History is local JSON records (no git, hooks, network or raw config snapshots).";

pub(super) fn run(args: &[&str]) -> ExitCode {
    if args == ["--help"] || args == ["-h"] {
        return output(&format!("{HELP}\n"), 0);
    }
    let (operation, tail) = match args {
        ["list", tail @ ..] => (Operation::List, tail),
        ["history", tail @ ..] => (Operation::History, tail),
        ["undo", id, tail @ ..] if !id.starts_with('-') => (Operation::Undo { id }, tail),
        ["get", key, tail @ ..] if !key.starts_with('-') => (Operation::Get { key }, tail),
        ["reset", key, tail @ ..] if !key.starts_with('-') => (Operation::Reset { key }, tail),
        ["set", key, value, tail @ ..] if !key.starts_with('-') => {
            (Operation::Set { key, value }, tail)
        }
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
            "--json" if !json => json = true,
            "--timeout-ms"
                if timeout.is_none()
                    && matches!(
                        operation,
                        Operation::Set { .. } | Operation::Reset { .. } | Operation::Undo { .. }
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
