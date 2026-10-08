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

Installed systems select a source in `/etc/emaki/channel` [update channel]:

```
Include = /usr/share/emaki/mirrors/stable.conf
```

`/etc/pacman.d/emaki-mirrorlist` includes this selector; the selected package-owned
file supplies `https://pkgs.emaki.sh/stable/$arch` or the testing address [emaki-mirrorlist].
The package preserves a valid stable or testing selector and repairs missing or invalid
selectors on installation or upgrade [channel selector].

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
- R2 binding `DL` → bucket `emaki-dl`, and route `dl.emaki.sh/iso/*` to the same Worker. Both
  are required for image downloads, and `iso-sources` needs them for the source pointer.
  Without either one the image silently falls through to the bucket's domain, so
  `publish.sh iso` checks for the Worker's `X-Emaki-Image: 1` before it uploads anything and
  on every read-back (step 4 of the ISO list below): a missing binding or route refuses the
  publication.
- The image itself, `iso/<version>/emaki-<version>-x86_64.iso`, is answered from `DL` by the
  Worker instead of being passed to the bucket's domain. The image is above the 512 MB cache
  limit (`cf-cache-status: BYPASS`), and for it that domain ignores `If-Range` (even a stale
  validator gets `206`) and its answer to a range depends on the cache: right after a `HEAD`
  answered with `cf-cache-status: MISS`, the next ranged `GET` got the whole file (`200`), the
  one after it `206` again (measured 2026-10-07 on the 0.3.0 image; this is what refused the
  first 0.3.0 publication, "with Range answered 200, not 206"). A browser that gets `200` for
  its resume request starts the download again from zero. Every answer of the Worker for the
  image (`200`, `206`, `304`, `404`, `405`, `412`, `416`, `503`) carries `X-Emaki-Image: 1`;
  a `206` without it proves nothing. The Worker answers `GET` and `HEAD` (any other method:
  `405` with `Allow: GET, HEAD`) with the object's `ETag` (R2's quoted etag), `Last-Modified`,
  `Accept-Ranges: bytes`, `Content-Length` and the object's stored HTTP metadata
  (`Content-Type`; `Cache-Control` only when the object has one). One range `bytes=a-b`, `a-`
  or `-n` answers `206` with `Content-Range`, an unsatisfiable one `416` with
  `Content-Range: bytes */<size>`, several ranges or a malformed `Range` the whole image.
  `If-Range` must be the ETag (strong comparison) or exactly the `Last-Modified` date, otherwise
  the whole image is sent. `If-None-Match` and `If-Modified-Since` answer `304`, a failed
  `If-Match` or `If-Unmodified-Since` `412`, in the order of RFC 9110. A missing image is a `404`
  and an unreadable bucket a `503`, both `Cache-Control: no-store`; an answer the Worker makes
  itself is never stored in the zone cache, unlike a `404` of the bucket's domain. The
  `cache-control: max-age=14400` the bucket's domain adds to the image (the zone's browser
  cache time) is not in the Worker's answer; that is harmless for a download. Without the
  `DL` binding the image falls through to the bucket's domain as before, unmarked.
- Image source companions redirect through `iso/<version>/source-pointer` to one immutable
  source snapshot. Without a pointer, requests fall through to the original R2 objects, as
  every other path under `/iso/` does (checksum, signature, key, `closure.txt`).
- Four package routes: `pkgs.emaki.sh/stable/x86_64/emaki.*`,
  `pkgs.emaki.sh/testing/x86_64/emaki.*`, `pkgs.emaki.sh/stable/x86_64/SOURCES*`,
  and `pkgs.emaki.sh/testing/x86_64/SOURCES*`. All four must be added in Cloudflare.
- For `<channel>/x86_64/emaki.db`, `.db.sig`, `.files`, `.files.sig` it answers
  `302` to `/snap/<channel>/<id>/<file>` with `Cache-Control: no-store`. A missing or malformed
  pointer, or an unreadable bucket, answers `503`. `SOURCES` and `SOURCES.json` follow the
  same pointer. Any other path is passed to the bucket. Channel confirmation fetches `SOURCES`
  through the public channel address, so a missing source route stops verification.
