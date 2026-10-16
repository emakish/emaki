// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later

use std::fs;
use std::io;
use std::path::Path;
use std::process::{Child, Command, ExitStatus, Output, Stdio};
use std::sync::Mutex;

// Every fork in a test binary must share this lock with executable fixture writes.
// CLOEXEC alone leaves a writable descriptor inherited until the child's exec,
// which can make another child executing that fixture fail with ETXTBSY.
static CHILD_SPAWN: Mutex<()> = Mutex::new(());

pub fn write_executable(path: impl AsRef<Path>, contents: impl AsRef<[u8]>) -> io::Result<()> {
    let _spawn = CHILD_SPAWN.lock().unwrap();
    fs::write(path, contents)
}

pub trait CommandExt {
    fn guarded_spawn(&mut self) -> io::Result<Child>;
    // Capture both output streams with null stdin. Callers with custom stdio use
    // guarded_spawn instead; the lock never covers waiting for a child to exit.
    fn guarded_output(&mut self) -> io::Result<Output>;
    #[allow(dead_code)]
    fn guarded_status(&mut self) -> io::Result<ExitStatus>;
}

impl CommandExt for Command {
    fn guarded_spawn(&mut self) -> io::Result<Child> {
        let _spawn = CHILD_SPAWN.lock().unwrap();
        self.spawn()
    }

    fn guarded_output(&mut self) -> io::Result<Output> {
        self.stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .guarded_spawn()?
            .wait_with_output()
    }

    fn guarded_status(&mut self) -> io::Result<ExitStatus> {
        self.guarded_spawn()?.wait()
    }
}
