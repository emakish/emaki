// Shared by the QML client and the transcript tests. No secret is retained here.
function initial() {
    return { connected: false, ready: false, serial: 0, pending: {}, version: "",
        inventory: null, plan: null, deadline: 0, jobId: "", lastLogSeq: 0,
        phase: "", phasePct: 0, totalPct: 0, indeterminate: false,
        running: false, confirming: false, planning: false, probing: false,
        outcome: "", error: null, notice: "Connecting to the installer…",
        logs: [], cancelPending: false, cancelMessage: "", started: 0, seconds: 0 };
}
function invalidate(s) { s.plan = null; s.deadline = 0; }
function request(s, type, fields) {
    if (type === "plan" || type === "probe") invalidate(s);
    if (type === "plan") s.planning = true;
    if (type === "probe") s.probing = true;
    if (type === "confirm") s.confirming = true;
    if (type === "cancel") s.cancelPending = true;
    const id = "ui-" + (++s.serial);
    s.pending[id] = type;
    return Object.assign({ type: type, id: id }, fields || {});
}
function disconnected(s) {
    s.connected = false; s.ready = false;
    s.planning = false; s.probing = false;
    s.pending = {};
    // Confirm can have reached the worker before the connection was lost.
    // Keep that intent until hello tells us whether there is a running job.
    invalidate(s);
    s.notice = "Connection lost. Reconnecting…";
}
function expire(s, now) {
    if (s.plan && now >= s.deadline) {
        invalidate(s);
        s.notice = "The review expired. Enter your password again to prepare a new plan.";
        return true;
    }
    return false;
}
function receive(s, m, now) {
    const out = [];
    const pending = s.pending[m.for_id || m.id];
    if (m.job_id && s.jobId && m.job_id !== s.jobId && pending !== "confirm") return out;
    switch (m.type) {
    case "hello":
        if (m.proto !== 1) { s.notice = "This worker uses an unsupported protocol."; break; }
        s.connected = true; s.ready = true; s.version = m.emaki_version;
        s.notice = "";
        if (m.busy_job || s.jobId) {
            if (m.busy_job && m.busy_job !== s.jobId) {
                s.jobId = m.busy_job; s.lastLogSeq = 0; s.logs = [];
                s.started = now; s.running = true; s.outcome = "";
            }
            out.push(request(s, "resume", {job_id: s.jobId, since_seq: s.lastLogSeq}));
        } else {
            if (s.confirming) s.notice = "The confirmation could not be recovered. Check the worker log before preparing a new plan.";
            s.confirming = false;
            out.push(request(s, "probe"));
        }
        break;
    case "inventory":
        if (pending !== "probe") break;
        s.inventory = m; s.probing = false;
        break;
    case "plan_ack":
        if (pending !== "plan") break;
        s.planning = false; s.plan = m;
        s.deadline = m.token ? now + m.expires_s * 1000 : 0;
        s.notice = "";
        break;
    case "reply":
        if (pending === "confirm") {
            s.confirming = false;
            if (m.ok) {
                s.jobId = m.job_id; s.lastLogSeq = 0; s.logs = [];
                s.running = true; s.outcome = ""; s.error = null;
                s.cancelPending = false; s.cancelMessage = ""; s.started = now;
                s.phase = "prepare_disk"; s.phasePct = null; s.totalPct = 0; s.indeterminate = true;
                invalidate(s);
            }
        }
        if (pending === "probe") s.probing = false;
        if (pending === "plan") s.planning = false;
        if (pending === "cancel" && !s.running) s.cancelPending = false;
        if (!m.ok) {
            s.notice = m.msg || m.code || "The request failed.";
            if (m.code === "token_expired" || m.code === "token_invalid") {
                invalidate(s);
                s.notice += " Enter your password again to prepare a new plan.";
            }
            if (m.code === "job_not_found") {
                s.running = false; s.confirming = false; s.jobId = "";
                s.outcome = "error";
                s.error = {message: "The worker no longer has this job. Inspect the installation log before starting again.", retryable: false};
            }
            if (m.code === "busy") out.push(request(s, "hello", {proto: 1}));
        } else if (pending === "save_log") s.notice = "Log saved to the selected removable medium.";
        else if (pending === "reboot") s.notice = "Restart requested…";
        break;
    case "state": case "progress":
        if (!m.job_id) break;
        s.jobId = m.job_id; s.running = true; s.confirming = false;
        s.phase = m.phase; s.phasePct = m.phase_pct; s.totalPct = m.total_pct;
        s.indeterminate = m.indeterminate;
        if (!s.started) s.started = now;
        break;
    case "log":
        if (!m.job_id || m.seq <= s.lastLogSeq) break;
        s.lastLogSeq = m.seq;
        s.logs = s.logs.concat([m.line]).slice(-100);
        break;
    case "cancel_ack":
        s.cancelPending = false;
        s.cancelMessage = m.disk_changed ? "Cancelled at a phase boundary. Disk changes have not been undone." : "Cancelled before any disk changes.";
        break;
    case "error":
        s.error = m; s.running = false; s.confirming = false;
        s.outcome = "error"; s.cancelPending = false;
        break;
    case "done":
        s.running = false; s.confirming = false; s.outcome = "done";
        s.seconds = m.seconds; s.totalPct = 100; s.cancelPending = false;
        break;
    }
    if (m.id) delete s.pending[m.id];
    return out;
}
function loginFromName(name) {
    return name.toLowerCase().replace(/[^a-z0-9 _-]/g, "").trim().replace(/\s+/g, "-").replace(/^[^a-z_]+/, "").slice(0, 32);
}
function accountError(name, login, password, confirm, hostname) {
    if (!/^[a-z_][a-z0-9_-]{0,31}$/.test(login) || ["root", "greeter", "live", "nobody"].indexOf(login) !== -1)
        return "Use 1–32 lowercase letters, digits, _ or - for your login, starting with a letter or _. This login must not be reserved.";
    if (Array.from(name).length > 128 || /[\x00-\x1f:\x7f]/.test(name)) return "Your name must be at most 128 characters and contain no colon or control characters.";
    if (!password || /[\x00\r\n]/.test(password) || unescape(encodeURIComponent(password)).length > 1024)
        return "Enter a password without line breaks (at most 1024 UTF-8 bytes).";
    if (password !== confirm) return "The passwords do not match.";
    if (!/^[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$/.test(hostname)) return "Computer name: 1–63 letters, digits or hyphens, beginning and ending with a letter or digit.";
    return "";
}
function diskReason(d) {
    if (d.reason) return typeof d.reason === "string" ? d.reason : d.reason.msg;
    if (d.refusal_reason) return d.refusal_reason;
    if (d.is_boot_medium) return "The live boot medium cannot be a target.";
    if ((d.partitions || []).some(p => !!p.mountpoint)) return "A partition on this disk is mounted.";
    if (d.size_bytes < 24 * 1073741824) return "The disk must be at least 24 GiB.";
    return "";
}
function alongside(d) {
    return !!d && (d.partitions || []).some(p => p.os_hint === "windows" &&
        ((p.shrink && !p.shrink.reason) || (d.shrink && !d.shrink.reason)));
}
