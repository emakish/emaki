use super::*;
use crate::transport::helper;
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
}
impl Profile {
    fn open(root: Option<&Path>) -> Result<Self> {
        let root = root.ok_or_else(|| err("isolated_profile_required"))?;
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
        })
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
    fn lock(&self) -> Result<File> {
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
fn validate(stage: &Path, timeout: Duration) -> Result<()> {
    let deadline = Instant::now() + timeout;
    let niri = option_env!("EMAKI_NIRI").unwrap_or("niri");
    let call = |args: &[&str]| helper(niri, args, deadline).map_err(|e| err(e.reason));
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
    if desired == original && profile.prepared(desired, gaps)? {
        return Ok(("unchanged", "already_prepared", None));
    }
    desired.generation = Some(id());
    let files = desired.files(gaps);
    let stage = write_generation(&profile.runtime.join("emaki-settings-staging"), &files)?;
    checkpoint(profile, "after_stage")?;
    validate(&stage.path, timeout)?;
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
        if read(&profile.source())?.as_deref().unwrap_or_default() != before {
            return Err(err("settings_conflict"));
        }
        io(fs::rename(&temporary_path, profile.source()))?;
        committed = true;
        checkpoint(profile, "after_rename")?;
        sync_dir(&profile.config)?;
        sync_dir(profile.config.parent().expect("config parent"))?;
        journal.append(profile, &entry)?;
        checkpoint(profile, "after_history")?;
        journal::remove_file(&profile.pending())?;
        sync_dir(&profile.history())?;
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
    // Reads also observe manual edits and finish pending transactions under this lock.
    let _lock = profile.lock()?;
    let mut journal = journal::Journal::load(&profile)?;
    let recovery = journal::recover(&profile, &mut journal)?;
    let before = read(&profile.source())?;
    let original = Document::parse(before.as_deref())?;
    let manual_changes_recorded = journal.observe(&profile, &original)?.into_iter().collect();
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
        session_applied: false,
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
