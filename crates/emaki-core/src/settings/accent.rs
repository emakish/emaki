// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
//! The chosen accent for programs outside the shell (DECISIONS 2026-10-15).
//!
//! The packaged configs keep the default accent. The session path that builds the niri
//! border writes the chosen accent into the runtime directory: kitty's packaged theme
//! includes `kitty.conf`; `emaki-config-path` hands fuzzel the `fuzzel/fuzzel.ini` copy and
//! `emaki-lock` hands hyprlock the `hypr/hyprlock.conf` copy when they exist. A missing file
//! always means the packaged default; no packaged file is edited.
//!
//! Qt reads persistent state instead (qt6ct and KDE read their files outside a session too):
//! the qt6ct colour scheme `$XDG_STATE_HOME/emaki/accent/qt6ct.conf` is always written; the
//! packaged default `qt6ct.conf` (the one qt6ct copies into a home) names it as
//! `~/.local/state/…`, the default state directory. Only an unchanged historical skel copy
//! of qt6ct.conf is migrated; edited files stay personal. KDE's `kdeglobals` takes the accent from
//! `$XDG_STATE_HOME/emaki/defaults/kdeglobals`, the managed layer the session puts on
//! `XDG_CONFIG_DIRS` (scripts/niri-emaki-session); it exists only for a chosen accent.
use super::live::replace;
use super::storage::{Profile, mkdir, read};
use super::*;
use std::fs;
use std::os::unix::fs::MetadataExt;

mod migration;

const PACKAGED_HYPRLOCK: &str = include_str!("../../../../hypr/hyprlock.conf");
const PACKAGED_QT6CT: &str = include_str!("../../../../qt6ct/emaki.conf");
const PACKAGED_KDEGLOBALS: &str = include_str!("../../../../packaging/emaki-config/kdeglobals");
/// Every file starts with this line; the launchers use a copy only when it does.
pub(super) const HEADER: &str =
    "# Emaki accent: written by the session from settings.toml; edits are replaced.\n";
pub(super) const KITTY: &str = "kitty.conf";
pub(super) const FUZZEL: &str = "fuzzel/fuzzel.ini";
pub(super) const HYPRLOCK: &str = "hypr/hyprlock.conf";

/// `$XDG_RUNTIME_DIR/emaki-settings/accent`, the path the packaged configs name.
pub(super) fn dir(profile: &Profile) -> PathBuf {
    if profile.installed {
        profile.runtime.join("accent")
    } else {
        // An isolated profile's runtime directory is XDG_RUNTIME_DIR itself.
        profile.runtime.join("emaki-settings/accent")
    }
}

fn colours(doc: &Document) -> (&'static str, &'static str, &'static str) {
    let accents = palette()["accents"].as_table().expect("accents");
    let (name, accent) = doc
        .appearance
        .accent
        .as_deref()
        .and_then(|name| accents.get_key_value(name))
        .unwrap_or_else(|| {
            accents
                .get_key_value(default_accent())
                .expect("default accent")
        });
    (
        name,
        accent["base"].as_str().expect("accent base"),
        accent["text"].as_str().expect("accent text"),
    )
}

