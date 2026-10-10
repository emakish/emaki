// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
//! Typed input controls and the complete packaged shortcut catalog.
use super::storage::Profile;
use super::*;
use std::fs;

struct Input {
    key: &'static str,
    default: Value,
    kind: &'static str,
    allowed: &'static str,
}
fn inputs() -> &'static [Input] {
    static INPUTS: OnceLock<Vec<Input>> = OnceLock::new();
    INPUTS.get_or_init(|| {
        let mut result = vec![
            Input {
                key: "keyboard.repeat_delay",
                default: json!(600),
                kind: "integer",
                allowed: "100..2000 milliseconds",
            },
            Input {
                key: "keyboard.repeat_rate",
                default: json!(25),
                kind: "integer",
                allowed: "1..100 repeats per second",
            },
            Input {
                key: "mouse.speed",
                default: json!(0.0),
                kind: "number",
                allowed: "-1..1",
            },
            Input {
                key: "touchpad.speed",
                default: json!(0.0),
                kind: "number",
                allowed: "-1..1",
            },
        ];
        for (key, default) in [
            ("mouse.natural_scroll", false),
            ("touchpad.natural_scroll", true),
            ("touchpad.tap", true),
            ("touchpad.two_finger_right_click", true),
            ("touchpad.disable_while_typing", true),
            ("gestures.dnd_edge_view_scroll", true),
            ("gestures.dnd_edge_workspace_switch", true),
        ] {
            result.push(Input {
                key,
                default: if key == "touchpad.two_finger_right_click" {
                    Value::Null
                } else {
                    json!(default)
                },
                kind: "boolean",
                allowed: "true | false",
            });
        }
        result
    })
}
struct Shortcut {
    key: String,
    chord: String,
    properties: String,
    action: String,
    label: String,
}
fn shortcuts() -> &'static [Shortcut] {
    static SHORTCUTS: OnceLock<Vec<Shortcut>> = OnceLock::new();
    SHORTCUTS.get_or_init(|| {
        // Packaged bindings deliberately occupy one line each. Keep the full action and
        // properties, including quoted shell braces, repeat and locked-session policy.
        let mut in_binds = false;
        NIRI_DEFAULT
            .lines()
            .filter_map(|line| {
                let line = line.trim();
                if line == "binds {" {
                    in_binds = true;
                    return None;
                }
                if !in_binds || line.starts_with("//") {
                    return None;
                }
                if line == "}" {
                    in_binds = false;
                    return None;
                }
                let (head, tail) = line.split_once('{')?;
                let end = tail.rfind('}')?;
                let action = tail[..end].trim().to_owned();
                let mut fields = head.trim().splitn(2, char::is_whitespace);
                let chord = canonical(fields.next()?).expect("packaged shortcut syntax");
                let properties = fields.next().unwrap_or("").trim().to_owned();
                let label = properties
                    .split_once("hotkey-overlay-title=\"")
                    .and_then(|(_, rest)| rest.split_once('"'))
                    .map(|(label, _)| label.to_owned())
                    .unwrap_or_else(|| action_label(&action));
                Some(Shortcut {
                    key: format!("shortcuts.{chord}"),
                    chord,
                    properties,
                    action,
                    label,
                })
            })
            .collect()
    })
}
fn action_label(action: &str) -> String {
    match action.trim_end_matches(';') {
        "focus-column-left" => "Focus the column to the left".into(),
        "focus-column-right" => "Focus the column to the right".into(),
        "focus-window-or-workspace-up" => "Focus the window or workspace above".into(),
        "focus-window-or-workspace-down" => "Focus the window or workspace below".into(),
        "move-column-left" => "Move the column left".into(),
        "move-column-right" => "Move the column right".into(),
        "move-window-up-or-to-workspace-up" => {
            "Move the window up or to the workspace above".into()
        }
        "move-window-down-or-to-workspace-down" => {
            "Move the window down or to the workspace below".into()
        }
        "close-window" => "Close the window".into(),
        "fullscreen-window" => "Toggle full screen".into(),
        "switch-preset-column-width" => "Cycle column widths".into(),
        "maximize-column" => "Maximize the column".into(),
        "toggle-overview" => "Toggle the overview".into(),
        "toggle-window-floating" => "Toggle floating window".into(),
        "screenshot" => "Take an interactive screenshot".into(),
        "show-hotkey-overlay" => "Show keyboard shortcuts".into(),
        "switch-layout \"next\"" => "Switch to the next keyboard layout".into(),
        value if value.starts_with("focus-workspace ") => format!(
            "Focus workspace {}",
            value.trim_start_matches("focus-workspace ")
        ),
        value if value.starts_with("move-column-to-workspace ") => format!(
            "Move the column to workspace {}",
            value.trim_start_matches("move-column-to-workspace ")
        ),
        value if value.contains("kbd_backlight") => if value.contains("10%+") {
            "Increase keyboard brightness"
        } else {
            "Decrease keyboard brightness"
        }
        .into(),
        value if value.contains("playerctl") => if value.contains("play-pause") {
            "Play or pause media"
        } else if value.contains("previous") {
            "Previous track"
        } else if value.contains("next") {
            "Next track"
        } else {
            "Stop media"
        }
        .into(),
        value if value.contains("wpctl") => if value.contains("SOURCE") {
            "Mute or unmute microphone"
        } else if value.contains("set-mute") {
            "Mute or unmute sound"
        } else if value.contains("5%+") {
            "Increase volume"
        } else {
            "Decrease volume"
        }
        .into(),
        value if value.contains("brightnessctl") => if value.contains("5%+") {
            "Increase screen brightness"
        } else {
            "Decrease screen brightness"
        }
        .into(),
        value if value.contains("launcher") => "Open launcher".into(),
        value => value.replace('-', " ").replace([';', '"'], ""),
    }
}
pub(super) fn canonical(value: &str) -> Result<String> {
    if value.len() > 80 || !value.is_ascii() {
        return Err(err("Enter a shortcut using modifiers and one key."));
    }
    let parts: Vec<_> = value.split('+').collect();
    let (key, modifiers) = parts
        .split_last()
        .ok_or_else(|| err("Enter a shortcut using modifiers and one key."))?;
    let key = match key.to_ascii_lowercase().as_str() {
        "esc" | "escape" => "Escape".to_owned(),
        "space" => "Space".to_owned(),
        _ => key.to_string(),
    };
    if key.is_empty() || !key.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_') {
        return Err(err("Enter a shortcut using modifiers and one key."));
    }
    let function_key = key
        .strip_prefix('F')
        .and_then(|n| n.parse::<u8>().ok())
        .is_some_and(|n| (1..=24).contains(&n));
    let mut found = BTreeSet::new();
    for modifier in modifiers {
        let normalized = match modifier.to_ascii_lowercase().as_str() {
            "mod" | "super" => "Mod",
            "ctrl" | "control" => "Ctrl",
            "alt" => "Alt",
            "shift" => "Shift",
            _ => return Err(err("Use Super, Ctrl, Alt or Shift as shortcut modifiers.")),
        };
        if !found.insert(normalized) {
            return Err(err("A shortcut modifier can only appear once."));
        }
    }
    if !["Mod", "Ctrl", "Alt"]
        .iter()
        .any(|modifier| found.contains(modifier))
        && !function_key
        && !key.starts_with("XF86")
        && key != "Print"
    {
        return Err(err(
            "Use Super, Ctrl or Alt with this key, such as Super+T.",
        ));
    }
    let mut result: Vec<_> = ["Mod", "Ctrl", "Alt", "Shift"]
        .into_iter()
        .filter(|m| found.contains(m))
        .map(str::to_owned)
        .collect();
    result.push(if key.len() == 1 {
        key.to_ascii_uppercase()
    } else {
        key
    });
    Ok(result.join("+"))
}
pub(super) fn known(key: &str) -> bool {
    inputs().iter().any(|i| i.key == key) || shortcuts().iter().any(|s| s.key == key)
}
pub(super) fn overrides(doc: &Document, values: &mut BTreeMap<String, Value>) {
    for input in inputs() {
        values.insert(
            input.key.into(),
            doc.input.get(input.key).cloned().unwrap_or(Value::Null),
        );
    }
    for shortcut in shortcuts() {
        values.insert(
            shortcut.key.clone(),
            doc.shortcuts
                .get(&shortcut.chord)
                .map_or(Value::Null, |s| json!(s)),
        );
    }
    // Unknown manual keys must fail validation, never disappear silently.
    for (key, value) in &doc.input {
        values.insert(key.clone(), value.clone());
    }
    for (key, value) in &doc.shortcuts {
        values.insert(format!("shortcuts.{key}"), json!(value));
    }
}
pub(super) fn set(doc: &mut Document, key: &str, value: &str) -> Result<()> {
    if let Some(input) = inputs().iter().find(|i| i.key == key) {
        if value.is_empty() {
            doc.input.remove(key);
            return Ok(());
        }
        let parsed = match input.kind {
            "boolean" => json!(boolean(value)?),
            "integer" => {
                let number = value
                    .parse::<u32>()
                    .map_err(|_| err("Enter a whole number within the allowed range."))?;
                let range = if key.ends_with("delay") {
                    100..=2000
                } else {
                    1..=100
                };
                if !range.contains(&number) {
                    return Err(err("Enter a whole number within the allowed range."));
                }
                json!(number)
            }
            _ => {
                let number = value
                    .parse::<f64>()
                    .map_err(|_| err("Pointer speed must be between -1 and 1."))?;
                if !number.is_finite() || !(-1.0..=1.0).contains(&number) {
                    return Err(err("Pointer speed must be between -1 and 1."));
                }
                json!(number)
            }
        };
        doc.input.insert(key.into(), parsed);
        return Ok(());
    }
    let shortcut = shortcuts()
        .iter()
        .find(|s| s.key == key)
        .ok_or_else(|| err("unknown_setting"))?;
    if value.is_empty() && !doc.shortcuts.contains_key(&shortcut.chord) {
        return Ok(());
    }
    let new = if value.is_empty() {
        shortcut.chord.clone()
    } else {
        canonical(value)?
    };
    if shortcut.chord == "Mod+Ctrl+Escape" || new == "Mod+Ctrl+Escape" {
        return Err(err(
            "Super+Ctrl+Esc is reserved for revoking administrator access.",
        ));
    }
    if value.is_empty() {
        doc.shortcuts.remove(&shortcut.chord);
    } else {
        doc.shortcuts.insert(shortcut.chord.clone(), new);
    }
    Ok(())
}
fn effective(doc: &Document, key: &str) -> Value {
    doc.input.get(key).cloned().unwrap_or_else(|| {
        inputs()
            .iter()
            .find(|i| i.key == key)
            .expect("registered input")
            .default
            .clone()
    })
}
pub(super) fn text(doc: &Document, text: &mut String) {
    if !doc.input.is_empty() {
        text.push_str("\n[input]\n");
        for (key, value) in &doc.input {
            text.push_str(&format!("{key:?} = {value}\n"));
        }
    }
    if !doc.shortcuts.is_empty() {
        text.push_str("\n[shortcuts]\n");
        for (key, value) in &doc.shortcuts {
            text.push_str(&format!("{key:?} = {value:?}\n"));
        }
    }
}
pub(super) fn fragment(doc: &Document, text: &mut String) {
    let value = |key: &str| effective(doc, key);
    let flag = |key: &str| value(key).as_bool().unwrap_or(false);
    if doc.windows.focus_follows_mouse == Some(true)
        || doc.input.keys().any(|k| !k.starts_with("gestures."))
    {
        text.push_str("input {\n");
        if doc.windows.focus_follows_mouse == Some(true) {
            text.push_str("    focus-follows-mouse\n");
        }
        if doc.input.keys().any(|k| k.starts_with("keyboard.")) {
            text.push_str("    keyboard {\n");
            for (key, node) in [
                ("keyboard.repeat_delay", "repeat-delay"),
                ("keyboard.repeat_rate", "repeat-rate"),
            ] {
                if let Some(value) = doc.input.get(key) {
                    text.push_str(&format!("        {node} {value}\n"));
                }
            }
            text.push_str("    }\n");
        }
        if doc.input.keys().any(|k| k.starts_with("mouse.")) {
            text.push_str(&format!(
                "    mouse {{\n        accel-speed {}\n",
                value("mouse.speed")
            ));
            if flag("mouse.natural_scroll") {
                text.push_str("        natural-scroll\n");
            }
            text.push_str("    }\n");
        }
        if doc.input.keys().any(|k| k.starts_with("touchpad.")) {
            text.push_str(&format!(
                "    touchpad {{\n        dwtp\n        accel-speed {}\n",
                value("touchpad.speed")
            ));
            for (key, node) in [
                ("touchpad.tap", "tap"),
                ("touchpad.natural_scroll", "natural-scroll"),
                ("touchpad.disable_while_typing", "dwt"),
            ] {
                if flag(key) {
                    text.push_str(&format!("        {node}\n"));
                }
            }
            if doc.input.contains_key("touchpad.two_finger_right_click") {
                text.push_str(if flag("touchpad.two_finger_right_click") { "        click-method \"clickfinger\"\n        tap-button-map \"left-right-middle\"\n" } else { "        click-method \"button-areas\"\n        tap-button-map \"left-middle-right\"\n" });
            }
            text.push_str("    }\n");
        }
        text.push_str("}\n");
    }
    if doc.input.keys().any(|k| k.starts_with("gestures.")) {
        text.push_str("gestures {\n");
        for (key, node) in [
            ("gestures.dnd_edge_view_scroll", "dnd-edge-view-scroll"),
            (
                "gestures.dnd_edge_workspace_switch",
                "dnd-edge-workspace-switch",
            ),
        ] {
            if doc.input.contains_key(key) {
                text.push_str(&format!(
                    "    {node} {{ max-speed {}; }}\n",
                    if flag(key) { 1500 } else { 0 }
                ));
            }
        }
        text.push_str("}\n");
    }
    if !doc.shortcuts.is_empty() || doc.keybindings.toggle_window_floating.is_some() {
        text.push_str("binds {\n");
        let mut binds = BTreeMap::new();
        for shortcut in shortcuts() {
            if doc
                .shortcuts
                .get(&shortcut.chord)
                .is_some_and(|new| new != &shortcut.chord)
            {
                if !doc
                    .keybindings
                    .toggle_window_floating
                    .as_ref()
                    .is_some_and(|key| key.eq_ignore_ascii_case(&shortcut.chord))
                {
                    binds.insert(shortcut.chord.clone(), "{ spawn; }".to_owned());
                }
            }
        }
        for shortcut in shortcuts() {
            if let Some(new) = doc.shortcuts.get(&shortcut.chord) {
                binds.insert(
                    new.clone(),
                    format!("{} {{ {} }}", shortcut.properties, shortcut.action),
                );
            }
        }
        if let Some(key) = &doc.keybindings.toggle_window_floating {
            binds.insert(key.clone(), "{ toggle-window-floating; }".into());
        }
        for (chord, binding) in binds {
            text.push_str(&format!("    {chord} {binding}\n"));
        }
        text.push_str("}\n");
    }
}
pub(super) fn rows(doc: &Document, rows: &mut Vec<Setting>) {
    for input in inputs() {
        let override_value = doc.input.get(input.key).cloned().unwrap_or(Value::Null);
        rows.push(Setting {
            key: input.key,
            title: None,
            explanation: None,
            editable: true,
            declared_by: None,
            machine_reason: None,
            secret: false,
            value: effective(doc, input.key),
            source: if override_value.is_null() {
                "embedded:niri/default.kdl and niri input defaults"
            } else {
                "settings.toml"
            },
            override_value,
            default: input.default.clone(),
            value_type: input.kind,
            allowed: input.allowed,
            application: "managed niri fragment; acknowledged session reload",
            validation: "typed range; niri configuration validation",
            undo: UNDO,
        });
    }
    for shortcut in shortcuts() {
        let override_value = doc
            .shortcuts
            .get(&shortcut.chord)
            .map_or(Value::Null, |s| json!(s));
        rows.push(Setting {
            key: &shortcut.key, title: Some(shortcut.label.clone()),
            explanation: Some(if shortcut.chord == "Mod+Ctrl+Escape" { "Reserved for revoking administrator access.".into() } else { "Choose the key combination for this action.".into() }),
            editable: shortcut.chord != "Mod+Ctrl+Escape", secret: false,
            declared_by: None, machine_reason: None,
            value: doc.shortcuts.get(&shortcut.chord).map_or_else(|| json!(shortcut.chord), |s| json!(s)),
            source: if override_value.is_null() { "embedded:niri/default.kdl" } else { "settings.toml" },
            override_value, default: json!(shortcut.chord), value_type: "key_chord",
            allowed: "Super/Ctrl/Alt/Shift and one XKB key; F1–F24, XF86 keys and Print may stand alone; Super+Ctrl+Esc is reserved",
            application: "managed niri fragment; acknowledged session reload", validation: "shortcut conflict check; niri keysym validation", undo: UNDO,
        });
    }
}

