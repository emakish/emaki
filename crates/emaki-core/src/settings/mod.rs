//! Typed, isolated-profile settings. Generated files are never attached to a live session.
mod journal;
mod storage;

use std::collections::{BTreeMap, BTreeSet};
use std::sync::OnceLock;

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::path::{Path, PathBuf};
use std::time::Duration;

const TOKENS: &str = include_str!("../../../../tokens.toml");
const NIRI_DEFAULT: &str = include_str!("../../../../niri/default.kdl");
const NIRI_THEME: &str = include_str!("../../../../niri/theme.kdl");
// default.kdl includes shell.kdl (binds and layer rules of the shell) since 2026-09-24.
const NIRI_SHELL: &str = include_str!("../../../../niri/shell.kdl");
const GAPS: &str = "appearance.gaps";
const WALLPAPER: &str = "appearance.wallpaper";
const FLOATING: &str = "keybindings.toggle_window_floating";
const LAYOUTS: &str = "keyboard.layouts";
const SWITCH_KEY: &str = "keyboard.switch_key";
const TERMINAL: &str = "defaults.terminal";
const BROWSER: &str = "defaults.browser";
const FILES: &str = "defaults.files";
const BAR_AUTOHIDE: &str = "bar.autohide";
const BAR_OVERVIEW: &str = "bar.overview_workspaces";
const DOCK_ON: &str = "dock.on";
const DOCK_AUTO_HIDE: &str = "dock.auto_hide";
/// Labels of the mockup segment "Switch layouts with"; Super+Space is the packaged
/// `Mod+Space { switch-layout "next"; }` bind, the other two are XKB group options.
const SWITCH_KEYS: [(&str, Option<&str>); 3] = [
    ("Super+Space", None),
    ("Alt+Shift", Some("grp:alt_shift_toggle")),
    ("Caps Lock", Some("grp:caps_toggle")),
];
/// Layout names are checked against the installed XKB rules list, because
/// `niri validate` accepts any string there (verified with niri 26.04).
const XKB_RULES: &str = match option_env!("EMAKI_XKB_RULES") {
    Some(path) => path,
    None => "/usr/share/X11/xkb/rules/evdev.lst",
};

#[derive(Clone, Copy)]
struct Field {
    key: &'static str,
    secret: bool,
}
const SCHEMA: &[Field] = &[
    Field {
        key: GAPS,
        secret: false,
    },
    Field {
        key: WALLPAPER,
        secret: false,
    },
    Field {
        key: FLOATING,
        secret: false,
    },
    Field {
        key: LAYOUTS,
        secret: false,
    },
    Field {
        key: SWITCH_KEY,
        secret: false,
    },
    Field {
        key: TERMINAL,
        secret: false,
    },
    Field {
        key: BROWSER,
        secret: false,
    },
    Field {
        key: FILES,
        secret: false,
    },
    Field {
        key: BAR_AUTOHIDE,
        secret: false,
    },
    Field {
        key: BAR_OVERVIEW,
        secret: false,
    },
    Field {
        key: DOCK_ON,
        secret: false,
    },
    Field {
        key: DOCK_AUTO_HIDE,
        secret: false,
    },
];
// Fail closed: an unregistered future field is never journalled.
fn recordable(key: &str, schema: &[Field]) -> bool {
    schema.iter().any(|field| field.key == key && !field.secret)
}

