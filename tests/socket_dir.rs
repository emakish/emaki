// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later

use std::os::unix::fs::DirBuilderExt;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};

static NEXT: AtomicU64 = AtomicU64::new(0);

pub fn short_socket_dir() -> PathBuf {
    loop {
        let path = PathBuf::from(format!(
            "/tmp/em-r-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        match std::fs::DirBuilder::new().mode(0o700).create(&path) {
            Ok(()) => return path,
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(error) => panic!("{error}"),
        }
    }
}

#[test]
fn socket_directory_is_short_private_and_unique() {
    use std::os::unix::fs::PermissionsExt;
    let first = short_socket_dir();
    let second = short_socket_dir();
    assert_ne!(first, second);
    assert_eq!(first.parent(), Some(std::path::Path::new("/tmp")));
    assert!(first.join("niri.sock").as_os_str().len() < 108);
    assert_eq!(
        std::fs::metadata(&first).unwrap().permissions().mode() & 0o777,
        0o700
    );
    std::fs::remove_dir(&first).unwrap();
    std::fs::remove_dir(&second).unwrap();
    assert!(!first.exists());
    assert!(!second.exists());
}
