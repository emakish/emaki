use super::*;
use crate::transport::{helper, helper_with_lock};
use rustix::fs::{FlockOperation, OFlags, flock};
use std::fs::{self, DirBuilder, File, OpenOptions};
use std::io::{Read, Write};
use std::os::unix::fs::{DirBuilderExt, MetadataExt, OpenOptionsExt};
use std::path::Component;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Instant, SystemTime, UNIX_EPOCH};

const LIMIT: u64 = 64 * 1024;
static SERIAL: AtomicU64 = AtomicU64::new(0);

pub(super) fn io<T>(result: std::io::Result<T>) -> Result<T> {
    result.map_err(|e| {
        err(match e.kind() {
            std::io::ErrorKind::PermissionDenied => "access_denied",
            _ => "filesystem_error",
        })
    })
}

// Reject redirection before touching anything. This prevents accidental symlink/
// hardlink escapes, not malicious concurrent directory replacement by our own UID.
pub(super) fn safe_path(path: &Path) -> Result<()> {
    if !path.is_absolute()
        || path
            .components()
            .any(|c| !matches!(c, Component::RootDir | Component::Normal(_)))
    {
        return Err(err("unsafe_profile_path"));
    }
    let mut part = PathBuf::new();
    for component in path.components() {
        part.push(component);
        match fs::symlink_metadata(&part) {
            Ok(meta) if meta.file_type().is_symlink() => return Err(err("symlink_refused")),
            Ok(_) => {}
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
            Err(e) => return io(Err(e)),
        }
    }
    Ok(())
}
fn private_dir(path: &Path) -> Result<()> {
    safe_path(path)?;
    let meta = io(fs::metadata(path))?;
    if !meta.is_dir()
        || meta.uid() != rustix::process::geteuid().as_raw()
        || meta.mode() & 0o022 != 0
    {
        return Err(err("unsafe_profile_directory"));
    }
    Ok(())
}
pub(super) fn mkdir(path: &Path) -> Result<()> {
    safe_path(path)?;
    match DirBuilder::new().mode(0o700).create(path) {
        Ok(()) => {}
        Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {}
        Err(e) => return io(Err(e)),
    }
    private_dir(path)
}
pub(super) fn read(path: &Path) -> Result<Option<Vec<u8>>> {
    safe_path(path)?;
    let mut file = match OpenOptions::new()
        .read(true)
        .custom_flags((OFlags::NOFOLLOW | OFlags::NONBLOCK).bits() as i32)
        .open(path)
    {
        Ok(file) => file,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return io(Err(e)),
    };
    let meta = io(file.metadata())?;
    if !meta.is_file() || meta.nlink() != 1 || meta.uid() != rustix::process::geteuid().as_raw() {
        return Err(err("unsafe_settings_file"));
    }
    if meta.len() > LIMIT {
        return Err(err("settings_file_too_large"));
    }
    let mut bytes = Vec::new();
    io((&mut file).take(LIMIT + 1).read_to_end(&mut bytes))?;
    if bytes.len() as u64 > LIMIT {
        return Err(err("settings_file_too_large"));
    }
    Ok(Some(bytes))
}
pub(super) fn write_new(path: &Path, bytes: &[u8]) -> Result<()> {
    safe_path(path)?;
    let mut file = io(OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .custom_flags(OFlags::NOFOLLOW.bits() as i32)
        .open(path))?;
    let mut guard = Temporary {
        path: path.into(),
        directory: false,
        keep: false,
    };
    io(file.write_all(bytes))?;
    io(file.sync_all())?;
    guard.keep = true;
    Ok(())
}
pub(super) fn sync_dir(path: &Path) -> Result<()> {
    io(io(File::open(path))?.sync_all())
}
pub(super) fn id() -> String {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    format!(
        "g-{nanos:x}-{:x}-{:x}",
        std::process::id(),
        SERIAL.fetch_add(1, Ordering::Relaxed)
    )
}
pub(super) struct Profile {
    pub(super) config: PathBuf,
    pub(super) state: PathBuf,
    pub(super) runtime: PathBuf,
    pub(super) installed: bool,
}
impl Profile {
    pub(super) fn open(root: Option<&Path>) -> Result<Self> {
        let Some(root) = root else {
            let home = std::env::var_os("HOME").map(PathBuf::from);
            let base = |key: &str, fallback: &str| -> Result<PathBuf> {
                if let Some(path) = std::env::var_os(key).map(PathBuf::from)
                    && path.is_absolute()
                {
                    return Ok(path);
                }
                home.as_ref()
                    .filter(|path| path.is_absolute())
                    .map(|path| path.join(fallback))
                    .ok_or_else(|| err("home_unavailable"))
            };
            return Self::installed(
                &base("XDG_CONFIG_HOME", ".config")?,
                &base("XDG_STATE_HOME", ".local/state")?,
                std::env::var_os("XDG_RUNTIME_DIR")
                    .map(PathBuf::from)
                    .as_deref(),
            );
        };
        private_dir(root)?;
        if root == Path::new("/")
            || std::env::var_os("HOME").is_some_and(|home| {
                let home = PathBuf::from(home);
                root == home
                    || root.starts_with(home.join(".config"))
                    || root.starts_with(home.join(".local"))
            })
        {
            return Err(err("unsafe_profile_path"));
        }
        for (key, directory) in [
            ("XDG_CONFIG_HOME", "config"),
            ("XDG_STATE_HOME", "state"),
            ("XDG_RUNTIME_DIR", "runtime"),
        ] {
            let expected = root.join(directory);
            if std::env::var_os(key).map(PathBuf::from).as_deref() != Some(expected.as_path()) {
                return Err(err("isolated_xdg_mismatch"));
            }
            private_dir(&expected)?;
        }
        Ok(Self {
            config: root.join("config/emaki"),
            state: root.join("state/emaki"),
            runtime: root.join("runtime"),
            installed: false,
        })
    }
    fn installed(config: &Path, state: &Path, runtime: Option<&Path>) -> Result<Self> {
        safe_path(config)?;
        safe_path(state)?;
        let config = config.join("emaki");
        let state = state.join("emaki");
        let runtime = match runtime.filter(|path| path.is_absolute()) {
            Some(path) => {
                private_dir(path)?;
                path.join("emaki-settings")
            }
            None => state.join("runtime"),
        };
        Ok(Self {
            config,
            state,
            runtime,
            installed: true,
        })
    }
    fn initialize(&self) -> Result<()> {
        fn parents(path: &Path) -> Result<()> {
            safe_path(path)?;
            if !path.exists() {
                parents(path.parent().ok_or_else(|| err("unsafe_profile_path"))?)?;
                mkdir(path)?;
            }
            Ok(())
        }
        for path in [&self.config, &self.state, &self.runtime] {
            parents(path)?;
            private_dir(path)?;
        }
        Ok(())
    }
    pub(super) fn source(&self) -> PathBuf {
        self.config.join("settings.toml")
    }
    pub(super) fn generations(&self) -> PathBuf {
        self.state.join("generations")
    }
    pub(super) fn history(&self) -> PathBuf {
        self.state.join("history")
    }
    pub(super) fn pending(&self) -> PathBuf {
        self.history().join("pending.json")
    }
    fn generation(&self, doc: &Document) -> Option<PathBuf> {
        doc.generation.as_ref().map(|g| self.generations().join(g))
    }
    pub(super) fn prepared(&self, doc: &Document, gaps: u16) -> Result<bool> {
        let Some(dir) = self.generation(doc) else {
            return Ok(false);
        };
        for (name, contents) in doc.files(gaps) {
            if read(&dir.join(name))?.as_deref() != Some(contents.as_bytes()) {
                return Ok(false);
            }
        }
        Ok(true)
    }
    fn lock(&self, timeout: Duration) -> Result<File> {
        if self.installed {
            // Persistent inode: deleting a lock file could split concurrent writers.
            let path = self.state.join("settings.lock");
            safe_path(&path)?;
            let file = io(OpenOptions::new()
                .read(true)
                .write(true)
                .create(true)
                .truncate(false)
                .mode(0o600)
                .custom_flags((OFlags::NOFOLLOW | OFlags::NONBLOCK).bits() as i32)
                .open(&path))?;
            let meta = io(file.metadata())?;
            if !meta.is_file()
                || meta.nlink() != 1
                || meta.uid() != rustix::process::geteuid().as_raw()
                || meta.mode() & 0o077 != 0
            {
                return Err(err("unsafe_settings_file"));
            }
            let deadline = Instant::now() + timeout;
            loop {
                match flock(&file, FlockOperation::NonBlockingLockExclusive) {
                    Ok(()) => return Ok(file),
                    Err(e) if e == rustix::io::Errno::WOULDBLOCK => {
                        if Instant::now() >= deadline {
                            return Err(err("settings_busy"));
                        }
                        std::thread::sleep(
                            Duration::from_millis(10)
                                .min(deadline.saturating_duration_since(Instant::now())),
                        );
                    }
                    Err(_) => return Err(err("lock_failed")),
                }
            }
        }
        // Lock the existing directory, avoiding a persistent lock-file on rejection.
        let file = io(File::open(&self.runtime))?;
        flock(&file, FlockOperation::NonBlockingLockExclusive).map_err(|e| {
            err(if e == rustix::io::Errno::WOULDBLOCK {
                "settings_busy"
            } else {
                "lock_failed"
            })
        })?;
        Ok(file)
    }
}

