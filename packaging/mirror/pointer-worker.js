// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
//
// pkgs.emaki.sh: answer a channel's database with a redirect into the snapshot that
// pointers/<channel> names. pacman takes the signature from the redirected address, so a
// database and its signature always come from the same immutable snapshot (docs/mirror.md).
// Source directions follow the same pointer as the database.
// Bindings: PKGS -> emaki-pkgs; DL -> the image bucket.
// Image source metadata shares one atomic pointer; legacy images fall through to the bucket.
// dl.emaki.sh: the image itself is read from DL here, so an interrupted browser download
// resumes. The bucket's own domain ignores If-Range and, depending on its cache state, answers
// a range of the image with the whole file (2026-10-07). Every answer for the image carries
// X-Emaki-Image: 1; without DL it falls through unmarked, which publication refuses.
// Only regular expressions are exported next to the handler: the runtime takes an exported
// function for an entrypoint class.

export const ROUTE = /^\/(stable|testing)\/x86_64\/(emaki\.(?:db|files)(?:\.sig)?|SOURCES(?:\.json)?)$/;
export const ISO_ROUTE = /^\/iso\/(\d+\.\d+\.\d+)\/(SOURCES-ISO\.txt|ARCH-SOURCES\.json|MISSING-SOURCES\.json)$/;
export const IMAGE_ROUTE = /^\/iso\/(\d+\.\d+\.\d+)\/emaki-\1-x86_64\.iso$/;
export const SOURCE_SNAPSHOT_ID = /^[a-f0-9]{64}$/;
export const SNAPSHOT_ID = /^\d{8}T\d{6}Z$/;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const iso = url.pathname.match(ISO_ROUTE);
    if (iso) {
      let id;
      try {
        const pointer = await env.DL.get(`iso/${iso[1]}/source-pointer`);
        if (pointer === null) {
          const original = await fetch(request);
          const response = new Response(original.body, original);
          response.headers.set("X-Emaki-Source-Pointer", "1");
          return response;
        }
        id = (await pointer.text()).trim();
      } catch (error) {
        return new Response("source pointer unreadable\n", { status: 503, headers: { "Cache-Control": "no-store", "X-Emaki-Source-Pointer": "1" } });
      }
      if (!SOURCE_SNAPSHOT_ID.test(id)) {
        return new Response("source pointer invalid\n", { status: 503, headers: { "Cache-Control": "no-store", "X-Emaki-Source-Pointer": "1" } });
      }
      return new Response(null, {
        status: 302,
        headers: {
          Location: `${url.origin}/iso/${iso[1]}/source-snapshots/${id}/${iso[2]}`,
          "Cache-Control": "no-store",
          "X-Emaki-Source-Pointer": "1",
        },
      });
    }
    if (env.DL && IMAGE_ROUTE.test(url.pathname)) {
      let response;
      try {
        response = await image(request, env.DL, url.pathname.slice(1));
      } catch (error) {
        response = plain(503, "image unreadable\n");
      }
      response.headers.set("X-Emaki-Image", "1");
      return response;
    }
    const match = url.pathname.match(ROUTE);
    if (!match) return fetch(request);
    let id = "";
    try {
      const pointer = await env.PKGS.get(`pointers/${match[1]}`);
      id = pointer ? (await pointer.text()).trim() : "";
    } catch (error) {
      return new Response("pointer unreadable\n", { status: 503, headers: { "Cache-Control": "no-store" } });
    }
    if (!SNAPSHOT_ID.test(id)) {
      return new Response("pointer missing\n", { status: 503, headers: { "Cache-Control": "no-store" } });
    }
    return new Response(null, {
      status: 302,
      headers: {
        Location: `${url.origin}/snap/${match[1]}/${id}/${match[2]}`,
        "Cache-Control": "no-store",
      },
    });
  },
};

