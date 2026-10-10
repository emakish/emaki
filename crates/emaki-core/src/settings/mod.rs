//! Typed managed settings shared by session, terminal and graphical interfaces.
mod input;
mod journal;
mod live;
mod machine;
mod mime;
mod niri_live;
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
const COLUMN_WIDTH: &str = "windows.default_column_width";
const FOCUS_MOUSE: &str = "windows.focus_follows_mouse";
const WALLPAPER: &str = "appearance.wallpaper";
const FLOATING: &str = "keybindings.toggle_window_floating";
const LAYOUTS: &str = "keyboard.layouts";
const SWITCH_KEY: &str = "keyboard.switch_key";
const TERMINAL: &str = "defaults.terminal";
const BROWSER: &str = "defaults.browser";
const FILES: &str = "defaults.files";
const MAIL: &str = "defaults.mail";
const EDITOR: &str = "defaults.editor";
const CLOCK_24_HOUR: &str = "bar.clock_24_hour";
const BAR_AUTOHIDE: &str = "bar.autohide";
const BAR_OVERVIEW: &str = "bar.overview_workspaces";
const DOCK_ON: &str = "dock.on";
const DND: &str = "notifications.dnd";
const UNTIL: &str = "notifications.until";
const SCHEDULE: &str = "notifications.schedule";
const RULES: &str = "notifications.rules";
const SYSTEM_SOUNDS: &str = "sound.system_sounds";
const DOCK_AUTO_HIDE: &str = "dock.auto_hide";
/// Labels of the mockup segment "Switch layouts with"; Super+Space is the packaged
/// `Mod+Space { switch-layout "next"; }` bind. No generated niri file gets an xkb section
/// (it would replace the machine's list for the session) or a `grp:` option. Installed
/// profiles read and change both keyboard keys through the machine-settings provider
/// (systemd-localed, which niri follows live); isolated profiles store them inert.
const SWITCH_KEYS: [&str; 3] = ["Super+Space", "Alt+Shift", "Caps Lock"];
/// Offered as layout names (installer and provider), stored by the machine as us variants.
const US_VARIANTS: [&str; 2] = ["dvorak", "colemak"];
fn data_dir() -> PathBuf {
    option_env!("EMAKI_DATADIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            Path::new(option_env!("EMAKI_PREFIX").unwrap_or("/usr")).join("share/emaki")
        })
}

fn packaged_niri_default() -> String {
    let authentication = option_env!("EMAKI_POLKIT_AGENT")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            Path::new(option_env!("EMAKI_PREFIX").unwrap_or("/usr"))
                .join("lib/polkit-kde-authentication-agent-1")
        });
    NIRI_DEFAULT
        .replace("@EMAKI_DATADIR@", &data_dir().to_string_lossy())
        .replace("@EMAKI_POLKIT_AGENT@", &authentication.to_string_lossy())
}

/// Layout names are checked against the installed XKB rules list, because
/// `niri validate` accepts any string there (verified with niri 26.04).
fn xkb_rules() -> PathBuf {
    option_env!("EMAKI_XKB_RULES")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            Path::new(option_env!("EMAKI_PREFIX").unwrap_or("/usr"))
                .join("share/X11/xkb/rules/evdev.lst")
        })
}

#[derive(Clone, Copy)]
struct Field {
    key: &'static str,
    secret: bool,
}
static SCHEMA: &[Field] = &[
    Field {
        key: SYSTEM_SOUNDS,
        secret: false,
    },
    Field {
        key: RULES,
        secret: false,
    },
    Field {
        key: SCHEDULE,
        secret: false,
    },
    Field {
        key: UNTIL,
        secret: false,
    },
    Field {
        key: DND,
        secret: false,
    },
    Field {
        key: COLUMN_WIDTH,
        secret: false,
    },
    Field {
        key: FOCUS_MOUSE,
        secret: false,
    },
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
        key: MAIL,
        secret: false,
    },
    Field {
        key: EDITOR,
        secret: false,
    },
    Field {
        key: FILES,
        secret: false,
    },
    Field {
        key: CLOCK_24_HOUR,
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
        || (std::ptr::eq(schema, SCHEMA) && input::known(key))
}