struct Temporary {
    path: PathBuf,
    directory: bool,
    keep: bool,
}
impl Drop for Temporary {
    fn drop(&mut self) {
        if self.keep {
            return;
        }
        if self.directory {
            let _ = fs::remove_dir_all(&self.path);
        } else {
            let _ = fs::remove_file(&self.path);
        }
    }
}
fn write_generation(dir: &Path, files: &[(&str, String)]) -> Result<Temporary> {
    // create_dir (not create_dir_all): never overwrite or adopt another generation.
    safe_path(dir)?;
    io(DirBuilder::new().mode(0o700).create(dir))?;
    let guard = Temporary {
        path: dir.into(),
        directory: true,
        keep: false,
    };
    mkdir(&dir.join("package"))?;
    for (name, bytes) in files {
        write_new(&dir.join(name), bytes.as_bytes())?;
    }
    sync_dir(&dir.join("package"))?;
    sync_dir(dir)?;
    Ok(guard)
}
fn validate(profile: &Profile, stage: &Path, timeout: Duration) -> Result<()> {
    let deadline = Instant::now() + timeout;
    let niri = option_env!("EMAKI_NIRI").unwrap_or("niri");
    let call = |args: &[&str]| {
        if profile.installed {
            helper_with_lock(niri, args, deadline, &profile.state.join("helper.lock"))
        } else {
            helper(niri, args, deadline)
        }
        .map_err(|e| err(e.reason))
    };
    let version = call(&["--version"])?;
    if !version.success
        || std::str::from_utf8(&version.stdout)
            .ok()
            .and_then(|v| v.split_whitespace().nth(1))
            != Some(crate::niri::IPC_RELEASE)
    {
        return Err(err("unsupported_validator_version"));
    }
    for (name, reason) in [
        ("niri.kdl", "fragment_validation_failed"),
        ("config.kdl", "combined_validation_failed"),
    ] {
        let path = stage.join(name);
        let path = path
            .to_str()
            .ok_or_else(|| err("non_utf8_validation_path"))?;
        if !call(&["validate", "--config", path])?.success {
            return Err(err(reason));
        }
    }
    Ok(())
}

