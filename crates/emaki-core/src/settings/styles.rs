// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
//! Local style bundles share the settings generation and rollback transaction.
use super::*;
use std::io::Read;

const BUILTIN: &str = include_str!("../../../../themes/catalog.json");

#[derive(Clone, Deserialize, Serialize)]
pub struct Style {
    pub id: String,
    pub name: String,
    pub accent: String,
    pub mode: String,
    pub wallpaper: String,
    pub overview_video: String,
    pub overview_still: String,
    pub thumbnail: String,
    #[serde(default)]
    pub available: bool,
    #[serde(default)]
    pub wallpaper_available: bool,
    #[serde(default)]
    pub video_available: bool,
    #[serde(default)]
    pub selected: bool,
}
#[derive(Deserialize)]
struct Catalog {
    schema_version: u32,
    styles: Vec<Style>,
}

pub(super) fn valid_id(id: &str) -> bool {
    !id.is_empty()
        && id.len() <= 64
        && id
            .bytes()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'-')
}

fn resolve(root: &Path, relative: &str) -> Result<String> {
    if relative.is_empty()
        || Path::new(relative)
            .components()
            .any(|c| !matches!(c, std::path::Component::Normal(_)))
    {
        return Err(err("invalid_style_asset"));
    }
    wallpaper(
        root.join(relative)
            .to_str()
            .ok_or_else(|| err("invalid_style_asset"))?,
    )
}

fn parse(text: &str, root: &Path) -> Result<Vec<Style>> {
    let catalog: Catalog = serde_json::from_str(text).map_err(|_| err("invalid_style_catalog"))?;
    if catalog.schema_version != 1 || catalog.styles.len() > 64 || !root.is_absolute() {
        return Err(err("invalid_style_catalog"));
    }
    let mut ids = BTreeSet::new();
    let mut styles = catalog.styles;
    for style in &mut styles {
        if !valid_id(&style.id)
            || !ids.insert(style.id.clone())
            || style.name.is_empty()
            || style.name.len() > 128
            || style.name.chars().any(char::is_control)
            || !matches!(style.mode.as_str(), "light" | "dark")
        {
            return Err(err("invalid_style_catalog"));
        }
        Document::default().set(ACCENT, &style.accent)?;
        style.wallpaper = resolve(root, &style.wallpaper)?;
        style.overview_video = resolve(root, &style.overview_video)?;
        style.overview_still = resolve(root, &style.overview_still)?;
        style.thumbnail = resolve(root, &style.thumbnail)?;
        // Small packaged previews keep the catalog useful before optional media is installed.
        if !Path::new(&style.thumbnail).is_file() {
            style.thumbnail = data_dir()
                .join("themes/previews")
                .join(format!("{}.jpg", style.id))
                .to_string_lossy()
                .into_owned();
        }
        // A still is mandatory; missing or unsupported videos fall back to it in the shell.
        style.wallpaper_available = Path::new(&style.wallpaper).is_file();
        style.available = style.wallpaper_available && Path::new(&style.overview_still).is_file();
        style.video_available = Path::new(&style.overview_video).is_file();
        style.selected = false;
    }
    Ok(styles)
}

