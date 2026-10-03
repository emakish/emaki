//! Expected locations and ownership; never stat, create or rewrite user files.
use serde::Serialize;
use std::ffi::OsString;
use std::path::{Path, PathBuf};

#[derive(Debug, Serialize)]
pub struct Entry {
    pub id: &'static str,
    pub owner: &'static str,
    pub path: Option<PathBuf>,
    pub source: String,
    pub lifecycle: &'static str,
}

#[derive(Debug, Serialize)]
pub struct Map {
    pub schema_version: u32,
    pub emaki_version: &'static str,
    pub entries: Vec<Entry>,
}

fn absolute(value: Option<OsString>) -> Option<PathBuf> {
    value.map(PathBuf::from).filter(|path| path.is_absolute())
}

fn xdg(key: &str, fallback: Option<&str>) -> (Option<PathBuf>, String) {
    if let Some(path) = absolute(std::env::var_os(key)) {
        return (Some(path), key.into());
    }
    let path = fallback
        .and_then(|suffix| absolute(std::env::var_os("HOME")).map(|home| home.join(suffix)));
    let source = match (&path, fallback) {
        (Some(_), Some(suffix)) => format!("HOME/{suffix}; {key} unset, empty or relative"),
        _ => format!("unresolved: {key} must be absolute; no implicit runtime directory"),
    };
    (path, source)
}

fn packaged(key: &str, value: Option<&str>, default: PathBuf) -> (Option<PathBuf>, String) {
    let path = value.map(PathBuf::from).unwrap_or(default);
    let source = format!("build:{key}; expected location, existence not probed");
    (path.is_absolute().then_some(path), source)
}

pub fn collect() -> Map {
    let prefix = option_env!("EMAKI_PREFIX").unwrap_or("/usr");
    let (data, data_source) = packaged(
        "EMAKI_DATADIR/EMAKI_PREFIX",
        option_env!("EMAKI_DATADIR"),
        Path::new(prefix).join("share/emaki"),
    );
    let (libexec, libexec_source) = packaged(
        "EMAKI_LIBEXECDIR/EMAKI_PREFIX",
        option_env!("EMAKI_LIBEXECDIR"),
        Path::new(prefix).join("libexec/emaki"),
    );
    let (sysconf, sysconf_source) = packaged(
        "EMAKI_SYSCONFDIR",
        option_env!("EMAKI_SYSCONFDIR"),
        PathBuf::from("/etc"),
    );
    let (config, config_source) = xdg("XDG_CONFIG_HOME", Some(".config"));
    let (state, state_source) = xdg("XDG_STATE_HOME", Some(".local/state"));
    let (runtime, runtime_source) = xdg("XDG_RUNTIME_DIR", None);
    let mut entries = Vec::new();
    let mut add = |id, owner, root: &Option<PathBuf>, suffix: &str, source: &str, lifecycle| {
        entries.push(Entry {
            id,
            owner,
            path: root.as_ref().map(|p| p.join(suffix)),
            source: source.into(),
            lifecycle,
        });
    };
    add(
        "package_defaults",
        "emaki_package",
        &data,
        "",
        &data_source,
        "existing_layout",
    );
    add(
        "niri_defaults",
        "emaki_package",
        &data,
        "niri/default.kdl",
        &data_source,
        "existing_layout",
    );
    add(
        "system_xdg",
        "distribution_and_packages",
        &sysconf,
        "xdg",
        &sysconf_source,
        "existing_layout",
    );
    add(
        "adapters",
        "distribution_adapter",
        &libexec,
        "adapters",
        &libexec_source,
        "planned",
    );
    add(
        "personal_niri",
        "user",
        &config,
        "niri/config.kdl",
        &config_source,
        "existing_layout",
    );
    add(
        "emaki_settings",
        "emaki_core_and_user",
        &config,
        "emaki/settings.toml",
        &config_source,
        "isolated_profile_only",
    );
    add(
        "cases",
        "emaki_core_and_user",
        &config,
        "emaki/cases",
        &config_source,
        "planned",
    );
    add(
        "managed_generations",
        "emaki_generator",
        &state,
        "emaki/generations",
        &state_source,
        "isolated_profile_only",
    );
    add(
        "history",
        "emaki_core",
        &state,
        "emaki/history",
        &state_source,
        "isolated_profile_only",
    );
    add(
        "runtime",
        "emaki_core",
        &runtime,
        "emaki",
        &runtime_source,
        "planned",
    );
    Map {
        schema_version: crate::MAP_SCHEMA_VERSION,
        emaki_version: crate::VERSION,
        entries,
    }
}

impl Map {
    pub fn exit_code(&self) -> u8 {
        u8::from(self.entries.iter().any(|e| e.path.is_none()))
    }

    pub fn human(&self) -> String {
        let mut text = format!(
            "Emaki {} — map format {} (expected paths, not an installed-file inventory)\n",
            self.emaki_version, self.schema_version
        );
        for entry in &self.entries {
            // Debug formatting escapes embedded newlines/control characters in paths.
            let path = entry
                .path
                .as_ref()
                .map(|p| format!("{p:?}"))
                .unwrap_or("unresolved".into());
            text.push_str(&format!(
                "{}: {}\n  owner: {}; {}; source: {}\n",
                entry.id, path, entry.owner, entry.lifecycle, entry.source
            ));
        }
        text
    }
}
