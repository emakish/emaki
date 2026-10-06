# The package mirror (pkgs.emaki.sh, dl.emaki.sh)

Reference for the mirror as it is meant to exist: hosts and buckets, the object layout, the
pointer Worker, the cache rules, the publishing commands and the ISO. How a person updates is in
`docs/updates.md`.

## Hosts and buckets

| Bucket | Host | Holds | Token |
|---|---|---|---|
| `emaki-pkgs` | `pkgs.emaki.sh` | the `[emaki]` package repository | "Object Read & Write", this bucket only; used at every publish |
| `emaki-dl` | `dl.emaki.sh` | ISO images, their `.sha256` and `.sig`, Arch source archives, the public signing key | "Object Read & Write", this bucket only; used a few times a year |

Both hosts are R2 custom domains in the `emaki.sh` zone (an `r2.dev` address is rate-limited and
meant for development only). Two buckets and two tokens, so the package publishing credentials
cannot delete or replace an ISO.

Installed systems read one line, in `/etc/pacman.d/emaki-mirrorlist` (package
`emaki-mirrorlist`):

```
Server = https://pkgs.emaki.sh/stable/$arch
```

## Object layout in `emaki-pkgs`

```
<channel>/x86_64/<name>.pkg.tar.zst   and .sig      written once, never replaced
sources/sha256/<sha256>/<archive>                  shared source archives, written once
snap/<channel>/<id>/emaki.db  emaki.db.sig  emaki.files  emaki.files.sig  MANIFEST
                   SOURCES  SOURCES.json
                                                   written once, never replaced
pointers/<channel>                                 one line: the <id> this channel serves.
                                                   THE ONLY OBJECT THAT IS EVER OVERWRITTEN
locks/publish                                      exists while a publish runs; names the publishing
                                                   machine by a random id (`status` prints its own)
served/<channel>/<id>                              record of every snapshot a channel served
released/github-stable/<id>                        record of a release copied to GitHub stable
released/iso/<version>                             record of an ISO published on dl.emaki.sh
<channel>/x86_64/emaki.db, .files, and their .sig  NOT objects: answered by the Worker
<channel>/x86_64/SOURCES and SOURCES.json          NOT objects: answered by the Worker
```

`<channel>` is `testing` or `stable`. `<id>` is the UTC time of the publish,
`YYYYMMDDTHHMMSSZ`; ids only grow. `MANIFEST` lists the sha256 of the four database files and of
every package, package signature, source archive and source index the database names; it holds no id or date, so a promoted
or withdrawn copy has the same `MANIFEST` as its source.

Nothing in R2 is pruned in this version. Source archives use their SHA-256 in the object path;
testing, stable and ISO directions reference the same Emaki object. For a 70–90 MB niri archive,
budget 70–90 MB per distinct archive instead of 210–270 MB across three release locations.
Unchanged Arch sources share a separate pool in `emaki-dl` across image releases.
The available 1,073-package closure suggests a planning allowance of 10–30 GiB of compressed
Arch sources, with 100–200 GiB of temporary collection workspace. This is an offline estimate,
not a measured total: closure files do not contain licences or source sizes. Measure the
collector output before approving storage; later releases add only distinct source archives.
GitHub bridge directions reference the shared archives; the bridge does not copy source archives.
A later prune may delete an object only when no retained release or snapshot names it.

## Why a pointer and a redirect

R2 guarantees each single write, but not two writes together, and pacman fetches `emaki.db`
and then `emaki.db.sig` as two requests. With both written in place, any machine that syncs
between the two writes, or after a publish dies between them, gets
`error: emaki: signature from "…" is invalid` until somebody finishes the publish. pacman 7.1
takes the signature from the address it was redirected to (`lib/libalpm/dload.c`, measured in
`tests/test-publish.py`), so the channel address answers with a redirect into an immutable
snapshot, and the only thing a publish changes in place is the one-line pointer. A machine
then sees the complete old pair or the complete new pair. `tests/test-publish.py` kills the
publisher after every step and syncs a real pacman after each kill; its flat-layout self-check
shows the old in-place procedure failing the same way.

One more rule that follows from pacman: it sends `If-Modified-Since` with the time of the
database it holds, at one-second resolution, and fetches the signature even after a `304`.
`publish.sh` therefore waits until the clock is past the current snapshot's `Last-Modified`
before it uploads a new one.

## The pointer Worker

`packaging/mirror/pointer-worker.js`, deployed once:

- R2 binding `PKGS` → bucket `emaki-pkgs`.
- Four routes: `pkgs.emaki.sh/stable/x86_64/emaki.*`,
  `pkgs.emaki.sh/testing/x86_64/emaki.*`, `pkgs.emaki.sh/stable/x86_64/SOURCES*`,
  and `pkgs.emaki.sh/testing/x86_64/SOURCES*`. All four must be added in Cloudflare.