- Unit tests: `node --test tests/test-pointer-worker.mjs`, with a fake R2 binding that follows
  the Workers R2 API (`head`, `get` with `range` and `onlyIf`). The route table in
  `tests/fixtures/pointer-routes.json` is also checked against the local server
  `tests/pointer-server.py`, which the publish tests use in its place; it serves the image
  with `X-Emaki-Image`, an ETag and `If-Range` as the Worker does.

Workers Free allows 100,000 requests a day; each `pacman -Sy` costs two (database and
signature), and every request under `dl.emaki.sh/iso/` costs one (a download, each resumed
piece of it, a checksum or signature). Over the limit, a route answers by its request limit
failure mode, set per route:

- the four `pkgs.emaki.sh` routes: **Fail closed**. `pacman -Sy` then fails with a retrieve
  error (Cloudflare error 1027); it never receives a mixed pair.
- `dl.emaki.sh/iso/*`: **Fail open**. Requests then reach the bucket's domain as if there were
  no Worker: the image stays downloadable, but a resumed download may start again from zero,
  and the source companions are the original files of the image's first publication instead
  of the snapshot `source-pointer` selects.

## Cache rules (zone `emaki.sh`)

Three Cache Rules, in this order:

1. Hostname equals `pkgs.emaki.sh` AND (URI path ends with `.db`, `.files`, `.sig` or `.txt`, OR
   URI path starts with `/pointers/`, `/locks/`, `/served/` or `/released/`) → **Bypass cache**.
2. Hostname equals `pkgs.emaki.sh` AND URI path ends with `.pkg.tar.zst` → **Eligible for
   cache**, edge TTL 1 month (package names are never reused).
3. Hostname equals `dl.emaki.sh` AND (URI path ends with `.sha256`, `.sig`, `.asc`,
   `SOURCES-ISO.txt`, `ARCH-SOURCES.json` or `MISSING-SOURCES.json`, OR URI path ends with
   `/source-pointer`) → **Bypass cache**. (The ISO is over the 512 MB cache limit and is always served from R2.)

Rule 1 matters for package signatures as well: Cloudflare caches a `404` for 3 minutes, and
`publish.sh` uploads every package before any database names it.

## Publishing, in one paragraph

