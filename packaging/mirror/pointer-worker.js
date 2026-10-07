// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
//
// pkgs.emaki.sh: answer a channel's database with a redirect into the snapshot that
// pointers/<channel> names. pacman takes the signature from the redirected address, so a
// database and its signature always come from the same immutable snapshot (docs/mirror.md).
// Source directions follow the same pointer as the database.
// Bindings: PKGS -> emaki-pkgs; DL -> the image bucket.
// Image source metadata shares one atomic pointer; legacy images fall through to the bucket.

export const ROUTE = /^\/(stable|testing)\/x86_64\/(emaki\.(?:db|files)(?:\.sig)?|SOURCES(?:\.json)?)$/;
export const ISO_ROUTE = /^\/iso\/(\d+\.\d+\.\d+)\/(SOURCES-ISO\.txt|ARCH-SOURCES\.json|MISSING-SOURCES\.json)$/;
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