// Deliberately absent from normal and packaged builds. No environment variable can
// enable this in a binary compiled without the explicit test-hooks feature.
#[cfg(feature = "test-hooks")]
pub(super) fn checkpoint(profile: &Profile, point: &str) -> Result<()> {
    if std::env::var("EMAKI_TEST_PAUSE").ok().as_deref() == Some(point) {
        let marker = profile.runtime.join("emaki-settings-hook.json");
        let bytes = serde_json::to_vec(&json!({"phase": point, "pid": std::process::id()}))
            .map_err(|_| err("serialization_failed"))?;
        write_new(&marker, &bytes)?;
        sync_dir(&profile.runtime)?;
        rustix::process::kill_process(rustix::process::getpid(), rustix::process::Signal::STOP)
            .map_err(|_| err("test_hook_failed"))?;
    }
    Ok(())
}
#[cfg(not(feature = "test-hooks"))]
pub(super) fn checkpoint(_profile: &Profile, _point: &str) -> Result<()> {
    Ok(())
}

// History stores semantic undo data independently of derived generations.
fn prune_generations(profile: &Profile, current: &Document) -> Result<()> {
    let mut ids = match fs::read_dir(profile.generations()) {
        Ok(entries) => entries
            .map(|entry| io(entry).map(|entry| entry.file_name()))
            .collect::<Result<Vec<_>>>()?,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(()),
        Err(e) => return io(Err(e)),
    };
    ids.retain(|id| id.to_str().is_some_and(journal::valid_id));
    ids.sort();
    let old = ids.len().saturating_sub(16);
    for id in ids.into_iter().take(old) {
        if current.generation.as_deref() != id.to_str() {
            journal::remove_generation(&profile.generations().join(&id))?;
            for prefix in ["session", "settings"] {
                journal::remove_file(
                    &profile
                        .runtime
                        .join(format!("{prefix}-{}.kdl", id.to_string_lossy())),
                )?;
            }
        }
    }
    sync_dir(&profile.generations())
}

