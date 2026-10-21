// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
//! Bounded GNU gettext catalogue loading for human command-line output.
use std::collections::HashMap;
use std::env;
use std::fs::File;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

const MAX_CATALOGUE: u64 = 8 * 1024 * 1024;
static CATALOGUE: OnceLock<HashMap<String, String>> = OnceLock::new();

fn parse(bytes: &[u8]) -> Option<HashMap<String, String>> {
    let magic: [u8; 4] = bytes.get(..4)?.try_into().ok()?;
    let little = match magic {
        [0xde, 0x12, 0x04, 0x95] => true,
        [0x95, 0x04, 0x12, 0xde] => false,
        _ => return None,
    };
    let number = |offset: usize| -> Option<usize> {
        let value = bytes.get(offset..offset.checked_add(4)?)?.try_into().ok()?;
        Some(if little {
            u32::from_le_bytes(value)
        } else {
            u32::from_be_bytes(value)
        } as usize)
    };
    if number(4)? >> 16 != 0 {
        return None;
    }
    let count = number(8)?;
    let originals = number(12)?;
    let translated = number(16)?;
    let table_len = count.checked_mul(8)?;
    bytes.get(originals..originals.checked_add(table_len)?)?;
    bytes.get(translated..translated.checked_add(table_len)?)?;
    let string = |offset: usize| -> Option<&str> {
        let length = number(offset)?;
        let start = number(offset.checked_add(4)?)?;
        let end = start.checked_add(length)?;
        if *bytes.get(end)? != 0 {
            return None;
        }
        std::str::from_utf8(bytes.get(start..end)?).ok()
    };
    let mut result = HashMap::new();
    let mut decoded_bytes = 0usize;
    for index in 0..count {
        let source = string(originals + index * 8)?;
        let target = string(translated + index * 8)?;
        decoded_bytes = decoded_bytes
            .checked_add(source.len())?
            .checked_add(target.len())?;
        if decoded_bytes > MAX_CATALOGUE as usize {
            return None;
        }
        // The CLI currently has no plural sentences; never expose plural payloads.
        if !source.is_empty()
            && !source.contains('\0')
            && !target.is_empty()
            && !target.contains('\0')
        {
            result.insert(source.to_owned(), target.to_owned());
        }
    }
    Some(result)
}

fn load() -> HashMap<String, String> {
    let directory = env::var_os("EMAKI_LOCALE_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            PathBuf::from(
                option_env!("EMAKI_DATADIR")
                    .unwrap_or(concat!(env!("CARGO_MANIFEST_DIR"), "/../../i18n")),
            )
            .join("locale")
        });
    load_directory(&directory, &languages(|key| env::var(key).ok()))
}

fn load_directory(directory: &Path, preferences: &[String]) -> HashMap<String, String> {
    let mut result = HashMap::new();
    for preference in preferences {
        let name = preference.split('.').next().unwrap_or("");
        if name.is_empty()
            || !name
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || b"_@-".contains(&byte))
        {
            continue;
        }
        if matches!(name, "C" | "POSIX") {
            break;
        }
        for locale in [name, name.split('_').next().unwrap_or(name)] {
            let path = directory.join(locale).join("LC_MESSAGES/cli.mo");
            let Ok(file) = File::open(path) else {
                continue;
            };
            let mut bytes = Vec::new();
            if file.take(MAX_CATALOGUE + 1).read_to_end(&mut bytes).is_ok()
                && bytes.len() <= MAX_CATALOGUE as usize
                && let Some(catalogue) = parse(&bytes)
            {
                for (source, target) in catalogue {
                    result.entry(source).or_insert(target);
                }
            }
        }
    }
    result
}