/// The files for `doc`'s accent; `None` removes a file so the packaged one is used.
/// kitty's file is always written: its include of a missing file logs on every start.
pub(super) fn files(doc: &Document) -> [(&'static str, Option<String>); 3] {
    let (name, base, text) = colours(doc);
    let (default, default_base, default_text) = colours(&Document::default());
    let chosen = name != default;
    [
        (
            KITTY,
            Some(format!(
                "{HEADER}selection_foreground #{text}\nselection_background #{base}\n\
                 url_color #{base}\nactive_border_color #{base}\n"
            )),
        ),
        (
            FUZZEL,
            chosen.then(|| {
                format!(
                    "{HEADER}include={}\n\n[colors]\nmatch={base}ff\nselection={base}ff\n\
                     selection-text={text}ff\nselection-match={text}ff\nborder={base}ff\n",
                    data_dir().join("fuzzel/fuzzel.ini").display()
                )
            }),
        ),
        (
            HYPRLOCK,
            chosen.then(|| {
                format!(
                    "{HEADER}{}",
                    PACKAGED_HYPRLOCK
                        .replace(default_base, base)
                        .replace(default_text, text)
                )
            }),
        ),
    ]
}

/// The qt6ct colour scheme (always) and the `kdeglobals` layer (only for a chosen accent).
pub(super) fn qt_files(doc: &Document) -> (String, Option<String>) {
    let (name, base, text) = colours(doc);
    let (default, default_base, default_text) = colours(&Document::default());
    let scheme = PACKAGED_QT6CT
        .replace(&format!("#ff{default_base}"), &format!("#ff{base}"))
        .replace(&format!("#ff{default_text}"), &format!("#ff{text}"));
    let rgb = |hex: &str| -> String {
        (0..3)
            .map(|i| u8::from_str_radix(&hex[i * 2..i * 2 + 2], 16).expect("hex colour"))
            .map(|n| n.to_string())
            .collect::<Vec<_>>()
            .join(",")
    };
    let kde = (name != default).then(|| {
        // Only the keys drawn from the accent; every other key stays the packaged file's.
        let (from_base, from_text) = (rgb(default_base), rgb(default_text));
        let mut out = HEADER.to_owned();
        let mut group = "";
        let mut open = false;
        for line in PACKAGED_KDEGLOBALS.lines() {
            if line.starts_with('[') {
                group = line;
                open = false;
            } else if let Some((key, value)) = line.split_once('=') {
                let value = if value == from_base {
                    rgb(base)
                } else if value == from_text {
                    rgb(text)
                } else {
                    continue;
                };
                if !open {
                    out.push_str(&format!("\n{group}\n"));
                    open = true;
                }
                out.push_str(&format!("{key}={value}\n"));
            }
        }
        out
    });
    (format!("{HEADER}{scheme}"), kde)
}

/// The packaged `qt6ct.conf` names `~/QT6CT_SCHEME`: qt6ct expands only `~` and `$VAR/` (an
/// unset variable to nothing), so it cannot name `$XDG_STATE_HOME`. The session writes the
/// scheme to `$XDG_STATE_HOME/emaki/accent/qt6ct.conf`, which is that path whenever
/// `XDG_STATE_HOME` is unset or the default; never below HOME otherwise (a test's or a
/// person's own state directory is all the session writes to).
#[cfg(test)]
const QT6CT_SCHEME: &str = ".local/state/emaki/accent/qt6ct.conf";
pub(super) fn qt6ct_scheme(profile: &Profile) -> PathBuf {
    profile.state.join("accent/qt6ct.conf")
}

const QT6CT_LIMIT: u64 = 64 * 1024;

/// A configuration file as a program reads it, without ever blocking the session start: links
/// followed (dotfiles), opened non-blocking, only a regular file of at most 64 KiB.
fn read_config(path: &Path) -> Option<String> {
    use rustix::fs::OFlags;
    use std::io::Read;
    use std::os::unix::fs::OpenOptionsExt;
    let file = fs::OpenOptions::new()
        .read(true)
        .custom_flags((OFlags::NONBLOCK | OFlags::CLOEXEC).bits() as i32)
        .open(path)
        .ok()?;
    let meta = file.metadata().ok()?;
    if !meta.is_file() || meta.len() > QT6CT_LIMIT {
        return None;
    }
    let mut text = String::new();
    file.take(QT6CT_LIMIT + 1).read_to_string(&mut text).ok()?;
    Some(text)
}

/// The qt6ct.conf qt6ct uses (`Qt6CT::initConfig`): the person's if it exists, otherwise the first
/// `qt6ct/qt6ct.conf` file on `XDG_CONFIG_DIRS` (default `/etc/xdg`), which qt6ct copies. `None`:
/// none, or one that cannot be read safely.
fn effective_qt6ct(profile: &Profile) -> Option<String> {
    let personal = profile.config.parent()?.join("qt6ct/qt6ct.conf");
    if fs::metadata(&personal).is_ok() {
        return read_config(&personal);
    }
    let dirs = std::env::var("XDG_CONFIG_DIRS").unwrap_or_default();
    let dirs: Vec<&str> = if dirs.is_empty() {
        vec!["/etc/xdg"]
    } else {
        dirs.split(':').filter(|d| d.starts_with('/')).collect()
    };
    dirs.into_iter()
        .map(|dir| Path::new(dir).join("qt6ct/qt6ct.conf"))
        .find(|path| fs::metadata(path).is_ok_and(|meta| meta.is_file()))
        .and_then(|path| read_config(&path))
}

/// A QSettings (ini) string value: unquoted up to a `;` comment, or one double-quoted string with
/// backslash escapes. `None` for what QSettings reads as a list or a typed value, or escapes in
/// an unquoted value: not a path this code can resolve the way qt6ct would.
fn qsettings_string(raw: &str) -> Option<String> {
    let value = raw.trim();
    if let Some(quoted) = value.strip_prefix('"') {
        let (mut out, mut chars) = (String::new(), quoted.chars());
        loop {
            match chars.next()? {
                '"' => break,
                '\\' => out.push(match chars.next()? {
                    'n' => '\n',
                    't' => '\t',
                    'r' => '\r',
                    other => other,
                }),
                c => out.push(c),
            }
        }
        let rest = chars.as_str().trim();
        return (rest.is_empty() || rest.starts_with(';')).then_some(out);
    }
    let value = value.split(';').next().unwrap_or_default().trim();
    (!value.starts_with('@') && !value.contains([',', '\\', '"'])).then(|| value.to_owned())
}

/// `Qt6CT::resolvePath`: every `~` becomes HOME; then for each `$NAME/` (NAME of `A`–`Z` and
/// `_`) found in that string, every `$NAME` becomes the variable's value, an unset one nothing.
fn qt6ct_resolve(path: &str) -> String {
    let home = std::env::var("HOME").unwrap_or_default();
    let mut text = path.replace('~', &home);
    if !text.contains('$') {
        return text;
    }
    let bytes = text.as_bytes();
    let (mut names, mut i) = (Vec::new(), 0);
    while i < bytes.len() {
        if bytes[i] == b'$' {
            let start = i + 1;
            let mut end = start;
            while end < bytes.len() && (bytes[end].is_ascii_uppercase() || bytes[end] == b'_') {
                end += 1;
            }
            if end > start && bytes.get(end) == Some(&b'/') {
                names.push(text[start..end].to_owned());
                i = end + 1;
                continue;
            }
        }
        i += 1;
    }
    for name in names {
        text = text.replace(
            &format!("${name}"),
            &std::env::var(&name).unwrap_or_default(),
        );
    }
    text
}

/// Whether qt6ct's effective palette is the session scheme: the qt6ct.conf it uses (only
/// read) has the custom palette on (`QVariant::toBool`) and a scheme path that resolves, as
/// qt6ct resolves it, to `scheme`. Anything else is another palette, and KDE keeps its own.
fn qt6ct_follows(profile: &Profile, scheme: &Path) -> bool {
    let Some(text) = effective_qt6ct(profile) else {
        return false;
    };
    qt6ct_selects(&text, scheme)
}

/// Apply the same palette and path rules to an existing selector or a proposed replacement.
fn qt6ct_selects(text: &str, scheme: &Path) -> bool {
    let (mut section, mut path, mut custom) = ("", None, false);
    for line in text.lines().map(str::trim) {
        if line.starts_with(';') || line.starts_with('#') {
            continue;
        }
        if line.starts_with('[') {
            section = line;
        } else if section == "[Appearance]" {
            match line.split_once('=').map(|(k, v)| (k.trim(), v)) {
                Some(("color_scheme_path", value)) => path = Some(qsettings_string(value)),
                Some(("custom_palette", value)) => {
                    custom = qsettings_string(value).is_some_and(|v| {
                        !(v.is_empty() || v == "0" || v.eq_ignore_ascii_case("false"))
                    })
                }
                _ => {}
            }
        }
    }
    custom
        && path
            .flatten()
            .is_some_and(|p| Path::new(&qt6ct_resolve(&p)) == scheme)
}

/// Create the missing directories of `path` (private, like the profile's own); existing
/// ancestors are left as they are.
fn parents(path: &Path) -> Result<()> {
    storage::safe_path(path)?;
    if !path.exists() {
        parents(path.parent().ok_or_else(|| err("unsafe_profile_path"))?)?;
        mkdir(path)?;
    }
    Ok(())
}

/// `$XDG_STATE_HOME/emaki/defaults/kdeglobals`, above the packaged `kdeglobals` in a session.
pub(super) fn kdeglobals(profile: &Profile) -> PathBuf {
    profile.state.join("defaults/kdeglobals")
}

/// Bring the Qt state files in line with `doc`; the session writes them before Qt starts.
pub(super) fn publish_qt(profile: &Profile, doc: &Document) -> Result<()> {
    let (scheme, kde) = qt_files(doc);
    let scheme_path = qt6ct_scheme(profile);
    parents(scheme_path.parent().expect("accent state"))?;
    if read(&scheme_path)?.as_deref() != Some(scheme.as_bytes()) {
        replace(&scheme_path, Some(scheme.as_bytes()))?;
    }
    migration::publish(profile);
    // Decide after migration, so Qt and KDE follow the same palette on the first login too.
    let kde = kde.filter(|_| qt6ct_follows(profile, &scheme_path));
    let kde_path = kdeglobals(profile);
    let kde_dir = kde_path.parent().expect("defaults");
    if kde.is_some() {
        parents(kde_dir)?;
    }
    if (kde.is_some() || kde_dir.exists())
        && read(&kde_path)?.as_deref() != kde.as_deref().map(str::as_bytes)
    {
        replace(&kde_path, kde.as_deref().map(str::as_bytes))?;
    }
    Ok(())
}

/// Bring the runtime files in line with `doc`. Unchanged files are not rewritten.
pub(super) fn publish(profile: &Profile, doc: &Document) -> Result<()> {
    let root = dir(profile);
    for (name, contents) in files(doc) {
        let path = root.join(name);
        if contents.is_some() {
            mkdir(root.parent().expect("runtime"))?;
            mkdir(&root)?;
            mkdir(path.parent().expect("accent parent"))?;
        } else if !path.parent().expect("accent parent").exists() {
            continue;
        }
        if read(&path)?.as_deref() != contents.as_deref().map(str::as_bytes) {
            replace(&path, contents.as_deref().map(str::as_bytes))?;
        }
    }
    Ok(())
}

/// Ask every kitty of this session to reload its configuration (kitty reloads on SIGUSR1).
/// Only processes of this user whose XDG_RUNTIME_DIR is ours: their include names our file.
pub(super) fn reload_terminals() {
    let Some(runtime) = std::env::var_os("XDG_RUNTIME_DIR") else {
        return;
    };
    let mut wanted = b"XDG_RUNTIME_DIR=".to_vec();
    wanted.extend_from_slice(runtime.as_encoded_bytes());
    let uid = rustix::process::getuid().as_raw();
    let Ok(entries) = fs::read_dir("/proc") else {
        return;
    };
    for entry in entries.flatten() {
        let Some(pid) = entry
            .file_name()
            .to_str()
            .and_then(|name| name.parse::<i32>().ok())
            .and_then(rustix::process::Pid::from_raw)
        else {
            continue;
        };
        let path = entry.path();
        if fs::metadata(&path).map(|meta| meta.uid()).ok() != Some(uid)
            || fs::read(path.join("comm")).ok().as_deref() != Some(b"kitty\n")
            || !fs::read(path.join("environ"))
                .is_ok_and(|environ| environ.split(|b| *b == 0).any(|v| v == wanted))
        {
            continue;
        }
        let _ = rustix::process::kill_process(pid, rustix::process::Signal::USR1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn doc(accent: Option<&str>, theme: Option<&str>) -> Document {
        let mut doc = Document::default();
        doc.appearance.accent = accent.map(Into::into);
        doc.appearance.theme = theme.map(Into::into);
        doc
    }

    #[test]
    fn default_accent_files_repeat_the_packaged_configs() {
        let [kitty, fuzzel, hyprlock] = files(&doc(None, None));
        let packaged = include_str!("../../../../kitty/theme.conf");
        for line in kitty.1.unwrap().lines().skip(1) {
            assert!(packaged.lines().any(|l| l == line), "{line}");
        }
        // Never chosen: fuzzel and hyprlock use the packaged files themselves.
        assert_eq!(fuzzel.1, None);
        assert_eq!(hyprlock.1, None);
        assert_eq!(files(&doc(Some(default_accent()), None))[1].1, None);
    }

    #[test]
    fn chosen_orange_reaches_every_file_in_light_and_dark() {
        let orange = palette()["accents"]["orange"]["base"].as_str().unwrap();
        let (_, rose, _) = colours(&doc(None, None));
        for theme in [None, Some("light"), Some("dark"), Some("automatic")] {
            for (name, contents) in files(&doc(Some("orange"), theme)) {
                let contents = contents.unwrap_or_else(|| panic!("{name} missing"));
                assert!(contents.starts_with(HEADER), "{name}");
                assert!(contents.contains(orange), "{name}");
                assert!(!contents.contains(rose), "{name}: {contents}");
            }
        }
    }

    #[test]
    fn copies_keep_every_packaged_line_except_the_accent() {
        let (_, rose, rose_text) = colours(&doc(None, None));
        let orange = palette()["accents"]["orange"]["base"].as_str().unwrap();
        let hyprlock = files(&doc(Some("orange"), None))[2].1.clone().unwrap();
        assert_eq!(
            hyprlock.strip_prefix(HEADER).unwrap(),
            PACKAGED_HYPRLOCK.replace(rose, orange)
        );
        assert!(!PACKAGED_HYPRLOCK.contains(rose_text));
        assert!(PACKAGED_HYPRLOCK.contains(&format!("rgb({rose})")));
        let fuzzel = files(&doc(Some("orange"), None))[1].1.clone().unwrap();
        let packaged = include_str!("../../../../fuzzel/fuzzel.ini");
        let keys = |text: &str| -> Vec<String> {
            text.split("[colors]")
                .nth(1)
                .unwrap()
                .lines()
                .filter_map(|l| l.split_once('=').map(|(k, _)| k.to_owned()))
                .collect()
        };
        // Every packaged colour drawn from the accent is overridden, and nothing else.
        let accent_keys: Vec<_> = keys(packaged)
            .into_iter()
            .filter(|key| {
                packaged.lines().any(|l| {
                    l.starts_with(&format!("{key}=")) && (l.contains(rose) || l.contains(rose_text))
                })
            })
            .collect();
        let section = fuzzel.split("[colors]").nth(1).unwrap();
        let ours: Vec<_> = section
            .lines()
            .filter_map(|l| l.split_once('=').map(|(k, _)| k.to_owned()))
            .collect();
        assert_eq!(ours, accent_keys);
    }

    #[test]
    fn qt_files_change_only_the_accent_and_repeat_the_packaged_default() {
        for theme in [None, Some("light"), Some("dark")] {
            let (scheme, kde) = qt_files(&doc(None, theme));
            assert_eq!(scheme, format!("{HEADER}{PACKAGED_QT6CT}"));
            assert_eq!(kde, None);
        }
        let (scheme, kde) = qt_files(&doc(Some("orange"), Some("light")));
        let changed: Vec<_> = PACKAGED_QT6CT
            .lines()
            .zip(scheme.strip_prefix(HEADER).unwrap().lines())
            .filter(|(a, b)| a != b)
            .collect();
        // active, disabled (the link colour) and inactive colours.
        assert_eq!(changed.len(), 3, "{changed:?}");
        for (packaged, ours) in changed {
            assert_eq!(
                packaged
                    .replace("#ffe780aa", "#ffe2733f")
                    .replace("#ff300c1c", "#ff2a0f05"),
                ours
            );
        }
        assert!(!scheme.contains("#ffe780aa") && !scheme.contains("#ff300c1c"));
        let kde = kde.unwrap();
        assert!(kde.starts_with(HEADER));
        assert!(kde.contains(
            "[Colors:Selection]\nBackgroundNormal=226,115,63\nForegroundNormal=42,15,5\n"
        ));
        // Every packaged key drawn from the accent is overridden, and nothing else.
        let accent_keys = PACKAGED_KDEGLOBALS
            .lines()
            .filter(|l| l.ends_with("=231,128,170") || l.ends_with("=48,12,28"))
            .count();
        let ours: Vec<_> = kde.lines().filter(|l| l.contains('=')).collect();
        assert_eq!(ours.len(), accent_keys);
        assert!(
            ours.iter()
                .all(|l| l.ends_with("=226,115,63") || l.ends_with("=42,15,5"))
        );
    }

    #[test]
    fn qt_state_follows_the_accent_and_no_personal_file_is_written() {
        let root = std::env::temp_dir().join(format!("emaki-accent-qt-{}", storage::id()));
        fs::create_dir(&root).unwrap();
        // Isolated: the state is not below this process's HOME, so here qt6ct follows only a
        // qt6ct.conf naming the scheme's absolute path (the Python test covers `~/…`).
        let profile = Profile {
            config: root.join("config/emaki"),
            state: root.join("state/emaki"),
            runtime: root.join("runtime"),
            installed: false,
        };
        let skel = include_str!("../../../../etc-skel/.config/qt6ct/qt6ct.conf");
        let follows = format!(
            "[Appearance]\ncolor_scheme_path={}\ncustom_palette=true\n",
            root.join("state/emaki/accent/qt6ct.conf").display()
        );
        let personal = [
            (root.join("config/qt6ct/qt6ct.conf"), follows.clone()),
            (
                root.join("config/kdeglobals"),
                "[General]\nColorScheme=Mine\n".to_owned(),
            ),
        ];
        for (path, text) in &personal {
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(path, text).unwrap();
        }
        let scheme = qt6ct_scheme(&profile);
        assert_eq!(scheme, root.join("state/emaki/accent/qt6ct.conf"));
        // Never chosen: the scheme exists in Rose (missing directories created), no KDE layer.
        publish_qt(&profile, &doc(None, None)).unwrap();
        assert!(fs::read_to_string(&scheme).unwrap().contains("#ffe780aa"));
        assert!(!kdeglobals(&profile).exists());
        publish_qt(&profile, &doc(Some("orange"), None)).unwrap();
        assert!(fs::read_to_string(&scheme).unwrap().contains("#ffe2733f"));
        assert!(
            fs::read_to_string(kdeglobals(&profile))
                .unwrap()
                .contains("226,115,63")
        );
        publish_qt(&profile, &doc(None, None)).unwrap();
        assert!(fs::read_to_string(&scheme).unwrap().contains("#ffe780aa"));
        assert!(!kdeglobals(&profile).exists());
        // An edited qt6ct.conf naming another scheme (the packaged path) or with the
        // custom palette off: Qt keeps that palette, so KDE gets no accent layer either.
        let packaged = format!(
            "# Personal palette\n[Appearance]\ncolor_scheme_path={}\ncustom_palette=true\n",
            data_dir().join("qt6ct/emaki.conf").display()
        );
        // The packaged default names ~/…, which is not this state here: qt6ct would not read it.
        for own in [
            packaged,
            follows.replace("custom_palette=true", "custom_palette=false"),
            skel.to_owned(),
        ] {
            fs::write(&personal[0].0, &own).unwrap();
            publish_qt(&profile, &doc(Some("orange"), None)).unwrap();
            assert!(!kdeglobals(&profile).exists(), "{own}");
            assert!(fs::read_to_string(&scheme).unwrap().contains("#ffe2733f"));
            assert_eq!(fs::read_to_string(&personal[0].0).unwrap(), own);
        }
        fs::write(&personal[0].0, &follows).unwrap();
        // The person's files are exactly as they were, whatever was chosen.
        for (path, text) in &personal {
            assert_eq!(&fs::read_to_string(path).unwrap(), text);
        }
        assert_eq!(fs::read_dir(root.join("config/qt6ct")).unwrap().count(), 1);
        // No qt6ct.conf yet: qt6ct's copy of the packaged default would name ~/…, not this
        // state, so no KDE layer; and none is created.
        fs::remove_file(&personal[0].0).unwrap();
        publish_qt(&profile, &doc(Some("orange"), None)).unwrap();
        assert!(!kdeglobals(&profile).exists());
        assert!(!personal[0].0.exists());
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn qt6ct_values_and_paths_resolve_as_qt6ct_reads_them() {
        assert_eq!(qsettings_string(" /a/b ").as_deref(), Some("/a/b"));
        assert_eq!(qsettings_string("\"/a b/c\"").as_deref(), Some("/a b/c"));
        assert_eq!(qsettings_string("\"/a\\\"b\"").as_deref(), Some("/a\"b"));
        assert_eq!(qsettings_string("/a/b ; comment").as_deref(), Some("/a/b"));
        for list_or_typed in ["/a, /b", "@Invalid()", "\"/a\", \"/b\"", "\"/a"] {
            assert_eq!(qsettings_string(list_or_typed), None, "{list_or_typed}");
        }
        let home = std::env::var("HOME").unwrap_or_default();
        assert_eq!(qt6ct_resolve("~/x/~"), format!("{home}/x/{home}"));
        assert_eq!(qt6ct_resolve("$HOME/x"), format!("{home}/x"));
        // An unset variable becomes nothing; `$NAME` without a slash after it stays.
        assert_eq!(qt6ct_resolve("$EMAKI_UNSET_FOR_TESTS/abs/x"), "/abs/x");
        assert_eq!(qt6ct_resolve("/a/$HOME"), "/a/$HOME");
        assert_eq!(qt6ct_resolve("/a/$lower/x"), "/a/$lower/x");
    }

    #[test]
    fn the_packaged_qt6ct_conf_names_the_session_scheme() {
        let packaged = include_str!("../../../../etc-skel/.config/qt6ct/qt6ct.conf");
        assert!(
            packaged
                .lines()
                .any(|l| l == format!("color_scheme_path=~/{QT6CT_SCHEME}"))
        );
    }

    #[test]
    fn publish_writes_chosen_and_removes_back_to_packaged() {
        let root = std::env::temp_dir().join(format!("emaki-accent-{}", storage::id()));
        fs::create_dir(&root).unwrap();
        let profile = Profile {
            config: root.join("config"),
            state: root.join("state"),
            runtime: root.join("runtime"),
            installed: true,
        };
        mkdir(&profile.runtime).unwrap();
        publish(&profile, &doc(Some("orange"), Some("light"))).unwrap();
        let accent = dir(&profile);
        for name in [KITTY, FUZZEL, HYPRLOCK] {
            let text = fs::read_to_string(accent.join(name)).unwrap();
            assert!(text.contains("e2733f"), "{name}");
        }
        publish(&profile, &doc(None, None)).unwrap();
        assert!(
            fs::read_to_string(accent.join(KITTY))
                .unwrap()
                .contains("e780aa")
        );
        assert!(!accent.join(FUZZEL).exists());
        assert!(!accent.join(HYPRLOCK).exists());
        // An isolated profile's runtime directory is XDG_RUNTIME_DIR: the same path below it.
        let isolated = Profile {
            runtime: root.join("isolated"),
            installed: false,
            ..profile
        };
        mkdir(&isolated.runtime).unwrap();
        publish(&isolated, &doc(Some("orange"), None)).unwrap();
        assert!(
            fs::read_to_string(root.join("isolated/emaki-settings/accent/hypr/hyprlock.conf"))
                .unwrap()
                .contains("e2733f")
        );
        fs::remove_dir_all(&root).unwrap();
    }
}
