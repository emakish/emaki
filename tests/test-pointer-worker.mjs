// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
// Unit tests for packaging/mirror/pointer-worker.js with a fake R2 binding (node --test).
// The same route table is checked against tests/pointer-server.py by tests/test-publish.py;
// the image route and its answers are checked against it here.
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import worker, { IMAGE_ROUTE } from "../packaging/mirror/pointer-worker.js";

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
      assert.equal(response.headers.get("x-emaki-image"), null);
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

// dl.emaki.sh/iso/<version>/emaki-<version>-x86_64.iso straight from the image bucket.
//
// A fake R2 binding with the semantics of the Workers R2 API (developers.cloudflare.com
// r2/api/workers/workers-api-reference, and workerd src/workerd/api/r2-bucket.c++): head(key)
// answers an R2Object or null; get(key, {range, onlyIf}) answers an R2ObjectBody, an R2Object
// without body when onlyIf fails, or null. onlyIf.etagMatches takes the unquoted etag (a quoted
// one throws TypeError), httpEtag is the quoted one; a range offset at or past the end throws
// like R2's InvalidRange (10039); a length past the end returns fewer bytes.
const IMAGE = "/iso/0.3.0/emaki-0.3.0-x86_64.iso";
const IMAGE_KEY = IMAGE.slice(1);
const ETAG = "c95669d2ea3b4a391aea7da1ee1857ae-75";
const UPLOADED = new Date("2026-10-07T19:30:43.512Z");
const LAST_MODIFIED = "Wed, 07 Oct 2026 19:30:43 GMT";
const DATA = Uint8Array.from({ length: 1000 }, (_, index) => (index * 7 + 3) % 251);

function r2Object(key, item) {
  return {
    key, size: item.data.length, etag: item.etag, httpEtag: `"${item.etag}"`, uploaded: item.uploaded,
    httpMetadata: item.httpMetadata, customMetadata: {},
    writeHttpMetadata(headers) {
      const metadata = this.httpMetadata;
      if (metadata.contentType) headers.set("Content-Type", metadata.contentType);
      if (metadata.contentLanguage) headers.set("Content-Language", metadata.contentLanguage);
      if (metadata.contentDisposition) headers.set("Content-Disposition", metadata.contentDisposition);
      if (metadata.contentEncoding) headers.set("Content-Encoding", metadata.contentEncoding);
      if (metadata.cacheControl) headers.set("Cache-Control", metadata.cacheControl);
      if (metadata.cacheExpiry) headers.set("Expires", metadata.cacheExpiry.toUTCString());
    },
  };
}

function imageBucket(objects = {}, { fail = null, replaceAfterHead = null } = {}) {
  const calls = [];
  const store = {
    [IMAGE_KEY]: { data: DATA, etag: ETAG, uploaded: UPLOADED, httpMetadata: { contentType: "application/octet-stream" } },
    ...objects,
  };
  return {
    calls,
    async head(key) {
      calls.push({ op: "head", key });
      if (fail === "head") throw new Error("R2 unavailable");
      const item = store[key];
      const object = item ? r2Object(key, item) : null;
      if (replaceAfterHead) store[key] = replaceAfterHead;
      return object;
    },
    async get(key, options = {}) {
      calls.push({ op: "get", key, options });
      if (fail === "get") throw new Error("R2 unavailable");
      const item = store[key];
      if (!item) return null;
      const object = r2Object(key, item);
      const { onlyIf, range, ...rest } = options;
      assert.deepEqual(rest, {}, "options this fake does not implement");
      if (onlyIf !== undefined) {
        assert.ok(!(onlyIf instanceof Headers), "onlyIf as Headers is not implemented here");
        assert.deepEqual(Object.keys(onlyIf).filter(name => name !== "etagMatches"), [], "conditions this fake does not implement");
        if (/^".*"$/.test(onlyIf.etagMatches)) throw new TypeError("Conditional ETag should not be wrapped in quotes");
        if (onlyIf.etagMatches !== "*" && onlyIf.etagMatches !== item.etag) return object;
      }
      let offset = 0;
      let length = item.data.length;
      if (range !== undefined) {
        assert.ok(!(range instanceof Headers), "range as Headers is not implemented here");
        if ("suffix" in range) {
          offset = Math.max(0, length - range.suffix);
          length -= offset;
        } else {
          offset = range.offset ?? 0;
          if (offset >= item.data.length) throw new Error("get: The requested range is not satisfiable (10039)");
          length = Math.min(range.length ?? length - offset, item.data.length - offset);
        }
        object.range = { offset, length };
      }
      object.body = new Blob([item.data.subarray(offset, offset + length)]).stream();
      return object;
    },
  };
}

