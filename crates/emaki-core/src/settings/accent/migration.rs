// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
//! Replace only a byte-identical selector originally seeded by Emaki.
use super::{Profile, QT6CT_LIMIT};
use rustix::fs::{AtFlags, Mode, OFlags, fchmod, openat, renameat, statat, unlinkat};
use std::fs::{File, Metadata};
use std::io::{Read, Write};
use std::os::unix::fs::{MetadataExt, OpenOptionsExt};

const CURRENT: &[u8] = include_bytes!("../../../../../etc-skel/.config/qt6ct/qt6ct.conf");
// All selector blobs in git history: 6326a7b (42f8b41), then 6ff5be0 (1f5e5ea).
// These are historical bytes, never a path to open or a configurable installation prefix.
const HISTORICAL: &[&[u8]] = &[
    b"[Appearance]\ncolor_scheme_path=/usr/share/emaki/qt6ct/emaki.conf\ncustom_palette=true\n",
    CURRENT,
];
const NAME: &str = "qt6ct.conf";

pub(super) fn publish(profile: &Profile) {
    if std::str::from_utf8(CURRENT)
        .is_ok_and(|text| super::qt6ct_selects(text, &super::qt6ct_scheme(profile)))
        && migrate(profile, rustix::process::geteuid().as_raw()) == Some(true)
    {
        eprintln!("Emaki: migrated the unchanged qt6ct configuration to the session accent.");
    }
}

fn candidate(directory: &File, uid: u32) -> Option<(Metadata, Vec<u8>)> {
    let mut file = File::from(
        openat(
            directory,
            NAME,
            OFlags::RDONLY | OFlags::NOFOLLOW | OFlags::NONBLOCK | OFlags::CLOEXEC,
            Mode::empty(),
        )
        .ok()?,
    );
    let meta = file.metadata().ok()?;
    if !meta.is_file() || meta.nlink() != 1 || meta.uid() != uid || meta.len() > QT6CT_LIMIT {
        return None;
    }
    let mut bytes = Vec::new();
    (&mut file)
        .take(QT6CT_LIMIT + 1)
        .read_to_end(&mut bytes)
        .ok()?;
    if bytes.len() as u64 > QT6CT_LIMIT {
        return None;
    }
    Some((meta, bytes))
}

fn unchanged(before: &Metadata, after: &Metadata) -> bool {
    before.dev() == after.dev()
        && before.ino() == after.ino()
        && before.mode() == after.mode()
        && before.len() == after.len()
        && before.mtime() == after.mtime()
        && before.mtime_nsec() == after.mtime_nsec()
        && before.ctime() == after.ctime()
        && before.ctime_nsec() == after.ctime_nsec()
}

/// Recheck the directory entry after the content read, with no intervening file operations
/// before rename. A concurrent writer can still race the final stat and rename syscalls.
fn unchanged_at(directory: &File, before: &Metadata) -> Option<bool> {
    let after = statat(directory, NAME, AtFlags::SYMLINK_NOFOLLOW).ok()?;
    Some(
        after.st_nlink == 1
            && before.uid() == after.st_uid
            && before.dev() == after.st_dev
            && before.ino() == after.st_ino
            && before.mode() == after.st_mode
            && before.len() == after.st_size as u64
            && before.mtime() == after.st_mtime
            && before.mtime_nsec() == after.st_mtime_nsec as i64
            && before.ctime() == after.st_ctime
            && before.ctime_nsec() == after.st_ctime_nsec as i64,
    )
}

