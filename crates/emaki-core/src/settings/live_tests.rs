// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
use super::*;
use std::os::unix::fs::{DirBuilderExt, PermissionsExt};

struct Fixture {
    root: PathBuf,
    profile: Profile,
    before: Document,
    after: Document,
}
impl Fixture {
    fn new() -> Self {
        let root = std::env::temp_dir().join(format!("emaki-live-recovery-{}", storage::id()));
        fs::DirBuilder::new().mode(0o700).create(&root).unwrap();
        let profile = Profile {
            config: root.join("config/emaki"),
            state: root.join("state/emaki"),
            runtime: root.join("runtime"),
            installed: true,
        };
        for path in [&profile.config, &profile.history(), &profile.runtime] {
            fs::DirBuilder::new()
                .recursive(true)
                .mode(0o700)
                .create(path)
                .unwrap();
        }
        let before = Document::default();
        let mut after = before.clone();
        after.generation = Some("g-1-2-3".into());
        after.defaults.browser = Some("browser.desktop".into());
        Self {
            root,
            profile,
            before,
            after,
        }
    }
    fn marker(&self) -> Pending {
        Pending {
            schema_version: 1,
            candidate: self.after.generation.clone().unwrap(),
            before: self.before.text(),
            after: self.after.text(),
            niri: true,
            shell: true,
            mime: true,
            mime_fallback: true,
            wallpaper: Some("previous-wallpaper".into()),
            mime_before: Some("previous MIME contents\n".into()),
            legacy_mime_before: None,
        }
    }
    fn stage(&self, marker: &Pending) {
        storage::mkdir(mime_path(&self.profile).parent().unwrap()).unwrap();
        replace(
            &pending_path(&self.profile),
            Some(&serde_json::to_vec(marker).unwrap()),
        )
        .unwrap();
        replace(&mime_path(&self.profile), Some(b"new MIME contents\n")).unwrap();
    }
    fn recover(&self) -> Result<()> {
        recover(&self.profile, Duration::from_millis(1), true)
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.root).unwrap();
    }
}

#[test]
fn crash_before_source_rename_restores_previous_mime() {
    let fixture = Fixture::new();
    fixture.stage(&fixture.marker());
    fixture.recover().unwrap();
    assert_eq!(
        fs::read(mime_path(&fixture.profile)).unwrap(),
        b"previous MIME contents\n"
    );
    assert!(!pending_path(&fixture.profile).exists());
    assert!(!fixture.profile.source().exists());
    fixture.recover().unwrap();
}

#[test]
fn crash_before_source_rename_removes_new_mime_when_none_existed() {
    let fixture = Fixture::new();
    let mut marker = fixture.marker();
    marker.mime_before = None;
    fixture.stage(&marker);
    fixture.recover().unwrap();
    assert!(!mime_path(&fixture.profile).exists());
    assert!(!pending_path(&fixture.profile).exists());
}

#[test]
fn crash_after_source_rename_preserves_committed_mime() {
    let fixture = Fixture::new();
    fixture.stage(&fixture.marker());
    let source = fixture.after.text();
    replace(&fixture.profile.source(), Some(source.as_bytes())).unwrap();
    fixture.recover().unwrap();
    assert_eq!(
        fs::read(mime_path(&fixture.profile)).unwrap(),
        b"new MIME contents\n"
    );
    assert_eq!(
        fs::read(fixture.profile.source()).unwrap(),
        source.as_bytes()
    );
    assert!(!pending_path(&fixture.profile).exists());
}

#[test]
fn changed_source_wins_and_clears_recovery_once() {
    for matching_generation in [false, true] {
        let fixture = Fixture::new();
        fixture.stage(&fixture.marker());
        let mut changed = fixture.after.clone();
        if !matching_generation {
            changed.generation = Some("g-4-5-6".into());
        }
        changed.defaults.browser = Some("different.desktop".into());
        let source = changed.text();
        replace(&fixture.profile.source(), Some(source.as_bytes())).unwrap();
        // The recovery publisher only replaces its own managed MIME file.
        replace(&mime_path(&fixture.profile), Some(MIME_HEADER.as_bytes())).unwrap();
        fixture.recover().unwrap();
        assert!(!pending_path(&fixture.profile).exists());
        assert!(
            fs::read_to_string(mime_path(&fixture.profile))
                .unwrap()
                .contains("different.desktop")
        );
        fixture.recover().unwrap();
        assert_eq!(
            fs::read(fixture.profile.source()).unwrap(),
            source.as_bytes()
        );
    }
}