// The image: GET and HEAD with RFC 9110 conditional and single byte range requests. Errors
// and refusals are never stored by a cache; the image's own answers keep the bucket's metadata.
async function image(request, bucket, key) {
  const method = request.method;
  if (method !== "GET" && method !== "HEAD") {
    return plain(405, "method not allowed\n", { Allow: "GET, HEAD" });
  }
  const object = await bucket.head(key);
  if (object === null) return plain(404, "not found\n");
  const headers = new Headers();
  object.writeHttpMetadata(headers);
  headers.set("ETag", object.httpEtag);
  headers.set("Last-Modified", object.uploaded.toUTCString());
  headers.set("Accept-Ranges", "bytes");
  const condition = preconditions(request.headers, object);
  if (condition === 412) return plain(412, "precondition failed\n");
  if (condition === 304) {
    const kept = new Headers();
    for (const name of ["Cache-Control", "Expires", "ETag", "Last-Modified"]) {
      if (headers.has(name)) kept.set(name, headers.get(name));
    }
    return new Response(null, { status: 304, headers: kept });
  }
  // Range is defined for GET only (RFC 9110, 14.2); If-Range only together with Range.
  let range = null;
  if (method === "GET" && request.headers.has("Range") && ifRange(request.headers.get("If-Range"), object)) {
    range = byteRange(request.headers.get("Range"), object.size);
  }
  if (range === UNSATISFIABLE) {
    return plain(416, "range not satisfiable\n", { "Content-Range": `bytes */${object.size}`, "Accept-Ranges": "bytes" });
  }
  if (range) {
    headers.set("Content-Range", `bytes ${range.start}-${range.end}/${object.size}`);
    headers.set("Content-Length", String(range.end - range.start + 1));
  } else {
    headers.set("Content-Length", String(object.size));
  }
  const status = range ? 206 : 200;
  if (method === "HEAD") return new Response(null, { status, headers });
  const options = { onlyIf: { etagMatches: object.etag } };
  if (range) options.range = { offset: range.start, length: range.end - range.start + 1 };
  const body = await bucket.get(key, options);
  if (body === null) return plain(404, "not found\n");
  // The object changed between head() and get(): never send other bytes under this ETag.
  if (!body.body) return plain(503, "image changed while reading\n");
  // Stored bytes are sent as they are, also under a stored Content-Encoding.
  return new Response(body.body, { status, headers, encodeBody: "manual" });
}

function plain(status, text, extra = {}) {
  return new Response(text, { status, headers: { "Cache-Control": "no-store", "Content-Type": "text/plain", ...extra } });
}

// RFC 9110, 13.2.2: If-Match, else If-Unmodified-Since (412); If-None-Match, else
// If-Modified-Since (304 for GET and HEAD, the only methods that reach here).
function preconditions(headers, object) {
  const updated = Math.floor(object.uploaded.getTime() / 1000) * 1000;
  const ifMatch = headers.get("If-Match");
  if (ifMatch !== null) {
    if (!tagListMatches(ifMatch, object.httpEtag, false)) return 412;
  } else {
    const since = httpDate(headers.get("If-Unmodified-Since"));
    if (since !== null && updated > since) return 412;
  }
  const ifNoneMatch = headers.get("If-None-Match");
  if (ifNoneMatch !== null) {
    if (tagListMatches(ifNoneMatch, object.httpEtag, true)) return 304;
  } else {
    const since = httpDate(headers.get("If-Modified-Since"));
    if (since !== null && updated <= since) return 304;
  }
  return 0;
}

// RFC 9110, 13.1.5: a strong match with the ETag, or a date equal to Last-Modified.
function ifRange(value, object) {
  if (value === null) return true;
  const text = value.trim();
  // A weak ETag (W/"...") is neither a strong ETag nor a date: it never matches.
  if (text.startsWith('"')) {
    const tags = entityTags(text);
    const current = entityTags(object.httpEtag);
    return tags !== null && tags.length === 1 && current !== null && current.length === 1 &&
      strongMatch(tags[0], current[0]);
  }
  const date = httpDate(text);
  return date !== null && date === Math.floor(object.uploaded.getTime() / 1000) * 1000;
}