async function ask(headers = {}, { method = "GET", path = IMAGE, bucket = imageBucket() } = {}) {
  globalThis.fetch = async () => assert.fail("the image must not fall through to the bucket's domain");
  const response = await worker.fetch(new Request(`https://dl.emaki.sh${path}`, { method, headers }), { DL: bucket, PKGS: {} });
  // Every answer of the image branch is marked, so publication can tell it from the bucket's domain.
  assert.equal(response.headers.get("x-emaki-image"), "1", `${method} ${path} ${JSON.stringify(headers)}`);
  return { response, body: new Uint8Array(await response.arrayBuffer()), bucket };
}

function assertImageHeaders(response, length) {
  assert.equal(response.headers.get("etag"), `"${ETAG}"`);
  assert.equal(response.headers.get("last-modified"), LAST_MODIFIED);
  assert.equal(response.headers.get("accept-ranges"), "bytes");
  assert.equal(response.headers.get("content-type"), "application/octet-stream");
  assert.equal(response.headers.get("content-length"), String(length));
  assert.equal(response.headers.get("cache-control"), null, "no caching beyond the object's metadata");
}

async function assertWhole(headers, method = "GET") {
  const { response, body, bucket } = await ask(headers, { method });
  assert.equal(response.status, 200);
  assertImageHeaders(response, DATA.length);
  assert.equal(response.headers.get("content-range"), null);
  if (method === "GET") {
    assert.deepEqual(body, DATA);
    assert.deepEqual(bucket.calls.at(-1), { op: "get", key: IMAGE_KEY, options: { onlyIf: { etagMatches: ETAG } } });
  } else {
    assert.equal(body.length, 0);
    assert.deepEqual(bucket.calls, [{ op: "head", key: IMAGE_KEY }]);
  }
}

async function assertPart(headers, start, end) {
  const { response, body, bucket } = await ask(headers);
  assert.equal(response.status, 206, JSON.stringify(headers));
  assertImageHeaders(response, end - start + 1);
  assert.equal(response.headers.get("content-range"), `bytes ${start}-${end}/${DATA.length}`);
  assert.deepEqual(body, DATA.subarray(start, end + 1));
  assert.deepEqual(bucket.calls, [{ op: "head", key: IMAGE_KEY },
    { op: "get", key: IMAGE_KEY, options: { onlyIf: { etagMatches: ETAG }, range: { offset: start, length: end - start + 1 } } }]);
}

async function assertStatus(headers, status, method = "GET") {
  const { response, body, bucket } = await ask(headers, { method });
  assert.equal(response.status, status, JSON.stringify(headers));
  assert.ok(bucket.calls.every(call => call.op === "head"), "no bytes are read for a refusal");
  return { response, body };
}

test("the image: GET answers the whole object with the bucket's validators and metadata", async () => {
  await assertWhole({});
});

test("the image: HEAD reads only the metadata and ignores Range", async () => {
  await assertWhole({}, "HEAD");
  await assertWhole({ Range: "bytes=0-99" }, "HEAD");
  await assertWhole({ Range: "bytes=0-99", "If-Range": `"${ETAG}"` }, "HEAD");
});

test("the image: the three single range forms answer 206", async () => {
  await assertPart({ Range: "bytes=0-99" }, 0, 99);
  await assertPart({ Range: "bytes=500-500" }, 500, 500);
  await assertPart({ Range: "bytes=990-5000" }, 990, 999);
  await assertPart({ Range: "bytes=900-" }, 900, 999);
  await assertPart({ Range: "bytes=0-" }, 0, 999);
  await assertPart({ Range: "bytes=-100" }, 900, 999);
  await assertPart({ Range: "bytes=-5000" }, 0, 999);
  await assertPart({ Range: "Bytes=10-19" }, 10, 19);
  await assertPart({ Range: "bytes=10-19," }, 10, 19);
});

