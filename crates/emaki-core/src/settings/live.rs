// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
//! Session side effects have a durable rollback record separate from semantic history.
use super::storage::{Profile, io, read, sync_dir, write_new};
use super::*;
use crate::transport::{helper_wait, helper_with_lock};
use std::fs;
use std::time::Instant;

use super::mime::{HEADER as MIME_HEADER, path as mime_path, publish as publish_mime};

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Pending {
    schema_version: u32,
    candidate: String,
    before: String,
    after: String,
    niri: bool,
    shell: bool,
    mime: bool,
    #[serde(default)]
    mime_fallback: bool,
    wallpaper: Option<String>,
    mime_before: Option<String>,
    #[serde(default)]
    legacy_mime_before: Option<String>,
}
fn pending_path(profile: &Profile) -> PathBuf {
    profile.history().join("live-pending.json")
}
fn legacy_mime_path(profile: &Profile) -> PathBuf {
    profile
        .config
        .parent()
        .expect("config parent")
        .join("niri-mimeapps.list")
}
pub(super) fn replace(path: &Path, contents: Option<&[u8]>) -> Result<()> {
    if let Some(bytes) = contents {
        let tmp = path.with_extension(format!("{}.tmp", storage::id()));
        write_new(&tmp, bytes)?;
        if let Err(error) = io(fs::rename(&tmp, path)) {
            let _ = fs::remove_file(&tmp);
            return Err(error);
        }
    } else {
        journal::remove_file(path)?;
    }
    sync_dir(path.parent().expect("file parent"))
}
fn command(
    profile: &Profile,
    program: &str,
    args: &[&str],
    timeout: Duration,
    reason: &'static str,
) -> Result<String> {
    let result = helper_with_lock(
        program,
        args,
        Instant::now() + timeout,
        &profile.state.join("helper.lock"),
    )
    .map_err(|_| err(reason))?;
    if !result.success {
        return Err(err(reason));
    }
    String::from_utf8(result.stdout).map_err(|_| err(reason))
}
/// Only what changes at run time goes to the shell; it keeps the descriptive fields of the
/// rows it already listed. The IPC server of Quickshell 0.3 never answers a call that arrives
/// in more than one read, so a large message can leave the transaction unconfirmed.
fn shell_rows(rows: Vec<Setting>) -> Vec<Value> {
    rows.into_iter()
        .map(|row| {
            let mut compact = json!({"key": row.key, "value": row.value});
            if !row.override_value.is_null() {
                compact["override_value"] = row.override_value;
            }
            compact
        })
        .collect()
}
fn shell(profile: &Profile, doc: &Document, gaps: u16, timeout: Duration) -> Result<()> {
    let rows = serde_json::to_string(&json!({"rows": shell_rows(rows(doc, gaps))}))
        .map_err(|_| err("invalid_settings"))?;
    if command(
        profile,
        "emaki-shell",
        &["call", "settings", "apply", &rows],
        timeout,
        "shell_apply_failed",
    )?
    .trim()
        != "applied"
    {
        return Err(err("shell_apply_unconfirmed"));
    }
    Ok(())
}
/// A wrapper always includes the personal configuration; none of its files are edited.
pub(super) fn config(profile: &Profile, doc: &Document) -> Result<PathBuf> {
    let mut effective = doc.clone();
    input::protect_personal(profile, &mut effective);
    let doc = &effective;
    let home = std::env::var_os("HOME").ok_or_else(|| err("home_missing"))?;
    let base = if Path::new(&home).join(".config/niri/config.kdl").exists() {
        "niri/fork.kdl"
    } else {
        "niri/fork-system.kdl"
    };
    let base = data_dir().join(base);
    let mut text = "// Emaki managed session configuration.\n".to_owned();
    if profile.installed {
        // Output blocks use first-match precedence, unlike layout overrides.
        // Keep the managed display layer ahead of inherited personal defaults.
        let displays = profile.config.join("displays.kdl");
        let displays = displays
            .to_str()
            .ok_or_else(|| err("invalid_config_path"))?;
        text.push_str(&format!(
            "include optional=true {}\n",
            serde_json::to_string(displays).unwrap()
        ));
    }
    text.push_str(&format!("include {base:?}\n"));
    if doc.overrides().values().any(|value| !value.is_null()) {
        let id = doc.generation.as_deref().unwrap_or("manual");
        // Re-derive from current package defaults after an update, retaining only overrides.
        let fragment = profile.runtime.join(format!("settings-{id}.kdl"));
        let contents = doc
            .files(default_gaps()?)
            .into_iter()
            .find(|(name, _)| *name == "niri.kdl")
            .expect("niri fragment")
            .1;
        if read(&fragment)?.as_deref() != Some(contents.as_bytes()) {
            replace(&fragment, Some(contents.as_bytes()))?;
        }
        let path = fragment
            .to_str()
            .ok_or_else(|| err("invalid_config_path"))?;
        text.push_str(&format!(
            "include {}\n",
            serde_json::to_string(path).unwrap()
        ));
    }
    if doc.appearance.wallpaper.is_some() {
        text.push_str("emaki-wallpaper { mode \"image\"; }\n");
    }
    let path = profile.runtime.join(format!(
        "session-{}.kdl",
        doc.generation.as_deref().unwrap_or("default")
    ));
    if read(&path)?.as_deref() != Some(text.as_bytes()) {
        replace(&path, Some(text.as_bytes()))?;
    }
    Ok(path)
}
fn changed(before: &Document, after: &Document, keys: &[&str]) -> bool {
    let a = before.overrides();
    let b = after.overrides();
    keys.iter().any(|key| a.get(*key) != b.get(*key))
}
impl Pending {
    fn rollback(&self, profile: &Profile, timeout: Duration, offline: bool) -> Result<()> {
        let doc = Document::parse(Some(self.before.as_bytes()))?;
        let mut failure = None;
        // Try every compensation even when one service is unavailable.
        if self.mime {
            storage::mkdir(mime_path(profile).parent().expect("MIME parent"))?;
            if let Some(contents) = &self.legacy_mime_before {
                if let Err(e) = replace(&legacy_mime_path(profile), Some(contents.as_bytes())) {
                    failure = Some(e);
                }
            }
            if let Err(e) = replace(
                &if self.mime_fallback {
                    mime_path(profile)
                } else {
                    legacy_mime_path(profile)
                },
                self.mime_before.as_deref().map(str::as_bytes),
            ) {
                failure = Some(e);
            }
        }
        if let Some(snapshot) = &self.wallpaper {
            if !offline {
                if let Err(e) = command(
                    profile,
                    "emaki-settings-wallpaper",
                    &["restore", snapshot],
                    timeout,
                    "wallpaper_rollback_failed",
                ) {
                    failure = Some(e);
                }
            }
        }
        if self.shell && !offline {
            if let Err(e) = shell(profile, &doc, default_gaps()?, timeout) {
                failure = Some(e);
            }
        }
        if self.niri && !offline {
            if let Err(e) = config(profile, &doc).and_then(|path| niri_live::load(&path, timeout)) {
                failure = Some(e);
            }
        }
        failure.map_or(Ok(()), Err)
    }
}