#[derive(Clone, Debug, PartialEq, Eq, Default, Deserialize)]
#[serde(deny_unknown_fields)]
struct Appearance {
    gaps: Option<u16>,
    wallpaper: Option<String>,
}
#[derive(Clone, Debug, PartialEq, Eq, Default, Deserialize)]
#[serde(deny_unknown_fields)]
struct Keybindings {
    toggle_window_floating: Option<String>,
}
#[derive(Clone, Debug, PartialEq, Eq, Default, Deserialize)]
#[serde(deny_unknown_fields)]
struct Keyboard {
    layouts: Option<Vec<String>>,
    switch_key: Option<String>,
}
#[derive(Clone, Debug, PartialEq, Eq, Default, Deserialize)]
#[serde(deny_unknown_fields)]
struct Defaults {
    terminal: Option<String>,
    browser: Option<String>,
    files: Option<String>,
}
#[derive(Clone, Debug, PartialEq, Eq, Default, Deserialize)]
#[serde(deny_unknown_fields)]
struct Bar {
    autohide: Option<bool>,
    overview_workspaces: Option<bool>,
}
#[derive(Clone, Debug, PartialEq, Eq, Default, Deserialize)]
#[serde(deny_unknown_fields)]
struct Dock {
    on: Option<bool>,
    // Accepted from older files and dropped on write: the dock is bottom-only (2026-09-24).
    #[allow(dead_code)]
    position: Option<String>,
    auto_hide: Option<bool>,
}
#[derive(Clone, Debug, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
struct Document {
    schema_version: u32,
    generation: Option<String>,
    #[serde(default)]
    appearance: Appearance,
    #[serde(default)]
    keybindings: Keybindings,
    #[serde(default)]
    keyboard: Keyboard,
    #[serde(default)]
    defaults: Defaults,
    #[serde(default)]
    bar: Bar,
    #[serde(default)]
    dock: Dock,
}
impl Default for Document {
    fn default() -> Self {
        Self {
            schema_version: 1,
            generation: None,
            appearance: Appearance::default(),
            keybindings: Keybindings::default(),
            keyboard: Keyboard::default(),
            defaults: Defaults::default(),
            bar: Bar::default(),
            dock: Dock::default(),
        }
    }
}

#[derive(Clone, Copy, Debug)]
struct Error {
    reason: &'static str,
}
type Result<T> = std::result::Result<T, Error>;
fn err(reason: &'static str) -> Error {
    Error { reason }
}

fn chord(value: &str) -> Result<String> {
    if value.len() > 80 || !value.is_ascii() {
        return Err(err("invalid_keybinding"));
    }
    let parts: Vec<_> = value.split('+').collect();
    let (key, modifiers) = parts
        .split_last()
        .ok_or_else(|| err("invalid_keybinding"))?;
    if !modifiers.contains(&"Mod")
        || key.is_empty()
        || !key.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_')
    {
        return Err(err("invalid_keybinding"));
    }
    let allowed = ["Mod", "Ctrl", "Alt", "Shift"];
    if modifiers.iter().any(|m| !allowed.contains(m))
        || allowed
            .iter()
            .any(|m| modifiers.iter().filter(|v| *v == m).count() > 1)
    {
        return Err(err("invalid_keybinding"));
    }
    let mut result: Vec<_> = allowed
        .into_iter()
        .filter(|m| modifiers.contains(m))
        .collect();
    result.push(key);
    Ok(result.join("+"))
}