#[test]
fn malformed_pending_records_are_rejected_without_side_effects() {
    for variant in 0..3 {
        let fixture = Fixture::new();
        let mut marker = fixture.marker();
        match variant {
            0 => marker.schema_version = 2,
            1 => marker.candidate = "../invalid".into(),
            _ => marker.candidate = "g-4-5-6".into(),
        }
        fixture.stage(&marker);
        let contents = fs::read(pending_path(&fixture.profile)).unwrap();
        assert_eq!(
            fixture.recover().unwrap_err().reason,
            "live_recovery_corrupt"
        );
        assert_eq!(fs::read(pending_path(&fixture.profile)).unwrap(), contents);
        assert_eq!(
            fs::read(mime_path(&fixture.profile)).unwrap(),
            b"new MIME contents\n"
        );
    }
}

#[test]
fn atomic_replacement_uses_private_modes_and_removes_temporary_files() {
    let fixture = Fixture::new();
    let path = fixture.profile.runtime.join("test-file");
    replace(&path, Some(b"previous")).unwrap();
    fs::set_permissions(&path, fs::Permissions::from_mode(0o644)).unwrap();
    let old = fs::File::open(&path).unwrap();
    replace(&path, Some(b"replacement")).unwrap();
    assert_eq!(fs::read(&path).unwrap(), b"replacement");
    assert_eq!(
        fs::metadata(&path).unwrap().permissions().mode() & 0o777,
        0o600
    );
    use std::io::Read;
    let mut previous = String::new();
    (&old).read_to_string(&mut previous).unwrap();
    assert_eq!(previous, "previous");
    assert_eq!(fs::read_dir(&fixture.profile.runtime).unwrap().count(), 1);
    replace(&path, None).unwrap();
    assert_eq!(fs::read_dir(&fixture.profile.runtime).unwrap().count(), 0);
}

#[test]
fn session_wrapper_derives_overrides_and_wallpaper_mode_without_editing_generations() {
    let fixture = Fixture::new();
    let mut document = fixture.after.clone();
    document.appearance.wallpaper = Some("/tmp/managed-wallpaper.png".into());
    document.appearance.gaps = Some(4);
    let path = config(&fixture.profile, &document).unwrap();
    let wrapper = fs::read_to_string(&path).unwrap();
    assert!(wrapper.contains("include \"/usr/share/emaki/niri/fork"));
    assert!(wrapper.contains("emaki-wallpaper { mode \"image\"; }"));
    let fragment = fixture.profile.runtime.join(format!(
        "settings-{}.kdl",
        document.generation.as_deref().unwrap()
    ));
    assert!(fs::read_to_string(&fragment).unwrap().contains("gaps 4"));
    document.appearance.wallpaper = None;
    document.appearance.gaps = None;
    assert_eq!(config(&fixture.profile, &document).unwrap(), path);
    assert!(
        !fs::read_to_string(&path)
            .unwrap()
            .contains("emaki-wallpaper")
    );
    assert!(!fs::read_to_string(&fragment).unwrap().contains("gaps"));
    assert!(!fixture.profile.generations().exists());
}

#[test]
fn legacy_pending_mime_snapshot_restores_its_original_location() {
    let fixture = Fixture::new();
    let mut marker = fixture.marker();
    marker.mime_fallback = false;
    fixture.stage(&marker);
    replace(
        &legacy_mime_path(&fixture.profile),
        Some(b"candidate legacy associations"),
    )
    .unwrap();
    fixture.recover().unwrap();
    assert_eq!(
        fs::read(legacy_mime_path(&fixture.profile)).unwrap(),
        b"previous MIME contents\n"
    );
    assert!(!pending_path(&fixture.profile).exists());
}

#[test]
fn login_rebuilds_mime_from_current_source_and_migrates_only_owned_legacy_files() {
    let fixture = Fixture::new();
    let legacy = legacy_mime_path(&fixture.profile);
    let personal = b"[Default Applications]\ntext/html=personal.desktop;\n";
    replace(&legacy, Some(personal)).unwrap();
    migrate_mime(&fixture.profile, &fixture.after).unwrap();
    assert!(
        fs::read_to_string(mime_path(&fixture.profile))
            .unwrap()
            .contains("browser.desktop")
    );
    assert_eq!(fs::read(&legacy).unwrap(), personal);
    replace(&legacy, Some(MIME_HEADER.as_bytes())).unwrap();
    migrate_mime(&fixture.profile, &fixture.after).unwrap();
    assert!(!legacy.exists());
    migrate_mime(&fixture.profile, &fixture.before).unwrap();
    assert!(!mime_path(&fixture.profile).exists());
}