pub(super) fn catalog() -> Result<Vec<Style>> {
    let root = std::env::var_os("EMAKI_THEME_ASSET_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|| data_dir().join("themes"));
    let text = if let Some(path) = std::env::var_os("EMAKI_THEME_CATALOG") {
        let mut bytes = Vec::new();
        std::fs::File::open(path)
            .and_then(|f| f.take(65537).read_to_end(&mut bytes))
            .map_err(|_| err("style_catalog_unavailable"))?;
        if bytes.len() > 65536 {
            return Err(err("invalid_style_catalog"));
        }
        String::from_utf8(bytes).map_err(|_| err("invalid_style_catalog"))?
    } else {
        BUILTIN.to_owned()
    };
    parse(&text, &root)
}

/// Resolve defaults without turning them into personal overrides.
pub(super) fn defaults() -> Style {
    catalog()
        .unwrap_or_default()
        .into_iter()
        .find(|s| s.id == "emaki")
        .unwrap_or_else(|| {
            parse(BUILTIN, &data_dir().join("themes"))
                .expect("builtin catalog")
                .remove(0)
        })
}

// Read personal blocks without mistaking comments or property strings for nodes.
// These files remain owned by the person; managed defaults never rewrite them.
pub(super) fn personal_node(profile: Option<&storage::Profile>, node: &str) -> bool {
    let Some(profile) = profile else { return false };
    let mut paths = vec![profile.config.join("niri-emaki.kdl")];
    if matches!(node, "cursor" | "xcursor-theme") {
        if let Some(config) = profile.config.parent() {
            paths.push(config.join("niri/config.kdl"));
        }
    }
    let home = if profile.installed {
        std::env::var_os("HOME").map(PathBuf::from)
    } else {
        profile
            .config
            .parent()
            .and_then(Path::parent)
            .map(|root| root.join("home"))
    };
    if profile.installed {
        if let Some(home) = &home {
            paths.push(home.join(".config/emaki/niri-emaki.kdl"));
            if matches!(node, "cursor" | "xcursor-theme") {
                paths.push(home.join(".config/niri/config.kdl"));
            }
        }
    }
    paths
        .into_iter()
        .any(|path| personal_file_node(&path, node, home.as_deref(), 0, true))
}

fn same_config_file(path: &Path, packaged: &Path) -> bool {
    path == packaged
        || std::fs::canonicalize(path)
            .ok()
            .zip(std::fs::canonicalize(packaged).ok())
            .is_some_and(|(path, packaged)| path == packaged)
}

fn personal_file_node(
    path: &Path,
    node: &str,
    home: Option<&Path>,
    depth: usize,
    optional: bool,
) -> bool {
    // Package includes define inherited defaults, not personal choices.
    if same_config_file(path, &data_dir().join("niri/default.kdl"))
        || same_config_file(path, &Path::new("/etc").join("emaki/niri.kdl"))
    {
        return false;
    }
    if depth > 16 {
        return true;
    }
    use std::os::unix::fs::OpenOptionsExt;
    match std::fs::metadata(path) {
        Ok(meta) if meta.is_file() && meta.len() <= 1_048_576 => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            return !optional || std::fs::symlink_metadata(path).is_ok();
        }
        _ => return true,
    }
    let mut raw = Vec::new();
    // Nonblocking open also covers a file replaced by a FIFO after metadata lookup.
    let file = match std::fs::OpenOptions::new()
        .read(true)
        .custom_flags(rustix::fs::OFlags::NONBLOCK.bits() as i32)
        .open(path)
    {
        Ok(file) => file,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            return !optional || std::fs::symlink_metadata(path).is_ok();
        }
        Err(_) => return true,
    };
    if !file.metadata().is_ok_and(|meta| meta.is_file()) {
        return true;
    }
    if file.take(1_048_577).read_to_end(&mut raw).is_err() || raw.len() > 1_048_576 {
        return true;
    }
    let Ok(text) = std::str::from_utf8(&raw) else {
        return true;
    };
    let Some(nodes) = personal_nodes(text) else {
        return true;
    };
    nodes.iter().any(|header| {
        if header[0] == node {
            return true;
        }
        if header[0] != "include" {
            return false;
        }
        let Some(name) = header.iter().skip(1).find(|part| !part.contains('=')) else {
            return true;
        };
        let included = if let Some(name) = name.strip_prefix("~/") {
            home.map(|home| home.join(name))
        } else {
            Some(path.parent().unwrap_or(Path::new("/")).join(name))
        };
        let optional = header.iter().any(|part| part == "optional=true");
        included
            .is_none_or(|included| personal_file_node(&included, node, home, depth + 1, optional))
    })
}

#[cfg(test)]
fn has_node(text: &str, wanted: &str) -> bool {
    personal_nodes(text).is_some_and(|nodes| nodes.iter().any(|header| header[0] == wanted))
}