pub(super) fn apply(
    profile: &Profile,
    before: &Document,
    after: &Document,
    gaps: u16,
    timeout: Duration,
) -> Result<()> {
    if changed(before, after, &[LAYOUTS, SWITCH_KEY]) {
        // Installed keyboard keys live on the machine; an undo of an observed hand edit of
        // these inert values is not replayed (set them with `emaki settings set`).
        return Err(err("machine_setting_not_in_history"));
    }
    let niri = before.input != after.input
        || before.shortcuts != after.shortcuts
        || changed(before, after, &[GAPS, FLOATING, COLUMN_WIDTH, FOCUS_MOUSE])
        || (changed(before, after, &[WALLPAPER]) && std::env::var_os("NIRI_SOCKET").is_some());
    let shell_changed = changed(
        before,
        after,
        &[
            CLOCK_24_HOUR,
            BAR_AUTOHIDE,
            BAR_OVERVIEW,
            DOCK_ON,
            DOCK_AUTO_HIDE,
            DND,
            UNTIL,
            SCHEDULE,
            RULES,
            SYSTEM_SOUNDS,
        ],
    );
    let mime = changed(before, after, &[BROWSER, FILES, MAIL, EDITOR]);
    let wallpaper_changed = changed(before, after, &[WALLPAPER]);
    for key in [TERMINAL, BROWSER, FILES, MAIL, EDITOR] {
        if changed(before, after, &[key]) {
            if let Some(id) = after.overrides().get(key).and_then(Value::as_str) {
                command(
                    profile,
                    "emaki-terminal",
                    &[
                        if key == TERMINAL {
                            "--check-terminal"
                        } else {
                            "--check"
                        },
                        id,
                    ],
                    timeout,
                    "application_unavailable",
                )?;
            }
        }
    }
    if niri && std::env::var_os("NIRI_SOCKET").is_none() {
        return Err(err("niri_socket_missing"));
    }
    if mime
        && !std::env::var("XDG_CURRENT_DESKTOP")
            .unwrap_or_default()
            .split(':')
            .any(|v| v.eq_ignore_ascii_case("niri"))
    {
        return Err(err("desktop_defaults_unavailable"));
    }
    let mime_before = if mime {
        read(&mime_path(profile))?
            .map(|bytes| String::from_utf8(bytes).map_err(|_| err("invalid_mime_file")))
            .transpose()?
    } else {
        None
    };
    if mime_before
        .as_deref()
        .is_some_and(|s| !s.starts_with(MIME_HEADER))
    {
        return Err(err("personal_desktop_defaults_conflict"));
    }
    let legacy_mime_before = if mime {
        read(&legacy_mime_path(profile))?
            .map(|bytes| String::from_utf8(bytes).map_err(|_| err("invalid_mime_file")))
            .transpose()?
            .filter(|text| text.starts_with(MIME_HEADER))
    } else {
        None
    };
    let wallpaper = if wallpaper_changed {
        Some(command(
            profile,
            "emaki-settings-wallpaper",
            &["snapshot"],
            timeout,
            "wallpaper_snapshot_failed",
        )?)
    } else {
        None
    };
    let pending = Pending {
        schema_version: 1,
        candidate: after
            .generation
            .clone()
            .ok_or_else(|| err("invalid_generation_reference"))?,
        before: before.text(),
        after: after.text(),
        niri,
        shell: shell_changed,
        mime,
        mime_fallback: true,
        wallpaper,
        mime_before,
        legacy_mime_before,
    };
    let snapshot = serde_json::to_vec(&pending).unwrap();
    // Recovery uses the same bounded reader as the source; reject before side effects.
    if snapshot.len() > 64 * 1024 {
        return Err(err("session_snapshot_too_large"));
    }
    write_new(&pending_path(profile), &snapshot)?;
    sync_dir(&profile.history())?;
    let result = (|| {
        if niri {
            niri_live::load(&config(profile, after)?, timeout)?;
        }
        if wallpaper_changed {
            command(
                profile,
                "emaki-settings-wallpaper",
                &["apply", after.appearance.wallpaper.as_deref().unwrap_or("")],
                timeout,
                "wallpaper_apply_failed",
            )?;
        }
        if mime {
            publish_mime(profile, after, gaps)?;
            if pending.legacy_mime_before.is_some() {
                replace(&legacy_mime_path(profile), None)?;
            }
            mime::verify(profile, after, timeout)?;
        }
        if shell_changed {
            shell(profile, after, gaps, timeout)?;
        }
        Ok(())
    })();
    if let Err(error) = result {
        if pending.rollback(profile, timeout, false).is_err() {
            return Err(err("session_apply_and_rollback_failed"));
        }
        finish(profile)?;
        return Err(error);
    }
    Ok(())
}
pub(super) fn migrate_mime(profile: &Profile, doc: &Document) -> Result<()> {
    let legacy = legacy_mime_path(profile);
    let owned_legacy =
        read(&legacy)?.is_some_and(|bytes| bytes.starts_with(MIME_HEADER.as_bytes()));
    if owned_legacy
        || doc.defaults.browser.is_some()
        || doc.defaults.files.is_some()
        || doc.defaults.mail.is_some()
        || doc.defaults.editor.is_some()
        || read(&mime_path(profile))?.is_some()
    {
        // Rebuild disposable associations from the current source at login too.
        publish_mime(profile, doc, default_gaps()?)?;
    }
    if owned_legacy {
        replace(&legacy, None)?;
    }
    Ok(())
}
pub(super) fn finish(profile: &Profile) -> Result<()> {
    journal::remove_file(&pending_path(profile))?;
    sync_dir(&profile.history())
}
pub(super) fn pending(profile: &Profile) -> Result<bool> {
    Ok(read(&pending_path(profile))?.is_some())
}
/// Source rename is authoritative: an uncommitted side effect is compensated before journal cleanup.
pub(super) fn recover(profile: &Profile, timeout: Duration, offline: bool) -> Result<()> {
    helper_wait(&profile.state.join("helper.lock"), Instant::now() + timeout)
        .map_err(|e| err(e.reason))?;
    let Some(bytes) = read(&pending_path(profile))? else {
        return Ok(());
    };
    let pending: Pending =
        serde_json::from_slice(&bytes).map_err(|_| err("live_recovery_corrupt"))?;
    if pending.schema_version != 1 || !journal::valid_id(&pending.candidate) {
        return Err(err("live_recovery_corrupt"));
    }
    let current = Document::parse(read(&profile.source())?.as_deref())?;
    let before = Document::parse(Some(pending.before.as_bytes()))?;
    let after = Document::parse(Some(pending.after.as_bytes()))?;
    if after.generation.as_deref() != Some(&pending.candidate) {
        return Err(err("live_recovery_corrupt"));
    }
    if !offline
        && std::env::var_os("NIRI_SOCKET").is_none()
        && std::env::var_os("WAYLAND_DISPLAY").is_none()
        && (pending.niri || pending.shell || pending.wallpaper.is_some())
    {
        return Err(err("session_recovery_deferred"));
    }
    if current != before && current != after {
        // The source belongs to the person: converge to it, never overwrite it.
        if pending.mime {
            publish_mime(profile, &current, default_gaps()?)?;
            migrate_mime(profile, &current)?;
        }
        if !offline {
            if pending.niri {
                niri_live::load(&config(profile, &current)?, timeout)?;
            }
            if pending.shell {
                shell(profile, &current, default_gaps()?, timeout)?;
            }
            if pending.wallpaper.is_some() {
                command(
                    profile,
                    "emaki-settings-wallpaper",
                    &[
                        "apply",
                        current.appearance.wallpaper.as_deref().unwrap_or(""),
                    ],
                    timeout,
                    "wallpaper_rollback_failed",
                )?;
            }
        }
        finish(profile)?;
        eprintln!(
            "Settings recovery kept your edited settings.toml and rebuilt its managed overrides."
        );
        return Ok(());
    }
    if current != after {
        pending.rollback(profile, timeout, offline)?;
    }
    finish(profile)
}

#[cfg(test)]
#[path = "live_tests.rs"]
mod tests;
