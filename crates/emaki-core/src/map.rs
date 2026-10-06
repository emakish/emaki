//! Expected locations and ownership; never stat, create or rewrite user files.
//!
//! Every entry names its zone as docs/ZONES.md defines it (installed as
//! `<prefix>/share/doc/emaki/ZONES.md`, entry `zones_doc`): `package`, `managed` or `yours`.
//! Owner, way of change, what an update does and history are attributes, never more zones.
use serde::Serialize;
use std::ffi::OsString;
use std::path::{Path, PathBuf};

#[derive(Debug, Serialize)]
pub struct Entry {
    pub id: &'static str,
    pub zone: &'static str,
    pub owner: &'static str,
    pub path: Option<PathBuf>,
    pub source: String,
    pub lifecycle: &'static str,
    pub change_via: &'static str,
    pub on_update: &'static str,
    pub history: &'static str,
}

#[derive(Debug, Serialize)]
pub struct Map {
    pub schema_version: u32,
    pub emaki_version: &'static str,
    pub entries: Vec<Entry>,
}

/// A zone with its attributes; one per kind of file on the zone page.
#[derive(Clone, Copy)]
struct Rules {
    zone: &'static str,
    change_via: &'static str,
    on_update: &'static str,
    history: &'static str,
}

/// Package files: an update replaces them whole, an edit is lost.
const PACKAGE: Rules = Rules {
    zone: "package",
    change_via: "not_by_hand",
    on_update: "replaced",
    history: "none",
};
/// Package files in /etc that pacman protects (backup=): an edit stays, the new file
/// is saved next to it as .pacnew.
const PACKAGE_ETC: Rules = Rules {
    zone: "package",
    change_via: "administrator",
    on_update: "replaced_unless_edited",
    history: "none",
};
/// `emaki settings` files; not connected to the session yet (lifecycle says so).
const MANAGED: Rules = Rules {
    zone: "managed",
    change_via: "emaki_settings",
    on_update: "never_touched",
    history: "emaki_settings",
};
/// The core's own runtime directory: a lock, nothing to undo.
const MANAGED_RUNTIME: Rules = Rules {
    history: "none",
    ..MANAGED
};
/// Choices the shell stores on a click; outside the `emaki settings` history, no undo.
const SHELL_STATE: Rules = Rules {
    zone: "managed",
    change_via: "shell",
    on_update: "never_touched",
    history: "none",
};
/// The person's files in the home; no update writes them.
const YOURS: Rules = Rules {
    zone: "yours",
    change_via: "any",
    on_update: "never_written",
    history: "yours",
};
/// The machine's own settings: written once by the installer, then the administrator's.
const MACHINE: Rules = Rules {
    zone: "yours",
    change_via: "administrator",
    on_update: "not_replaced",
    history: "yours",
};

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
    let (doc, doc_source) = packaged(
        "EMAKI_PREFIX",
        None,
        Path::new(prefix).join("share/doc/emaki"),
    );
    let (sysconf, sysconf_source) = packaged(
        "EMAKI_SYSCONFDIR",
        option_env!("EMAKI_SYSCONFDIR"),
        PathBuf::from("/etc"),
    );
    // GRUB's generated menu is outside the configuration directory on every install.
    let boot = Some(PathBuf::from("/boot"));
    let boot_source = "fixed: the installer's GRUB location; existence not probed";
    // mkinitcpio reads hooks from its own directory only, whatever the prefix.
    let initcpio = Some(PathBuf::from("/usr/lib/initcpio"));
    let initcpio_source = "fixed: mkinitcpio's hook directory; existence not probed";
    let (config, config_source) = xdg("XDG_CONFIG_HOME", Some(".config"));
    let (state, state_source) = xdg("XDG_STATE_HOME", Some(".local/state"));
    let (runtime, runtime_source) = xdg("XDG_RUNTIME_DIR", None);
    let mut entries = Vec::new();
    let mut add =
        |id, owner, root: &Option<PathBuf>, suffix: &str, source: &str, lifecycle, rules: Rules| {
            entries.push(Entry {
                id,
                zone: rules.zone,
                owner,
                path: root.as_ref().map(|p| p.join(suffix)),
                source: source.into(),
                lifecycle,
                change_via: rules.change_via,
                on_update: rules.on_update,
                history: rules.history,
            });
        };
    add(
        "package_defaults",
        "emaki_package",
        &data,
        "",
        &data_source,
        "existing_layout",
        PACKAGE,
    );
    add(
        "niri_defaults",
        "emaki_package",
        &data,
        "niri/default.kdl",
        &data_source,
        "existing_layout",
        PACKAGE,
    );
    add(
        "system_xdg",
        "distribution_and_packages",
        &sysconf,
        "xdg",
        &sysconf_source,
        "existing_layout",
        PACKAGE_ETC,
    );
    add(
        "adapters",
        "distribution_adapter",
        &libexec,
        "adapters",
        &libexec_source,
        "planned",
        PACKAGE,
    );
    add(
        "personal_niri",
        "user",
        &config,
        "niri/config.kdl",
        &config_source,
        "existing_layout",
        YOURS,
    );
    add(
        "emaki_settings",
        "emaki_core_and_user",
        &config,
        "emaki/settings.toml",
        &config_source,
        "isolated_profile_only",
        MANAGED,
    );
    add(
        "cases",
        "emaki_core_and_user",
        &config,
        "emaki/cases",
        &config_source,
        "planned",
        MANAGED,
    );
    add(
        "managed_generations",
        "emaki_generator",
        &state,
        "emaki/generations",
        &state_source,
        "isolated_profile_only",
        MANAGED,
    );
    add(
        "history",
        "emaki_core",
        &state,
        "emaki/history",
        &state_source,
        "isolated_profile_only",
        MANAGED,
    );
    add(
        "runtime",
        "emaki_core",
        &runtime,
        "emaki",
        &runtime_source,
        "planned",
        MANAGED_RUNTIME,
    );
    add(
        "zones_doc",
        "emaki_package",
        &doc,
        "ZONES.md",
        &doc_source,
        "existing_layout",
        PACKAGE,
    );
    // The snapshot boot hook, named in HOOKS on btrfs installs.
    for (id, suffix) in [
        ("snapshot_hook", "hooks/emaki-snapshot-fstab"),
        ("snapshot_hook_install", "install/emaki-snapshot-fstab"),
    ] {
        add(
            id,
            "emaki_package",
            &initcpio,
            suffix,
            initcpio_source,
            "existing_layout",
            PACKAGE,
        );
    }
    for (id, suffix) in [
        ("skel_kitty", "skel/.config/kitty/kitty.conf"),
        ("skel_qt6ct", "skel/.config/qt6ct/qt6ct.conf"),
        ("skel_wpaperd", "skel/.config/wpaperd/config.toml"),
    ] {
        let lifecycle = "existing_layout";
        add(
            id,
            "emaki_package",
            &sysconf,
            suffix,
            &sysconf_source,
            lifecycle,
            PACKAGE,
        );
    }
    for (id, suffix) in [
        ("niri_system_entry", "niri/config.kdl"),
        ("xdg_mimeapps", "xdg/mimeapps.list"),
        ("xdg_kdeglobals", "xdg/kdeglobals"),
        ("xdg_dolphinrc", "xdg/dolphinrc"),
        ("xdg_qt6ct", "xdg/qt6ct/qt6ct.conf"),
        ("xdg_menu", "xdg/menus/emaki-applications.menu"),
        ("xdg_portals", "xdg/xdg-desktop-portal/niri-portals.conf"),
        ("xdg_fastfetch", "xdg/fastfetch/config.jsonc"),
        ("xdg_hyprlock", "xdg/hypr/hyprlock.conf"),
        ("lock_pam", "pam.d/emaki-lock"),
        ("mirrorlist", "pacman.d/emaki-mirrorlist"),
    ] {
        let lifecycle = "existing_layout";
        add(
            id,
            "emaki_package",
            &sysconf,
            suffix,
            &sysconf_source,
            lifecycle,
            PACKAGE_ETC,
        );
    }
    for (id, suffix) in [
        ("shell_dock", "emaki/dock.json"),
        ("shell_apps", "emaki/apps.json"),
        ("shell_recent", "emaki/recent.json"),
        ("shell_notifications", "emaki/notifications.json"),
        ("shell_night_light", "emaki/night-light.json"),
        ("shell_welcome", "emaki/welcome.json"),
    ] {
        let lifecycle = "existing_layout";
        add(
            id,
            "emaki_shell",
            &state,
            suffix,
            &state_source,
            lifecycle,
            SHELL_STATE,
        );
    }
    for (id, suffix) in [
        ("personal_niri_emaki", "emaki/niri-emaki.kdl"),
        ("personal_kitty", "kitty/kitty.conf"),
        ("personal_qt6ct", "qt6ct/qt6ct.conf"),
        ("personal_wpaperd", "wpaperd/config.toml"),
    ] {
        let lifecycle = "existing_layout";
        add(
            id,
            "user",
            &config,
            suffix,
            &config_source,
            lifecycle,
            YOURS,
        );
    }
    for (id, suffix) in [
        ("machine_vconsole", "vconsole.conf"),
        ("machine_locale", "locale.conf"),
        ("machine_locale_gen", "locale.gen"),
        ("machine_localtime", "localtime"),
        ("machine_hostname", "hostname"),
        ("machine_wireless_regdom", "conf.d/wireless-regdom"),
        ("machine_fstab", "fstab"),
        ("machine_grub_defaults", "default/grub"),
        ("machine_mkinitcpio", "mkinitcpio.conf"),
        ("machine_mkinitcpio_presets", "mkinitcpio.d"),
        ("machine_cryptsetup_keys", "cryptsetup-keys.d"),
        ("machine_snapper", "snapper/configs/root"),
        ("machine_grub_btrfs", "default/grub-btrfs/config"),
        ("machine_sudoers", "sudoers.d/10-wheel"),
        ("machine_zram", "systemd/zram-generator.conf"),
        ("machine_pacman", "pacman.conf"),
    ] {
        let lifecycle = "existing_layout";
        add(
            id,
            "administrator",
            &sysconf,
            suffix,
            &sysconf_source,
            lifecycle,
            MACHINE,
        );
    }
    add(
        "machine_grub_menu",
        "administrator",
        &boot,
        "grub/grub.cfg",
        boot_source,
        "existing_layout",
        MACHINE,
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
        let page = self.entries.iter().find(|e| e.id == "zones_doc");
        if let Some(page) = page.and_then(|e| e.path.as_ref()) {
            text.push_str(&format!(
                "Zones (package, managed, yours) are explained in {page:?}\n"
            ));
        }
        for entry in &self.entries {
            // Debug formatting escapes embedded newlines/control characters in paths.
            let path = entry
                .path
                .as_ref()
                .map(|p| format!("{p:?}"))
                .unwrap_or("unresolved".into());
            let lifecycle = match entry.lifecycle {
                "isolated_profile_only" => "isolated_profile_only, not connected yet",
                other => other,
            };
            text.push_str(&format!(
                "{}: {}\n  zone: {}; on update: {}; change via: {}; history: {}\n  owner: {}; {}; source: {}\n",
                entry.id,
                path,
                entry.zone,
                entry.on_update,
                entry.change_via,
                entry.history,
                entry.owner,
                lifecycle,
                entry.source
            ));
        }
        text
    }
}
