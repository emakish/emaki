//! Append-only semantic records. Every write crosses the schema allowlist.
use super::storage::{Profile, id, io, mkdir, read, safe_path, sync_dir, write_new};
use super::*;
use std::fs;
use std::time::{SystemTime, UNIX_EPOCH};

pub(super) type Snapshot = BTreeMap<String, Value>;
fn project(values: &Snapshot, schema: &[Field]) -> Snapshot {
    values
        .iter()
        .filter(|(key, _)| recordable(key, schema))
        .map(|(key, value)| (key.clone(), value.clone()))
        .collect()
}
fn changes(before: &Snapshot, after: &Snapshot, schema: &[Field]) -> Vec<Change> {
    let keys: BTreeSet<_> = before.keys().chain(after.keys()).collect();
    keys.into_iter()
        .filter(|key| recordable(key, schema))
        .filter_map(|key| {
            let before = before.get(key).cloned().unwrap_or(Value::Null);
            let after = after.get(key).cloned().unwrap_or(Value::Null);
            (before != after).then(|| Change {
                key: key.clone(),
                before,
                after,
            })
        })
        .collect()
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Kind {
    Set,
    Undo,
    Manual,
    Import,
    Prepare,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Change {
    pub key: String,
    pub before: Value,
    pub after: Value,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Entry {
    pub schema_version: u32,
    pub id: String,
    pub sequence: u64,
    pub parent: Option<String>,
    pub observed_at_unix_ms: u64,
    pub kind: Kind,
    pub undo_of: Option<String>,
    pub generation: Option<String>,
    pub changes: Vec<Change>,
}
#[derive(Serialize)]
pub struct Conflict {
    pub key: String,
    pub requested_change: String,
    pub actual_change: Option<String>,
    pub expected_value: Value,
    pub actual_value: Value,
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Pending {
    pub schema_version: u32,
    pub before_generation: Option<String>,
    pub before: Snapshot,
    pub entry: Entry,
}
pub(super) fn valid_id(value: &str) -> bool {
    let parts: Vec<_> = value.split('-').collect();
    parts.len() == 4
        && parts[0] == "g"
        && value.len() < 100
        && parts[1..]
            .iter()
            .all(|p| !p.is_empty() && p.bytes().all(|b| b.is_ascii_hexdigit()))
}
impl Entry {
    fn filtered(&self, schema: &[Field]) -> Self {
        let mut entry = self.clone();
        entry.changes.retain(|c| recordable(&c.key, schema));
        entry
    }
    fn filename(&self) -> String {
        format!("{:020}-{}.json", self.sequence, self.id)
    }
}
impl Pending {
    fn filtered(&self, schema: &[Field]) -> Self {
        Self {
            schema_version: self.schema_version,
            before_generation: self.before_generation.clone(),
            before: project(&self.before, schema),
            entry: self.entry.filtered(schema),
        }
    }
}

pub(super) struct Journal {
    pub entries: Vec<Entry>,
    pub state: Snapshot,
    last_change: BTreeMap<String, String>,
}
impl Journal {
    pub fn load(profile: &Profile) -> Result<Self> {
        let mut result = Self {
            entries: vec![],
            state: project(&Document::default().overrides(), SCHEMA),
            last_change: BTreeMap::new(),
        };
        safe_path(&profile.history())?;
        let paths = match fs::read_dir(profile.history()) {
            Ok(paths) => paths,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(result),
            Err(e) => return io(Err(e)),
        };
        let mut files = vec![];
        for path in paths {
            let path = io(path)?.path();
            let name = path
                .file_name()
                .and_then(|s| s.to_str())
                .ok_or_else(|| err("history_corrupt"))?;
            if [
                "pending.json",
                "live-pending.json",
                ".record.tmp",
                ".pending.tmp",
            ]
            .contains(&name)
            {
                continue;
            }
            if !name.ends_with(".json") || files.len() >= 4096 {
                return Err(err("history_corrupt_or_limit"));
            }
            files.push(path);
        }
        files.sort();
        let mut total = 0;
        for path in files {
            let bytes = read(&path)?.ok_or_else(|| err("history_corrupt"))?;
            total += bytes.len();
            if total > 16 * 1024 * 1024 {
                return Err(err("history_limit"));
            }
            let entry: Entry =
                serde_json::from_slice(&bytes).map_err(|_| err("history_corrupt"))?;
            if path.file_name().and_then(|s| s.to_str()) != Some(&entry.filename()) {
                return Err(err("history_corrupt"));
            }
            result.accept(entry)?;
        }
        Ok(result)
    }
    fn accept(&mut self, entry: Entry) -> Result<()> {
        if self.entries.len() >= 4096 {
            return Err(err("history_limit"));
        }
        if entry.schema_version != 1
            || !valid_id(&entry.id)
            || entry.sequence != self.entries.len() as u64 + 1
            || entry.parent.as_deref() != self.entries.last().map(|e| e.id.as_str())
            || self.entries.iter().any(|e| e.id == entry.id)
            || entry.generation.as_ref().is_some_and(|g| g != &entry.id)
            || (entry.kind == Kind::Undo) != entry.undo_of.is_some()
            || entry
                .undo_of
                .as_ref()
                .is_some_and(|id| !self.entries.iter().any(|e| &e.id == id))
        {
            return Err(err("history_corrupt"));
        }
        let mut checked = Document::default();
        let mut keys = std::collections::BTreeSet::new();
        for change in &entry.changes {
            if !recordable(&change.key, SCHEMA)
                || !keys.insert(&change.key)
                || self.state.get(&change.key) != Some(&change.before)
                || change.before == change.after
            {
                return Err(err("history_corrupt"));
            }
            checked.restore(&change.key, &change.before)?;
            checked.restore(&change.key, &change.after)?;
        }
        for change in &entry.changes {
            self.state.insert(change.key.clone(), change.after.clone());
            self.last_change
                .insert(change.key.clone(), entry.id.clone());
        }
        self.entries.push(entry);
        Ok(())
    }
    pub fn make(
        &self,
        doc: &Document,
        kind: Kind,
        undo_of: Option<String>,
        generation: Option<String>,
    ) -> Entry {
        Entry {
            schema_version: 1,
            id: generation.clone().unwrap_or_else(id),
            sequence: self.entries.len() as u64 + 1,
            parent: self.entries.last().map(|e| e.id.clone()),
            observed_at_unix_ms: SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap_or_default()
                .as_millis()
                .try_into()
                .unwrap_or(u64::MAX),
            kind,
            undo_of,
            generation,
            changes: changes(&self.state, &doc.overrides(), SCHEMA),
        }
    }
    fn check_next(&self, entry: &Entry) -> Result<()> {
        let mut copy = Self {
            entries: self.entries.clone(),
            state: self.state.clone(),
            last_change: self.last_change.clone(),
        };
        copy.accept(entry.clone())
    }
    pub fn append(&mut self, profile: &Profile, entry: &Entry) -> Result<()> {
        let entry = entry.filtered(SCHEMA);
        if self.entries.last() == Some(&entry) {
            return Ok(());
        } // recovery is idempotent
        // Validate before writing; restore in-memory state from disk if append fails.
        let mut check = Self {
            entries: self.entries.clone(),
            state: self.state.clone(),
            last_change: self.last_change.clone(),
        };
        check.accept(entry.clone())?;
        mkdir(&profile.state)?;
        mkdir(&profile.history())?;
        remove_file(&profile.history().join(".record.tmp"))?;
        let temporary = profile.history().join(".record.tmp");
        let bytes = serde_json::to_vec_pretty(&entry).map_err(|_| err("serialization_failed"))?;
        write_new(&temporary, &bytes)?;
        let target = profile.history().join(entry.filename());
        if read(&target)?.is_some() {
            return Err(err("history_corrupt"));
        }
        io(fs::rename(temporary, target))?;
        sync_dir(&profile.history())?;
        sync_dir(&profile.state)?;
        sync_dir(profile.state.parent().expect("state parent"))?;
        *self = check;
        Ok(())
    }
    pub fn observe(&mut self, profile: &Profile, doc: &Document) -> Result<Option<String>> {
        if self.state == project(&doc.overrides(), SCHEMA) {
            return Ok(None);
        }
        let kind = if self.entries.is_empty() {
            Kind::Import
        } else {
            Kind::Manual
        };
        let entry = self.make(doc, kind, None, None);
        self.append(profile, &entry)?;
        Ok(Some(entry.id))
    }
    pub fn undo(&self, id: &str, current: &Document) -> Result<(Document, Vec<Conflict>)> {
        let entry = self
            .entries
            .iter()
            .find(|entry| entry.id == id)
            .ok_or_else(|| err("history_entry_not_found"))?;
        if entry.changes.is_empty() || entry.kind == Kind::Import {
            return Err(err("entry_not_undoable"));
        }
        let mut desired = current.clone();
        let mut conflicts = vec![];
        for change in &entry.changes {
            if self.last_change.get(&change.key) != Some(&entry.id) {
                conflicts.push(Conflict {
                    key: change.key.clone(),
                    requested_change: entry.id.clone(),
                    actual_change: self.last_change.get(&change.key).cloned(),
                    expected_value: change.after.clone(),
                    actual_value: self.state.get(&change.key).cloned().unwrap_or(Value::Null),
                });
            } else {
                desired.restore(&change.key, &change.before)?;
            }
        }
        Ok((desired, conflicts))
    }
    pub fn begin(&self, profile: &Profile, before: &Document, entry: Entry) -> Result<()> {
        self.check_next(&entry)?;
        mkdir(&profile.state)?;
        mkdir(&profile.history())?;
        if read(&profile.pending())?.is_some() {
            return Err(err("recovery_required"));
        }
        remove_file(&profile.history().join(".pending.tmp"))?;
        let pending = Pending {
            schema_version: 1,
            before_generation: before.generation.clone(),
            before: before.overrides(),
            entry,
        }
        .filtered(SCHEMA);
        let bytes = serde_json::to_vec_pretty(&pending).map_err(|_| err("serialization_failed"))?;
        let temp = profile.history().join(".pending.tmp");
        write_new(&temp, &bytes)?;
        io(fs::rename(temp, profile.pending()))?;
        sync_dir(&profile.history())?;
        sync_dir(&profile.state)?;
        sync_dir(profile.state.parent().expect("state parent"))
    }
}

pub(super) fn remove_file(path: &Path) -> Result<()> {
    if read(path)?.is_some() {
        io(fs::remove_file(path))?;
    }
    Ok(())
}
// Only our known files/directories, never a blanket remove of arbitrary profile contents.
pub(super) fn remove_generation(path: &Path) -> Result<()> {
    safe_path(path)?;
    let entries = match fs::read_dir(path) {
        Ok(e) => e,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(()),
        Err(e) => return io(Err(e)),
    };
    let mut files = vec![];
    let mut directories = vec![];
    for entry in entries {
        let p = io(entry)?.path();
        match p.file_name().and_then(|s| s.to_str()) {
            Some("package") => {
                safe_path(&p)?;
                for file in io(fs::read_dir(&p))? {
                    let file = io(file)?.path();
                    if !matches!(
                        file.file_name().and_then(|s| s.to_str()),
                        Some("default.kdl" | "theme.kdl" | "shell.kdl")
                    ) {
                        return Err(err("unknown_temporary_file"));
                    }
                    files.push(file);
                }
                directories.push(p);
            }
            Some(name) if GENERATION_FILES.contains(&name) => files.push(p),
            _ => return Err(err("unknown_temporary_file")),
        }
    }
    // Inspect everything before deletion; an unknown/redirected file stops cleanup.
    for file in &files {
        read(file)?;
    }
    for file in files {
        remove_file(&file)?;
    }
    for directory in directories {
        io(fs::remove_dir(directory))?;
    }
    io(fs::remove_dir(path))
}

pub(super) fn recover(profile: &Profile, journal: &mut Journal) -> Result<Vec<String>> {
    let mut notes = vec![];
    remove_file(&profile.runtime.join("emaki-settings-hook.json"))?;
    if let Some(bytes) = read(&profile.pending())? {
        let pending: Pending =
            serde_json::from_slice(&bytes).map_err(|_| err("recovery_corrupt"))?;
        if pending.schema_version != 1
            || !valid_id(&pending.entry.id)
            || pending.entry.generation.as_ref() != Some(&pending.entry.id)
            || pending
                .before_generation
                .as_deref()
                .is_some_and(|id| !valid_id(id))
            || pending.before != project(&pending.before, SCHEMA)
            || pending.entry != pending.entry.filtered(SCHEMA)
        {
            return Err(err("recovery_corrupt"));
        }
        let current = Document::parse(read(&profile.source())?.as_deref())?;
        let was_recorded = journal.entries.last() == Some(&pending.entry);
        let mut expected_before = journal.state.clone();
        if was_recorded {
            for change in &pending.entry.changes {
                expected_before.insert(change.key.clone(), change.before.clone());
            }
        } else {
            journal.check_next(&pending.entry)?;
        }
        if pending.before != expected_before {
            return Err(err("recovery_corrupt"));
        }
        if current.generation.as_deref() == Some(&pending.entry.id) || was_recorded {
            // Source rename happened. Keep the selected generation and finish the log once.
            let snapshot = read(
                &profile
                    .generations()
                    .join(&pending.entry.id)
                    .join("settings.toml"),
            )?
            .ok_or_else(|| err("recovery_generation_missing"))?;
            let generated = Document::parse(Some(&snapshot))?;
            let mut expected_after = pending.before.clone();
            for change in &pending.entry.changes {
                expected_after.insert(change.key.clone(), change.after.clone());
            }
            if generated.generation.as_deref() != Some(&pending.entry.id)
                || project(&generated.overrides(), SCHEMA) != expected_after
                || !profile.prepared(&generated, default_gaps()?)?
            {
                return Err(err("recovery_generation_changed"));
            }
            sync_dir(&profile.config)?;
            journal.append(profile, &pending.entry)?;
            super::storage::checkpoint(profile, "recovery_after_history")?;
            notes.push(format!("finalized:{}", pending.entry.id));
        } else if current.generation == pending.before_generation
            && project(&current.overrides(), SCHEMA) == pending.before
        {
            // No source commit: only remove the specifically tracked unselected candidate.
            if journal
                .entries
                .iter()
                .any(|entry| entry.generation.as_ref() == Some(&pending.entry.id))
            {
                return Err(err("recovery_conflict"));
            }
            remove_generation(&profile.generations().join(&pending.entry.id))?;
            notes.push(format!("aborted:{}", pending.entry.id));
        } else {
            // An external edit wins over an uncommitted candidate.
            remove_generation(&profile.generations().join(&pending.entry.id))?;
            notes.push(format!("kept_personal_edit:{}", pending.entry.id));
        }
        remove_file(&profile.config.join(format!(".{}.tmp", pending.entry.id)))?;
        remove_file(&profile.pending())?;
        sync_dir(&profile.history())?;
    }
    remove_file(&profile.history().join(".record.tmp"))?;
    remove_file(&profile.history().join(".pending.tmp"))?;
    let stage = profile.runtime.join("emaki-settings-staging");
    if stage.exists() {
        remove_generation(&stage)?;
        notes.push("removed_staging".into());
    }
    remove_file(&profile.runtime.join("emaki-settings-hook.json"))?;
    Ok(notes)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn secret_schema_field_is_absent_from_records_and_recovery_journal() {
        let schema = [
            Field {
                key: GAPS,
                secret: false,
            },
            Field {
                key: "credentials.token",
                secret: true,
            },
        ];
        let before = BTreeMap::from([
            (GAPS.into(), json!(2)),
            ("credentials.token".into(), json!("SECRET_BEFORE")),
        ]);
        let after = BTreeMap::from([
            (GAPS.into(), json!(4)),
            ("credentials.token".into(), json!("SECRET_AFTER")),
            ("unknown".into(), json!("SECRET_UNKNOWN")),
        ]);
        let entry = Entry {
            schema_version: 1,
            id: "g-1-2-3".into(),
            sequence: 1,
            parent: None,
            observed_at_unix_ms: 0,
            kind: Kind::Set,
            undo_of: None,
            generation: Some("g-1-2-3".into()),
            changes: changes(&before, &after, &schema),
        };
        assert_eq!(entry.changes.len(), 1);
        let mut contaminated = entry.clone();
        contaminated.changes.push(Change {
            key: "credentials.token".into(),
            before: json!("SECRET_BEFORE"),
            after: json!("SECRET_AFTER"),
        });
        let pending = Pending {
            schema_version: 1,
            before_generation: None,
            before,
            entry: contaminated,
        };
        for encoded in [
            serde_json::to_string(&entry).unwrap(),
            serde_json::to_string(&pending.filtered(&schema)).unwrap(),
        ] {
            assert!(
                !encoded.contains("SECRET")
                    && !encoded.contains("credentials.token")
                    && !encoded.contains("unknown")
            );
            assert!(encoded.contains(GAPS));
        }
    }
}