// A bounded token scan is sufficient: retain node headers and skip strings,
// comments and slash-dash disabled subtrees before following personal includes.
fn personal_nodes(text: &str) -> Option<Vec<Vec<String>>> {
    let mut chars = text.chars().peekable();
    let mut nodes = Vec::new();
    let mut header = Vec::new();
    let mut disabled = false;
    let mut skipped_depth = 0usize;
    while let Some(c) = chars.next() {
        match c {
            '/' if chars.peek() == Some(&'/') => {
                chars.next();
                while chars.peek().is_some_and(|c| *c != '\n') {
                    chars.next();
                }
            }
            '/' if chars.peek() == Some(&'*') => {
                chars.next();
                let mut depth = 1;
                while depth != 0 {
                    let c = chars.next()?;
                    if c == '/' && chars.peek() == Some(&'*') {
                        chars.next();
                        depth += 1;
                    } else if c == '*' && chars.peek() == Some(&'/') {
                        chars.next();
                        depth -= 1;
                    }
                }
            }
            '/' if chars.peek() == Some(&'-') => {
                chars.next();
                disabled = true;
            }
            '\n' | ';' | '{' | '}' => {
                if !header.is_empty() && !disabled && skipped_depth == 0 {
                    nodes.push(std::mem::take(&mut header));
                }
                header.clear();
                if c == '{' && (disabled || skipped_depth > 0) {
                    skipped_depth += 1;
                }
                if c == '}' {
                    skipped_depth = skipped_depth.saturating_sub(1);
                }
                disabled = false;
            }
            c if c.is_whitespace() => {}
            '"' | '#' => {
                let mut hashes = usize::from(c == '#');
                if hashes > 0 {
                    while chars.peek() == Some(&'#') {
                        chars.next();
                        hashes += 1;
                    }
                    if chars.next()? != '"' {
                        return None;
                    }
                }
                let mut value = String::new();
                loop {
                    let c = chars.next()?;
                    if c == '"' {
                        let mut end = chars.clone();
                        if (0..hashes).all(|_| end.next() == Some('#')) {
                            chars = end;
                            break;
                        }
                    }
                    if c == '\\' && hashes == 0 {
                        let escaped = chars.next()?;
                        value.push(match escaped {
                            'n' => '\n',
                            't' => '\t',
                            'r' => '\r',
                            other => other,
                        });
                    } else {
                        value.push(c);
                    }
                }
                header.push(value);
            }
            'r' if matches!(chars.peek(), Some('#' | '"')) => {
                // KDL v1 raw strings use an r prefix. Reuse the raw-string scanner
                // by consuming the complete token without exposing its contents.
                let mut hashes = 0;
                while chars.peek() == Some(&'#') {
                    chars.next();
                    hashes += 1;
                }
                if chars.next()? != '"' {
                    return None;
                }
                let mut value = String::new();
                loop {
                    let c = chars.next()?;
                    if c == '"' {
                        let mut end = chars.clone();
                        if (0..hashes).all(|_| end.next() == Some('#')) {
                            chars = end;
                            break;
                        }
                    }
                    value.push(c);
                }
                header.push(value);
            }
            c => {
                let mut value = String::from(c);
                while chars.peek().is_some_and(|c| {
                    !c.is_whitespace() && !matches!(c, '{' | '}' | ';' | '/' | '"' | '#')
                }) {
                    value.push(chars.next().unwrap());
                }
                header.push(value);
            }
        }
    }
    if !header.is_empty() && !disabled && skipped_depth == 0 {
        nodes.push(header);
    }
    Some(nodes)
}

fn personal_wallpaper(profile: &storage::Profile) -> bool {
    personal_node(Some(profile), "emaki-wallpaper")
}

