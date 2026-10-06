// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
// Unit tests for packaging/mirror/pointer-worker.js with a fake R2 binding (node --test).
// The same route table is checked against tests/pointer-server.py by tests/test-publish.py.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import worker from "../packaging/mirror/pointer-worker.js";

const cases = JSON.parse(readFileSync(new URL("./fixtures/pointer-routes.json", import.meta.url)));

function bucket(pointers, fail = false) {
  return {
    async get(key) {
      if (fail) throw new Error("R2 unavailable");
      const name = key.replace(/^pointers\//, "");
      if (!key.startsWith("pointers/") || !(name in pointers)) return null;
      return { text: async () => pointers[name] };
    },
  };
}

for (const item of cases) {
  test(`${item.path} with ${JSON.stringify(item.pointer)} -> ${item.status}`, async () => {
    let passed = null;
    globalThis.fetch = async (request) => {
      passed = request;
      return new Response("bucket", { status: 200 });
    };
    const response = await worker.fetch(new Request(`https://pkgs.emaki.sh${item.path}`), { PKGS: bucket(item.pointer) });
    if (item.status === "fallthrough") {
      assert.ok(passed, "request reached the bucket");
      assert.equal(new URL(passed.url).pathname, item.path);
      assert.equal(await response.text(), "bucket");
      return;
    }
    assert.equal(passed, null, "the bucket must not be asked");
    assert.equal(response.status, item.status);
    assert.equal(response.headers.get("cache-control"), "no-store");
    if (item.status === 302) {
      assert.equal(response.headers.get("location"), `https://pkgs.emaki.sh${item.location}`);
    }
  });
}

test("an unreadable pointer is a 503, never a fall-through to a stale object", async () => {
  globalThis.fetch = async () => assert.fail("must not fall through");
  const response = await worker.fetch(new Request("https://pkgs.emaki.sh/stable/x86_64/emaki.db"), {
    PKGS: bucket({}, true),
  });
  assert.equal(response.status, 503);
});

test("HEAD gets the same redirect", async () => {
  const response = await worker.fetch(new Request("https://pkgs.emaki.sh/testing/x86_64/emaki.db", { method: "HEAD" }), {
    PKGS: bucket({ testing: "20261012T183001Z" }),
  });
  assert.equal(response.status, 302);
});