test("the image: an unsatisfiable range answers 416 with the size", async () => {
  for (const value of ["bytes=1000-", "bytes=1000-1001", "bytes=5000-", "bytes=-0"]) {
    const { response } = await assertStatus({ Range: value }, 416);
    assert.equal(response.headers.get("content-range"), "bytes */1000");
    assert.equal(response.headers.get("cache-control"), "no-store");
  }
});

test("the image: several ranges or a malformed Range answer the whole image", async () => {
  for (const value of ["bytes=0-1,5-6", "bytes=0-1, -5", "bytes=5-3", "bytes=abc", "bytes=", "bytes=-",
    "bytes=1-2-3", "bytes=0x10-", "bytes=1.5-2", "items=0-5", "bytes 0-5", "0-5"]) {
    await assertWhole({ Range: value });
  }
});

test("the image: Chromium's resume request (Range from an offset, If-Range with the ETag) answers 206", async () => {
  await assertPart({ Range: "bytes=600-", "If-Range": `"${ETAG}"` }, 600, 999);
});

test("the image: Chromium's request without If-Range (If-Match and If-Unmodified-Since) answers 206", async () => {
  await assertPart({ Range: "bytes=600-", "If-Match": `"${ETAG}"`, "If-Unmodified-Since": LAST_MODIFIED }, 600, 999);
});

test("the image: If-Range with the exact Last-Modified, in any HTTP-date form, answers 206", async () => {
  for (const date of [LAST_MODIFIED, "Wednesday, 07-Oct-26 19:30:43 GMT", "Wed Oct  7 19:30:43 2026"]) {
    await assertPart({ Range: "bytes=600-", "If-Range": date }, 600, 999);
  }
});

test("the image: a stale or weak If-Range answers the whole image", async () => {
  for (const validator of ['"another"', `W/"${ETAG}"`, `"${ETAG}", "another"`, ETAG, `"${ETAG}`,
    "Wed, 07 Oct 2026 19:30:42 GMT", "Wed, 07 Oct 2026 19:30:44 GMT", "yesterday", ""]) {
    await assertWhole({ Range: "bytes=600-", "If-Range": validator });
  }
  // A stale If-Range ignores Range even when the range could not be satisfied.
  await assertWhole({ Range: "bytes=5000-", "If-Range": '"another"' });
});

test("the image: If-Range without Range is ignored", async () => {
  await assertWhole({ "If-Range": '"another"' });
});

test("the image: If-None-Match and If-Modified-Since answer 304 without bytes", async () => {
  for (const method of ["GET", "HEAD"]) {
    for (const headers of [{ "If-None-Match": `"${ETAG}"` }, { "If-None-Match": `W/"${ETAG}"` },
      { "If-None-Match": `"another", "${ETAG}"` }, { "If-None-Match": "*" },
      { "If-Modified-Since": LAST_MODIFIED }, { "If-Modified-Since": "Thu, 08 Oct 2026 00:00:00 GMT" },
      { "If-None-Match": `"${ETAG}"`, Range: "bytes=0-9" }]) {
      const { response, body } = await assertStatus(headers, 304, method);
      assert.equal(body.length, 0);
      assert.equal(response.headers.get("etag"), `"${ETAG}"`);
      assert.equal(response.headers.get("last-modified"), LAST_MODIFIED);
      assert.equal(response.headers.get("content-range"), null);
    }
  }
});

test("the image: a fresh If-None-Match or If-Modified-Since answers the image", async () => {
  await assertWhole({ "If-None-Match": '"another"' });
  await assertWhole({ "If-Modified-Since": "Wed, 07 Oct 2026 19:30:42 GMT" });
  await assertWhole({ "If-Modified-Since": "not a date" });
  // If-Modified-Since is ignored next to If-None-Match (RFC 9110, 13.1.3).
  await assertWhole({ "If-None-Match": '"another"', "If-Modified-Since": LAST_MODIFIED });
});