pub(super) fn wallpaper_row(
    profile: Option<&storage::Profile>,
    doc: &Document,
    rows: &mut [Setting],
) {
    if landscape(doc) {
        if let Some(row) = rows.iter_mut().find(|row| row.key == WALLPAPER) {
            row.value = json!(data_dir().join("wallpaper/ring.png"));
        }
    } else if doc.appearance.wallpaper.is_none() && inherited_wallpaper(profile).is_some() {
        if let Some(row) = rows.iter_mut().find(|row| row.key == WALLPAPER) {
            row.value = Value::Null;
            row.explanation = Some(
                "Wallpaper is controlled by your personal Emaki compositor configuration.".into(),
            );
        }
    }
}

/// Only choices made for Emaki override its default wallpaper. A wpaperd
/// configuration belongs to the underlying session and is not an Emaki choice.
pub(super) fn inherited_wallpaper(profile: Option<&storage::Profile>) -> Option<Option<String>> {
    let profile = profile?;
    personal_wallpaper(profile).then_some(None)
}

pub(super) fn landscape(doc: &Document) -> bool {
    ["wallpaper/ring.png", "wallpaper/fallback.png"]
        .iter()
        .any(|path| doc.appearance.wallpaper.as_deref() == data_dir().join(path).to_str())
}

pub(super) fn lane(profile: Option<&storage::Profile>, doc: &Document) -> Option<String> {
    match &doc.appearance.wallpaper {
        None if inherited_wallpaper(profile).is_some() => None,
        None => Some(defaults().wallpaper),
        Some(path)
            if catalog()
                .unwrap_or_default()
                .iter()
                .any(|s| &s.wallpaper == path) =>
        {
            Some(path.clone())
        }
        Some(_) => None,
    }
}

pub(super) fn rows(profile: &storage::Profile, doc: &Document) -> Vec<Style> {
    let defaults = defaults();
    let mut styles = catalog().unwrap_or_default();
    let inherited = inherited_wallpaper(Some(profile));
    let effective_style = doc.appearance.style.as_deref().or_else(|| {
        if inherited.is_none() {
            Some(defaults.id.as_str())
        } else {
            None
        }
    });
    for style in &mut styles {
        style.selected = effective_style == Some(style.id.as_str())
            && doc
                .appearance
                .wallpaper
                .as_deref()
                .unwrap_or(&defaults.wallpaper)
                == style.wallpaper
            && doc.appearance.accent.as_deref().unwrap_or(default_accent()) == style.accent;
    }
    styles
}