struct Commit<'a> {
    before: &'a [u8],
    original: &'a Document,
    desired: &'a mut Document,
    gaps: u16,
    timeout: Duration,
    kind: journal::Kind,
    undo_of: Option<String>,
}
fn apply(
    profile: &Profile,
    journal: &mut journal::Journal,
    commit: Commit<'_>,
) -> Result<(&'static str, &'static str, Option<String>)> {
    let Commit {
        before,
        original,
        desired,
        gaps,
        timeout,
        kind,
        undo_of,
    } = commit;
    if desired == original && (profile.installed || profile.prepared(desired, gaps)?) {
        return Ok(("unchanged", "settings_unchanged", None));
    }
    desired.generation = Some(id());
    let files = desired.files(gaps);
    let stage = write_generation(&profile.runtime.join("emaki-settings-staging"), &files)?;
    checkpoint(profile, "after_stage")?;
    validate(profile, &stage.path, timeout)?;
    checkpoint(profile, "after_validation")?;
    if read(&profile.source())?.as_deref().unwrap_or_default() != before {
        return Err(err("settings_conflict"));
    }
    mkdir(&profile.config)?;
    mkdir(&profile.state)?;
    mkdir(&profile.generations())?;
    let mut entry = journal.make(desired, kind, undo_of, desired.generation.clone());
    if entry.changes.is_empty() {
        entry.kind = journal::Kind::Prepare;
        entry.undo_of = None;
    }
    journal.begin(profile, original, entry.clone())?;
    let destination = profile.generation(desired).expect("assigned generation");
    let temporary_path = profile.config.join(format!(".{}.tmp", entry.id));
    let mut committed = false;
    let transaction = (|| {
        let mut generation = write_generation(&destination, &files)?;
        // Pending tracks this candidate if the process exits anywhere from here.
        generation.keep = true;
        sync_dir(&profile.generations())?;
        sync_dir(&profile.state)?;
        checkpoint(profile, "after_generation")?;
        write_new(&temporary_path, desired.text().as_bytes())?;
        checkpoint(profile, "before_rename")?;
        if profile.installed {
            live::apply(profile, original, desired, gaps, timeout)?;
            checkpoint(profile, "after_session_apply")?;
        }
        if read(&profile.source())?.as_deref().unwrap_or_default() != before {
            return Err(err("settings_conflict"));
        }
        io(fs::rename(&temporary_path, profile.source()))?;
        committed = true;
        checkpoint(profile, "after_rename")?;
        sync_dir(&profile.config)?;
        sync_dir(profile.config.parent().expect("config parent"))?;
        if profile.installed {
            live::finish(profile)?;
        }
        journal.append(profile, &entry)?;
        checkpoint(profile, "after_history")?;
        journal::remove_file(&profile.pending())?;
        sync_dir(&profile.history())?;
        prune_generations(profile, desired)?;
        Ok(())
    })();
    match transaction {
        Ok(()) => Ok((
            "committed",
            "generation_and_history_committed",
            Some(entry.id),
        )),
        Err(_) if committed => Ok((
            "committed_history_pending",
            "recovery_required",
            Some(entry.id),
        )),
        Err(e) => {
            if profile.installed && live::pending(profile)? {
                // Keep failed live compensation for a later session retry, but release
                // the uncommitted storage transaction so reads remain available.
                let _ = live::recover(profile, timeout, false);
            }
            // This process knows rename did not happen; preserve a racing manual edit.
            // On cleanup failure retain pending so the next invocation cannot ignore it.
            journal::remove_file(&temporary_path)?;
            journal::remove_generation(&destination)?;
            journal::remove_file(&profile.pending())?;
            sync_dir(&profile.history())?;
            Err(e)
        }
    }
}