/// Layout codes of the `! layout` section of the installed XKB rules list.
fn xkb_layouts() -> Result<&'static BTreeSet<String>> {
    static KNOWN: OnceLock<Option<BTreeSet<String>>> = OnceLock::new();
    KNOWN
        .get_or_init(|| {
            let text = std::fs::read(XKB_RULES).ok()?;
            if text.len() > 1024 * 1024 {
                return None;
            }
            let text = String::from_utf8_lossy(&text);
            let mut section = false;
            let mut codes = BTreeSet::new();
            for line in text.lines() {
                if line.starts_with('!') {
                    section = line.trim() == "! layout";
                } else if section && let Some(code) = line.split_whitespace().next() {
                    codes.insert(code.to_owned());
                }
            }
            (!codes.is_empty()).then_some(codes)
        })
        .as_ref()
        .ok_or_else(|| err("xkb_rules_missing"))
}
fn layouts(value: &str) -> Result<Vec<String>> {
    let codes: Vec<String> = value.split(',').map(str::to_owned).collect();
    if codes.is_empty()
        || codes.len() > 4
        || codes
            .iter()
            .any(|c| c.len() < 2 || c.len() > 8 || !c.bytes().all(|b| b.is_ascii_lowercase()))
        || codes.iter().collect::<BTreeSet<_>>().len() != codes.len()
    {
        return Err(err("invalid_layouts"));
    }
    let known = xkb_layouts()?;
    if codes.iter().any(|c| !known.contains(c)) {
        return Err(err("unknown_layout"));
    }
    Ok(codes)
}
fn switch_key(value: &str) -> Result<String> {
    SWITCH_KEYS
        .iter()
        .find(|(label, _)| *label == value)
        .map(|(label, _)| (*label).to_owned())
        .ok_or_else(|| err("invalid_switch_key"))
}
// The path is written into TOML and read back by the wallpaper adapter; quotes,
// backslashes and control characters are refused rather than escaped.
fn wallpaper(value: &str) -> Result<String> {
    if !value.starts_with('/')
        || value.len() > 4096
        || value
            .chars()
            .any(|c| c.is_control() || c == '"' || c == '\\')
    {
        return Err(err("invalid_wallpaper"));
    }
    Ok(value.to_owned())
}
fn desktop_id(value: &str) -> Result<String> {
    let stem = value
        .strip_suffix(".desktop")
        .ok_or_else(|| err("invalid_desktop_id"))?;
    if stem.is_empty()
        || value.len() > 255
        || stem.starts_with('.')
        || !stem
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'.' || b == b'_' || b == b'-')
    {
        return Err(err("invalid_desktop_id"));
    }
    Ok(value.to_owned())
}
fn boolean(value: &str) -> Result<bool> {
    match value {
        "true" => Ok(true),
        "false" => Ok(false),
        _ => Err(err("invalid_boolean")),
    }
}
impl Document {
    fn parse(bytes: Option<&[u8]>) -> Result<Self> {
        let mut doc = match bytes {
            Some(bytes) => {
                toml::from_str(std::str::from_utf8(bytes).map_err(|_| err("invalid_settings"))?)
                    .map_err(|_| err("invalid_settings"))?
            }
            None => Self::default(),
        };
        if doc.schema_version != 1 {
            return Err(err("unsupported_settings_schema"));
        }
        if doc
            .generation
            .as_deref()
            .is_some_and(|g| !journal::valid_id(g))
        {
            return Err(err("invalid_generation_reference"));
        }
        if doc.keyboard.layouts.as_ref().is_some_and(Vec::is_empty) {
            doc.keyboard.layouts = None;
        }
        // Re-validate every manual value through the same checks as `set`.
        let overrides = doc.overrides();
        for (key, value) in &overrides {
            doc.restore(key, value)?;
        }
        Ok(doc)
    }
    fn overrides(&self) -> BTreeMap<String, Value> {
        BTreeMap::from([
            (GAPS.into(), json!(self.appearance.gaps)),
            (WALLPAPER.into(), json!(self.appearance.wallpaper)),
            (
                FLOATING.into(),
                json!(self.keybindings.toggle_window_floating),
            ),
            (LAYOUTS.into(), json!(self.keyboard.layouts)),
            (SWITCH_KEY.into(), json!(self.keyboard.switch_key)),
            (TERMINAL.into(), json!(self.defaults.terminal)),
            (BROWSER.into(), json!(self.defaults.browser)),
            (FILES.into(), json!(self.defaults.files)),
            (BAR_AUTOHIDE.into(), json!(self.bar.autohide)),
            (BAR_OVERVIEW.into(), json!(self.bar.overview_workspaces)),
            (DOCK_ON.into(), json!(self.dock.on)),
            (DOCK_AUTO_HIDE.into(), json!(self.dock.auto_hide)),
        ])
    }
    fn restore(&mut self, key: &str, value: &Value) -> Result<()> {
        let text = match (key, value) {
            (_, Value::Null) => {
                self.clear(key)?;
                return Ok(());
            }
            (GAPS, Value::Number(n)) if n.as_u64().is_some() => n.to_string(),
            (LAYOUTS, Value::Array(items)) => {
                let codes: Option<Vec<&str>> = items.iter().map(Value::as_str).collect();
                codes
                    .filter(|c| !c.is_empty())
                    .ok_or_else(|| err("history_corrupt"))?
                    .join(",")
            }
            (BAR_AUTOHIDE | BAR_OVERVIEW | DOCK_ON | DOCK_AUTO_HIDE, Value::Bool(b)) => {
                b.to_string()
            }
            (FLOATING | WALLPAPER | SWITCH_KEY | TERMINAL | BROWSER | FILES, Value::String(s)) => {
                s.clone()
            }
            _ => return Err(err("history_corrupt")),
        };
        self.set(key, &text)
    }
    fn clear(&mut self, key: &str) -> Result<()> {
        match key {
            GAPS => self.appearance.gaps = None,
            WALLPAPER => self.appearance.wallpaper = None,
            FLOATING => self.keybindings.toggle_window_floating = None,
            LAYOUTS => self.keyboard.layouts = None,
            SWITCH_KEY => self.keyboard.switch_key = None,
            TERMINAL => self.defaults.terminal = None,
            BROWSER => self.defaults.browser = None,
            FILES => self.defaults.files = None,
            BAR_AUTOHIDE => self.bar.autohide = None,
            BAR_OVERVIEW => self.bar.overview_workspaces = None,
            DOCK_ON => self.dock.on = None,
            DOCK_AUTO_HIDE => self.dock.auto_hide = None,
            _ => return Err(err("unknown_setting")),
        }
        Ok(())
    }
    // An empty value removes the override of the keys added for the Settings pages
    // (the two original keys keep their contract: empty is invalid).
    fn set(&mut self, key: &str, value: &str) -> Result<()> {
        if value.is_empty() && !matches!(key, GAPS | FLOATING) {
            return self.clear(key);
        }
        match key {
            GAPS => {
                if value.is_empty() || !value.bytes().all(|b| b.is_ascii_digit()) {
                    return Err(err("invalid_gaps"));
                }
                let value: u16 = value.parse().map_err(|_| err("invalid_gaps"))?;
                if value > 64 {
                    return Err(err("invalid_gaps"));
                }
                self.appearance.gaps = Some(value);
            }
            WALLPAPER => self.appearance.wallpaper = Some(wallpaper(value)?),
            FLOATING => self.keybindings.toggle_window_floating = Some(chord(value)?),
            LAYOUTS => self.keyboard.layouts = Some(layouts(value)?),
            SWITCH_KEY => self.keyboard.switch_key = Some(switch_key(value)?),
            TERMINAL => self.defaults.terminal = Some(desktop_id(value)?),
            BROWSER => self.defaults.browser = Some(desktop_id(value)?),
            FILES => self.defaults.files = Some(desktop_id(value)?),
            BAR_AUTOHIDE => self.bar.autohide = Some(boolean(value)?),
            BAR_OVERVIEW => self.bar.overview_workspaces = Some(boolean(value)?),
            DOCK_ON => self.dock.on = Some(boolean(value)?),
            DOCK_AUTO_HIDE => self.dock.auto_hide = Some(boolean(value)?),
            _ => return Err(err("unknown_setting")),
        }
        Ok(())
    }
    // All interpolated values are typed or restricted ASCII/UTF-8 without quotes.
    // No raw TOML, comments, action strings, command lines or unknown settings enter a generation.
    fn text(&self) -> String {
        let mut text = "schema_version = 1\n".to_owned();
        if let Some(generation) = &self.generation {
            text.push_str(&format!("generation = \"{generation}\"\n"));
        }
        let mut section = |name: &str, lines: Vec<Option<String>>| {
            let lines: Vec<_> = lines.into_iter().flatten().collect();
            if !lines.is_empty() {
                text.push_str(&format!("\n[{name}]\n{}\n", lines.join("\n")));
            }
        };
        let string = |name: &str, value: &Option<String>| {
            value.as_ref().map(|v| format!("{name} = \"{v}\""))
        };
        let flag = |name: &str, value: &Option<bool>| value.map(|v| format!("{name} = {v}"));
        section(
            "appearance",
            vec![
                self.appearance.gaps.map(|g| format!("gaps = {g}")),
                string("wallpaper", &self.appearance.wallpaper),
            ],
        );
        section(
            "keybindings",
            vec![string(
                "toggle_window_floating",
                &self.keybindings.toggle_window_floating,
            )],
        );
        section(
            "keyboard",
            vec![
                self.keyboard.layouts.as_ref().map(|codes| {
                    let quoted: Vec<_> = codes.iter().map(|c| format!("\"{c}\"")).collect();
                    format!("layouts = [{}]", quoted.join(", "))
                }),
                string("switch_key", &self.keyboard.switch_key),
            ],
        );
        section(
            "defaults",
            vec![
                string("terminal", &self.defaults.terminal),
                string("browser", &self.defaults.browser),
                string("files", &self.defaults.files),
            ],
        );
        section(
            "bar",
            vec![
                flag("autohide", &self.bar.autohide),
                flag("overview_workspaces", &self.bar.overview_workspaces),
            ],
        );
        section(
            "dock",
            vec![
                flag("on", &self.dock.on),
                flag("auto_hide", &self.dock.auto_hide),
            ],
        );
        text
    }
    fn xkb_options(&self) -> Option<&'static str> {
        let label = self.keyboard.switch_key.as_deref()?;
        SWITCH_KEYS
            .iter()
            .find(|(l, _)| *l == label)
            .and_then(|(_, option)| *option)
    }
    fn files(&self, default_gaps: u16) -> Vec<(&'static str, String)> {
        let mut fragment = format!(
            "// Generated by Emaki. Edit settings.toml, not this file.\nlayout {{\n    gaps {}\n}}\n",
            self.appearance.gaps.unwrap_or(default_gaps)
        );
        if let Some(key) = &self.keybindings.toggle_window_floating {
            fragment.push_str(&format!(
                "binds {{\n    {key} {{ toggle-window-floating; }}\n}}\n"
            ));
        }
        // `input` sections of separate includes merge in niri 26.04 (validated),
        // so the packaged input block stays untouched.
        let options = self.xkb_options();
        if self.keyboard.layouts.is_some() || options.is_some() {
            fragment.push_str("input {\n    keyboard {\n        xkb {\n");
            if let Some(codes) = &self.keyboard.layouts {
                fragment.push_str(&format!("            layout \"{}\"\n", codes.join(",")));
            }
            if let Some(option) = options {
                fragment.push_str(&format!("            options \"{option}\"\n"));
            }
            fragment.push_str("        }\n    }\n}\n");
        }
        let mut files = vec![
            ("niri.kdl", fragment),
            (
                "config.kdl",
                "include \"package/default.kdl\"\ninclude \"niri.kdl\"\n".into(),
            ),
            ("package/default.kdl", NIRI_DEFAULT.into()),
            ("package/theme.kdl", NIRI_THEME.into()),
            ("package/shell.kdl", NIRI_SHELL.into()),
        ];
        if let Some(path) = &self.appearance.wallpaper {
            files.push((
                "wpaperd.toml",
                format!("# Generated by Emaki. Edit settings.toml, not this file.\n[default]\npath = \"{path}\"\n"),
            ));
        }
        if self.defaults.browser.is_some() || self.defaults.files.is_some() {
            let mut list = "[Default Applications]\n".to_owned();
            if let Some(id) = &self.defaults.browser {
                for mime in [
                    "x-scheme-handler/http",
                    "x-scheme-handler/https",
                    "text/html",
                ] {
                    list.push_str(&format!("{mime}={id}\n"));
                }
            }
            if let Some(id) = &self.defaults.files {
                list.push_str(&format!("inode/directory={id}\n"));
            }
            files.push(("mimeapps.list", list));
        }
        if let Some(id) = &self.defaults.terminal {
            files.push(("xdg-terminals.list", format!("{id}\n")));
        }
        files.push(("settings.toml", self.text()));
        files
    }
}
/// Names a generation may contain besides `package/`; cleanup refuses anything else.
pub(crate) const GENERATION_FILES: [&str; 7] = [
    "niri.kdl",
    "config.kdl",
    "settings.toml",
    "wpaperd.toml",
    "mimeapps.list",
    "xdg-terminals.list",
    "package",
];

