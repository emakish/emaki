// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
//! Installed keyboard keys belong to the machine: their one place is the machine-settings
//! provider, a fixed command another platform implements with the same JSON answers.
use super::*;
use crate::transport::helper;
use std::time::Instant;

const PROVIDER: &str = "emaki-machine-settings";
pub(super) const KEYS: [&str; 2] = [LAYOUTS, SWITCH_KEY];
/// A machine change can wait for another change's lock and an administrator prompt; the
/// provider's own budget (10 s lock wait, 90 s D-Bus call, reads) fits inside it.
const CHANGE_TIMEOUT: Duration = Duration::from_secs(120);
/// Reasons the provider may answer; anything else is reported as one fixed reason.
const REASONS: &[&str] = &[
    "authorization_refused",
    "authorization_required",
    "declarations_invalid",
    "declared_by_system_configuration",
    "invalid_layouts",
    "invalid_switch_key",
    "invalid_value",
    "layouts_unrecognised",
    "machine_service_timeout",
    "machine_service_unavailable",
    "machine_settings_busy",
    "machine_setting_read_only",
    "machine_setting_unconfirmed",
    "machine_value_refused",
    "switch_key_unrecognised",
    "unknown_setting",
];
fn reason(value: &Value) -> &'static str {
    value
        .as_str()
        .and_then(|text| REASONS.iter().find(|known| **known == text))
        .copied()
        .unwrap_or("machine_setting_rejected")
}
fn source(value: &Value) -> &'static str {
    match value.as_str() {
        Some("machine") => "machine",
        Some("declared") => "declared",
        _ => "unavailable",
    }
}
fn query(args: &[&str], deadline: Instant) -> std::result::Result<Value, &'static str> {
    let output = helper(PROVIDER, args, deadline).map_err(|failure| match failure.reason {
        "helper_missing" => "machine_settings_unavailable",
        "deadline_exceeded" => "machine_service_timeout",
        _ => "machine_service_unavailable",
    })?;
    let reply: Value =
        serde_json::from_slice(&output.stdout).map_err(|_| "machine_settings_invalid_reply")?;
    if reply["schema_version"] != 1 {
        return Err("machine_settings_invalid_reply");
    }
    Ok(reply)
}

/// Replace the keyboard rows of a reply with the machine's values, source and editability.
pub(super) fn overlay(settings: &mut [Setting], timeout: Duration) {
    let keys: Vec<&str> = settings
        .iter()
        .map(|row| row.key)
        .filter(|key| KEYS.contains(key))
        .collect();
    if keys.is_empty() {
        return;
    }
    let mut args = vec!["get"];
    args.extend(&keys);
    args.push("--json");
    let reply = query(&args, Instant::now() + timeout);
    for row in settings.iter_mut().filter(|row| KEYS.contains(&row.key)) {
        row.override_value = Value::Null;
        let machine = reply.as_ref().ok().and_then(|reply| {
            reply["settings"]
                .as_array()?
                .iter()
                .find(|machine| machine["key"] == row.key)
        });
        match machine {
            Some(machine) => {
                row.value = machine["value"].clone();
                row.source = source(&machine["source"]);
                row.editable = machine["editable"] == true && row.source == "machine";
                row.declared_by = machine["declared_by"]
                    .as_str()
                    .map(|option| option.chars().take(200).collect());
                row.machine_reason =
                    (!machine["reason"].is_null()).then(|| reason(&machine["reason"]));
            }
            None => {
                row.value = Value::Null;
                row.source = "unavailable";
                row.editable = false;
                row.declared_by = None;
                row.machine_reason = Some(match &reply {
                    Err(why) => why,
                    Ok(_) => "machine_settings_invalid_reply",
                });
            }
        }
        row.application = "machine settings: emaki-machine-settings (systemd-localed); applied live by the session";
    }
}

/// Installed layouts: the provider's own form, so every value it reports (a `before` among
/// them) can be set again: 1..4 distinct `layout` or `layout(variant)` items, layouts and
/// variants listed in the installed XKB rules; `dvorak`/`colemak` stand for the us variants.
/// Isolated profiles keep the plain codes of `layouts`.
fn machine_layouts(value: &str) -> Result<()> {
    let items: Vec<&str> = value.split(',').collect();
    if items.len() > 4 || items.iter().collect::<BTreeSet<_>>().len() != items.len() {
        return Err(err("invalid_layouts"));
    }
    let word = |text: &str, low: usize, high: usize, upper: bool| {
        (low..=high).contains(&text.len())
            && text.bytes().all(|b| {
                b.is_ascii_lowercase()
                    || (upper && (b.is_ascii_alphanumeric() || b == b'_' || b == b'-'))
            })
    };
    let (known, variants) = (xkb_layouts()?, xkb_variants()?);
    for item in items {
        let (layout, variant) = match item.strip_suffix(')').and_then(|rest| rest.split_once('(')) {
            Some((layout, variant)) => (layout, Some(variant)),
            None => (item, None),
        };
        if !word(layout, 2, 8, false) || !variant.is_none_or(|v| word(v, 1, 32, true)) {
            return Err(err("invalid_layouts"));
        }
        let listed = match variant {
            Some(variant) => variants.contains(&(layout.to_owned(), variant.to_owned())),
            None => {
                known.contains(layout) || (US_VARIANTS.contains(&layout) && known.contains("us"))
            }
        };
        if !listed {
            return Err(err("unknown_layout"));
        }
    }
    Ok(())
}