test("the image: If-Match and If-Unmodified-Since failures answer 412", async () => {
  for (const method of ["GET", "HEAD"]) {
    for (const headers of [{ "If-Match": '"another"' }, { "If-Match": `W/"${ETAG}"` }, { "If-Match": ETAG },
      { "If-Unmodified-Since": "Wed, 07 Oct 2026 19:30:42 GMT" },
      { "If-Match": '"another"', "If-None-Match": `"${ETAG}"` },
      { "If-Match": '"another"', Range: "bytes=0-9", "If-Range": `"${ETAG}"` }]) {
      const { response } = await assertStatus(headers, 412, method);
      assert.equal(response.headers.get("cache-control"), "no-store");
    }
  }
});

test("the image: matching If-Match or If-Unmodified-Since answers the image", async () => {
  await assertWhole({ "If-Match": `"${ETAG}"` });
  await assertWhole({ "If-Match": `"another", "${ETAG}"` });
  await assertWhole({ "If-Match": "*" });
  await assertWhole({ "If-Unmodified-Since": LAST_MODIFIED });
  await assertWhole({ "If-Unmodified-Since": "garbage" });
  // If-Unmodified-Since is ignored next to If-Match (RFC 9110, 13.1.4).
  await assertWhole({ "If-Match": `"${ETAG}"`, "If-Unmodified-Since": "Wed, 07 Oct 2026 19:30:42 GMT" });
});

test("the image: other methods answer 405 with Allow and read nothing", async () => {
  for (const method of ["POST", "PUT", "DELETE", "OPTIONS", "PATCH"]) {
    const { response, bucket } = await ask({}, { method });
    assert.equal(response.status, 405);
    assert.equal(response.headers.get("allow"), "GET, HEAD");
    assert.deepEqual(bucket.calls, []);
  }
});

test("the image: a missing image is a 404 that no cache keeps", async () => {
  for (const method of ["GET", "HEAD"]) {
    const { response, bucket } = await ask({ Range: "bytes=0-9" }, { method, path: "/iso/9.9.9/emaki-9.9.9-x86_64.iso" });
    assert.equal(response.status, 404);
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert.deepEqual(bucket.calls, [{ op: "head", key: "iso/9.9.9/emaki-9.9.9-x86_64.iso" }]);
  }
});

test("the image: an unreadable bucket is a 503 that no cache keeps", async () => {
  for (const fail of ["head", "get"]) {
    const { response } = await ask({}, { bucket: imageBucket({}, { fail }) });
    assert.equal(response.status, 503);
    assert.equal(response.headers.get("cache-control"), "no-store");
  }
});

test("the image: bytes never go out under the ETag of another object", async () => {
  const other = { data: DATA.map(byte => byte ^ 255), etag: "another", uploaded: UPLOADED, httpMetadata: {} };
  const { response, body, bucket } = await ask({ Range: "bytes=0-9" }, { bucket: imageBucket({}, { replaceAfterHead: other }) });
  assert.equal(response.status, 503);
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.notDeepEqual(body, other.data.subarray(0, 10));
  assert.equal(bucket.calls.at(-1).options.onlyIf.etagMatches, ETAG);
});

test("the image: the object's own HTTP metadata is kept, nothing is added", async () => {
  const expiry = new Date("2027-01-01T00:00:00Z");
  const bucket = imageBucket({ [IMAGE_KEY]: { data: DATA, etag: ETAG, uploaded: UPLOADED, httpMetadata: {
    contentType: "application/x-iso9660-image", contentDisposition: "attachment", cacheControl: "public, max-age=60",
    cacheExpiry: expiry } } });
  for (const headers of [{}, { Range: "bytes=0-9" }]) {
    const { response } = await ask(headers, { bucket });
    assert.equal(response.headers.get("content-type"), "application/x-iso9660-image");
    assert.equal(response.headers.get("content-disposition"), "attachment");
    assert.equal(response.headers.get("cache-control"), "public, max-age=60");
    assert.equal(response.headers.get("expires"), expiry.toUTCString());
  }
  const { response } = await ask({ "If-None-Match": `"${ETAG}"` }, { bucket });
  assert.equal(response.status, 304);
  assert.equal(response.headers.get("cache-control"), "public, max-age=60");
  assert.equal(response.headers.get("expires"), expiry.toUTCString());
});