pub(super) fn run(
    operation: Operation<'_>,
    root: Option<&Path>,
    timeout: Duration,
) -> Result<Reply> {
    let profile = Profile::open(root)?;
    let gaps = default_gaps()?;
    let reading = profile.installed
        && matches!(
            operation,
            Operation::List | Operation::Get { .. } | Operation::History
        );
    let _lock = if reading {
        None
    } else {
        if profile.installed {
            profile.initialize()?;
        }
        Some(profile.lock(timeout)?)
    };
    if profile.installed && !reading {
        live::recover(&profile, timeout, false)?;
    }
    let mut journal = journal::Journal::load(&profile)?;
    let recovery = if reading {
        if live::pending(&profile)? {
            vec!["session_recovery_pending".into()]
        } else {
            vec![]
        }
    } else {
        journal::recover(&profile, &mut journal)?
    };
    let mut before = read(&profile.source())?;
    let mut original = Document::parse(before.as_deref())?;
    if profile.installed && before.is_none() && journal.entries.is_empty() {
        // Import only deviations, and persist them together with the first change.
        if let Some(bytes) = read(&profile.state.join("dock.json"))? {
            if let Ok(legacy) = serde_json::from_slice::<Value>(&bytes) {
                if legacy.get("version").and_then(Value::as_u64) == Some(1) {
                    let defaults = rows(&Document::default(), gaps);
                    for (legacy_key, key) in [("on", DOCK_ON), ("auto_hide", DOCK_AUTO_HIDE)] {
                        if let Some(value) =
                            legacy.get(legacy_key).filter(|value| value.is_boolean())
                        {
                            let default = &defaults
                                .iter()
                                .find(|row| row.key == key)
                                .expect("dock key")
                                .default;
                            if value != default {
                                original.restore(key, value)?;
                            }
                        }
                    }
                }
            }
        }
    }
    if !reading && before.is_none() && original != Document::default() {
        // Save the migrated baseline before attempting live changes. A failed first
        // change must not turn a legacy preference back into the package default.
        let temporary = Temporary {
            path: profile.config.join(format!(".import-{}.tmp", id())),
            directory: false,
            keep: false,
        };
        let bytes = original.text().into_bytes();
        write_new(&temporary.path, &bytes)?;
        if read(&profile.source())?.is_some() {
            return Err(err("settings_conflict"));
        }
        io(fs::rename(&temporary.path, profile.source()))?;
        sync_dir(&profile.config)?;
        before = Some(bytes);
    }
    let manual_changes_recorded = if reading {
        vec![]
    } else {
        journal.observe(&profile, &original)?.into_iter().collect()
    };
    let mut document = original.clone();
    let (mut status, mut reason, mut change_id) = ("read", "settings_read", None);
    let mut conflicts = vec![];
    match operation {
        Operation::Set { key, value } => {
            // Existence is a precondition of an explicit set only: undo/history replay
            // must not fail because a picture was deleted later.
            if key == WALLPAPER && !value.is_empty() {
                wallpaper(value)?;
                if !fs::metadata(value).is_ok_and(|meta| meta.is_file()) {
                    return Err(err("wallpaper_missing"));
                }
            }
            document.set(key, value)?;
            (status, reason, change_id) = apply(
                &profile,
                &mut journal,
                Commit {
                    before: before.as_deref().unwrap_or_default(),
                    original: &original,
                    desired: &mut document,
                    gaps,
                    timeout,
                    kind: journal::Kind::Set,
                    undo_of: None,
                },
            )?;
        }
        Operation::Reset { key } => {
            // Same commit path and journal as set: a set entry whose after is null.
            document.clear(key)?;
            (status, reason, change_id) = apply(
                &profile,
                &mut journal,
                Commit {
                    before: before.as_deref().unwrap_or_default(),
                    original: &original,
                    desired: &mut document,
                    gaps,
                    timeout,
                    kind: journal::Kind::Set,
                    undo_of: None,
                },
            )?;
        }
        Operation::Undo { id } => {
            (document, conflicts) = journal.undo(id, &original)?;
            if conflicts.is_empty() {
                (status, reason, change_id) = apply(
                    &profile,
                    &mut journal,
                    Commit {
                        before: before.as_deref().unwrap_or_default(),
                        original: &original,
                        desired: &mut document,
                        gaps,
                        timeout,
                        kind: journal::Kind::Undo,
                        undo_of: Some(id.into()),
                    },
                )?;
            } else {
                document = original.clone();
                status = "rejected";
                reason = "undo_conflict";
            }
        }
        _ => {}
    }
    let prepared = if status.starts_with("committed") {
        true
    } else {
        profile.prepared(&document, gaps)?
    };
    let generation_path = profile.generation(&document);
    let mut settings = rows(&document, gaps);
    if let Operation::Get { key } = operation {
        settings.retain(|row| row.key == key);
        if settings.is_empty() {
            return Err(err("unknown_setting"));
        }
    }
    Ok(Reply {
        schema_version: 1,
        status,
        reason,
        session_applied: profile.installed && status.starts_with("committed"),
        generation_status: if prepared {
            "prepared"
        } else {
            "needs_generation"
        },
        generation: document.generation,
        generation_path,
        settings,
        change_id,
        conflicts,
        history: if matches!(operation, Operation::History) {
            journal.entries
        } else {
            vec![]
        },
        manual_changes_recorded,
        recovery,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;

    fn fixture() -> (Temporary, Profile) {
        let path = std::env::temp_dir().join(format!("emaki-store-{}", id()));
        mkdir(&path).unwrap();
        let profile = Profile::installed(&path.join("config"), &path.join("state"), None).unwrap();
        profile.initialize().unwrap();
        (
            Temporary {
                path,
                directory: true,
                keep: false,
            },
            profile,
        )
    }

    #[test]
    fn installed_store_starts_empty_and_uses_private_files() {
        let (_directory, profile) = fixture();
        assert!(profile.installed);
        let bytes = read(&profile.source()).unwrap();
        assert!(bytes.is_none());
        let document = Document::parse(bytes.as_deref()).unwrap();
        assert_eq!(document.schema_version, 1);
        write_new(&profile.source(), document.text().as_bytes()).unwrap();
        assert_eq!(
            Document::parse(read(&profile.source()).unwrap().as_deref()).unwrap(),
            document
        );
        assert_eq!(
            fs::metadata(profile.source()).unwrap().permissions().mode() & 0o777,
            0o600
        );
        assert_eq!(
            fs::metadata(&profile.config).unwrap().permissions().mode() & 0o777,
            0o700
        );
    }

    #[test]
    fn old_derived_generations_are_bounded_without_pruning_history() {
        let (_directory, profile) = fixture();
        mkdir(&profile.generations()).unwrap();
        let mut current = Document::default();
        for i in 0..22 {
            current.generation = Some(format!("g-{i:04x}-1-1"));
            let mut generated =
                write_generation(&profile.generation(&current).unwrap(), &current.files(8))
                    .unwrap();
            generated.keep = true;
        }
        prune_generations(&profile, &current).unwrap();
        assert_eq!(fs::read_dir(profile.generations()).unwrap().count(), 16);
        assert!(profile.prepared(&current, 8).unwrap());
    }

    #[test]
    fn corrupt_and_future_documents_are_preserved() {
        let (_directory, profile) = fixture();
        for (bytes, reason) in [
            (b"not valid = [".as_slice(), "invalid_settings"),
            (
                b"schema_version = 99\n".as_slice(),
                "unsupported_settings_schema",
            ),
        ] {
            fs::write(profile.source(), bytes).unwrap();
            assert_eq!(
                Document::parse(read(&profile.source()).unwrap().as_deref())
                    .unwrap_err()
                    .reason,
                reason
            );
            assert_eq!(read(&profile.source()).unwrap().unwrap(), bytes);
        }
    }

    #[test]
    fn managed_lock_waits_and_times_out_without_replacing_inode() {
        let (_directory, profile) = fixture();
        let held = profile.lock(Duration::ZERO).unwrap();
        let inode = fs::metadata(profile.state.join("settings.lock"))
            .unwrap()
            .ino();
        assert_eq!(
            profile.lock(Duration::from_millis(20)).unwrap_err().reason,
            "settings_busy"
        );
        drop(held);
        let _next = profile.lock(Duration::ZERO).unwrap();
        assert_eq!(
            fs::metadata(profile.state.join("settings.lock"))
                .unwrap()
                .ino(),
            inode
        );
    }

    #[test]
    fn concurrent_writers_serialize_atomic_replacements() {
        let (directory, profile) = fixture();
        write_new(&profile.source(), b"0").unwrap();
        let mut threads = vec![];
        for _ in 0..8 {
            let root = directory.path.clone();
            threads.push(std::thread::spawn(move || {
                let profile =
                    Profile::installed(&root.join("config"), &root.join("state"), None).unwrap();
                for _ in 0..10 {
                    let _lock = profile.lock(Duration::from_secs(5)).unwrap();
                    let bytes = read(&profile.source()).unwrap().unwrap();
                    let value = std::str::from_utf8(&bytes).unwrap().parse::<u32>().unwrap();
                    let candidate = profile.config.join(format!("{}.tmp", id()));
                    write_new(&candidate, (value + 1).to_string().as_bytes()).unwrap();
                    assert_eq!(read(&profile.source()).unwrap().unwrap(), bytes);
                    fs::rename(candidate, profile.source()).unwrap();
                    sync_dir(&profile.config).unwrap();
                }
            }));
        }
        for thread in threads {
            thread.join().unwrap();
        }
        assert_eq!(read(&profile.source()).unwrap().unwrap(), b"80");
    }

    #[test]
    fn managed_store_rejects_redirected_paths_and_shared_lock_files() {
        let (directory, profile) = fixture();
        let alias = directory.path.join("alias");
        std::os::unix::fs::symlink(&profile.config, &alias).unwrap();
        assert!(Profile::installed(&alias, &directory.path.join("other"), None).is_err());
        let lock = profile.state.join("settings.lock");
        write_new(&lock, b"").unwrap();
        fs::hard_link(&lock, directory.path.join("lock-copy")).unwrap();
        assert_eq!(
            profile.lock(Duration::ZERO).unwrap_err().reason,
            "unsafe_settings_file"
        );
    }
}

/// Prepare the selected derived wrapper before starting a compositor.
pub(super) fn session_config(timeout: Duration) -> Result<PathBuf> {
    let profile = Profile::open(None)?;
    profile.initialize()?;
    let _lock = profile.lock(timeout)?;
    let recovery: Result<()> = (|| {
        live::recover(&profile, timeout, true)?;
        let mut journal = journal::Journal::load(&profile)?;
        journal::recover(&profile, &mut journal)?;
        Ok(())
    })();
    let document = Document::parse(read(&profile.source())?.as_deref())?;
    if let Err(error) = recovery {
        eprintln!(
            "Settings recovery is pending ({}); using your current managed overrides.",
            error.reason
        );
    } else if let Err(error) = live::migrate_mime(&profile, &document) {
        eprintln!(
            "Default application recovery is pending ({}); using your current compositor overrides.",
            error.reason
        );
    }
    live::config(&profile, &document)
}