#[derive(Clone, Debug, PartialEq, Eq, Default, Deserialize)]
#[serde(deny_unknown_fields)]
struct Appearance {
    gaps: Option<u16>,
    wallpaper: Option<String>,
}
#[derive(Clone, Debug, PartialEq, Eq, Default, Deserialize)]
#[serde(deny_unknown_fields)]
struct Windows {
    default_column_width: Option<String>,
    focus_follows_mouse: Option<bool>,
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
    mail: Option<String>,
    editor: Option<String>,
}
#[derive(Clone, Debug, PartialEq, Eq, Default, Deserialize)]
#[serde(deny_unknown_fields)]
struct Bar {
    clock_24_hour: Option<bool>,
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
#[derive(Clone, Debug, PartialEq, Eq, Default, Deserialize)]
#[serde(deny_unknown_fields)]
struct Notifications {
    dnd: Option<bool>,
    until: Option<String>,
    schedule: Option<String>,
    rules: Option<String>,
}
#[derive(Clone, Debug, PartialEq, Eq, Default, Deserialize)]
#[serde(deny_unknown_fields)]
struct Sound {
    system_sounds: Option<bool>,
}
#[derive(Clone, Debug, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
struct Document {
    #[serde(default)]
    notifications: Notifications,
    #[serde(default)]
    sound: Sound,
    schema_version: u32,
    generation: Option<String>,
    #[serde(default)]
    input: BTreeMap<String, Value>,
    #[serde(default)]
    shortcuts: BTreeMap<String, String>,
    #[serde(default)]
    appearance: Appearance,
    #[serde(default)]
    windows: Windows,
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
            notifications: Notifications::default(),
            sound: Sound::default(),
            generation: None,
            input: BTreeMap::new(),
            shortcuts: BTreeMap::new(),
            appearance: Appearance::default(),
            windows: Windows::default(),
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
    let value = input::canonical(value).map_err(|_| err("invalid_keybinding"))?;
    let value = value.as_str();
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
            let text = std::fs::read(xkb_rules()).ok()?;
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
/// (layout, variant) pairs of the `! variant` section of the installed XKB rules list.
fn xkb_variants() -> Result<&'static BTreeSet<(String, String)>> {
    static KNOWN: OnceLock<Option<BTreeSet<(String, String)>>> = OnceLock::new();
    KNOWN
        .get_or_init(|| {
            let text = std::fs::read(xkb_rules()).ok()?;
            if text.len() > 1024 * 1024 {
                return None;
            }
            let text = String::from_utf8_lossy(&text);
            let mut section = false;
            let mut pairs = BTreeSet::new();
            for line in text.lines() {
                if line.starts_with('!') {
                    section = line.trim() == "! variant";
                } else if section {
                    let mut words = line.split_whitespace();
                    if let (Some(variant), Some(layout)) = (words.next(), words.next())
                        && let Some(layout) = layout.strip_suffix(':')
                    {
                        pairs.insert((layout.to_owned(), variant.to_owned()));
                    }
                }
            }
            (!pairs.is_empty()).then_some(pairs)
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
    if codes
        .iter()
        .any(|c| !known.contains(c) && !(US_VARIANTS.contains(&c.as_str()) && known.contains("us")))
    {
        return Err(err("unknown_layout"));
    }
    Ok(codes)
}
fn switch_key(value: &str) -> Result<String> {
    SWITCH_KEYS
        .iter()
        .find(|label| **label == value)
        .map(|label| (*label).to_owned())
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
fn notification_value(key: &str, value: &str) -> Result<String> {
    let invalid = || err("invalid_notification_policy");
    if key == UNTIL {
        let timestamp: u64 = value.parse().map_err(|_| invalid())?;
        if timestamp > 8_640_000_000_000_000 || !value.bytes().all(|b| b.is_ascii_digit()) {
            return Err(invalid());
        }
        return Ok(timestamp.to_string());
    }
    if value.len() > 65536 {
        return Err(invalid());
    }
    let parsed: Value = serde_json::from_str(value).map_err(|_| invalid())?;
    let object = parsed.as_object().ok_or_else(invalid)?;
    if key == RULES {
        if object.len() > 256
            || object.iter().any(|(id, rule)| {
                id.is_empty()
                    || id.len() > 512
                    || id.chars().any(char::is_control)
                    || !matches!(rule.as_str(), Some("allow" | "silent" | "off"))
            })
        {
            return Err(invalid());
        }
    } else {
        let time = |name: &str| {
            object.get(name).and_then(Value::as_str).is_some_and(|t| {
                let b = t.as_bytes();
                b.len() == 5
                    && b[2] == b':'
                    && b.iter()
                        .enumerate()
                        .all(|(i, c)| i == 2 || c.is_ascii_digit())
                    && t[..2].parse::<u8>().is_ok_and(|n| n < 24)
                    && t[3..].parse::<u8>().is_ok_and(|n| n < 60)
            })
        };
        if object.len() != 3
            || !object.get("enabled").is_some_and(Value::is_boolean)
            || !time("start")
            || !time("end")
        {
            return Err(invalid());
        }
    }
    serde_json::to_string(&parsed).map_err(|_| invalid())
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
        input::validate(&doc)?;
        Ok(doc)
    }
    fn overrides(&self) -> BTreeMap<String, Value> {
        let mut values = BTreeMap::from([
            (GAPS.into(), json!(self.appearance.gaps)),
            (
                COLUMN_WIDTH.into(),
                json!(self.windows.default_column_width),
            ),
            (FOCUS_MOUSE.into(), json!(self.windows.focus_follows_mouse)),
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
            (MAIL.into(), json!(self.defaults.mail)),
            (EDITOR.into(), json!(self.defaults.editor)),
            (CLOCK_24_HOUR.into(), json!(self.bar.clock_24_hour)),
            (BAR_AUTOHIDE.into(), json!(self.bar.autohide)),
            (BAR_OVERVIEW.into(), json!(self.bar.overview_workspaces)),
            (DOCK_ON.into(), json!(self.dock.on)),
            (DOCK_AUTO_HIDE.into(), json!(self.dock.auto_hide)),
            (SYSTEM_SOUNDS.into(), json!(self.sound.system_sounds)),
            (RULES.into(), json!(self.notifications.rules)),
            (SCHEDULE.into(), json!(self.notifications.schedule)),
            (UNTIL.into(), json!(self.notifications.until)),
            (DND.into(), json!(self.notifications.dnd)),
        ]);
        input::overrides(self, &mut values);
        values
    }
    fn restore(&mut self, key: &str, value: &Value) -> Result<()> {
        if input::known(key) {
            return if value.is_null() {
                self.clear(key)
            } else {
                self.set(
                    key,
                    &value
                        .as_str()
                        .map(str::to_owned)
                        .unwrap_or_else(|| value.to_string()),
                )
            };
        }
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
            (
                FOCUS_MOUSE | CLOCK_24_HOUR | BAR_AUTOHIDE | BAR_OVERVIEW | DOCK_ON
                | DOCK_AUTO_HIDE | DND | SYSTEM_SOUNDS,
                Value::Bool(b),
            ) => b.to_string(),
            (
                COLUMN_WIDTH | FLOATING | WALLPAPER | SWITCH_KEY | TERMINAL | BROWSER | FILES
                | MAIL | EDITOR | UNTIL | SCHEDULE | RULES,
                Value::String(s),
            ) => s.clone(),
            _ => return Err(err("history_corrupt")),
        };
        self.set(key, &text)
    }
    fn clear(&mut self, key: &str) -> Result<()> {
        if input::known(key) {
            return input::set(self, key, "");
        }
        match key {
            GAPS => self.appearance.gaps = None,
            COLUMN_WIDTH => self.windows.default_column_width = None,
            FOCUS_MOUSE => self.windows.focus_follows_mouse = None,
            WALLPAPER => self.appearance.wallpaper = None,
            FLOATING => self.keybindings.toggle_window_floating = None,
            LAYOUTS => self.keyboard.layouts = None,
            SWITCH_KEY => self.keyboard.switch_key = None,
            TERMINAL => self.defaults.terminal = None,
            BROWSER => self.defaults.browser = None,
            FILES => self.defaults.files = None,
            MAIL => self.defaults.mail = None,
            EDITOR => self.defaults.editor = None,
            CLOCK_24_HOUR => self.bar.clock_24_hour = None,
            BAR_AUTOHIDE => self.bar.autohide = None,
            BAR_OVERVIEW => self.bar.overview_workspaces = None,
            DOCK_ON => self.dock.on = None,
            DOCK_AUTO_HIDE => self.dock.auto_hide = None,
            SYSTEM_SOUNDS => self.sound.system_sounds = None,
            RULES => self.notifications.rules = None,
            SCHEDULE => self.notifications.schedule = None,
            UNTIL => self.notifications.until = None,
            DND => self.notifications.dnd = None,
            _ => return Err(err("unknown_setting")),
        }
        Ok(())
    }
    // An empty value removes the override of the keys added for the Settings pages
    // (the two original keys keep their contract: empty is invalid).
    fn set(&mut self, key: &str, value: &str) -> Result<()> {
        if input::known(key) {
            return input::set(self, key, value);
        }
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
            COLUMN_WIDTH => {
                if !matches!(value, "full" | "half" | "third" | "twothirds") {
                    return Err(err("invalid_column_width"));
                }
                self.windows.default_column_width = Some(value.into());
            }
            FOCUS_MOUSE => self.windows.focus_follows_mouse = Some(boolean(value)?),
            WALLPAPER => self.appearance.wallpaper = Some(wallpaper(value)?),
            FLOATING => {
                let value = chord(value)?;
                self.keybindings.toggle_window_floating = Some(value);
            }
            LAYOUTS => self.keyboard.layouts = Some(layouts(value)?),
            SWITCH_KEY => self.keyboard.switch_key = Some(switch_key(value)?),
            TERMINAL => self.defaults.terminal = Some(desktop_id(value)?),
            BROWSER => self.defaults.browser = Some(desktop_id(value)?),
            FILES => self.defaults.files = Some(desktop_id(value)?),
            MAIL => self.defaults.mail = Some(desktop_id(value)?),
            EDITOR => self.defaults.editor = Some(desktop_id(value)?),
            CLOCK_24_HOUR => self.bar.clock_24_hour = Some(boolean(value)?),
            BAR_AUTOHIDE => self.bar.autohide = Some(boolean(value)?),
            BAR_OVERVIEW => self.bar.overview_workspaces = Some(boolean(value)?),
            DOCK_ON => self.dock.on = Some(boolean(value)?),
            DOCK_AUTO_HIDE => self.dock.auto_hide = Some(boolean(value)?),
            SYSTEM_SOUNDS => self.sound.system_sounds = Some(boolean(value)?),
            RULES => self.notifications.rules = Some(notification_value(RULES, value)?),
            SCHEDULE => self.notifications.schedule = Some(notification_value(SCHEDULE, value)?),
            UNTIL => self.notifications.until = Some(notification_value(UNTIL, value)?),
            DND => self.notifications.dnd = Some(boolean(value)?),
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
            "windows",
            vec![
                string("default_column_width", &self.windows.default_column_width),
                flag("focus_follows_mouse", &self.windows.focus_follows_mouse),
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
                string("mail", &self.defaults.mail),
                string("editor", &self.defaults.editor),
            ],
        );
        section(
            "bar",
            vec![
                flag("clock_24_hour", &self.bar.clock_24_hour),
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
        section(
            "notifications",
            vec![
                flag("dnd", &self.notifications.dnd),
                self.notifications
                    .until
                    .as_ref()
                    .map(|v| format!("until = {}", json!(v))),
                self.notifications
                    .schedule
                    .as_ref()
                    .map(|v| format!("schedule = {}", json!(v))),
                self.notifications
                    .rules
                    .as_ref()
                    .map(|v| format!("rules = {}", json!(v))),
            ],
        );
        section(
            "sound",
            vec![flag("system_sounds", &self.sound.system_sounds)],
        );
        input::text(self, &mut text);
        text
    }
    fn files(&self, _default_gaps: u16) -> Vec<(&'static str, String)> {
        let mut fragment = "// Emaki managed overrides. Change with emaki settings.\n".to_owned();
        let mut layout = String::new();
        if let Some(gaps) = self.appearance.gaps {
            layout.push_str(&format!("    gaps {gaps}\n"));
        }
        if let Some(width) = &self.windows.default_column_width {
            let proportion = match width.as_str() {
                "full" => "1.0",
                "half" => "0.5",
                "third" => "0.3333333333333333",
                "twothirds" => "0.6666666666666666",
                _ => unreachable!("validated column width"),
            };
            layout.push_str(&format!(
                "    default-column-width {{ proportion {proportion}; }}\n"
            ));
        }
        if !layout.is_empty() {
            fragment.push_str(&format!("layout {{\n{layout}}}\n"));
        }
        input::fragment(self, &mut fragment);
        // Machine keyboard layouts never enter the generated xkb configuration.
        let mut files = vec![
            ("niri.kdl", fragment),
            (
                "config.kdl",
                "include \"package/default.kdl\"\ninclude \"niri.kdl\"\n".into(),
            ),
            ("package/default.kdl", packaged_niri_default()),
            ("package/theme.kdl", NIRI_THEME.into()),
            ("package/shell.kdl", NIRI_SHELL.into()),
        ];
        if let Some(path) = &self.appearance.wallpaper {
            files.push((
                "wpaperd.toml",
                format!("# Generated by Emaki. Edit settings.toml, not this file.\n[default]\npath = \"{path}\"\n"),
            ));
        }
        if self.defaults.browser.is_some()
            || self.defaults.files.is_some()
            || self.defaults.mail.is_some()
            || self.defaults.editor.is_some()
        {
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
            for (mime, selected) in [
                ("x-scheme-handler/mailto", &self.defaults.mail),
                ("text/plain", &self.defaults.editor),
            ] {
                if let Some(id) = selected {
                    list.push_str(&format!("{mime}={id}\n"));
                }
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
    Undo {
        id: &'a str,
    },
    Get {
        key: &'a str,
    },
    Set {
        key: &'a str,
        value: &'a str,
    },
    /// Remove the override of any key, including the two that refuse an empty value.
    Reset {
        key: &'a str,
    },
}
#[derive(Serialize)]
pub struct Setting {
    pub key: &'static str,
    pub secret: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub title: Option<String>,
    pub explanation: Option<String>,
    pub value: Value,
    pub override_value: Value,
    pub default: Value,
    pub source: &'static str,
    pub value_type: &'static str,
    pub allowed: &'static str,
    pub application: &'static str,
    pub validation: &'static str,
    pub undo: &'static str,
    /// False for a machine key the machine's configuration declares or the provider cannot reach.
    pub editable: bool,
    /// The option of the machine's configuration that sets a declared machine key.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub declared_by: Option<String>,
    /// Why a machine key's value is missing or read-only (a fixed reason).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub machine_reason: Option<&'static str>,
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
const SHELL_ONLY: &str = "acknowledged live shell bindings; read again after session startup";
fn rows(doc: &Document, gaps: u16) -> Vec<Setting> {
    let overrides = doc.overrides();
    let descriptions = [
        Description {
            key: GAPS,
            default: json!(gaps),
            default_source: "embedded:tokens.toml#geometry.gaps",
            value_type: "integer",
            allowed: "0..64 logical pixels",
            application: "managed niri fragment; acknowledged session reload",
            validation: "range; niri validate fragment and combined package defaults",
        },
        Description {
            key: WALLPAPER,
            default: Value::Null,
            default_source: "wpaperd config of the session (unset)",
            value_type: "path",
            allowed: "absolute path to an existing regular image file; <=4096 bytes, no quotes/backslashes/control characters; empty unsets",
            application: "wpaperctl with output verification; generation/wpaperd.toml at session startup",
            validation: "syntax; file exists at set time",
        },
        Description {
            key: FLOATING,
            default: Value::Null,
            default_source: "embedded:niri/default.kdl (unset)",
            value_type: "key_chord",
            allowed: "Mod + optional Ctrl/Alt/Shift + one XKB keysym; <=80 ASCII bytes",
            application: "managed niri fragment: toggle-window-floating; acknowledged session reload",
            validation: "restricted syntax; niri keysym validation; package bindings may be overridden",
        },
        Description {
            key: LAYOUTS,
            default: Value::Null,
            default_source: "embedded:niri/default.kdl (unset: xkb default of the session)",
            value_type: "string_list",
            allowed: "1..4 distinct XKB layout codes, comma-separated (us,ru); empty unsets",
            application: "isolated profiles store it inert; installed profiles read and change the machine's layouts",
            validation: "codes listed in the installed XKB rules, or the us variants dvorak and colemak; installed profiles also take layout(variant) as listed there",
        },
        Description {
            key: SWITCH_KEY,
            default: json!("Super+Space"),
            default_source: "embedded:niri/default.kdl (Mod+Space switch-layout)",
            value_type: "choice",
            allowed: "Super+Space | Alt+Shift | Caps Lock; empty unsets",
            application: "isolated profiles store it inert; installed profiles read and change the machine's switch key",
            validation: "choice",
        },
        Description {
            key: TERMINAL,
            default: Value::Null,
            default_source: "shell terminal adapter (unset)",
            value_type: "desktop_id",
            allowed: "desktop entry id ending in .desktop; empty unsets",
            application: "emaki-terminal reads the selected desktop entry for each launch",
            validation: "desktop ID syntax; installed application and executable checked before commit",
        },
        Description {
            key: BROWSER,
            default: Value::Null,
            default_source: "GIO default handler of the session (unset)",
            value_type: "desktop_id",
            allowed: "desktop entry id ending in .desktop; empty unsets",
            application: "managed defaults/mimeapps.list: http, https, text/html; verified with GIO",
            validation: "desktop ID syntax; installed application and executable checked before commit",
        },
        Description {
            key: FILES,
            default: Value::Null,
            default_source: "GIO default handler of the session (unset)",
            value_type: "desktop_id",
            allowed: "desktop entry id ending in .desktop; empty unsets",
            application: "managed defaults/mimeapps.list: inode/directory; verified with GIO",
            validation: "desktop ID syntax; installed application and executable checked before commit",
        },
        Description {
            key: MAIL,
            default: Value::Null,
            default_source: "GIO default handler of the session (unset)",
            value_type: "desktop_id",
            allowed: "desktop entry id ending in .desktop; empty unsets",
            application: "managed defaults/mimeapps.list: x-scheme-handler/mailto; verified with GIO",
            validation: "desktop ID syntax; installed application and executable checked before commit",
        },
        Description {
            key: EDITOR,
            default: Value::Null,
            default_source: "GIO default handler of the session (unset)",
            value_type: "desktop_id",
            allowed: "desktop entry id ending in .desktop; empty unsets",
            application: "managed defaults/mimeapps.list: text/plain; verified with GIO",
            validation: "desktop ID syntax; installed application and executable checked before commit",
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
            key: CLOCK_24_HOUR,
            default: json!(true),
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
            key: DND,
            default: json!(false),
            default_source: "shell default (unset)",
            value_type: "boolean",
            allowed: "validated notification or sound policy; empty unsets",
            application: SHELL_ONLY,
            validation: "typed policy",
        },
        Description {
            key: UNTIL,
            default: json!("0"),
            default_source: "shell default (unset)",
            value_type: "string",
            allowed: "validated notification or sound policy; empty unsets",
            application: SHELL_ONLY,
            validation: "typed policy",
        },
        Description {
            key: SCHEDULE,
            default: json!(r#"{"enabled":false,"start":"22:00","end":"07:00"}"#),
            default_source: "shell default (unset)",
            value_type: "string",
            allowed: "validated notification or sound policy; empty unsets",
            application: SHELL_ONLY,
            validation: "typed policy",
        },
        Description {
            key: RULES,
            default: json!("{}"),
            default_source: "shell default (unset)",
            value_type: "string",
            allowed: "validated notification or sound policy; empty unsets",
            application: SHELL_ONLY,
            validation: "typed policy",
        },
        Description {
            key: SYSTEM_SOUNDS,
            default: json!(true),
            default_source: "shell default (unset)",
            value_type: "boolean",
            allowed: "validated notification or sound policy; empty unsets",
            application: SHELL_ONLY,
            validation: "typed policy",
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
        Description {
            key: COLUMN_WIDTH,
            default: json!("full"),
            default_source: "embedded:niri/default.kdl#layout.default-column-width",
            value_type: "choice",
            allowed: "full | half | third | twothirds; empty unsets",
            application: "managed niri fragment; acknowledged session reload; applies to new columns",
            validation: "choice; niri validate fragment and combined package defaults",
        },
        Description {
            key: FOCUS_MOUSE,
            default: json!(false),
            default_source: "embedded:niri/default.kdl (unset)",
            value_type: "boolean",
            allowed: "true | false; false or empty restores inherited focus behavior",
            application: "managed niri fragment; acknowledged session reload",
            validation: "boolean; niri validate fragment and combined package defaults",
        },
    ];
    let mut result: Vec<_> = descriptions
        .into_iter()
        .map(|d| {
            let override_value = overrides.get(d.key).cloned().unwrap_or(Value::Null);
            let secret = !recordable(d.key, SCHEMA);
            let set = !override_value.is_null();
            Setting {
                key: d.key,
                title: None,
                explanation: None,
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
                editable: true,
                declared_by: None,
                machine_reason: None,
            }
        })
        .collect();
    input::rows(doc, &mut result);
    result
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
    /// Key, before and after of a machine change (and requested, when uncertain); set it
    /// back with the before value.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub machine_change: Option<Value>,
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
            machine_change: None,
        }
    }
    pub fn exit_code(&self) -> u8 {
        match self.status {
            "rejected" => 1,
            "committed_durability_unknown" | "committed_history_pending" | "uncertain" => 3,
            _ => 0,
        }
    }
    pub fn human(&self) -> String {
        let mut text = format!(
            "settings: {} ({}); session_applied={}; generation={}\n",
            self.status, self.reason, self.session_applied, self.generation_status
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
        if let Some(change) = &self.machine_change {
            text.push_str(&format!(
                "machine {}: before {}, after {}\n",
                change["key"].as_str().unwrap_or_default(),
                change["before"],
                change["after"]
            ));
        }
        for note in &self.recovery {
            text.push_str(&format!("recovery: {note}\n"));
        }
        text
    }
}

/// Installed XDG store, or an explicitly isolated profile with matching XDG paths.
pub fn run(operation: Operation<'_>, root: Option<&Path>, timeout: Duration) -> Reply {
    match storage::run(operation, root, timeout) {
        Ok(reply) => reply,
        Err(error) => Reply::rejected(error.reason),
    }
}

/// Construct a managed wrapper for session startup without contacting live services.
pub fn session_config(timeout: Duration) -> std::result::Result<PathBuf, &'static str> {
    storage::session_config(timeout).map_err(|error| error.reason)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn embedded_defaults_resolve_install_paths() {
        let files = Document::default().files(8);
        let default = &files
            .iter()
            .find(|(name, _)| *name == "package/default.kdl")
            .unwrap()
            .1;
        assert!(!default.contains("@EMAKI_"));
        assert!(
            default.contains(
                &data_dir()
                    .join("shell/helpers/clipboard_store.py")
                    .to_string_lossy()
                    .to_string()
            )
        );
    }

    #[test]
    fn clock_format_round_trips_without_compositor_changes() {
        let mut doc = Document::default();
        let niri = doc
            .files(8)
            .into_iter()
            .find(|(name, _)| *name == "niri.kdl")
            .unwrap();
        doc.set(CLOCK_24_HOUR, "false").unwrap();
        assert_eq!(Document::parse(Some(doc.text().as_bytes())).unwrap(), doc);
        assert_eq!(doc.overrides()[CLOCK_24_HOUR], json!(false));
        assert_eq!(
            doc.files(8)
                .into_iter()
                .find(|(name, _)| *name == "niri.kdl")
                .unwrap(),
            niri
        );
        assert!(doc.set(CLOCK_24_HOUR, "invalid").is_err());
        doc.clear(CLOCK_24_HOUR).unwrap();
        assert_eq!(doc, Document::default());
    }

    #[test]
    fn unset_gaps_leave_package_defaults_live() {
        let default = Document::default();
        let fragment = |doc: &Document, gaps| {
            doc.files(gaps)
                .into_iter()
                .find(|(name, _)| *name == "niri.kdl")
                .unwrap()
                .1
        };
        assert_eq!(fragment(&default, 8), fragment(&default, 12));
        assert!(!fragment(&default, 8).contains("gaps"));
        let mut override_doc = default;
        override_doc.set(GAPS, "4").unwrap();
        assert!(fragment(&override_doc, 12).contains("gaps 4"));
    }

    #[test]
    fn window_behavior_fragment_preserves_unset_and_keyboard_inheritance() {
        let mut doc = Document::default();
        let fragment = |doc: &Document| {
            doc.files(8)
                .into_iter()
                .find(|(name, _)| *name == "niri.kdl")
                .unwrap()
                .1
        };
        let inherited = fragment(&doc);
        assert!(!inherited.contains("layout"));
        assert!(!inherited.contains("input"));
        doc.set(GAPS, "6").unwrap();
        doc.set(COLUMN_WIDTH, "twothirds").unwrap();
        doc.set(FOCUS_MOUSE, "true").unwrap();
        let text = fragment(&doc);
        assert_eq!(text.matches("layout {").count(), 1);
        assert!(text.contains("gaps 6"));
        assert!(text.contains("default-column-width { proportion 0.6666666666666666; }"));
        assert!(text.contains("focus-follows-mouse"));
        assert!(!text.contains("keyboard"));
        assert!(!text.contains("xkb"));
        assert_eq!(Document::parse(Some(doc.text().as_bytes())).unwrap(), doc);
        doc.set(FOCUS_MOUSE, "false").unwrap();
        assert!(!fragment(&doc).contains("input"));
        doc.clear(FOCUS_MOUSE).unwrap();
        doc.clear(COLUMN_WIDTH).unwrap();
        doc.clear(GAPS).unwrap();
        assert_eq!(fragment(&doc), inherited);
        for text in [
            "schema_version = 1\n[windows]\ndefault_column_width = 'wide'\n",
            "schema_version = 1\n[windows]\nfocus_follows_mouse = 'true'\n",
            "schema_version = 1\n[windows]\nunknown = true\n",
        ] {
            assert!(Document::parse(Some(text.as_bytes())).is_err());
        }
    }

    // Keyboard layouts belong to the machine (localed). Any xkb section in a niri file Emaki
    // writes would replace that list for the session (and a grp: option would switch twice
    // with the packaged Mod+Space bind), so generated files never carry the keyboard keys.
    #[test]
    fn keyboard_keys_never_write_xkb_into_generated_files() {
        for switch in [
            None,
            Some("Super+Space"),
            Some("Alt+Shift"),
            Some("Caps Lock"),
        ] {
            let doc = Document {
                keyboard: Keyboard {
                    layouts: Some(vec!["us".into(), "ru".into()]),
                    switch_key: switch.map(str::to_owned),
                },
                ..Document::default()
            };
            let files = doc.files(8);
            for (name, text) in files
                .iter()
                .filter(|(name, _)| !name.starts_with("package/"))
            {
                if *name == "settings.toml" {
                    assert!(text.contains("layouts = [\"us\", \"ru\"]"), "{text}");
                    if let Some(label) = switch {
                        assert!(
                            text.contains(&format!("switch_key = \"{label}\"")),
                            "{text}"
                        );
                    }
                } else {
                    assert!(
                        !text.contains("xkb") && !text.contains("grp:"),
                        "{name}: {text}"
                    );
                }
            }
        }
    }
}

#[cfg(test)]
mod notification_policy_tests {
    use super::*;
    #[test]
    fn policy_values_round_trip_and_restore() {
        let mut doc = Document::default();
        for (key, value) in [
            (DND, "true"),
            (UNTIL, "123456789"),
            (
                SCHEDULE,
                r#"{"enabled":true,"start":"23:30","end":"06:15"}"#,
            ),
            (
                RULES,
                r#"{"name:Mail \"Home\"":"silent","desktop:org.example.Chat":"off"}"#,
            ),
            (SYSTEM_SOUNDS, "false"),
        ] {
            doc.set(key, value).unwrap();
        }
        let restored = Document::parse(Some(doc.text().as_bytes())).unwrap();
        assert_eq!(doc, restored);
        for (key, value) in doc.overrides() {
            let mut clean = Document::default();
            clean.restore(&key, &value).unwrap();
            assert_eq!(clean.overrides()[&key], value);
            clean.clear(&key).unwrap();
            assert_eq!(clean.overrides()[&key], Value::Null);
        }
    }
    #[test]
    fn rejects_malformed_notification_policy() {
        for (key, value) in [
            (UNTIL, "-1"),
            (UNTIL, "NaN"),
            (UNTIL, "8640000000000001"),
            (
                SCHEDULE,
                r#"{"enabled":true,"start":"24:00","end":"07:00"}"#,
            ),
            (
                SCHEDULE,
                r#"{"enabled":true,"start":"22:00","end":"07:00","extra":1}"#,
            ),
            (RULES, r#"{"name:Mail":"surprise"}"#),
            (RULES, r#"{"":"off"}"#),
            (RULES, "[]"),
        ] {
            assert!(
                Document::default().set(key, value).is_err(),
                "{key}: {value}"
            );
        }
    }
}
