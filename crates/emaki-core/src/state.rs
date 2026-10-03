//! Privacy-filtered observations of the running session, not a health guarantee.
use crate::transport::{connect, exchange, helper, helper_failure, remaining};
use serde::Serialize;
use serde_json::{Value, json};
use std::collections::BTreeMap;
use std::path::PathBuf;
use std::time::{Duration, Instant};

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Status {
    Available,
    Absent,
    Denied,
    Incomplete,
    Timeout,
    Error,
}

impl Status {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Available => "available",
            Self::Absent => "absent",
            Self::Denied => "denied",
            Self::Incomplete => "incomplete",
            Self::Timeout => "timeout",
            Self::Error => "error",
        }
    }
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct Failure {
    pub status: Status,
    pub reason: &'static str,
}
impl Failure {
    pub(crate) const fn new(status: Status, reason: &'static str) -> Self {
        Self { status, reason }
    }
}

#[derive(Debug, Serialize)]
pub struct Component {
    pub id: &'static str,
    pub status: Status,
    pub reason: &'static str,
    pub source: &'static str,
    pub data: BTreeMap<String, Value>,
}

#[derive(Debug, Serialize)]
pub struct State {
    pub schema_version: u32,
    pub emaki_version: &'static str,
    pub timeout_ms: u64,
    pub components: Vec<Component>,
}

impl State {
    /// 0: every probe available; 1: partial observation (still valid JSON).
    pub fn exit_code(&self) -> u8 {
        u8::from(
            self.components
                .iter()
                .any(|c| c.status != Status::Available),
        )
    }

    pub fn human(&self) -> String {
        let mut text = format!(
            "Emaki {} — state format {}, {} ms per component\n",
            self.emaki_version, self.schema_version, self.timeout_ms
        );
        for c in &self.components {
            text.push_str(&format!(
                "{}: {} ({})\n  source: {}\n",
                c.id,
                c.status.as_str(),
                c.reason,
                c.source
            ));
            for (key, value) in &c.data {
                text.push_str(&format!("  {key}: {value}\n"));
            }
        }
        text
    }
}

enum Probe {
    Niri,
    Bus {
        user: bool,
        name: &'static str,
        path: &'static str,
        property: Option<(&'static str, &'static str)>,
    },
    PipeWire,
    WirePlumber,
}

const PROBES: [(&str, &str, Probe); 9] = [
    (
        "niri",
        "niri IPC $NIRI_SOCKET: Version, Workspaces, Windows",
        Probe::Niri,
    ),
    (
        "networkmanager",
        "system D-Bus org.freedesktop.NetworkManager: owner, Version",
        Probe::Bus {
            user: false,
            name: "org.freedesktop.NetworkManager",
            path: "/org/freedesktop/NetworkManager",
            property: Some(("org.freedesktop.NetworkManager", "Version")),
        },
    ),
    (
        "bluez",
        "system D-Bus org.bluez: owner, Peer.Ping",
        Probe::Bus {
            user: false,
            name: "org.bluez",
            path: "/",
            property: None,
        },
    ),
    (
        "upower",
        "system D-Bus org.freedesktop.UPower: owner, DaemonVersion",
        Probe::Bus {
            user: false,
            name: "org.freedesktop.UPower",
            path: "/org/freedesktop/UPower",
            property: Some(("org.freedesktop.UPower", "DaemonVersion")),
        },
    ),
    (
        "power_profiles",
        "system D-Bus net.hadess.PowerProfiles: owner, ActiveProfile",
        Probe::Bus {
            user: false,
            name: "net.hadess.PowerProfiles",
            path: "/net/hadess/PowerProfiles",
            property: Some(("net.hadess.PowerProfiles", "ActiveProfile")),
        },
    ),
    (
        "logind",
        "system D-Bus org.freedesktop.login1: owner, Peer.Ping",
        Probe::Bus {
            user: false,
            name: "org.freedesktop.login1",
            path: "/org/freedesktop/login1",
            property: None,
        },
    ),
    (
        "pipewire",
        "PipeWire native protocol via pw-cli info 0: core version",
        Probe::PipeWire,
    ),
    (
        "wireplumber",
        "user D-Bus systemd1: GetUnit(wireplumber.service), ActiveState (unit only)",
        Probe::WirePlumber,
    ),
    (
        "user_systemd",
        "user D-Bus org.freedesktop.systemd1: owner, Peer.Ping",
        Probe::Bus {
            user: true,
            name: "org.freedesktop.systemd1",
            path: "/org/freedesktop/systemd1",
            property: None,
        },
    ),
];

