// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
// Unit tests for packaging/mirror/pointer-worker.js with a fake R2 binding (node --test).
// The same route table is checked against tests/pointer-server.py by tests/test-publish.py.
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
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

const sourceId = "abcd0123".repeat(8);
const sourceNames = ["SOURCES-ISO.txt", "ARCH-SOURCES.json", "MISSING-SOURCES.json"];
const imageCases = [];
for (const name of sourceNames) {
  const path = `/iso/0.2.0/${name}`;
  imageCases.push({ path, pointer: null, status: "fallthrough" });
  imageCases.push({ path, pointer: `${sourceId}\n`, status: 302,
    location: `/iso/0.2.0/source-snapshots/${sourceId}/${name}` });
  for (const pointer of ["", "bad", sourceId.toUpperCase(), sourceId.slice(1), `../${sourceId}`]) {
    imageCases.push({ path, pointer, status: 503 });
  }
}
for (const path of ["/iso/0.2.0/image.iso", `/iso/0.2.0/source-snapshots/${sourceId}/SOURCES-ISO.txt`,
  "/iso/not-a-version/SOURCES-ISO.txt", "/iso/0.2.0/SOURCES-ISO.txt.sig"]) {
  imageCases.push({ path, pointer: sourceId, status: "fallthrough" });
}
for (const item of imageCases) {
  test(`image ${item.path} with ${JSON.stringify(item.pointer)} -> ${item.status}`, async () => {
    for (const method of ["GET", "HEAD"]) {
      let passed = false;
      globalThis.fetch = async () => {
        passed = true;
        return new Response("legacy");
      };
      const response = await worker.fetch(new Request(`https://dl.emaki.sh${item.path}`, { method }), {
        DL: { get: async key => {
          assert.equal(key, "iso/0.2.0/source-pointer");
          return item.pointer === null ? null : { text: async () => item.pointer };
        } },
      });
      if (sourceNames.some(name => item.path === `/iso/0.2.0/${name}`)) {
        assert.equal(response.headers.get("x-emaki-source-pointer"), "1");
      } else {
        assert.equal(response.headers.get("x-emaki-source-pointer"), null);
      }
      assert.equal(passed, item.status === "fallthrough");
      if (passed) {
        assert.equal(await response.text(), "legacy");
      } else {
        assert.equal(response.status, item.status);
        assert.equal(response.headers.get("cache-control"), "no-store");
        if (item.location) assert.equal(response.headers.get("location"), `https://dl.emaki.sh${item.location}`);
      }
    }
  });
}

test("image binding or pointer read failures never serve stale metadata", async () => {
  globalThis.fetch = async () => assert.fail("must not fall through");
  for (const env of [{}, { DL: { get: async () => { throw new Error("unavailable"); } } },
    { DL: { get: async () => ({ text: async () => { throw new Error("unreadable"); } }) } }]) {
    const response = await worker.fetch(new Request("https://dl.emaki.sh/iso/0.2.0/SOURCES-ISO.txt"), env);
    assert.equal(response.status, 503);
    assert.equal(response.headers.get("cache-control"), "no-store");
  }
});

test("local image server uses the same routes and rejects unreadable pointers", () => {
  const result = spawnSync("python3", ["-c", `
import importlib.util
import json
import tempfile
from pathlib import Path
import sys
spec = importlib.util.spec_from_file_location("pointer_server", "tests/pointer-server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
with tempfile.TemporaryDirectory() as directory:
    pointer = Path(directory) / 'iso/0.2.0/source-pointer'
    pointer.parent.mkdir(parents=True)
    for item in json.loads(sys.argv[1]):
        if pointer.exists():
            pointer.unlink()
        if item['pointer'] is not None:
            pointer.write_text(item['pointer'])
        expected = None if item['status'] == 'fallthrough' else (item['status'], item.get('location'))
        assert server.route(directory, item['path']) == expected, item
    pointer.unlink()
    pointer.mkdir()
    assert server.route(directory, '/iso/0.2.0/SOURCES-ISO.txt') == (503, None)
    pointer.rmdir()
    pointer.write_bytes(bytes([255]))
    assert server.route(directory, '/iso/0.2.0/SOURCES-ISO.txt') == (503, None)
`, JSON.stringify(imageCases)], { encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
});