// The explicit fixture override is separate from the session's gettext policy.
fn languages(value: impl Fn(&str) -> Option<String>) -> Vec<String> {
    let nonempty = |key| value(key).filter(|value| !value.is_empty());
    if let Some(language) = nonempty("EMAKI_LANGUAGE") {
        return language.split(':').map(str::to_owned).collect();
    }
    let locale = ["LC_ALL", "LC_MESSAGES", "LANG"]
        .iter()
        .find_map(|key| nonempty(key))
        .unwrap_or_else(|| "C".into());
    if matches!(locale.split('.').next().unwrap_or(""), "C" | "POSIX") {
        return vec!["C".into()];
    }
    let mut result: Vec<String> = nonempty("LANGUAGE")
        .unwrap_or_default()
        .split(':')
        .filter(|item| !item.is_empty())
        .map(str::to_owned)
        .collect();
    result.push(locale);
    result
}

/// Translate a source message; absent or invalid catalogues preserve English.
pub fn tr(source: &str) -> String {
    CATALOGUE
        .get_or_init(load)
        .get(source)
        .map_or_else(|| source.to_owned(), Clone::clone)
}

/// Substitute named placeholders once, without interpreting inserted values.
pub fn format_text(source: &str, values: &[(&str, String)]) -> String {
    let translated = tr(source);
    let mut output = String::new();
    let mut remaining = translated.as_str();
    while let Some(start) = remaining.find('{') {
        output.push_str(&remaining[..start]);
        remaining = &remaining[start..];
        let Some(end) = remaining.find('}') else {
            break;
        };
        let key = &remaining[1..end];
        if let Some((_, value)) = values.iter().find(|(name, _)| *name == key) {
            output.push_str(value);
        } else {
            output.push_str(&remaining[..=end]);
        }
        remaining = &remaining[end + 1..];
    }
    output.push_str(remaining);
    output
}

#[cfg(test)]
mod tests {
    use super::*;

    fn catalogue(little: bool) -> Vec<u8> {
        let words: [u32; 11] = [0x9504_12de, 0, 1, 28, 36, 0, 0, 5, 44, 5, 50];
        let mut bytes = Vec::new();
        for word in words {
            bytes.extend(if little {
                word.to_le_bytes()
            } else {
                word.to_be_bytes()
            });
        }
        bytes.extend(b"Hello\0Hola!\0");
        bytes
    }