/// All components share the same budget length and run independently in parallel.
/// The deadline covers connecting, all requests and all helper processes.
pub fn collect(timeout: Duration) -> State {
    let niri_path = std::env::var_os("NIRI_SOCKET")
        .filter(|v| !v.is_empty())
        .map(PathBuf::from);
    let components = std::thread::scope(|scope| {
        let handles: Vec<_> = PROBES
            .into_iter()
            .map(|(id, source, probe)| {
                let path = niri_path.as_deref();
                scope.spawn(move || {
                    let deadline = Instant::now() + timeout;
                    let mut data = BTreeMap::new();
                    let result = match probe {
                        Probe::Niri => niri(path, deadline, &mut data),
                        Probe::Bus {
                            user,
                            name,
                            path,
                            property,
                        } => bus_probe(user, name, path, property, deadline, &mut data),
                        Probe::PipeWire => pipewire(deadline, &mut data),
                        Probe::WirePlumber => wireplumber(deadline, &mut data),
                    };
                    let (status, reason) = match result {
                        Ok(()) => (Status::Available, "probe_completed"),
                        Err(e) => (e.status, e.reason),
                    };
                    Component {
                        id,
                        source,
                        status,
                        reason,
                        data,
                    }
                })
            })
            .collect();
        handles
            .into_iter()
            .map(|h| h.join().expect("read-only probe panicked"))
            .collect()
    });
    State {
        schema_version: crate::STATE_SCHEMA_VERSION,
        emaki_version: crate::VERSION,
        timeout_ms: timeout.as_millis().try_into().unwrap_or(u64::MAX),
        components,
    }
}

fn invalid() -> Failure {
    Failure::new(Status::Incomplete, "invalid_response")
}

fn version(text: &str) -> Result<&str, Failure> {
    if text.is_empty()
        || text.len() > 128
        || !text
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b".-_+ ()".contains(&b))
    {
        Err(invalid())
    } else {
        Ok(text)
    }
}

fn niri(
    path: Option<&std::path::Path>,
    deadline: Instant,
    data: &mut BTreeMap<String, Value>,
) -> Result<(), Failure> {
    let path = path.ok_or(Failure::new(Status::Absent, "socket_not_configured"))?;
    if !path.is_absolute() {
        return Err(Failure::new(Status::Error, "invalid_socket_path"));
    }
    let mut stream = connect(path, deadline)?;
    for (request, key) in [
        ("Version", "version"),
        ("Workspaces", "workspace_count"),
        ("Windows", "window_count"),
    ] {
        let bytes = exchange(&mut stream, format!("\"{request}\"\n").as_bytes(), deadline)?;
        let reply: Value = serde_json::from_slice(&bytes).map_err(|_| invalid())?;
        if reply.get("Err").is_some() {
            return Err(Failure::new(Status::Error, "niri_rejected_request"));
        }
        let payload = reply
            .get("Ok")
            .and_then(|ok| ok.get(request))
            .ok_or_else(invalid)?;
        let value = if request == "Version" {
            json!(version(payload.as_str().ok_or_else(invalid)?)?)
        } else {
            let objects = payload.as_array().ok_or_else(invalid)?;
            if !objects
                .iter()
                .all(|object| object.get("id").and_then(Value::as_u64).is_some())
            {
                return Err(invalid());
            }
            json!(objects.len())
        };
        data.insert(key.into(), value);
    }
    Ok(())
}

fn bus(user: bool, args: &[&str], deadline: Instant) -> Result<Value, Failure> {
    let timeout = format!("--timeout={:.3}s", remaining(deadline)?.as_secs_f64());
    let mut options = vec![
        if user { "--user" } else { "--system" },
        "--json=short",
        "--no-pager",
        "--auto-start=no",
        "--allow-interactive-authorization=no",
        &timeout,
    ];
    options.extend_from_slice(args);
    let result = helper(
        option_env!("EMAKI_BUSCTL").unwrap_or("busctl"),
        &options,
        deadline,
    )?;
    if !result.success {
        return Err(helper_failure(&result.stderr));
    }
    // Methods with no return arguments (Peer.Ping) have no JSON output in busctl.
    if result.stdout.iter().all(u8::is_ascii_whitespace) {
        return Ok(Value::Null);
    }
    serde_json::from_slice(&result.stdout).map_err(|_| invalid())
}

