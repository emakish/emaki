// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
//! The managed cursor also reaches GTK and applications activated outside niri.
use super::storage::Profile;
use super::{Document, Result, err, live};
use std::time::Duration;

const INTERFACE: &str = "org.gnome.desktop.interface";
const KEY: &str = "cursor-theme";

pub(super) fn theme(doc: &Document) -> &'static str {
    match doc.appearance.cursor.as_deref() {
        None | Some("automatic") => {
            if super::accent::is_dark(doc) {
                "Emaki-Dark"
            } else {
                "Emaki-White"
            }
        }
        Some("white") => "Emaki-White",
        Some("dark") => "Emaki-Dark",
        _ => unreachable!("validated cursor choice"),
    }
}

pub(super) fn personal(profile: &Profile, doc: &Document) -> bool {
    matches!(doc.appearance.cursor.as_deref(), None | Some("automatic"))
        && super::styles::personal_node(Some(profile), "xcursor-theme")
}

/// Retain the GVariant string, including escaping, for compensation on failure.
pub(super) fn snapshot(profile: &Profile, timeout: Duration) -> Result<String> {
    let value = live::command(
        profile,
        "gsettings",
        &["get", INTERFACE, KEY],
        timeout,
        "cursor_settings_unavailable",
    )?;
    let value = value.trim();
    if value.len() < 2
        || value.len() > 4096
        || !((value.starts_with('\'') && value.ends_with('\''))
            || (value.starts_with('"') && value.ends_with('"')))
        || value.chars().any(char::is_control)
    {
        return Err(err("cursor_settings_invalid"));
    }
    Ok(value.to_owned())
}

fn gtk(profile: &Profile, value: &str, expected: &str, timeout: Duration) -> Result<()> {
    live::command(
        profile,
        "gsettings",
        &["set", INTERFACE, KEY, value],
        timeout,
        "cursor_settings_apply_failed",
    )?;
    // Some backends can report a successful command without persisting the value.
    if snapshot(profile, timeout)? != expected {
        return Err(err("cursor_settings_unconfirmed"));
    }
    Ok(())
}

fn activation(profile: &Profile, doc: &Document, timeout: Duration) -> Result<()> {
    live::command(
        profile,
        "dbus-update-activation-environment",
        &["--systemd", &format!("XCURSOR_THEME={}", theme(doc))],
        timeout,
        "cursor_environment_apply_failed",
    )?;
    Ok(())
}

pub(super) fn apply(profile: &Profile, doc: &Document, timeout: Duration) -> Result<()> {
    if personal(profile, doc) {
        return Ok(());
    }
    let theme = theme(doc);
    gtk(profile, theme, &format!("'{theme}'"), timeout)?;
    activation(profile, doc, timeout)
}

pub(super) fn restore(
    profile: &Profile,
    doc: &Document,
    before: &str,
    timeout: Duration,
) -> Result<()> {
    // Attempt both compensations even when one service is unavailable.
    let gtk = gtk(profile, before, before, timeout);
    let activation = activation(profile, doc, timeout);
    gtk.and(activation)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn automatic_cursor_tracks_appearance_and_overrides_stay_fixed() {
        let mut doc = Document::default();
        assert_eq!(theme(&doc), "Emaki-White");
        doc.set(super::super::THEME, "dark").unwrap();
        assert_eq!(theme(&doc), "Emaki-Dark");
        doc.set(super::super::CURSOR, "white").unwrap();
        assert_eq!(theme(&doc), "Emaki-White");
        doc.set(super::super::CURSOR, "dark").unwrap();
        doc.set(super::super::THEME, "light").unwrap();
        assert_eq!(theme(&doc), "Emaki-Dark");
        doc.set(super::super::CURSOR, "automatic").unwrap();
        assert_eq!(theme(&doc), "Emaki-White");
    }

    #[test]
    fn retired_cursor_migrates_to_inherited_appearance() {
        for (appearance, expected) in [("light", "Emaki-White"), ("dark", "Emaki-Dark")] {
            let source = format!(
                "schema_version = 1\n[appearance]\ncursor = \"emaki\"\ntheme = \"{appearance}\"\n"
            );
            let doc = Document::parse(Some(source.as_bytes())).unwrap();
            assert_eq!(doc.appearance.cursor, None);
            assert_eq!(theme(&doc), expected);
            assert!(!doc.text().contains("cursor ="));
            let mut restored = doc.clone();
            restored
                .restore(super::super::CURSOR, &serde_json::json!("emaki"))
                .unwrap();
            assert_eq!(restored.appearance.cursor, None);
        }
    }
}