test("the image without the DL binding falls through to the bucket's domain, as before", async () => {
  let passed = null;
  globalThis.fetch = async request => {
    passed = request;
    return new Response("bucket");
  };
  const response = await worker.fetch(new Request(`https://dl.emaki.sh${IMAGE}`, { headers: { Range: "bytes=0-9" } }), { PKGS: {} });
  assert.equal(new URL(passed.url).pathname, IMAGE);
  assert.equal(passed.headers.get("range"), "bytes=0-9");
  assert.equal(await response.text(), "bucket");
  assert.equal(response.headers.get("x-emaki-image"), null, "a fall-through is never marked");
});

test("other image files and source companions keep their answers next to the image route", async () => {
  for (const path of ["/iso/0.3.0/emaki-0.3.0-x86_64.iso.sha256", "/iso/0.3.0/emaki-0.3.0-x86_64.iso.sig",
    "/iso/0.3.0/emaki-0.2.0-x86_64.iso", "/iso/0.3.0/closure.txt", "/iso/0.3.0/emaki-signing-key.asc",
    "/iso/0.3/emaki-0.3-x86_64.iso", "/iso/0.3.0/emaki-0.3.0-x86_64.iso/", "/sources/sha256/x/emaki-0.3.0-x86_64.iso"]) {
    let passed = false;
    globalThis.fetch = async () => {
      passed = true;
      return new Response("bucket");
    };
    const bucket = imageBucket();
    const response = await worker.fetch(new Request(`https://dl.emaki.sh${path}`), { DL: bucket, PKGS: {} });
    assert.ok(passed, path);
    assert.equal(await response.text(), "bucket");
    assert.equal(response.headers.get("x-emaki-image"), null, path);
    assert.deepEqual(bucket.calls, [], path);
  }
  // A companion without a source pointer still falls through, and with one still redirects.
  globalThis.fetch = async () => new Response("legacy");
  const bucket = imageBucket();
  let response = await worker.fetch(new Request("https://dl.emaki.sh/iso/0.3.0/SOURCES-ISO.txt"), { DL: bucket });
  assert.equal(await response.text(), "legacy");
  assert.equal(response.headers.get("x-emaki-source-pointer"), "1");
  assert.equal(response.headers.get("x-emaki-image"), null);
  const pointed = imageBucket();
  pointed.get = async key => {
    assert.equal(key, "iso/0.3.0/source-pointer");
    return { text: async () => `${sourceId}\n` };
  };
  response = await worker.fetch(new Request("https://dl.emaki.sh/iso/0.3.0/SOURCES-ISO.txt"), { DL: pointed });
  assert.equal(response.status, 302);
  assert.equal(response.headers.get("location"), `https://dl.emaki.sh/iso/0.3.0/source-snapshots/${sourceId}/SOURCES-ISO.txt`);
});

test("the image: only exact HTTP-dates count, never a lenient or rolled-over reading", async () => {
  for (const date of ["Tue, 06 Oct 2026 43:30:43 GMT", "Wed, 07 Oct 2026 19:30:43 UTC", "Wed, 7 Oct 2026 19:30:43 GMT",
    "2026-10-07T19:30:43Z", "Wed, 07 Oct 2026 19:30:43.512 GMT", `${LAST_MODIFIED}, ${LAST_MODIFIED}`,
    "Wed, 07 Oct 2026 19:30:43 gmt"]) {
    await assertWhole({ Range: "bytes=600-", "If-Range": date });
    await assertWhole({ "If-Modified-Since": date });
  }
  await assertWhole({ "If-Unmodified-Since": "Tue, 06 Oct 2026 43:30:42 GMT" });
  // A two-digit year more than 50 years ahead is the most recent such year in the past (1994).
  await assertWhole({ "If-Modified-Since": "Sunday, 06-Nov-94 08:49:37 GMT" });
  await assertStatus({ "If-Unmodified-Since": "Sunday, 06-Nov-94 08:49:37 GMT" }, 412);
  await assertStatus({ "If-Modified-Since": "Wed Oct  7 19:30:43 2026" }, 304);
});