fn check_floating(doc: &Document, value: &str) -> Result<()> {
    let value = canonical(value)?;
    if value.eq_ignore_ascii_case("Mod+Ctrl+Escape") {
        return Err(err(
            "Super+Ctrl+Esc is reserved for revoking administrator access.",
        ));
    }
    if ["Mod+Tab", "Mod+Shift+Tab", "Alt+Tab", "Alt+Shift+Tab"].contains(&value.as_str())
        || shortcuts().iter().any(|s| {
            doc.shortcuts
                .get(&s.chord)
                .unwrap_or(&s.chord)
                .eq_ignore_ascii_case(&value)
        })
    {
        return Err(err(
            "That shortcut is already in use. Choose another key combination.",
        ));
    }
    Ok(())
}

pub(super) fn validate(doc: &Document) -> Result<()> {
    if doc
        .input
        .keys()
        .any(|key| !inputs().iter().any(|field| field.key == key))
    {
        return Err(err("unknown_setting"));
    }
    // These compositor switcher chords exist even without a packaged binds entry.
    let mut used: BTreeSet<String> = ["Mod+Tab", "Mod+Shift+Tab", "Alt+Tab", "Alt+Shift+Tab"]
        .into_iter()
        .filter(|chord| !shortcuts().iter().any(|s| s.chord == *chord))
        .map(str::to_ascii_lowercase)
        .collect();
    for shortcut in shortcuts() {
        let value = doc
            .shortcuts
            .get(&shortcut.chord)
            .unwrap_or(&shortcut.chord);
        if !used.insert(value.to_ascii_lowercase()) {
            return Err(err(
                "That shortcut is already in use. Choose another key combination.",
            ));
        }
    }
    if let Some(value) = &doc.keybindings.toggle_window_floating {
        check_floating(doc, value)?;
    }
    Ok(())
}