fn scalar<'a>(reply: &'a Value, signature: &str) -> Result<&'a Value, Failure> {
    if reply.get("type").and_then(Value::as_str) != Some(signature) {
        return Err(invalid());
    }
    let values = reply
        .get("data")
        .and_then(Value::as_array)
        .ok_or_else(invalid)?;
    if values.len() != 1 {
        return Err(invalid());
    }
    Ok(&values[0])
}

// busctl's get-property JSON contains the value directly, whereas call JSON
// contains an array of return arguments (even for a single scalar argument).
fn string_property(reply: &Value) -> Result<&str, Failure> {
    if reply.get("type").and_then(Value::as_str) != Some("s") {
        return Err(invalid());
    }
    reply
        .get("data")
        .and_then(Value::as_str)
        .ok_or_else(invalid)
}

fn owner(user: bool, name: &str, deadline: Instant) -> Result<(), Failure> {
    let reply = bus(
        user,
        &[
            "call",
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "NameHasOwner",
            "s",
            name,
        ],
        deadline,
    )?;
    match scalar(&reply, "b")?.as_bool() {
        Some(true) => Ok(()),
        Some(false) => Err(Failure::new(Status::Absent, "name_has_no_owner")),
        None => Err(invalid()),
    }
}

fn bus_probe(
    user: bool,
    name: &str,
    path: &str,
    property: Option<(&str, &str)>,
    deadline: Instant,
    data: &mut BTreeMap<String, Value>,
) -> Result<(), Failure> {
    owner(user, name, deadline)?;
    if let Some((interface, property)) = property {
        let reply = bus(
            user,
            &["get-property", name, path, interface, property],
            deadline,
        )?;
        let text = string_property(&reply)?;
        let key = if property == "ActiveProfile" {
            "active_profile"
        } else {
            "version"
        };
        if property == "ActiveProfile" {
            if !["performance", "balanced", "power-saver"].contains(&text) {
                return Err(invalid());
            }
        } else {
            version(text)?;
        }
        data.insert(key.into(), json!(text));
    } else {
        let reply = bus(
            user,
            &["call", name, path, "org.freedesktop.DBus.Peer", "Ping"],
            deadline,
        )?;
        if !reply.is_null()
            && !(reply.get("type") == Some(&json!("")) && reply.get("data") == Some(&json!([])))
        {
            return Err(invalid());
        }
    }
    Ok(())
}

fn wireplumber(deadline: Instant, data: &mut BTreeMap<String, Value>) -> Result<(), Failure> {
    owner(true, "org.freedesktop.systemd1", deadline)?;
    let reply = bus(
        true,
        &[
            "call",
            "org.freedesktop.systemd1",
            "/org/freedesktop/systemd1",
            "org.freedesktop.systemd1.Manager",
            "GetUnit",
            "s",
            "wireplumber.service",
        ],
        deadline,
    )?;
    let path = scalar(&reply, "o")?.as_str().ok_or_else(invalid)?;
    if path != "/org/freedesktop/systemd1/unit/wireplumber_2eservice" {
        return Err(invalid());
    }
    let reply = bus(
        true,
        &[
            "get-property",
            "org.freedesktop.systemd1",
            path,
            "org.freedesktop.systemd1.Unit",
            "ActiveState",
        ],
        deadline,
    )?;
    let value = string_property(&reply)?;
    if ![
        "active",
        "reloading",
        "inactive",
        "failed",
        "activating",
        "deactivating",
        "maintenance",
        "refreshing",
    ]
    .contains(&value)
    {
        return Err(invalid());
    }
    data.insert("active_state".into(), json!(value));
    if value == "active" {
        Ok(())
    } else {
        Err(Failure::new(Status::Error, "unit_not_active"))
    }
}

fn pipewire(deadline: Instant, data: &mut BTreeMap<String, Value>) -> Result<(), Failure> {
    let result = helper(
        option_env!("EMAKI_PW_CLI").unwrap_or("pw-cli"),
        &["info", "0"],
        deadline,
    )?;
    if !result.success {
        return Err(helper_failure(&result.stderr));
    }
    let text = std::str::from_utf8(&result.stdout).map_err(|_| invalid())?;
    let value = text
        .lines()
        .filter_map(|line| line.trim().split_once(':'))
        .find_map(|(key, value)| {
            (key.trim() == "version").then_some(value.trim().trim_matches('"'))
        })
        .ok_or_else(invalid)?;
    data.insert("version".into(), json!(version(value)?));
    Ok(())
}