/// A skipped or unavailable file must not prevent login. Anchor every operation to the
/// opened directory, stage a private complete file there, and recheck before atomic rename.
/// The caller supplies the effective uid so ownership rejection is testable without chown.
fn migrate(profile: &Profile, uid: u32) -> Option<bool> {
    let directory = File::options()
        .read(true)
        .custom_flags((OFlags::DIRECTORY | OFlags::NOFOLLOW | OFlags::CLOEXEC).bits() as i32)
        .open(profile.config.parent()?.join("qt6ct"))
        .ok()?;
    let (before, bytes) = candidate(&directory, uid)?;
    if bytes == CURRENT || !HISTORICAL.contains(&bytes.as_slice()) {
        return Some(false);
    }
    let temporary = format!(".qt6ct-{}.tmp", super::storage::id());
    let mut output = File::from(
        openat(
            &directory,
            temporary.as_str(),
            OFlags::WRONLY | OFlags::CREATE | OFlags::EXCL | OFlags::NOFOLLOW | OFlags::CLOEXEC,
            Mode::RUSR | Mode::WUSR,
        )
        .ok()?,
    );
    let result = (|| {
        output.write_all(CURRENT).ok()?;
        fchmod(&output, Mode::from_raw_mode(before.mode() & 0o777)).ok()?;
        output.sync_all().ok()?;
        let (after, latest) = candidate(&directory, uid)?;
        if !unchanged(&before, &after) || latest != bytes {
            return None;
        }
        if !unchanged_at(&directory, &before)? {
            return None;
        }
        renameat(&directory, temporary.as_str(), &directory, NAME).ok()?;
        // Rename already succeeded: report the migration even if directory sync fails.
        let _ = directory.sync_all();
        Some(true)
    })();
    let _ = unlinkat(&directory, temporary.as_str(), AtFlags::empty());
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::os::unix::fs::{FileTypeExt, PermissionsExt, symlink};

    struct Fixture(Profile);
    impl Fixture {
        fn new() -> Self {
            let root = std::env::temp_dir().join(format!(
                "emaki-qt-migration-{}",
                super::super::storage::id()
            ));
            fs::create_dir_all(root.join("config/qt6ct")).unwrap();
            Self(Profile {
                config: root.join("config/emaki"),
                state: root.join("state/emaki"),
                runtime: root.join("runtime"),
                installed: false,
            })
        }
        fn path(&self) -> std::path::PathBuf {
            self.0.config.parent().unwrap().join("qt6ct/qt6ct.conf")
        }
        fn migrate(&self) -> Option<bool> {
            migrate(&self.0, rustix::process::geteuid().as_raw())
        }
        fn only_selector(&self) {
            assert_eq!(
                fs::read_dir(self.path().parent().unwrap()).unwrap().count(),
                1
            );
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            fs::remove_dir_all(self.0.config.parent().unwrap().parent().unwrap()).unwrap();
        }
    }

    #[test]
    fn every_historical_version_reaches_current_once() {
        for bytes in HISTORICAL {
            let fixture = Fixture::new();
            fs::write(fixture.path(), bytes).unwrap();
            // An open reader retains the complete old file across replacement.
            let mut reader = File::open(fixture.path()).unwrap();
            let before = reader.metadata().unwrap();
            assert_eq!(fixture.migrate(), Some(*bytes != CURRENT));
            assert_eq!(fs::read(fixture.path()).unwrap(), CURRENT);
            let mut old = Vec::new();
            reader.read_to_end(&mut old).unwrap();
            assert_eq!(&old, bytes);
            let after = fs::metadata(fixture.path()).unwrap();
            if *bytes != CURRENT {
                assert_ne!(before.ino(), after.ino());
                assert_eq!(after.permissions().mode() & 0o777, before.mode() & 0o777);
            }
            assert_eq!(fixture.migrate(), Some(false));
            assert!(unchanged(&after, &fs::metadata(fixture.path()).unwrap()));
            fixture.only_selector();
        }
    }

    #[test]
    fn migration_preserves_permissions() {
        for mode in [0o644, 0o600] {
            let fixture = Fixture::new();
            fs::write(fixture.path(), HISTORICAL[0]).unwrap();
            fs::set_permissions(fixture.path(), fs::Permissions::from_mode(mode)).unwrap();
            assert_eq!(fixture.migrate(), Some(true));
            assert_eq!(fs::metadata(fixture.path()).unwrap().mode() & 0o777, mode);
            fixture.only_selector();
        }
    }

    #[test]
    fn hard_links_are_untouched() {
        let fixture = Fixture::new();
        fs::write(fixture.path(), HISTORICAL[0]).unwrap();
        let link = fixture.0.config.parent().unwrap().join("linked");
        fs::hard_link(fixture.path(), &link).unwrap();
        let before = fs::metadata(fixture.path()).unwrap();
        assert_eq!(fixture.migrate(), None);
        assert!(unchanged(&before, &fs::metadata(fixture.path()).unwrap()));
        assert_eq!(fs::metadata(fixture.path()).unwrap().nlink(), 2);
        assert_eq!(fs::read(fixture.path()).unwrap(), HISTORICAL[0]);
        assert_eq!(fs::read(link).unwrap(), HISTORICAL[0]);
        fixture.only_selector();
    }

    #[test]
    fn final_entry_check_rejects_changes_after_content_read() {
        for change in ["edit", "replace", "mode", "hardlink", "symlink", "remove"] {
            let fixture = Fixture::new();
            fs::write(fixture.path(), HISTORICAL[0]).unwrap();
            let directory = File::open(fixture.path().parent().unwrap()).unwrap();
            let (before, _) = candidate(&directory, rustix::process::geteuid().as_raw()).unwrap();
            assert_eq!(unchanged_at(&directory, &before), Some(true));
            let other = fixture.0.config.parent().unwrap().join("other");
            match change {
                "edit" => fs::write(fixture.path(), b"edited").unwrap(),
                "replace" => {
                    fs::write(&other, HISTORICAL[0]).unwrap();
                    fs::rename(&other, fixture.path()).unwrap();
                }
                "mode" => {
                    fs::set_permissions(fixture.path(), fs::Permissions::from_mode(0)).unwrap()
                }
                "hardlink" => fs::hard_link(fixture.path(), &other).unwrap(),
                "symlink" => {
                    fs::rename(fixture.path(), &other).unwrap();
                    symlink(&other, fixture.path()).unwrap();
                }
                "remove" => fs::remove_file(fixture.path()).unwrap(),
                _ => unreachable!(),
            }
            assert_ne!(unchanged_at(&directory, &before), Some(true), "{change}");
        }
    }

    #[test]
    fn edited_unknown_and_large_files_are_untouched() {
        for bytes in [
            [HISTORICAL[0], b"# My preference\n"].concat(),
            b"[Appearance]\ncustom_palette=false\n".to_vec(),
            vec![b'x'; QT6CT_LIMIT as usize + 1],
            vec![0xff],
        ] {
            let fixture = Fixture::new();
            fs::write(fixture.path(), &bytes).unwrap();
            let before = fs::metadata(fixture.path()).unwrap();
            assert_ne!(fixture.migrate(), Some(true));
            assert_eq!(fs::read(fixture.path()).unwrap(), bytes);
            assert!(unchanged(&before, &fs::metadata(fixture.path()).unwrap()));
            fixture.only_selector();
        }
    }

    #[test]
    fn missing_link_fifo_directory_and_foreign_owner_are_untouched() {
        let fixture = Fixture::new();
        assert_eq!(fixture.migrate(), None);
        assert!(!fixture.path().exists());
        let target = fixture.0.config.parent().unwrap().join("original");
        fs::write(&target, HISTORICAL[0]).unwrap();
        symlink(&target, fixture.path()).unwrap();
        assert_eq!(fixture.migrate(), None);
        assert_eq!(fs::read_link(fixture.path()).unwrap(), target);
        assert_eq!(fs::read(&target).unwrap(), HISTORICAL[0]);
        fs::remove_file(fixture.path()).unwrap();
        rustix::fs::mkfifoat(rustix::fs::CWD, fixture.path(), Mode::RUSR | Mode::WUSR).unwrap();
        assert_eq!(fixture.migrate(), None);
        assert!(
            fs::symlink_metadata(fixture.path())
                .unwrap()
                .file_type()
                .is_fifo()
        );
        fs::remove_file(fixture.path()).unwrap();
        fs::create_dir(fixture.path()).unwrap();
        assert_eq!(fixture.migrate(), None);
        assert!(fixture.path().is_dir());
        fs::remove_dir(fixture.path()).unwrap();
        fs::write(fixture.path(), HISTORICAL[0]).unwrap();
        let before = fs::metadata(fixture.path()).unwrap();
        assert_eq!(migrate(&fixture.0, before.uid().wrapping_add(1)), None);
        assert_eq!(fs::read(fixture.path()).unwrap(), HISTORICAL[0]);
        assert!(unchanged(&before, &fs::metadata(fixture.path()).unwrap()));
        fixture.only_selector();
    }

    #[test]
    fn unreadable_file_is_untouched() {
        let fixture = Fixture::new();
        fs::write(fixture.path(), HISTORICAL[0]).unwrap();
        fs::set_permissions(fixture.path(), fs::Permissions::from_mode(0)).unwrap();
        let before = fs::metadata(fixture.path()).unwrap();
        if !rustix::process::geteuid().is_root() {
            assert_eq!(fixture.migrate(), None);
            assert!(unchanged(&before, &fs::metadata(fixture.path()).unwrap()));
        }
        fs::set_permissions(fixture.path(), fs::Permissions::from_mode(0o600)).unwrap();
        assert_eq!(fs::read(fixture.path()).unwrap(), HISTORICAL[0]);
    }
}