fn apply(doc: &mut Document, style: &Style) -> Result<()> {
    if !style.available {
        return Err(err("style_assets_missing"));
    }
    // Prepare the complete bundle first, so validation failures never partly edit a document.
    let mut desired = doc.clone();
    for (key, value) in [
        (STYLE, &style.id),
        (WALLPAPER, &style.wallpaper),
        (OVERVIEW_VIDEO, &style.overview_video),
        (OVERVIEW_STILL, &style.overview_still),
        (ACCENT, &style.accent),
    ] {
        desired.set(key, value)?;
    }
    *doc = desired;
    Ok(())
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct OverviewSelection {
    video: String,
    still: String,
}

pub(super) fn select_overview(doc: &mut Document, value: &str) -> Result<()> {
    let selection = if let Some(id) = value.strip_prefix("catalog:") {
        let style = catalog()?
            .into_iter()
            .find(|s| s.id == id)
            .ok_or_else(|| err("unknown_style"))?;
        OverviewSelection {
            video: if style.video_available {
                style.overview_video
            } else {
                String::new()
            },
            still: style.overview_still,
        }
    } else {
        serde_json::from_str(value).map_err(|_| err("invalid_overview_background"))?
    };
    for path in [&selection.video, &selection.still] {
        if !path.is_empty() {
            wallpaper(path)?;
            if !Path::new(path).is_file() {
                return Err(err("overview_background_missing"));
            }
        }
    }
    if selection.still.is_empty() {
        return Err(err("invalid_overview_background"));
    }
    let mut desired = doc.clone();
    desired.set(OVERVIEW_VIDEO, &selection.video)?;
    desired.set(OVERVIEW_STILL, &selection.still)?;
    *doc = desired;
    Ok(())
}

pub(super) fn reset(doc: &mut Document, key: &str) -> Result<()> {
    if key == OVERVIEW_BACKGROUND {
        if doc.appearance.style.is_none() {
            doc.clear(OVERVIEW_VIDEO)?;
            doc.clear(OVERVIEW_STILL)?;
            return Ok(());
        }
        let style = catalog()?
            .into_iter()
            .find(|s| doc.appearance.style.as_deref() == Some(&s.id))
            .unwrap_or_else(defaults);
        doc.set(
            OVERVIEW_VIDEO,
            if style.video_available {
                &style.overview_video
            } else {
                ""
            },
        )?;
        doc.set(OVERVIEW_STILL, &style.overview_still)?;
        return Ok(());
    }

    // Only remove the accent when it still belongs to the selected bundle.
    let bundled_accent = key == STYLE
        && catalog().unwrap_or_default().iter().any(|style| {
            doc.appearance.style.as_deref() == Some(&style.id)
                && doc.appearance.accent.as_deref() == Some(&style.accent)
        });
    doc.clear(key)?;
    if key == STYLE {
        if bundled_accent {
            doc.clear(ACCENT)?;
        }
        for field in [WALLPAPER, OVERVIEW_VIDEO, OVERVIEW_STILL] {
            doc.clear(field)?;
        }
    }
    Ok(())
}

pub(super) fn select(doc: &mut Document, id: &str) -> Result<()> {
    let style = catalog()?
        .into_iter()
        .find(|s| s.id == id)
        .ok_or_else(|| err("unknown_style"))?;
    apply(doc, &style)
}

pub(super) fn fragment(profile: Option<&storage::Profile>, doc: &Document, text: &mut String) {
    if landscape(doc) {
        let root = data_dir().join("wallpaper");
        let ring = root.join("ring.png").to_string_lossy().into_owned();
        let front = root.join("ring-front.png").to_string_lossy().into_owned();
        text.push_str(&format!("emaki-wallpaper {{ mode \"ring\"; ring \"{ring}\"; front \"{front}\"; trains \"on\"; }}\n"));
    } else if let Some(path) = lane(profile, doc) {
        text.push_str(&format!(
            "emaki-wallpaper {{ mode \"strip\"; ring \"{path}\"; front \"\"; trains \"off\"; }}\n"
        ));
    }
    let defaults = defaults();
    let video = doc
        .appearance
        .overview_video
        .as_deref()
        .unwrap_or(&defaults.overview_video);
    let still = doc
        .appearance
        .overview_still
        .as_deref()
        .unwrap_or(&defaults.overview_still);
    if Path::new(video).is_file() || Path::new(still).is_file() {
        text.push_str("overview { emaki-backdrop { off; }; }\n");
    }
    text.push_str(
        "layer-rule { match namespace=\"^emaki-overview-video$\"; place-within-backdrop true; }\n",
    );
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn package_defaults_are_recognized_through_symlinks() {
        let root = std::env::temp_dir().join(format!("emaki-personal-default-{}", storage::id()));
        storage::mkdir(&root).unwrap();
        let packaged = root.join("default.kdl");
        let personal = root.join("config.kdl");
        std::fs::write(&packaged, "cursor { xcursor-theme \"Emaki-White\"; }\n").unwrap();
        std::os::unix::fs::symlink(&packaged, &personal).unwrap();
        assert!(same_config_file(&personal, &packaged));
        assert!(!same_config_file(&personal, &root.join("missing.kdl")));
        std::fs::remove_file(&packaged).unwrap();
        assert!(!same_config_file(&personal, &packaged));
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn personal_nodes_ignore_comments_and_property_strings() {
        assert!(!has_node(
            "// cursor { xcursor-theme \"Other\"; }\n",
            "xcursor-theme"
        ));
        assert!(!has_node("/* emaki-wallpaper { } */", "emaki-wallpaper"));
        assert!(!has_node(
            "spawn-at-startup \"xcursor-theme\"",
            "xcursor-theme"
        ));
        assert!(!has_node(
            "/- emaki-wallpaper { mode \"ring\"; }",
            "emaki-wallpaper"
        ));
        assert!(has_node(
            "cursor { xcursor-theme \"Other\"; }",
            "xcursor-theme"
        ));
        assert!(has_node(
            "\"emaki-wallpaper\" { mode \"ring\"; }",
            "emaki-wallpaper"
        ));
    }

    #[test]
    fn personal_nodes_skip_disabled_subtrees_raw_strings_and_commented_includes() {
        assert!(!has_node(
            r#"/- cursor { xcursor-theme "Other"; }"#,
            "xcursor-theme"
        ));
        assert!(!has_node("/* include \"personal.kdl\" */", "include"));
        assert!(!has_node(
            "spawn-at-startup r#\"line\nemaki-wallpaper {}\"#",
            "emaki-wallpaper"
        ));
        assert!(!has_node(
            "spawn-at-startup #\"line\nemaki-wallpaper {}\"#",
            "emaki-wallpaper"
        ));
        assert!(has_node(
            "/- cursor { xcursor-theme \"Other\"; }\ncursor { xcursor-theme \"Chosen\"; }",
            "xcursor-theme"
        ));
    }

    #[test]
    fn stored_legacy_landscape_keeps_ring_and_reports_landscape_path() {
        let mut doc = Document::default();
        let legacy = data_dir().join("wallpaper/fallback.png");
        doc.set(WALLPAPER, legacy.to_str().unwrap()).unwrap();
        assert!(landscape(&doc));
        let mut text = String::new();
        fragment(None, &doc, &mut text);
        assert!(text.contains("mode \"ring\""));
        assert!(text.contains("trains \"on\""));
        assert!(text.contains("wallpaper/ring.png"));
        assert_eq!(doc.appearance.wallpaper.as_deref(), legacy.to_str());
        let rows = super::super::rows(None, &doc, 8);
        assert_eq!(
            rows.iter().find(|row| row.key == WALLPAPER).unwrap().value,
            json!(data_dir().join("wallpaper/ring.png"))
        );
    }

    #[test]
    fn builtin_catalog_is_complete_and_paths_are_configurable() {
        let styles = parse(BUILTIN, Path::new("/nonexistent/themes")).unwrap();
        assert_eq!(styles.len(), 19);
        assert_eq!(styles[0].id, "emaki");
        assert!(
            styles
                .iter()
                .all(|s| !s.available && s.wallpaper.starts_with("/nonexistent/themes/"))
        );
        assert!(parse(BUILTIN, Path::new("relative")).is_err());
        assert!(resolve(Path::new("/tmp"), "../outside").is_err());
        assert!(resolve(Path::new("/tmp"), "/outside").is_err());
    }

    #[test]
    fn one_history_entry_restores_entire_bundle_and_detects_conflicts() {
        let root = std::env::temp_dir().join(format!("emaki-style-{}", storage::id()));
        storage::mkdir(&root).unwrap();
        let profile = storage::Profile {
            config: root.join("config"),
            state: root.join("state"),
            runtime: root.join("runtime"),
            installed: false,
        };
        let mut journal = journal::Journal::load(&profile).unwrap();
        let before = Document::default();
        let mut after = before.clone();
        let mut style = parse(BUILTIN, &root).unwrap().remove(0);
        style.available = true;
        apply(&mut after, &style).unwrap();
        let entry = journal.make(&after, journal::Kind::Set, None, None);
        assert_eq!(entry.changes.len(), 5);
        journal.append(&profile, &entry).unwrap();
        let (restored, conflicts) = journal.undo(&entry.id, &after).unwrap();
        assert!(conflicts.is_empty());
        assert_eq!(restored, before);
        after.set(ACCENT, "blue").unwrap();
        journal.observe(&profile, &after).unwrap();
        let (_, conflicts) = journal.undo(&entry.id, &after).unwrap();
        assert_eq!(conflicts.len(), 1);
        assert_eq!(conflicts[0].key, ACCENT);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn reset_style_clears_whole_bundle_and_reset_wallpaper_preserves_theme() {
        let mut style = parse(BUILTIN, Path::new("/tmp/themes")).unwrap().remove(0);
        style.available = true;
        let mut doc = Document::default();
        doc.set(THEME, "automatic").unwrap();
        doc.set(CURSOR, "dark").unwrap();
        let before = doc.clone();
        apply(&mut doc, &style).unwrap();
        reset(&mut doc, STYLE).unwrap();
        assert_eq!(doc, before);
        apply(&mut doc, &style).unwrap();
        reset(&mut doc, WALLPAPER).unwrap();
        assert!(doc.appearance.wallpaper.is_none());
        assert_eq!(doc.appearance.style.as_deref(), Some(style.id.as_str()));
        assert_eq!(
            doc.appearance.overview_video.as_deref(),
            Some(style.overview_video.as_str())
        );
        assert_eq!(
            doc.appearance.overview_still.as_deref(),
            Some(style.overview_still.as_str())
        );
        assert_eq!(doc.appearance.accent.as_deref(), Some("rose"));
        assert_eq!(doc.appearance.theme.as_deref(), Some("automatic"));
        assert_eq!(doc.appearance.cursor.as_deref(), Some("dark"));
    }

    #[test]
    fn landscape_restores_foreground_and_trains_without_changing_theme() {
        let root = data_dir().join("themes");
        for mut style in parse(BUILTIN, &root).unwrap().into_iter().take(2) {
            style.available = true;
            let mut doc = Document::default();
            apply(&mut doc, &style).unwrap();
            let before = doc.overrides();
            let ring = data_dir().join("wallpaper/ring.png");
            let front = data_dir().join("wallpaper/ring-front.png");
            doc.set(WALLPAPER, ring.to_str().unwrap()).unwrap();
            for (key, value) in doc.overrides() {
                if key != WALLPAPER {
                    assert_eq!(value, before[&key], "{key}");
                }
            }
            let mut runtime = String::new();
            fragment(None, &doc, &mut runtime);
            assert!(runtime.contains("mode \"ring\""));
            assert!(runtime.contains(&format!("ring \"{}\"", ring.display())));
            assert!(runtime.contains(&format!("front \"{}\"", front.display())));
            assert!(runtime.contains("trains \"on\""));
            assert!(!runtime.contains("mode \"strip\""));
            assert!(!runtime.contains("trains \"off\""));
        }
    }

    #[test]
    fn bundle_is_complete_roundtrips_and_preserves_appearance_policy() {
        let mut doc = Document::default();
        doc.set(THEME, "automatic").unwrap();
        doc.set(CURSOR, "dark").unwrap();
        let mut style = defaults();
        style.available = false;
        let original = doc.clone();
        assert_eq!(
            apply(&mut doc, &style).unwrap_err().reason,
            "style_assets_missing"
        );
        assert_eq!(doc, original);
        style.available = true;
        apply(&mut doc, &style).unwrap();
        assert_eq!(doc.appearance.theme.as_deref(), Some("automatic"));
        assert_eq!(doc.appearance.cursor.as_deref(), Some("dark"));
        assert_eq!(doc.appearance.style.as_deref(), Some("emaki"));
        assert_eq!(
            doc.appearance.overview_video.as_deref(),
            Some(style.overview_video.as_str())
        );
        assert_eq!(Document::parse(Some(doc.text().as_bytes())).unwrap(), doc);
        let files = doc.files(8);
        let niri = &files
            .iter()
            .find(|(name, _)| *name == "niri.kdl")
            .unwrap()
            .1;
        assert!(!niri.contains("emaki-wallpaper"));
        assert!(!niri.contains("emaki-backdrop"));
        let mut runtime = String::new();
        fragment(None, &doc, &mut runtime);
        assert!(runtime.contains("mode \"strip\""));
        assert!(runtime.contains("place-within-backdrop true"));
        assert_eq!(
            runtime.contains("emaki-backdrop { off; }"),
            Path::new(&style.overview_still).is_file()
                || Path::new(&style.overview_video).is_file()
        );
    }
}