    #[test]
    fn partial_catalogues_follow_the_preference_chain() {
        let directory = env::temp_dir().join(format!("emaki-catalogue-{}", std::process::id()));
        std::fs::create_dir(&directory).unwrap();
        for (language, source, target) in [("fr", b"Hello", b"Salut"), ("de", b"Other", b"Ander")] {
            let folder = directory.join(language).join("LC_MESSAGES");
            std::fs::create_dir_all(&folder).unwrap();
            let mut bytes = catalogue(true);
            bytes[44..49].copy_from_slice(source);
            bytes[50..55].copy_from_slice(target);
            std::fs::write(folder.join("cli.mo"), bytes).unwrap();
        }
        let result = load_directory(&directory, &["fr_FR.UTF-8".into(), "de".into()]);
        assert_eq!(result.get("Hello").map(String::as_str), Some("Salut"));
        assert_eq!(result.get("Other").map(String::as_str), Some("Ander"));
        assert!(
            !load_directory(&directory, &["fr".into(), "C".into(), "de".into()])
                .contains_key("Other")
        );
        std::fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn locale_preferences_obey_c_and_explicit_override() {
        for (environment, expected) in [
            (
                vec![("LANG", "en_US.UTF-8"), ("LANGUAGE", "fr:de")],
                vec!["fr", "de", "en_US.UTF-8"],
            ),
            (vec![("LC_ALL", "C.UTF-8"), ("LANGUAGE", "fr")], vec!["C"]),
            (vec![("LC_MESSAGES", "POSIX"), ("LANG", "fr_FR")], vec!["C"]),
            (
                vec![("LC_ALL", "C"), ("EMAKI_LANGUAGE", "en_XA")],
                vec!["en_XA"],
            ),
            (vec![], vec!["C"]),
        ] {
            assert_eq!(
                languages(|key| environment
                    .iter()
                    .find(|(name, _)| *name == key)
                    .map(|(_, value)| (*value).to_owned())),
                expected
            );
        }
    }

    #[test]
    fn compiled_portuguese_catalogues_follow_preferences_and_preserve_english() {
        let sources = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../i18n/cli");
        let directory =
            env::temp_dir().join(format!("emaki-portuguese-catalogue-{}", std::process::id()));
        std::fs::create_dir(&directory).unwrap();
        for locale in ["pt_BR", "en"] {
            let folder = directory.join(locale).join("LC_MESSAGES");
            std::fs::create_dir_all(&folder).unwrap();
            assert!(
                std::process::Command::new("msgfmt")
                    .arg("--check")
                    .arg("-o")
                    .arg(folder.join("cli.mo"))
                    .arg(sources.join(format!("{locale}.po")))
                    .status()
                    .expect("msgfmt is required for catalogue tests")
                    .success()
            );
        }
        std::fs::create_dir_all(directory.join("pt/LC_MESSAGES")).unwrap();
        std::fs::copy(
            directory.join("pt_BR/LC_MESSAGES/cli.mo"),
            directory.join("pt/LC_MESSAGES/cli.mo"),
        )
        .unwrap();
        let source = "Cannot read terminal settings";
        let portuguese = "Não é possível ler as configurações do terminal";
        for locale in ["pt_BR", "pt"] {
            let bytes = std::fs::read(directory.join(locale).join("LC_MESSAGES/cli.mo"))
                .expect("The compiled catalogue must be readable");
            assert_eq!(
                parse(&bytes).unwrap().get(source).map(String::as_str),
                Some(portuguese)
            );
        }
        for (environment, expected) in [
            (vec![("LANG", "pt_BR.UTF-8")], portuguese),
            (vec![("LANG", "pt")], portuguese),
            (vec![("LANG", "pt_PT.UTF-8")], portuguese),
            (
                vec![("LANG", "en_US.UTF-8"), ("LANGUAGE", "zz:pt:en")],
                portuguese,
            ),
            (vec![("LANG", "pt_BR.UTF-8"), ("LANGUAGE", "en:pt")], source),
            (vec![("LC_ALL", "C.UTF-8"), ("LANGUAGE", "pt")], source),
            (vec![("LC_ALL", "C"), ("EMAKI_LANGUAGE", "pt")], portuguese),
            (vec![("LANG", "en_US.UTF-8")], source),
        ] {
            let preferences = languages(|key| {
                environment
                    .iter()
                    .find(|(name, _)| *name == key)
                    .map(|(_, value)| (*value).to_owned())
            });
            let catalogue = load_directory(&directory, &preferences);
            assert_eq!(
                catalogue.get(source).map_or(source, String::as_str),
                expected,
                "environment: {environment:?}"
            );
            assert_eq!(
                catalogue
                    .get("Uncatalogued message {value}")
                    .map_or("Uncatalogued message {value}", String::as_str),
                "Uncatalogued message {value}"
            );
        }
        std::fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn standard_byte_orders_load_and_bad_offsets_fail() {
        for little in [true, false] {
            let bytes = catalogue(little);
            assert_eq!(
                parse(&bytes).unwrap().get("Hello").map(String::as_str),
                Some("Hola!")
            );
            for end in 0..bytes.len() {
                assert!(parse(&bytes[..end]).is_none());
            }
        }
        let mut bytes = catalogue(true);
        bytes[40..44].copy_from_slice(&u32::MAX.to_le_bytes());
        assert!(parse(&bytes).is_none());
    }

    #[test]
    fn malformed_catalogues_are_rejected() {
        assert!(parse(&[]).is_none());
        let mut bytes = vec![0; 28];
        bytes[..4].copy_from_slice(&0x9504_12de_u32.to_le_bytes());
        bytes[8..12].copy_from_slice(&u32::MAX.to_le_bytes());
        assert!(parse(&bytes).is_none());
    }

    #[test]
    fn substitution_does_not_reinterpret_values() {
        assert_eq!(
            format_text(
                "{first}: {second}",
                &[("first", "{second}".into()), ("second", "done".into())]
            ),
            "{second}: done"
        );
    }
}