test("the image: range ends past the size, huge numbers and an empty object", async () => {
  await assertPart({ Range: "bytes=0-0" }, 0, 0);
  await assertPart({ Range: "bytes=999-" }, 999, 999);
  await assertPart({ Range: "bytes=-1" }, 999, 999);
  await assertPart({ Range: "bytes=0-99999999999999999999" }, 0, 999);
  await assertPart({ Range: "bytes=000-009" }, 0, 9);
  await assertStatus({ Range: "bytes=99999999999999999999-" }, 416);
  const empty = () => imageBucket({ [IMAGE_KEY]: { data: new Uint8Array(0), etag: ETAG, uploaded: UPLOADED, httpMetadata: {} } });
  let { response } = await ask({ Range: "bytes=0-" }, { bucket: empty() });
  assert.equal(response.status, 416);
  assert.equal(response.headers.get("content-range"), "bytes */0");
  ({ response } = await ask({ Range: "bytes=-5" }, { bucket: empty() }));
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("content-length"), "0");
});

test("the local server classifies image paths like the Worker and honours If-Range on them", () => {
  const paths = [IMAGE, "/iso/0.2.0/emaki-0.2.0-x86_64.iso", "/iso/0.3.0/emaki-0.2.0-x86_64.iso",
    "/iso/0.3.0/emaki-0.3.0-x86_64.iso.sig", "/iso/0.2.0/image.iso", "/iso/1.2/emaki-1.2-x86_64.iso",
    "/iso/0.3.0/sub/emaki-0.3.0-x86_64.iso", "/stable/x86_64/emaki-0.3.0-x86_64.iso"];
  const expected = paths.map(path => IMAGE_ROUTE.test(path));
  assert.deepEqual(expected.filter(Boolean).length, 2);
  const result = spawnSync("python3", ["-c", `
import importlib.util
import json
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path
spec = importlib.util.spec_from_file_location("pointer_server", "tests/pointer-server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
paths, expected = json.loads(sys.argv[1]), json.loads(sys.argv[2])
assert [bool(server.IMAGE_ROUTE.fullmatch(path)) for path in paths] == expected
data = bytes((index * 7 + 3) % 251 for index in range(1000))
with tempfile.TemporaryDirectory() as directory:
    image = Path(directory) / 'iso/0.3.0/emaki-0.3.0-x86_64.iso'
    image.parent.mkdir(parents=True)
    image.write_bytes(data)
    running = server.serve(directory)
    url = f'http://127.0.0.1:{running.server_address[1]}/iso/0.3.0/emaki-0.3.0-x86_64.iso'
    def get(headers, method='GET'):
        request = urllib.request.Request(url, method=method, headers={'User-Agent': 'emaki-publish', **headers})
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.headers, error.read()
    try:
        status, headers, _ = get({}, 'HEAD')
        tag, modified = headers['ETag'], headers['Last-Modified']
        assert status == 200 and tag.startswith('"') and headers['Accept-Ranges'] == 'bytes', headers
        assert get({'Range': 'bytes=600-', 'If-Range': tag})[::2] == (206, data[600:])
        assert get({'Range': 'bytes=600-', 'If-Range': modified})[::2] == (206, data[600:])
        assert get({'Range': 'bytes=-100'})[::2] == (206, data[900:])
        assert get({'Range': 'bytes=600-', 'If-Range': '"stale"'})[::2] == (200, data)
        assert get({'Range': 'bytes=5-3'})[::2] == (200, data)
        assert get({'Range': 'bytes=0-1,5-6'})[::2] == (200, data)
        assert get({'Range': 'bytes=1000-'})[0] == 416
        assert get({'Range': 'bytes=-0'})[0] == 416
    finally:
        running.shutdown()
        running.server_close()
`, JSON.stringify(paths), JSON.stringify(expected)], { encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
});