// Personal configuration may include arbitrary files. Refuse rather than replacing
// a device block whose complete effective contents cannot be recovered safely.
fn personal_config(profile: &Profile) -> bool {
    let root = profile.config.parent().expect("config parent");
    let mut paths = vec![
        root.join("niri/config.kdl"),
        root.join("emaki/niri-emaki.kdl"),
    ];
    if profile.installed {
        if let Some(home) = std::env::var_os("HOME") {
            paths.push(PathBuf::from(&home).join(".config/niri/config.kdl"));
            paths.push(PathBuf::from(home).join(".config/emaki/niri-emaki.kdl"));
        }
    }
    paths.iter().any(|path| match fs::read_to_string(path) {
        Ok(text) => !safe_package_entry(&text),
        Err(error) => {
            error.kind() != std::io::ErrorKind::NotFound || fs::symlink_metadata(path).is_ok()
        }
    })
}
// Recognize package includes and the installer's output-scale grammar only.
// Unknown includes or declarations remain personal; no general KDL is rewritten.
fn safe_package_entry(text: &str) -> bool {
    let system = Path::new("/etc").join("emaki/niri.kdl");
    let package = data_dir().join("niri/default.kdl");
    let mut blocks = Vec::new();
    for line in text.lines().map(str::trim) {
        if line.is_empty() || line.starts_with("//") {
            continue;
        }
        if blocks.is_empty() {
            if line == format!("include \"{}\"", system.display())
                || line == format!("include \"{}\"", package.display())
            {
                continue;
            }
            if let Some(name) = line
                .strip_prefix("output ")
                .and_then(|s| s.strip_suffix(" {"))
            {
                if serde_json::from_str::<String>(name).is_ok() {
                    blocks.push("output");
                    continue;
                }
            }
        } else {
            if line == "}" {
                blocks.pop();
                continue;
            }
            let current = *blocks.last().unwrap();
            if (current == "output" && line == "layout {")
                || (current == "layout" && line == "struts {")
            {
                blocks.push(if current == "output" {
                    "layout"
                } else {
                    "struts"
                });
                continue;
            }
            let number = match current {
                "output" => line.strip_prefix("scale "),
                "struts" => line.strip_prefix("top "),
                _ => None,
            };
            if number.is_some_and(|s| s.parse::<f64>().is_ok_and(f64::is_finite)) {
                continue;
            }
        }
        return false;
    }
    blocks.is_empty()
}
fn personal_device(key: &str) -> bool {
    key.starts_with("mouse.") || key.starts_with("touchpad.")
}
pub(super) fn guard_personal(profile: &Profile, before: &Document, after: &Document) -> Result<()> {
    if personal_config(profile)
        && after
            .input
            .iter()
            .any(|(key, value)| personal_device(key) && before.input.get(key) != Some(value))
    {
        return Err(err(
            "Mouse and trackpad settings are controlled by your personal compositor configuration.",
        ));
    }
    if personal_config(profile)
        && (after.shortcuts != before.shortcuts && !after.shortcuts.is_empty()
            || after.keybindings.toggle_window_floating
                != before.keybindings.toggle_window_floating
                && after.keybindings.toggle_window_floating.is_some())
    {
        return Err(err(
            "Shortcuts are controlled by your personal compositor configuration.",
        ));
    }
    Ok(())
}
pub(super) fn personal_rows(profile: &Profile, rows: &mut [Setting]) {
    if profile.installed {
        let devices = fs::read_to_string("/proc/bus/input/devices")
            .ok()
            .map(|text| device_presence(&text));
        for row in rows.iter_mut().filter(|row| personal_device(row.key)) {
            let present = devices.map(|(mouse, touchpad)| {
                if row.key.starts_with("mouse.") {
                    mouse
                } else {
                    touchpad
                }
            });
            if present != Some(true) {
                row.editable = false;
                row.value = Value::Null;
                row.explanation = Some(
                    match (present, row.key.starts_with("mouse.")) {
                        (None, _) => "Connected pointing devices could not be checked.",
                        (_, true) => "No mouse is connected.",
                        (_, false) => "No trackpad is connected.",
                    }
                    .into(),
                );
            }
        }
    }
    if personal_config(profile) {
        for row in rows.iter_mut().filter(|row| {
            personal_device(row.key) || row.key.starts_with("shortcuts.") || row.key == FLOATING
        }) {
            row.editable = false;
            row.value = Value::Null;
            row.explanation = Some(if personal_device(row.key) {
                "Mouse and trackpad settings are controlled by your personal compositor configuration."
            } else {
                "Shortcuts are controlled by your personal compositor configuration."
            }.into());
        }
    }
}