/// What a machine change did: the core status and reason, the `machine_change` record and,
/// when the outcome is uncertain, how to look and go back.
pub(super) struct Change {
    pub status: &'static str,
    pub reason: &'static str,
    pub record: Value,
    pub recovery: Vec<String>,
}

/// Validate with the core's own checks, then let the provider change the machine.
/// `Err` only when the machine was not changed; a change that may have happened but is not
/// confirmed is `uncertain`, with before, requested and after kept.
pub(super) fn change(key: &str, value: Option<&str>) -> Result<Change> {
    let value = match value.filter(|value| !value.is_empty()) {
        Some(value) if key == LAYOUTS => {
            machine_layouts(value)?;
            value
        }
        Some(value) => {
            Document::default().set(key, value)?;
            value
        }
        // Reset: the package default switch key; layouts have no package default.
        None if key == SWITCH_KEY => SWITCH_KEYS[0],
        None => return Err(err("machine_setting_has_no_default")),
    };
    let deadline = Instant::now() + CHANGE_TIMEOUT;
    // Read first, so a provider that dies or overruns mid-change still leaves the way back.
    let read = query(&["get", key, "--json"], deadline).ok();
    let before = read
        .as_ref()
        .and_then(|reply| reply["settings"].as_array()?.first().cloned())
        .filter(|row| row["key"] == key)
        .map_or(Value::Null, |row| row["value"].clone());
    let requested = if key == LAYOUTS {
        json!(value.split(',').collect::<Vec<_>>())
    } else {
        json!(value)
    };
    let uncertain =
        |reason: &'static str, before: &Value, requested: &Value, after: &Value| Change {
            status: "uncertain",
            reason,
            record: json!({"key": key, "before": before, "requested": requested, "after": after}),
            recovery: vec![
                format!("read the machine: emaki-machine-settings get {key} --json"),
                "set the before value again to go back once that read shows the change; \
                 do not repeat the change blindly"
                    .into(),
            ],
        };
    let reply = match query(&["set", key, value, "--json"], deadline) {
        Ok(reply) => reply,
        // Never started: the machine is untouched.
        Err("machine_settings_unavailable") => return Err(err("machine_settings_unavailable")),
        // Started, then overran or died: it may have changed the machine.
        Err(why) => return Ok(uncertain(why, &before, &requested, &Value::Null)),
    };
    let settled = |status, reason| Change {
        status,
        reason,
        record: json!({"key": key, "before": reply["before"], "after": reply["after"]}),
        recovery: vec![],
    };
    match (reply["status"].as_str(), reply["key"] == key) {
        (Some("rejected"), _) => Err(err(reason(&reply["reason"]))),
        (Some("applied"), true) => Ok(settled("committed", "machine_setting_applied")),
        (Some("unchanged"), true) => Ok(settled("unchanged", "machine_setting_unchanged")),
        (Some("uncertain"), true) => Ok(uncertain(
            match reason(&reply["reason"]) {
                "machine_setting_rejected" => "machine_setting_unconfirmed",
                known => known,
            },
            &reply["before"],
            &reply["requested"],
            &reply["after"],
        )),
        _ => Ok(uncertain(
            "machine_settings_invalid_reply",
            &before,
            &requested,
            &Value::Null,
        )),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // Every layout(variant) the installed rules list is a value the provider can report as
    // `before`; the core must take each of them back.
    #[test]
    fn every_listed_variant_is_accepted_back() {
        let variants = xkb_variants().unwrap();
        assert!(variants.len() > 100, "{}", variants.len());
        for (layout, variant) in variants {
            if (2..=8).contains(&layout.len()) && layout.bytes().all(|b| b.is_ascii_lowercase()) {
                let value = format!("{layout}({variant})");
                assert!(machine_layouts(&value).is_ok(), "{value}");
            }
        }
        for (value, reason) in [
            ("us(nosuchvariant)", "unknown_layout"),
            ("ru(intl)", "unknown_layout"),
            ("us(intl", "invalid_layouts"),
            ("us(in tl)", "invalid_layouts"),
            ("us(intl),us(intl)", "invalid_layouts"),
            ("us,ru,de,fr,ua", "invalid_layouts"),
        ] {
            assert_eq!(
                machine_layouts(value).unwrap_err().reason,
                reason,
                "{value}"
            );
        }
        assert!(machine_layouts("dvorak,us(intl),de(T3)").is_ok());
    }
}
