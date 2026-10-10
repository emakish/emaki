// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
//! Managed defaults sit below personal configuration and above package defaults.
use super::storage::{Profile, mkdir, read};
use super::*;
use crate::transport::helper_with_lock;
use std::time::Instant;

pub(super) const HEADER: &str = "# Emaki managed defaults; change with emaki settings.\n";

pub(super) fn path(profile: &Profile) -> PathBuf {
    profile.state.join("defaults/mimeapps.list")
}

pub(super) fn publish(profile: &Profile, doc: &Document, gaps: u16) -> Result<()> {
    let destination = path(profile);
    mkdir(destination.parent().expect("MIME parent"))?;
    if read(&destination)?.is_some_and(|bytes| !bytes.starts_with(HEADER.as_bytes())) {
        return Err(err("personal_mime_file"));
    }
    let contents = doc
        .files(gaps)
        .into_iter()
        .find(|(name, _)| *name == "mimeapps.list")
        .map(|(_, contents)| format!("{HEADER}{contents}"));
    super::live::replace(&destination, contents.as_deref().map(str::as_bytes))
}

fn defaults(contents: &str, mime: &str) -> Vec<String> {
    let mut active = false;
    for line in contents.lines().map(str::trim) {
        if line.starts_with('[') {
            active = line == "[Default Applications]";
        } else if active
            && let Some((key, value)) = line.split_once('=')
            && key.trim() == mime
        {
            return value
                .split(';')
                .map(str::trim)
                .filter(|id| !id.is_empty())
                .map(str::to_owned)
                .collect();
        }
    }
    Vec::new()
}

pub(super) fn verify(profile: &Profile, doc: &Document, timeout: Duration) -> Result<()> {
    let root = profile.config.parent().expect("config parent");
    let mut personal = Vec::new();
    for desktop in std::env::var("XDG_CURRENT_DESKTOP")
        .unwrap_or_default()
        .split(':')
    {
        if !desktop.is_empty()
            && desktop
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
        {
            personal.push(root.join(format!("{}-mimeapps.list", desktop.to_ascii_lowercase())));
        }
    }
    personal.push(root.join("mimeapps.list"));
    let personal = personal
        .into_iter()
        .map(|path| {
            read(&path)?
                .map(|bytes| String::from_utf8(bytes).map_err(|_| err("invalid_mime_file")))
                .transpose()
        })
        .collect::<Result<Vec<_>>>()?;
    let directory = profile.state.join("defaults");
    let directory = directory
        .to_str()
        .filter(|value| !value.contains(':'))
        .ok_or_else(|| err("invalid_mime_directory"))?;
    let inherited = std::env::var("XDG_CONFIG_DIRS")
        .ok()
        .filter(|v| !v.is_empty())
        .unwrap_or_else(|| "/etc/xdg".into());
    let environment = format!("XDG_CONFIG_DIRS={directory}:{inherited}");
    for (mime, selected) in [
        ("x-scheme-handler/http", &doc.defaults.browser),
        ("x-scheme-handler/https", &doc.defaults.browser),
        ("text/html", &doc.defaults.browser),
        ("inode/directory", &doc.defaults.files),
        ("x-scheme-handler/mailto", &doc.defaults.mail),
        ("text/plain", &doc.defaults.editor),
    ] {
        if let Some(selected) = selected {
            let result = helper_with_lock(
                "/usr/bin/env",
                &[&environment, "gio", "mime", mime],
                Instant::now() + timeout,
                &profile.state.join("helper.lock"),
            )
            .map_err(|_| err("default_application_unconfirmed"))?;
            if !result.success {
                return Err(err("default_application_unconfirmed"));
            }
            let reply = String::from_utf8(result.stdout)
                .map_err(|_| err("default_application_unconfirmed"))?;
            let effective = reply
                .lines()
                .next()
                .and_then(|line| line.rsplit_once(": "))
                .map(|(_, value)| value.trim());
            // GIO skips unavailable entries. Permit its selected personal association,
            // or the managed fallback when no personal entry can handle this type.
            let accepted = effective == Some(selected.as_str())
                || personal.iter().flatten().any(|text| {
                    defaults(text, mime)
                        .iter()
                        .any(|id| Some(id.as_str()) == effective)
                });
            if !accepted {
                return Err(err("default_application_unconfirmed"));
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn only_default_associations_have_priority() {
        let text = "[Added Associations]\ntext/html=added.desktop;\n[Default Applications]\ntext/html = personal.desktop;fallback.desktop;\n";
        assert_eq!(
            defaults(text, "text/html"),
            ["personal.desktop", "fallback.desktop"]
        );
        assert!(defaults(text, "inode/directory").is_empty());
    }
}