pub(super) fn protect_personal(profile: &Profile, doc: &mut Document) {
    if personal_config(profile) {
        doc.input.retain(|key, _| !personal_device(key));
        doc.shortcuts.clear();
        doc.keybindings.toggle_window_floating = None;
    }
}

fn device_presence(text: &str) -> (bool, bool) {
    let bit = |block: &str, name: &str, bit: usize| {
        block
            .lines()
            .find_map(|line| line.strip_prefix(name))
            .and_then(|bits| {
                bits.split_whitespace()
                    .rev()
                    .nth(bit / usize::BITS as usize)
            })
            .and_then(|word| usize::from_str_radix(word, 16).ok())
            .is_some_and(|word| word & (1 << (bit % usize::BITS as usize)) != 0)
    };
    let mut mouse = false;
    let mut touchpad = false;
    for block in text.split("\n\n") {
        // BTN_LEFT with REL_X/REL_Y; BTN_TOOL_FINGER with ABS_X/ABS_Y.
        mouse |=
            bit(block, "B: KEY=", 0x110) && bit(block, "B: REL=", 0) && bit(block, "B: REL=", 1);
        touchpad |=
            bit(block, "B: KEY=", 0x145) && bit(block, "B: ABS=", 0) && bit(block, "B: ABS=", 1);
    }
    (mouse, touchpad)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn ordinary_shortcuts_require_more_than_shift() {
        for chord in [
            "T",
            "Shift+T",
            "shift+t",
            "Shift+1",
            "Shift+space",
            "Shift+Left",
            "F25",
        ] {
            assert!(canonical(chord).is_err(), "{chord}");
        }
        for chord in [
            "Super+Shift+T",
            "Ctrl+T",
            "Alt+Shift+T",
            "F1",
            "F24",
            "Shift+F7",
            "Print",
            "Shift+Print",
            "XF86AudioMute",
            "Shift+XF86AudioMute",
        ] {
            assert!(canonical(chord).is_ok(), "{chord}");
        }
    }
    #[test]
    fn installer_entry_is_safe_but_personal_includes_and_devices_are_not() {
        let entry = "include \"/etc/emaki/niri.kdl\"\n\n// Installation defaults\noutput \"Panel\" {\n scale 1.25\n layout {\n struts {\n top -2.4\n }\n }\n}\n";
        assert!(safe_package_entry(entry));
        for extra in [
            "include \"personal.kdl\"",
            "input { touchpad { off; }; }",
            "binds { Mod+B { spawn \"browser\"; }; }",
        ] {
            assert!(!safe_package_entry(&format!("{entry}{extra}")));
        }
        assert!(!safe_package_entry(
            "output \"Panel\" {\n include \"personal.kdl\"\n}"
        ));
        assert!(!safe_package_entry("output \"Panel\" {"));
    }
    #[test]
    fn pointing_devices_use_capabilities_and_update_after_removal() {
        let key = |bit: usize| {
            let mut words = vec![0usize; bit / usize::BITS as usize + 1];
            words[bit / usize::BITS as usize] = 1 << (bit % usize::BITS as usize);
            words
                .iter()
                .rev()
                .map(|word| format!("{word:x}"))
                .collect::<Vec<_>>()
                .join(" ")
        };
        let mouse = format!("N: Name=Unlabelled\nB: KEY={}\nB: REL=3", key(0x110));
        let touchpad = format!("N: Name=Unlabelled\nB: KEY={}\nB: ABS=3", key(0x145));
        assert_eq!(device_presence(""), (false, false));
        assert_eq!(device_presence(&mouse), (true, false));
        assert_eq!(device_presence(&touchpad), (false, true));
        assert_eq!(
            device_presence(&format!("{mouse}\n\n{touchpad}")),
            (true, true)
        );
    }
}