- For `<channel>/x86_64/emaki.db`, `.db.sig`, `.files`, `.files.sig` it answers
  `302` to `/snap/<channel>/<id>/<file>` with `Cache-Control: no-store`. A missing or malformed
  pointer, or an unreadable bucket, answers `503`. `SOURCES` and `SOURCES.json` follow the
  same pointer. Any other path is passed to the bucket. Channel confirmation fetches `SOURCES`
  through the public channel address, so a missing source route stops verification.
- Unit tests: `node --test tests/test-pointer-worker.mjs`. The route table in
  `tests/fixtures/pointer-routes.json` is also checked against the local server
  `tests/pointer-server.py`, which the publish tests use in its place.

Workers Free allows 100,000 requests a day; each `pacman -Sy` costs two (database and
signature). Over the limit, `pacman -Sy` fails with a retrieve error; it never receives a mixed
pair.

## Cache rules (zone `emaki.sh`)

Three Cache Rules, in this order:

1. Hostname equals `pkgs.emaki.sh` AND (URI path ends with `.db`, `.files`, `.sig` or `.txt`, OR
   URI path starts with `/pointers/`, `/locks/`, `/served/` or `/released/`) → **Bypass cache**.
2. Hostname equals `pkgs.emaki.sh` AND URI path ends with `.pkg.tar.zst` → **Eligible for
   cache**, edge TTL 1 month (package names are never reused).
3. Hostname equals `dl.emaki.sh` AND URI path ends with `.sha256`, `.sig` or `.asc` → **Bypass
   cache**. (The ISO is over the 512 MB cache limit and is always served from R2.)

Rule 1 matters for package signatures as well: Cloudflare caches a `404` for 3 minutes, and
`publish.sh` uploads every package before any database names it.

## Publishing, in one paragraph