fn default_gaps() -> Result<u16> {
    let tokens: toml::Table =
        toml::from_str(TOKENS).map_err(|_| err("invalid_package_defaults"))?;
    let value = tokens
        .get("geometry")
        .and_then(|v| v.get("gaps"))
        .and_then(toml::Value::as_integer)
        .ok_or_else(|| err("invalid_package_defaults"))?;
    let value = u16::try_from(value).map_err(|_| err("invalid_package_defaults"))?;
    if value > 64 {
        return Err(err("invalid_package_defaults"));
    }
    Ok(value)
}

pub enum Operation<'a> {
    List,
    History,
    Undo { id: &'a str },
    Get { key: &'a str },
    Set { key: &'a str, value: &'a str },
}
#[derive(Serialize)]
pub struct Setting {
    pub key: &'static str,
    pub secret: bool,
    pub value: Value,
    pub override_value: Value,
    pub default: Value,
    pub source: &'static str,
    pub value_type: &'static str,
    pub allowed: &'static str,
    pub application: &'static str,
    pub validation: &'static str,
    pub undo: &'static str,
}
struct Description {
    key: &'static str,
    default: Value,
    default_source: &'static str,
    value_type: &'static str,
    allowed: &'static str,
    application: &'static str,
    validation: &'static str,
}
const UNDO: &str =
    "settings undo ID: restore previous override/unset, only if no later change of this key";
const SHELL_ONLY: &str = "settings.toml only: read by the shell on start; no live reload";
fn rows(doc: &Document, gaps: u16) -> Vec<Setting> {
    let overrides = doc.overrides();
    let descriptions = [
        Description {
            key: GAPS,
            default: json!(gaps),
            default_source: "embedded:tokens.toml#geometry.gaps",
            value_type: "integer",
            allowed: "0..64 logical pixels",
            application: "generation/niri.kdl: layout.gaps; no live reload",
            validation: "range; niri validate fragment and combined package defaults",
        },
        Description {
            key: WALLPAPER,
            default: Value::Null,
            default_source: "wpaperd config of the session (unset)",
            value_type: "path",
            allowed: "absolute path to an existing regular image file; <=4096 bytes, no quotes/backslashes/control characters; empty unsets",
            application: "generation/wpaperd.toml: [default] path; wallpaper of the live session is not changed",
            validation: "syntax; file exists at set time",
        },
        Description {
            key: FLOATING,
            default: Value::Null,
            default_source: "embedded:niri/default.kdl (unset)",
            value_type: "key_chord",
            allowed: "Mod + optional Ctrl/Alt/Shift + one XKB keysym; <=80 ASCII bytes",
            application: "generation/niri.kdl: bind -> toggle-window-floating; no live reload",
            validation: "restricted syntax; niri keysym validation; package bindings may be overridden",
        },
        Description {
            key: LAYOUTS,
            default: Value::Null,
            default_source: "embedded:niri/default.kdl (unset: xkb default of the session)",
            value_type: "string_list",
            allowed: "1..4 distinct XKB layout codes, comma-separated (us,ru); empty unsets",
            application: "generation/niri.kdl: input.keyboard.xkb.layout; no live reload",
            validation: "codes listed in the installed XKB rules; niri validate fragment and combined",
        },
        Description {
            key: SWITCH_KEY,
            default: json!("Super+Space"),
            default_source: "embedded:niri/default.kdl (Mod+Space switch-layout)",
            value_type: "choice",
            allowed: "Super+Space | Alt+Shift | Caps Lock; empty unsets",
            application: "generation/niri.kdl: input.keyboard.xkb.options (Super+Space keeps only the packaged bind); no live reload",
            validation: "choice; niri validate fragment and combined",
        },
        Description {
            key: TERMINAL,
            default: Value::Null,
            default_source: "shell terminal adapter (unset)",
            value_type: "desktop_id",
            allowed: "desktop entry id ending in .desktop; empty unsets",
            application: "generation/xdg-terminals.list; not applied to the session",
            validation: "id syntax only; installation is checked by the shell",
        },
        Description {
            key: BROWSER,
            default: Value::Null,
            default_source: "GIO default handler of the session (unset)",
            value_type: "desktop_id",
            allowed: "desktop entry id ending in .desktop; empty unsets",
            application: "generation/mimeapps.list: x-scheme-handler/http, https, text/html; not applied to the session",
            validation: "id syntax only; installation is checked by the shell",
        },
        Description {
            key: FILES,
            default: Value::Null,
            default_source: "GIO default handler of the session (unset)",
            value_type: "desktop_id",
            allowed: "desktop entry id ending in .desktop; empty unsets",
            application: "generation/mimeapps.list: inode/directory; not applied to the session",
            validation: "id syntax only; installation is checked by the shell",
        },
        Description {
            key: BAR_AUTOHIDE,
            default: json!(false),
            default_source: "shell default (unset)",
            value_type: "boolean",
            allowed: "true | false; empty unsets",
            application: SHELL_ONLY,
            validation: "boolean",
        },
        Description {
            key: BAR_OVERVIEW,
            default: json!(true),
            default_source: "shell default (unset)",
            value_type: "boolean",
            allowed: "true | false; empty unsets",
            application: SHELL_ONLY,
            validation: "boolean",
        },
        Description {
            key: DOCK_ON,
            default: json!(true),
            default_source: "shell default (unset)",
            value_type: "boolean",
            allowed: "true | false; empty unsets",
            application: SHELL_ONLY,
            validation: "boolean",
        },
        Description {
            key: DOCK_AUTO_HIDE,
            default: json!(true),
            default_source: "shell default (unset)",
            value_type: "boolean",
            allowed: "true | false; empty unsets",
            application: SHELL_ONLY,
            validation: "boolean",
        },
    ];
    descriptions
        .into_iter()
        .map(|d| {
            let override_value = overrides.get(d.key).cloned().unwrap_or(Value::Null);
            let secret = !recordable(d.key, SCHEMA);
            let set = !override_value.is_null();
            Setting {
                key: d.key,
                secret,
                value: if secret {
                    Value::Null
                } else if set {
                    override_value.clone()
                } else {
                    d.default.clone()
                },
                override_value: if secret { Value::Null } else { override_value },
                default: if secret { Value::Null } else { d.default },
                source: if set {
                    "settings.toml"
                } else {
                    d.default_source
                },
                value_type: d.value_type,
                allowed: d.allowed,
                application: d.application,
                validation: d.validation,
                undo: UNDO,
            }
        })
        .collect()
}

#[derive(Serialize)]
pub struct Reply {
    pub schema_version: u32,
    pub status: &'static str,
    pub reason: &'static str,
    pub session_applied: bool,
    pub generation: Option<String>,
    pub generation_path: Option<PathBuf>,
    pub generation_status: &'static str,
    pub settings: Vec<Setting>,
    pub change_id: Option<String>,
    pub history: Vec<journal::Entry>,
    pub conflicts: Vec<journal::Conflict>,
    pub manual_changes_recorded: Vec<String>,
    pub recovery: Vec<String>,
}
impl Reply {
    fn rejected(reason: &'static str) -> Self {
        Self {
            schema_version: 1,
            status: "rejected",
            reason,
            session_applied: false,
            generation: None,
            generation_path: None,
            generation_status: "unknown",
            settings: vec![],
            change_id: None,
            history: vec![],
            conflicts: vec![],
            manual_changes_recorded: vec![],
            recovery: vec![],
        }
    }
    pub fn exit_code(&self) -> u8 {
        match self.status {
            "rejected" => 1,
            "committed_durability_unknown" | "committed_history_pending" => 3,
            _ => 0,
        }
    }
    pub fn human(&self) -> String {
        let mut text = format!(
            "settings: {} ({}); session_applied=false; generation={}\n",
            self.status, self.reason, self.generation_status
        );
        for row in &self.settings {
            text.push_str(&format!(
                "{} = {} (source: {}; default: {})\n",
                row.key, row.value, row.source, row.default
            ));
        }
        for entry in &self.history {
            text.push_str(&format!(
                "{} {:?}: {}\n",
                entry.id,
                entry.kind,
                serde_json::to_string(&entry.changes).unwrap_or_default()
            ));
        }
        for conflict in &self.conflicts {
            text.push_str(&format!(
                "conflict {}: last change {:?}, requested {}\n",
                conflict.key, conflict.actual_change, conflict.requested_change
            ));
        }
        for note in &self.recovery {
            text.push_str(&format!("recovery: {note}\n"));
        }
        text
    }
}

/// Night-time guard: explicit profile root plus matching XDG paths, never HOME fallback.
pub fn run(operation: Operation<'_>, root: Option<&Path>, timeout: Duration) -> Reply {
    match storage::run(operation, root, timeout) {
        Ok(reply) => reply,
        Err(error) => Reply::rejected(error.reason),
    }
}