// "*" or a list of entity tags; weak comparison for If-None-Match, strong for If-Match.
function tagListMatches(value, httpEtag, weak) {
  if (value.trim() === "*") return true;
  const tags = entityTags(value);
  const current = entityTags(httpEtag);
  if (tags === null || current === null || current.length !== 1) return false;
  return tags.some(tag => weak ? tag.opaque === current[0].opaque : strongMatch(tag, current[0]));
}

function strongMatch(a, b) {
  return !a.weak && !b.weak && a.opaque === b.opaque;
}

// entity-tag = [ "W/" ] DQUOTE *etagc DQUOTE, as a comma separated list (empty elements
// tolerated); null if malformed.
const ENTITY_TAG = /[ \t]*(?:,[ \t]*)*(W\/)?("[\x21\x23-\x7e\x80-\xff]*")[ \t]*(?:,|$)/y;
function entityTags(value) {
  const tags = [];
  ENTITY_TAG.lastIndex = 0;
  while (ENTITY_TAG.lastIndex < value.length) {
    const match = ENTITY_TAG.exec(value);
    if (!match) return null;
    tags.push({ weak: match[1] !== undefined, opaque: match[2] });
  }
  return tags.length ? tags : null;
}

// RFC 9110, 5.6.7: IMF-fixdate and the two obsolete forms; null when not exactly one date.
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const MONTH = MONTHS.join("|");
const IMF_FIXDATE = new RegExp(`^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun), (\\d{2}) (${MONTH}) (\\d{4}) (\\d{2}):(\\d{2}):(\\d{2}) GMT$`);
const RFC850_DATE = new RegExp(`^(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), (\\d{2})-(${MONTH})-(\\d{2}) (\\d{2}):(\\d{2}):(\\d{2}) GMT$`);
const ASCTIME_DATE = new RegExp(`^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) (${MONTH}) ([ \\d]\\d) (\\d{2}):(\\d{2}):(\\d{2}) (\\d{4})$`);
function httpDate(value) {
  if (value === null) return null;
  const text = value.trim();
  let day, month, year, hour, minute, second, match;
  if ((match = IMF_FIXDATE.exec(text))) {
    [, day, month, year, hour, minute, second] = match;
  } else if ((match = RFC850_DATE.exec(text))) {
    [, day, month, year, hour, minute, second] = match;
    // A two-digit year more than 50 years ahead is the most recent such year in the past.
    year = 2000 + Number(year);
    if (year > new Date().getUTCFullYear() + 50) year -= 100;
  } else if ((match = ASCTIME_DATE.exec(text))) {
    [, month, day, hour, minute, second, year] = match;
  } else {
    return null;
  }
  const parts = [Number(year), MONTHS.indexOf(month), Number(day), Number(hour), Number(minute), Number(second)];
  const date = new Date(Date.UTC(...parts));
  if (date.getUTCFullYear() !== parts[0] || date.getUTCMonth() !== parts[1] || date.getUTCDate() !== parts[2] ||
      date.getUTCHours() !== parts[3] || date.getUTCMinutes() !== parts[4] || date.getUTCSeconds() !== parts[5]) {
    return null;
  }
  return date.getTime();
}

// RFC 9110, 14.1.1: one range of the forms a-b, a- or -n. Several ranges, another unit or a
// malformed value: null (the whole image, 200). Unsatisfiable: 416.
const UNSATISFIABLE = "unsatisfiable";
function byteRange(value, size) {
  const set = /^bytes=(.*)$/i.exec(value.trim());
  if (!set) return null;
  const specs = set[1].split(",").map(spec => spec.trim()).filter(spec => spec !== "");
  if (specs.length !== 1) return null;
  const spec = /^(\d*)-(\d*)$/.exec(specs[0]);
  if (!spec || (spec[1] === "" && spec[2] === "")) return null;
  if (spec[1] === "") {
    const suffix = Number(spec[2]);
    if (suffix === 0) return UNSATISFIABLE;
    if (size === 0) return null;
    return { start: Math.max(0, size - suffix), end: size - 1 };
  }
  const start = Number(spec[1]);
  const last = spec[2] === "" ? Infinity : Number(spec[2]);
  if (last < start) return null;
  if (start >= size) return UNSATISFIABLE;
  return { start, end: Math.min(last, size - 1) };
}