`packaging/publish.sh` (all subcommands take `--dry-run`): `publish testing <dir>` uploads
signed packages and a new signed snapshot and flips the `testing` pointer (`stable` changes only
through `promote`); it refuses a dirty tree, a package not signed by a key in
`packaging/emaki-keyring`, a file name or a package version (under any file name) that was ever
released with other bytes (`packaging/mirror/released-packages.sha256` and every `MANIFEST` on
the mirror), a version lower than the one `testing` serves (pacman's `vercmp` order), two
versions of one package in the directory, and a candidate whose `emaki emaki-apps` do not
resolve against today's Arch `core`/`extra`. `packaging/build.sh` refuses an output directory
that is not empty. `promote` copies the current `testing` snapshot byte for byte to `stable` and
needs the acceptance stamp (`stamp`, written from the upgrade acceptance results; valid for 24
hours after its oldest run finished, for that exact `MANIFEST`). `promote --first` exists once,
for the bridge release. `withdraw <channel>` serves the files of an earlier snapshot again as a new snapshot.
`github [--tag testing] [--restore]` copies a channel to the old GitHub address during the
bridge period. `status`, `verify <channel>`, `unlock --yes`, `sign <dir>`. A publish that dies
is finished by running the same command again. The checks are recorded only once the lock is
held, and every run repeats the stamp and the closure check right before it moves the pointer;
a check that fails there closes the run and releases the lock, with nothing machines read
changed. A database that came out unsigned (pinentry cancelled or timed out: `repo-add -s`
then only warns) ends the run the same way; type the passphrase in the next run.

The build output travels as one set: packages, `BUILDINFO`, `SOURCES.json`, and source archives.
`BUILDINFO` records the clean checkout's commit and hashes the output. The publisher checks that
record before accepting new binaries. Every package carries its recipe, patches and local inputs
in a source archive. The build compiles the same prepared tree captured by the archive.
Cargo dependencies are vendored; portal archives include both upstream trees.

Every snapshot's `SOURCES` names package versions and complete source download addresses.
It does not depend on a private commit being present in the public repository.
`SOURCES.json` retains per-package provenance across partial updates. Promotion and withdrawal
reuse shared archives. Existing archives are checked through their stored identity instead of
being downloaded on every operation. New uploads are checked against their recorded hash.
The GitHub bridge uploads packages before publishing the matching database and directions;
its source links refer to the already verified shared archives. Its description preserves the directions in a plain-text code block. After a
successful update it removes old package assets no longer named by the new database, so legacy
binaries without sources do not remain available beside the new release.

Snapshots created before source indexes existed are refused when selected: source provenance
cannot be reconstructed from binary names. An existing mirror needs a separately verified
migration before using those snapshots with this publisher; do not edit immutable manifests
or invent build commits. A fresh mirror starts with complete source-bearing build output.

Credentials: `~/.config/emaki-signing/r2-emaki-pkgs.env` and `r2-emaki-dl.env` (mode 600,
outside git) with `EMAKI_R2_ACCOUNT_ID`, `EMAKI_R2_ACCESS_KEY_ID`, `EMAKI_R2_SECRET_ACCESS_KEY`.
In the environment the names carry the bucket and take precedence over its file
(`EMAKI_R2_EMAKI_PKGS_ACCESS_KEY_ID`, `EMAKI_R2_EMAKI_DL_ACCESS_KEY_ID`, …); the plain names are
refused there, because they would hand one token to both buckets. `packaging/mirror/r2.py` signs S3 requests
itself, because a generic sync tool cannot make a write conditional on the object not existing.

## dl.emaki.sh: the ISO

Before publishing an image, collect its Arch GPL/LGPL sources in the disposable build VM:

```sh
python3 packaging/mirror/iso_sources.py --closure <build-work>/closure.txt \
    --packages <build-work>/offline --output <arch-sources>
```

The collector reads each exact binary's licence, source package name and recorded recipe hash.
It searches that package's Arch packaging repository for the matching recipe, checks the exact
version, and runs `makepkg --allsource` with signature and checksum verification enabled.
Missing historical recipes or downloads and mutable upstream sources stop collection; they
require an exact, verified replacement before the image can be published. Run only as the
ordinary build user in the disposable VM: packaging recipes execute shell code.
Recipes that fetch additional inputs during preparation or compilation need a separately
verified source supplement before release; `makepkg --allsource` covers declared sources.
`--repositories <directory>` uses local Arch packaging clones for offline fixtures or a
previously fetched history. It does not bypass source verification.

The output is `ARCH-SOURCES.json` plus `sources/sha256/<sha256>/<archive>`. Archive timestamps
and ownership are normalized so unchanged source archives share one object between releases;
split packages from the same recipe share an archive. Transfer the entire output directory
with the image. The publisher rechecks the manifest against the closure and actual image
binaries and refuses missing or changed archives. These are source archives, not copies of
Arch binary packages or directions to a third-party download server.

`iso/build.sh` writes `emaki-<version>-x86_64.iso.sha256` next to the image (and a `.sig` only
when `EMAKI_ISO_SIGN_KEY` is set; the build VM holds no private key, so normally it is not).
`packaging/publish.sh iso <image> --arch-sources <arch-sources>/ARCH-SOURCES.json`
on the publishing machine:

1. refuses unless `stable` already serves every Emaki package in the image's offline repository
   with the same bytes, and the acceptance stamp names the `stable` snapshot;
2. writes the `.sha256` and a detached signature with the package key if they are missing, and
   checks both (the signature against the key in git);
3. uploads `iso/<version>/emaki-signing-key.asc` (the public key as it is that day: the yearly
   expiry extension changes its bytes, so each image keeps its own copy), then
   `iso/<version>/emaki-<version>-x86_64.iso` (multipart on R2: resumable, and visible only once
   complete), then `.sig`, the image's `closure.txt`, `SOURCES-ISO.txt` and source manifest,
   then `.sha256` **last**. Arch source objects are uploaded to the shared source pool before
   the image completion checksum; Emaki source links reuse the package pool. Each is write-once, so a published image name is
   never replaced; an image left by an interrupted run is downloaded whole and must have the
   image's sha256 before `.sig` and `.sha256` are written next to it;
4. reads back the source companions, signature, checksum, and the first and last megabyte of the image anonymously;
   `--full-check` downloads the whole image once and compares its sha256.

Text for the download page (the fingerprint is printed on GitHub and on emaki.sh, two hosts):

```
Linux:    sha256sum -c emaki-<version>-x86_64.iso.sha256
macOS:    shasum -a 256 -c emaki-<version>-x86_64.iso.sha256
Windows:  Get-FileHash .\emaki-<version>-x86_64.iso -Algorithm SHA256   (compare with the .sha256 file)

Signature (optional, stronger):
  curl -O https://dl.emaki.sh/iso/<version>/emaki-signing-key.asc
  gpg --show-keys emaki-signing-key.asc
      must print  6685 6995 6D2D 7478 47D8  7D01 F626 80BE 5833 63AC
  gpg --import emaki-signing-key.asc
  gpg --verify emaki-<version>-x86_64.iso.sig emaki-<version>-x86_64.iso
      must print  Good signature from "Emaki Package Signing <packages@emaki.sh>"
```

## What has been checked, and what has not

Checked locally (`tests/test-publish.py`, `tests/test-r2-client.py`,
`tests/test-pointer-worker.mjs`): the publishing procedure with the real pacman 7.1 as client
against a local server that redirects like the Worker; the S3 signer against AWS's published
example signatures; the R2 backend against a local S3 endpoint that verifies every signature.

Never run against Cloudflare: the bucket, the custom domains, the cache rules, the Worker route
in front of the R2 domain, R2's conditional writes, path-style requests to the R2 endpoint, and
`Last-Modified` / `If-Modified-Since` through the custom domain. Each of them is checked on the
real mirror (with `curl`, `publish.sh verify testing` and `tests/publish-drill.sh`) before
anything reaches `stable`.

The Markdown rendering regression test requires the Arch `python-markdown` package.
