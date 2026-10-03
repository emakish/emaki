// Replay the same transcript files served by mock-worker.py through production JS.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const context = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(__dirname, "../Protocol.js"), "utf8"), context);
const P = context;
const files = fs.readdirSync(path.join(__dirname, "transcripts")).filter(f => f.endsWith(".json") && f !== "render.json");
for (const file of files) {
    const rows = JSON.parse(fs.readFileSync(path.join(__dirname, "transcripts", file)));
    const s = P.initial();
    let seq = 0, queue = [], resumeCursor = 0;
    for (const row of rows) {
        if (row.expect === "disconnect") {
            resumeCursor = s.lastLogSeq;
            P.disconnected(s);
            assert.equal(s.ready, false);
            continue;
        }
        if (file === "error-retry.json" && row.expect === "probe" && s.outcome === "error") {
            assert.equal(s.error.retryable, true);
            s.outcome = ""; s.jobId = ""; s.error = null;
        }
        if (row.action === "gparted_exit") assert.ok(s.plan.token);
        const fields = row.expect === "hello" ? {proto: 1} : row.expect === "confirm" ? {plan_id: s.plan.plan_id, token: s.plan.token} : row.expect === "plan" ? {config: {user: {password: "in-memory-" + Math.random()}}} : {};
        const request = queue.shift() || P.request(s, row.expect, fields);
        assert.equal(request.type, row.expect);
        if (row.expect === "probe" || row.expect === "plan") assert.equal(s.plan, null);
        if (row.expect === "resume") assert.equal(request.since_seq, resumeCursor);
        for (const response of row.responses) {
            const event = ["state", "progress", "log", "done", "error", "cancel_ack"].includes(response.type);
            const message = {...response, seq: ++seq, id: event ? "" : request.id};
            if (message.type === "reply") message.for_id = request.id;
            queue.push(...P.receive(s, message, 10000 + seq));
            if (message.code === "token_expired") assert.equal(s.plan, null);
            if (message.type === "plan_ack" && !message.errors.length) assert.equal(s.deadline, 610000 + seq);
            if (message.type === "log") assert.ok(s.logs.includes(message.line));
        }
        if (row.expect === "plan") assert.ok(!JSON.stringify(s).includes(request.config.user.password));
    }
    if (file === "plan-errors.json") {
        assert.equal(s.plan.errors[0].code, "manual_layout"); assert.ok(!s.plan.token);
    } else if (file === "cancel.json") {
        assert.equal(s.outcome, "error"); assert.match(s.cancelMessage, /not been undone/);
    } else assert.equal(s.outcome, "done");
    console.log("PASS " + file);
}
// Expiry before confirm; hello's global seq must not advance the log cursor.
const s = P.initial();
s.plan = {token: "fixture"}; s.deadline = 100;
assert.equal(P.expire(s, 99), false);
assert.equal(P.expire(s, 100), true);
assert.equal(s.plan, null);
s.jobId = "known"; s.running = true; s.lastLogSeq = 9;
const res = P.receive(s, {type: "hello", proto: 1, emaki_version: "0.1.0", busy_job: "known", seq: 900}, 1000);
assert.equal(res[0].since_seq, 9);
P.receive(s, {type: "log", job_id: "known", seq: 10, line: "replayed"}, 1001);
P.receive(s, {type: "log", job_id: "known", seq: 10, line: "replayed"}, 1002);
assert.equal(s.logs.length, 1);
P.receive(s, {type: "done", job_id: "unrelated", seconds: 5}, 1003);
assert.equal(s.outcome, "");
assert.equal(P.loginFromName("Alex Morgan"), "alex-morgan");
const secret = "memory-" + Math.random();
assert.equal(P.accountError("Alex", "alex", secret, secret, "emaki"), "");
assert.equal(P.accountError("😀".repeat(128), "alex", secret, secret, "emaki"), "");
assert.ok(P.accountError("😀".repeat(129), "alex", secret, secret, "emaki"));
for (const login of ["root", "live", "greeter", "nobody", "9alex", "Alex", "a".repeat(33)]) assert.ok(P.accountError("Alex", login, secret, secret, "emaki"));
assert.ok(P.accountError("A:lex", "alex", secret, secret, "emaki"));
assert.ok(P.accountError("Alex", "alex", secret, "different", "emaki"));
assert.ok(P.accountError("Alex", "alex", "é".repeat(513), "é".repeat(513), "emaki"));
assert.ok(P.accountError("Alex", "alex", secret, secret, "-emaki"));
assert.equal(P.alongside({partitions: [{os_hint: "windows"}], shrink: null}), false);
assert.equal(P.alongside({partitions: [{os_hint: "macos"}], shrink: {min_bytes: 1}}), false);
assert.equal(P.alongside({partitions: [{os_hint: "windows"}], shrink: {min_bytes: 1}}), true);
console.log("PASS expiry, replay cursor, duplicate logs, job isolation, account validation, alongside gating");