`packaging/publish.sh` (all subcommands take `--dry-run`): `publish testing <dir>` uploads
signed packages and a new signed snapshot and flips the `testing` pointer (`stable` changes only
through `promote`); it refuses a dirty tree, a package not signed by a key in
`packaging/emaki-keyring`, a file name or a package version (under any file name) that was ever
released with other bytes (`packaging/mirror/released-packages.sha256` and every `MANIFEST` on
the mirror), a version lower than the one `testing` serves (pacman's `vercmp` order), two
versions of one package in the directory, and a candidate whose installer Minimal or Rich
transaction does not resolve against today's Arch `core`/`extra`. These are separate checks
using the installer's package lists; the live-only installer is checked against the image's
pinned repository during image publication. `packaging/build.sh` refuses an output directory
that is not empty. `promote` copies the current `testing` snapshot byte for byte to `stable` and
needs the acceptance stamp (`stamp`, written from the upgrade acceptance results; valid for 24
hours after its oldest run finished, for that exact `MANIFEST`). T1 and T2 must cover both
screen sizes from 0.1.0 and 0.1.1 and every image version in `released/iso/`, excluding records
for the candidate's own manifest. The records are read again at promotion: after publishing
0.2.0, the next candidate also requires 0.2.0 upgrade runs. `promote --first` exists once,
for the bridge release. `withdraw <channel>` serves the files of an earlier snapshot again as a new snapshot.
`github [--tag testing] [--restore]` copies a channel to the old GitHub address during the
bridge period. `status`, `verify <channel>`, `unlock --yes`, `sign <dir>`. A publish that dies
is finished by running the same command again. The checks are recorded only once the lock is
held, and every run repeats the stamp and the closure check right before it moves the pointer;
a check that fails there closes the run and releases the lock, with nothing machines read
changed. A database that came out unsigned (pinentry cancelled or timed out: `repo-add -s`
then only warns) ends the run the same way; type the passphrase in the next run.

For a later release, build only new package versions with repeated `build.sh --only NAME`
arguments (runbook step 15.1). The publisher merges this partial update into testing and retains
unchanged binaries and source records. Rebuilding an unchanged recipe is not safe reuse: its
new bytes may violate the published version's immutability.

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

GitHub `--restore` verifies saved source directions and archives before restoring binaries.
An old backup without these is refused; `--allow-sourceless-restore` explicitly overrides that
refusal for a deliberate legacy recovery.

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

Before publishing an image, collect its Arch GPL/LGPL sources in a disposable Arch Linux VM
with `base-devel`, `git`, `rust` (Cargo), `go`, Python 3.11 or newer, GnuPG (`gnupg`), `curl`,
and `bsdtar` (`libarchive`) installed. Use its 250 GB data disk for input, output and temporary work. Run the collector as a non-root user in that isolated
environment: packaging recipes execute shell code. Allow network access to
`gitlab.archlinux.org`, Arch mirrors, upstream source hosts and their official mirrors. Cargo
vendoring also needs the registries and Git dependency hosts in each archived Cargo.lock.
Go vendoring uses proxy.golang.org and sum.golang.org with the pinned go.mod and go.sum.
Copy an Emaki checkout and the image to the VM; no signing keys or publishing credentials
are needed there. HTTP requests identify themselves as `emaki-publish`.

The earlier 18-base sample extrapolated to approximately 65 GB of retained archives and six
hours of serial collection. It overrepresented large packages; this is a planning estimate,
not a full-closure measurement. Allow 100–200 GB of temporary space. Put the image, extracted
repository, output, and `TMPDIR` on the VM's data disk. The laptop's `/tmp` is too small
for source collection. For example, with that disk available as `/srv/emaki-sources` and the
working directories owned by the collection user:

```sh
mkdir -p /srv/emaki-sources/input /srv/emaki-sources/tmp /srv/emaki-sources/output
# For the legacy 0.2.0 image, the embedded repository is the full closure.
bsdtar -xf <image.iso> -C /srv/emaki-sources/input emaki/repo
export TMPDIR=/srv/emaki-sources/tmp
cd <emaki-checkout>
python3 packaging/mirror/collect_sources.py \
    --closure /srv/emaki-sources/input/emaki/repo/closure.txt \
    --packages /srv/emaki-sources/input/emaki/repo \
    --output /srv/emaki-sources/output --jobs 3
```

For newer split-repository images, collect from the retained build directory instead:
use `<build-work>/closure.txt` and `<build-work>/offline` for `--closure` and `--packages`.
Do not use `target-closure.txt` or `emaki/repo/closure.txt`: those describe only the
installer repository. The image carries the full live-and-target list as
`emaki/live-closure.txt`, plus `emaki/live-packages.json` with source identities and
binary hashes captured from the build archives before mastering. Keep the complete
build cache until source collection finishes.
Publication accepts absent live metadata only through version 0.2.0. Later image versions
require both files and the installed `pkglist.x86_64.txt`; the full closure must equal the
live package list plus target archives, with exact versions including epochs.

That last command collects the full closure with three parallel package-base jobs. Adjust
`--jobs` to the VM's disk, memory and network capacity. Repeating the same command resumes:
each completed job's binary metadata, source archive size and SHA-256, archived original recipe
and package identity, and required supplements are checked before it is skipped. Failed jobs run again. A lock prevents two runners sharing one output directory.
`summary.json` records every base, result, byte count, elapsed seconds and log path;
`jobs/<identity>/collect.log` retains all attempts for that base. A failed job does not stop
the other jobs. The command exits nonzero on any refusal, and writes the final
`ARCH-SOURCES.json` only after validating all source objects against the actual image binaries.
Preserve the entire output directory when resuming, including `jobs/` and its Git pin records.
The archive pool and job copies use hard links where possible.

The collector reads each exact binary's licence, source package name and recorded recipe hash.
It searches that package's Arch packaging repository for the matching recipe, checks the exact
version, and runs `makepkg --allsource` with signature and checksum verification enabled.
Git tags require non-SKIP recipe checksums even when signed and are recorded with their resolved commits; preserve `git-pins/` and the manifest when
rerunning collection so a moved tag is refused. Recipe `keys/pgp` keys are imported into a
fresh verification-only keyring; the personal keyring is never used.
Missing historical recipes or downloads and unpinned upstream sources stop collection; they
require an exact, verified replacement before the image can be published.
Reviewed, recipe-hash-bound submodule mappings can derive a secondary Git commit from the
pinned primary repository's gitlink. Only that exact secondary commit is fetched. The original
recipe stays unchanged; the manifest records the primary URL, primary commit, gitlink path and
secondary commit as evidence. Other moving sources remain refused. This eligibility check is
not proof that the upstream commit remains available or its signature will verify.
The exact GRUB recipe instead uses a fixed gnulib revision in its pinned bootstrap
configuration; both the configuration and the bootstrap code selecting it must match their
reviewed hashes. The exact reviewed vpnc binary permits omitting its unused wiki: its installed
documentation matches tracked files in the pinned primary tree. The archive preserves the original
recipe and metadata, and the manifest records the omission and binary/file evidence.
OBS Studio's reviewed nested capture-device source is retained inside its parent mirror's
`source-supplements/`, with parent/gitlink/commit evidence in the manifest and local URL wiring
instructions in `README.sources`. The original PKGBUILD is unchanged. Restoring those local
URLs is necessary for an offline rebuild; makepkg does not wire them automatically. Unknown
recursive submodules are refused until their exact inputs are covered.
Eleven reviewed Cargo recipe hashes require `cargo vendor --locked --versioned-dirs` supplements.
The collector extracts verified sources without running preparation, applies reviewed dependency
changes (including glycin's pinned cherry-picks), and vendors the prepared lock. The archive's
`<base>/_cargo/` contains dependencies, lock, configuration and restoration instructions;
manifest evidence records locked package identities and file hashes. Missing supplements and
unreviewed dependency fetches refuse collection. The scan covers every recipe function and
recognizes Cargo, Go, npm, pip and other fetch commands, options and common command aliases.
Indirect fetches in upstream build programs still require recipe review. The reviewed
bcachefs-tools and libimagequant workspaces require Cargo supplements too. Libimagequant's
published 4.4.1 source has no Cargo.lock. Only the reviewed 4.4.1-2 image binary accepts a
retained reconstruction resolved from sparse registry publications at or before its BUILDDATE;
every application crate version named in that binary matches. The supplement records the
lock, selected index entries, binary evidence and source manifest hashes. Historical yank
state and unnamed dependency versions are not independently proven; an unrestricted current
resolution is never accepted.
Reviewed kitty and cliphist recipes require `go mod vendor` supplements under `<base>/_go/`,
with unchanged go.mod/go.sum, module identities, vendor file hashes and offline restoration
instructions. Missing or changed Cargo/Go supplements refuse resume and publication.
Existing collections of bcachefs-tools, libimagequant, kitty and cliphist must be collected
again. Other bases retain the same source archive and pin formats.
The exact libgcrypt 1.12.4-1 exception verifies the tarball checksum and both current signatures,
requires a listed valid primary, and records both signers while retaining the signature unchanged.
Other signature cases retain normal makepkg verification.
libfakekey 0.3-4 is collected from the Arch sourceball at
`https://sources.archlinux.org/sources/packages/libfakekey-0.3-4.src.tar.gz` when its
normal route refuses. The reviewed archive hash, exact recipe, file inventory and
upstream tarball checksum remain required.
GNU checksum-pinned tarballs from `/gnu/` and `/pub/gnu/`, and Savannah release files,
can fall back to official mirrors, including FreeType's SourceForge releases; the manifest
records each requested and final serving URL and checksums. A checksum or signature
mismatch tries the next listed mirror and retains rejected bytes with provenance under
`rejected/<base>/` in that job's output. Coreutils and gnulib use
upstream project Git mirrors with exact commit checks and original tag signature verification.
Other Savannah Git sources use full Software Heritage origin snapshots and exact revision
git-bare bundles, restoring original annotated tag bytes only when the Git object hash matches.
The manifest retains recipe/archive origins, snapshot, tag object and commit. A vault that
is not already cooked refuses by default. The explicit `--cook-swh-vault` option enables
remote cooking of that exact revision and bounded polling; this option performs a POST and
must not be used for a collection restricted to read-only network access.
Missing public keys and missing signing subkeys are retrieved using only full recipe-listed
primary fingerprints. The collector checks each primary before importing into a fresh,
verification-only keyring and includes fetched key bytes in separate
`keys/pgp/<fingerprint>.refreshed.asc` files, preserving the recipe's original keys.
Collected dbus-glib 0.116-1, dosfstools 4.2-5 and e2fsprogs 1.47.4-1 archives containing
overwritten keys need re-collection. Checksums
and normal makepkg signature verification remain required. Unresolved sources refuse and
the full run can finish with a nonzero result.
`--repositories <directory>` uses local Arch packaging clones for offline fixtures or a
previously fetched history. It does not bypass source verification.

For a cheap recipe-only preflight, use the same image inputs with
`python3 packaging/mirror/audit_sources.py --closure <closure.txt> --packages <repo>
--repositories <recipe-cache> --archives <recipe-archives> --output <audit.json>`.
This reads binary metadata and exact Arch packaging revisions without invoking makepkg or
downloading upstream sources. GitLab packaging archives provide a fallback when Git requests
are throttled; the archived PKGBUILD must match the binary's recorded hash. `--offline` reuses
the caches. `conditional_vcs` means the reviewed mapping still needs actual upstream
evidence resolution during collection. Missing recipe metadata remains an explicit refusal.
The audit also lists skipped binaries and their licence labels for separate inspection.

The single-base collector `iso_sources.py` remains available for focused diagnostics. Its output
is `ARCH-SOURCES.json`, persistent `git-pins/` resolutions, and
`sources/sha256/<sha256>/<archive>`. Archive timestamps
and ownership, including makepkg’s generated `.SRCINFO` date, are normalized so unchanged
source archives share one object between releases;
split packages from the same recipe share an archive. Copy the entire output directory back
to the publishing laptop as `<arch-sources>`, preserving its relative paths. Keep it beside
the same image used for collection, then run
`packaging/publish.sh iso <image> --arch-sources <arch-sources>/ARCH-SOURCES.json` there.
For the approved incomplete 0.2.0 collection, also pass
`--missing-sources <arch-sources>/MISSING-SOURCES.json`. Keep this exact-image missing list
beside the source manifest when copying the collection. Omit it only for a complete collection.
The publisher rechecks the manifest against the full image list and its
embedded inventory, cross-checks offline archives against that inventory, and refuses
unaccounted missing sources or changed source archives. Every uncollected source must be
explicitly accounted for in the missing list; it is published with the source directions. Live-only binaries are represented by their recorded
hashes and source identities; their package archives do not need to be shipped. These are source archives, not copies of
Arch binary packages or directions to a third-party download server.

`iso/build.sh` writes `emaki-<version>-x86_64.iso.sha256` next to the image (and a `.sig` only
when `EMAKI_ISO_SIGN_KEY` is set; the build VM holds no private key, so normally it is not).
`packaging/publish.sh iso <image> --arch-sources <arch-sources>/ARCH-SOURCES.json`
on the publishing machine (add `--missing-sources <arch-sources>/MISSING-SOURCES.json`
for the approved incomplete collection):

1. refuses unless `stable` already serves every Emaki package in the full image inventory, including the live-only installer
   with the same bytes, and the acceptance stamp names the `stable` snapshot; and, before any
   upload, unless a `HEAD` of the image address answers with `X-Emaki-Image: 1` (the download
   Worker with its `DL` binding; the `HEAD` carries a query string, so a `404` of the bucket's
   domain is never cached under the plain address);
2. writes the `.sha256` and a detached signature with the package key if they are missing, and
   checks both (the signature against the key in git);
3. uploads `iso/<version>/emaki-signing-key.asc` (the public key as it is that day: the yearly
   expiry extension changes its bytes, so each image keeps its own copy), then
   `iso/<version>/emaki-<version>-x86_64.iso` (multipart on R2: resumable, and visible only once
   complete), then `.sig`, the full image list as `closure.txt`, `SOURCES-ISO.txt` and source manifest,
   then `.sha256` **last**. Arch and Emaki source objects are uploaded to the shared `dl.emaki.sh` source pool before
   the image completion checksum; every image source direction points to that pool. Each is write-once, so a published image name is
   never replaced; an image left by an interrupted run is downloaded whole and must have the
   image's sha256 before `.sig` and `.sha256` are written next to it;
4. reads back the source companions, signature, checksum, and the first and last megabyte of the image anonymously,
   then one megabyte from the middle the way a browser resumes a download: `Range` with
   `If-Range` set to the ETag a `HEAD` answers, which must be `206` with the same bytes. Every
   read of the image must carry `X-Emaki-Image: 1`;
   `--full-check` downloads the whole image once and compares its sha256. A refused read-back
   leaves the uploads in place; the same command finishes once the address answers.

### Adding collected sources after image publication

Deploy the download Worker route and cache rule above before the first addition. For an
already published image, run:

```sh
packaging/publish.sh iso-sources <version> \
  --arch-sources <arch-sources>/ARCH-SOURCES.json \
  --missing-sources <arch-sources>/MISSING-SOURCES.json
```

Both inputs are complete merged manifests for that exact image, not a delta: retain all
previously collected records and add only bases named in its published missing list. Remove
those bases from the missing list; keep the manifest with `bases: []` when collection is
complete. Copy the complete collection directory so each source archive remains available
at its recorded relative path. The command verifies source objects with the same checks as
initial publication and refuses replacements of previously collected records.

It uploads verified additions and a new immutable
`iso/<version>/source-snapshots/<sha256>/` containing `SOURCES-ISO.txt`, `ARCH-SOURCES.json`
and `MISSING-SOURCES.json`, then switches `iso/<version>/source-pointer` with a conditional
write. The three public companion addresses consequently select the same source snapshot.
The image, closure, signature, checksum and original companion objects remain unchanged.
Use `packaging/publish.sh --dry-run iso-sources` with the same arguments to validate an addition before uploading;
do not rerun `iso` with changed manifests.

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
`Last-Modified` / `If-Modified-Since` through the custom domain. The image answers of the Worker
are checked against the fake binding only: after the route is added, check that the image's
answers carry `X-Emaki-Image: 1`, that its `ETag`, `Last-Modified` and `Content-Length` are
the ones the bucket's domain served before (downloads started earlier resume with them), and
that `If-Range` with the ETag answers `206` while a stale `If-Range` answers `200`
(`docs/updates-runbook.md`, section 2, step 5). Each of them is checked on the
real mirror (with `curl`, `publish.sh verify testing` and `tests/publish-drill.sh`) before
anything reaches `stable`.

The Markdown rendering regression test requires the Arch `python-markdown` package.
