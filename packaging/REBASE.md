# Rebasing the niri and Quickshell patches

`niri-emaki` and `quickshell-emaki` build an upstream release tarball plus the
patch files next to their PKGBUILDs. The patches are cut from local fork
branches. This file records how the patch files relate to those branches, what
the package checks, and the places where an upstream change breaks the fork
without a build error.

Every command below was run read-only against the tracked patch files and the
fork clones unless it is marked **(not run)**. Where the fork history is
published is an open decision; today both forks are local clones whose
only remote is upstream, so the branches named here exist only in those clones.

## niri-emaki

### Patch order and branches

`prepare()` applies the patches to the `v26.04` tarball in this order. Each
patch is the step from the previous branch to the next one; the branches form
one linear stack on top of tag `v26.04`.

| Patch | Branch | Base |
|---|---|---|
| `0001-layer-Add-hide-from-own-screencopy-layer-rule.patch` | `emaki` | `v26.04` |
| `0002-keep-greeter-frame-and-cover-session-startup.patch` | `emaki-startup-cover` | `emaki` |
| `0003-emaki-overview-backdrop.patch` | `emaki-overview-backdrop` | `emaki-startup-cover` |
| `0004-crisp-xcursor-at-fractional-scale.patch` | `emaki-cursor-crisp` | `emaki-overview-backdrop` |
| `0005-emaki-wallpaper.patch` | `emaki-wallpaper` | `emaki-cursor-crisp` |
| `0006-exit-without-primary-renderer.patch` | `fix/exit` (only in the clone with the `fix/*` branches, see below) | `fix/wp` (in place of `emaki-wallpaper`) |
| `0007-protect-session-pixels-while-locked.patch` | patch delivered in the distribution tree | `v26.04` with `0001`–`0006` applied |
| `0008-account-for-static-blur-occlusion.patch` | patch delivered in the distribution tree | `v26.04` with `0001`–`0007` applied |

Check that the stack is linear (in the niri fork clone):

```sh
git merge-base --is-ancestor v26.04 emaki
git merge-base --is-ancestor emaki emaki-startup-cover
git merge-base --is-ancestor emaki-startup-cover emaki-overview-backdrop
git merge-base --is-ancestor emaki-overview-backdrop emaki-cursor-crisp
git merge-base --is-ancestor emaki-cursor-crisp emaki-wallpaper
git merge-base --is-ancestor fix/wp fix/exit   # in the clone with the fix/* branches
```

### How the patch files relate to the branches

Verified 2026-10-04 for pkgrel 7:

- `0001` is `git format-patch --stdout v26.04..emaki` except for the `From:`
  author line, which in the tracked file carries the GitHub noreply address,
  and the `Date:` line, which gives the same instant in UTC (`+0000`).
- `0002` to `0005`: from the first `diff --git` line on, each file is
  `git diff <base> <branch>` for its row of the table. `0003`, `0004` and
  `0005` are byte-identical to that output; in `0002` the 39 blank context
  lines have lost their leading space (`patch` accepts both). Everything before
  the first `diff --git` line (From, Date, Subject, description, `---`) is a
  hand-written header.
- Every `Date:` line of the tracked patches, here and in `quickshell-emaki`, is
  in UTC (`+0000`). `git format-patch` writes the commit's own time zone there:
  after regenerating a patch, rewrite its `Date:` line by hand to the same
  instant in UTC, as the `From:` line is rewritten, and only then update the
  PKGBUILD checksums. `patch` does not apply the mail header, so the package
  contents do not change. Dates in public files are written in UTC; the export guard of
  `scripts/make-public.sh` stops on the author's time zone.
- `0006` (pkgrel 8) was first cut as `git diff --no-index` between an export of
  `emaki-wallpaper` and that export with the edits applied (the "Generated ..."
  line of its header describes that cut). Branch `fix/exit` is that diff
  committed on `fix/wp`, and since 2026-10-05 the patch body is
  `git diff fix/wp fix/exit`, behind its hand-written header. Its scope is small
  (`src/backend/mod.rs`, `src/backend/tty.rs`, `src/niri.rs`, `src/main.rs`:
  exit status 3 when the primary renderer is missing).
- Applying `0001` to `0005` to an export of `v26.04` gives exactly the tree of the
  `emaki-wallpaper` branch:

  ```sh
  # In the niri fork clone. EMAKI is the Emaki checkout; base/ and tip/ are
  # empty scratch directories.
  git archive v26.04 | tar -x -C base
  git archive emaki-wallpaper | tar -x -C tip
  (cd base && for p in "$EMAKI"/packaging/niri-emaki/000[1-5]*.patch; do patch -Np1 -s -i "$p"; done)
  diff -r base tip
  ```

  This used `git archive`, not the GitHub tarball that `source=()` downloads
  **(tarball not compared)**. With `0006` applied on top, the tree is exactly
  that of `fix/exit` (`git archive fix/exit` as tip; checked 2026-10-05). Every
  patch must apply without an offset or fuzz: `patch` then leaves no `.orig`
  file, which `diff -r` would report.

Not recorded anywhere, and therefore not described here:

- the exact command line and options that produced the patch files.

How a change in the middle of the stack is carried up through the later
branches is recorded under "Carrying a change from the middle of the stack"
below (done once, on 2026-10-05, for `0002`).

What is known is the result: a regenerated patch must match the diff
described above, keep its header, and pass the `diff -r` stack check.

### Rebase checklist

1. Move the existing branches and patches `0007` and `0008` onto the new upstream tag, keeping the table order.
   No command for this step is recorded (see above) **(not run)**.
2. Go through the silent-break places below for every patch.
3. Regenerate the eight patch files so that they match the branch diffs above
   (`0006` from the `fix/wp`, `fix/exit` pair), keep each header with its UTC
   `Date:` line, and re-run the stack check with the new tag in place of `v26.04`.
4. Update `pkgver`, `pkgrel`, the stock `niri=` dependency and the
   `sha512sums`/`b2sums` arrays in the PKGBUILD. Each patch's checksums must
   appear in the PKGBUILD; this is how the current ones were checked:

   ```sh
   cd packaging/niri-emaki
   for f in 000*.patch; do
     grep -c "$(sha512sum "$f" | cut -d' ' -f1)" PKGBUILD
     grep -c "$(b2sum "$f" | cut -d' ' -f1)" PKGBUILD
   done
   ```

5. Build and run `check()` with makepkg **(not run here)**. `check()` runs only
   the fork's tests, not the full niri suite:

   ```sh
   export XDG_RUNTIME_DIR="$(mktemp -d)"
   export RAYON_NUM_THREADS=1
   cargo test --frozen --release --lib screencopy
   cargo test --frozen --release --lib startup_cover
   cargo test --frozen --release --lib keep_frame
   LIBGL_ALWAYS_SOFTWARE=1 cargo test --frozen --release --lib emaki_
   LIBGL_ALWAYS_SOFTWARE=1 cargo test --frozen --release --lib cursor::
   cargo test --frozen --release -p niri-config emaki_backdrop
   cargo test --frozen --release -p niri-config emaki_wallpaper
   cargo test --frozen --release -p niri-ipc emaki_wallpaper
   ```

### Carrying a change from the middle of the stack

Done once on 2026-10-05 for a one-statement change to `src/backend/tty.rs` in patch
`0002` (FORK-25), in a separate clone of the niri fork whose `origin/*`
branches are the table's branches. The new branches `fix/cover`, `fix/ov`,
`fix/cur` and `fix/wp` take the places of `emaki-startup-cover`,
`emaki-overview-backdrop`, `emaki-cursor-crisp` and `emaki-wallpaper`; `fix/wp`
already carried wallpaper fixes on top of `origin/emaki-wallpaper`. These are
the commands that were run:

```sh
git branch fix/wp-before-fork25 fix/wp   # keeps the old commits reachable
git switch -c fix/cover origin/emaki-startup-cover
# edit, run check() on this branch, commit
git branch fix/ov origin/emaki-overview-backdrop
git rebase --onto fix/cover origin/emaki-startup-cover fix/ov
git branch fix/cur origin/emaki-cursor-crisp
git rebase --onto fix/ov origin/emaki-overview-backdrop fix/cur
git rebase --onto fix/cur origin/emaki-cursor-crisp fix/wp
```

The rebases applied without conflicts. Each moved branch then differed from
the branch it replaces only by the change:

```sh
git diff origin/emaki-overview-backdrop fix/ov   # the two added lines only
git diff origin/emaki-cursor-crisp fix/cur        # the same
git diff fix/wp-before-fork25 fix/wp              # the same
```

Each patch kept its header (every line before the first `diff --git`) and got
the body `git diff <base> <branch>`: `0002` from `origin/emaki fix/cover`,
`0003` from `fix/cover fix/ov`, `0004` from `fix/ov fix/cur`, `0005` from
`fix/cur fix/wp`. For one patch (`P` is its path, `BASE` and `BRANCH` its
pair; `git diff` runs in the fork clone):

```sh
n=$(grep -n -m1 '^diff --git' "$P" | cut -d: -f1)
head -n "$((n - 1))" "$P" > header
git diff "$BASE" "$BRANCH" > body
old_sha=$(sha512sum "$P" | cut -d' ' -f1); old_b2=$(b2sum "$P" | cut -d' ' -f1)
cat header body > "$P"
sed -i "s/$old_sha/$(sha512sum "$P" | cut -d' ' -f1)/; s/$old_b2/$(b2sum "$P" | cut -d' ' -f1)/" PKGBUILD
```

`0003` and `0004` came out byte-identical. `0005` changed
only in its `src/backend/tty.rs` index line and one hunk header. `0002`
gained the change and new `tty.rs` index and hunk lines, and its 39 blank
context lines got their leading space back, so it is now byte-identical to
`git diff` as well. The headers were not edited: the commit named in the
"Generated ..." line of `0005` is the base before the re-stack. Then the
stack check above ran with `fix/wp` as the tip (no difference), and the
checksum loop found each patch's sums once in the PKGBUILD. The table's
branches were not moved; pointing them at the new commits is a separate step.

### Fixes on `fix/wp` and `fix/exit` (2026-10-05)

A second clone, made with `git clone --no-local` from the clone above (its
`origin/fix/*` are the branches above), carries the later fixes of `0005` and
`0006`. Before any change it kept the old tips reachable:

```sh
git branch fix/wp-before-npf fix/wp
git branch fix/exit-before-npf fix/exit
```

`0006` got its branch first: the body was regenerated as
`git diff fix/wp fix/exit` with the same `header` and `body` commands (only the
index lines and four hunk headers changed), and the stack check with `fix/exit`
as the tip found no difference. Before, `0006` applied with offsets (2, 2 and
10 lines) and left `src/backend/tty.rs.orig` and `src/niri.rs.orig` in the tree.

A fix of `0005` is a commit on `fix/wp`; `fix/exit` is then moved onto it, and
both patches are regenerated (`0005` from `fix/cur fix/wp`, `0006` from
`fix/wp fix/exit`). The previous `fix/wp` tip is the base of the move (done
twice on 2026-10-05: from `fix/wp-before-npf`, then from the first fix):

```sh
git rebase --onto fix/wp <previous fix/wp tip> fix/exit
git range-diff <previous fix/wp tip>..<previous fix/exit> fix/wp..fix/exit   # every commit "="
```

Then the stack check ran twice: `0001` to `0005` against `fix/wp` and `0001`
to `0006` against `fix/exit`.

### Silent-break places

Places where a patch depends on upstream code in a way the compiler does not
check: after an upstream change there the patch still applies and the fork still
builds, but it misbehaves. Source paths are paths inside the patched niri tree.

#### Wallpaper camera in the scrolling layout (patch 0005)

Patch `0005` threads the wallpaper camera through `src/layout/scrolling.rs`:
it routes every assignment of `active_column_idx` through
`set_active_column_idx`, and calls `preserve_wallpaper_camera` with the camera
captured beforehand at the column and view-offset changes it touches, so the
wallpaper does not jump when niri rebases the view. Upstream v26.04 has
neither function; all 16 lines that mention them are added by the patch:

```sh
grep -c 'preserve_wallpaper_camera\|set_active_column_idx' packaging/niri-emaki/0005-emaki-wallpaper.patch   # 16
```

A new upstream code path that assigns `active_column_idx` directly, or changes
column widths without these calls, compiles without a warning and leaves the
wallpaper camera unpreserved on that path. At each rebase:

- `set_active_column_idx` must stay the only assignment of `active_column_idx`.
  On the `emaki-wallpaper` branch this prints exactly one line, inside that
  function:

  ```sh
  git grep -n 'active_column_idx = \|active_column_idx += \|active_column_idx -= ' emaki-wallpaper -- src/layout/scrolling.rs
  ```

- Read the upstream diff of `src/layout/scrolling.rs` between the old and the
  new tag for new code that changes the active column or column widths, and
  cover it in `src/layout/tests/emaki_wallpaper.rs` (added by `0005`; run by
  `check()` through `cargo test --frozen --release --lib emaki_`).

#### Overview navigation actions (patch 0003)

Patch `0003` adds `fn emaki_navigation(` to `src/input/mod.rs`. It decides by
action name which niri actions move the overview camera (with a direction or
without one); its last arm is `_ => return None,`. An action added upstream
falls into that arm without a compiler warning, and the overview backdrop does
not react to it.

```sh
grep -c '^+fn emaki_navigation(' packaging/niri-emaki/0003-emaki-overview-backdrop.patch        # 1
grep -c '^+        _ => return None,' packaging/niri-emaki/0003-emaki-overview-backdrop.patch  # 1
```

At each rebase, diff niri's `Action` enum (`niri-config/src/binds.rs`, the
type `src/input/mod.rs` imports) between the old and the new tag, and add every
new action that moves a column, window, workspace or the view to the match:

```sh
git diff v26.04 <new-tag> -- niri-config/src/binds.rs
```

(Run against upstream `main` in place of `<new-tag>`: in `Action` it adds only
the test-only variant `TestAction`, which is not camera navigation; the other
additions in that file belong to the `Trigger` enum.)

#### hide-from-own-screencopy and ext-image-copy-capture (patch 0001)

Patch `0001` hides layer surfaces with the `hide-from-own-screencopy` rule from
wlr-screencopy captures requested by their own client. The requesting client
travels in the `RenderCtx` field `capture_client`, which only the two
wlr-screencopy render paths in `src/niri.rs` set; every other render path
passes `None`. On the `emaki-wallpaper` branch the paths that set it are:

```sh
git grep -n 'capture_client: client' emaki-wallpaper -- src   # 2 lines, src/niri.rs
```

niri v26.04 has no ext-image-copy-capture, and no patch touches it today:

```sh
git grep 'image_copy_capture\|ext-image-copy' v26.04 -- src                      # no output
grep -c 'image_copy_capture\|ext-image-copy' packaging/niri-emaki/000*.patch    # 0 for all six
```

Upstream niri after v26.04 gains ext-image-copy-capture: commit `849c576f` on
upstream `main` adds `src/handlers/image_copy_capture.rs` (clone state: `main` of
2026-09-25; no tag in the clone contains the commit yet). When the fork moves
to a niri that contains it, the same filter must be added to that capture
path: it has to pass the requesting client as `capture_client`. Otherwise a
shell that captures through ext-image-copy-capture sees its own surfaces
again. The filter and its test (`cargo test --frozen --release --lib
screencopy` in `check()`) are written at that rebase; there is nothing to
change on 26.04.

#### Cursor element scale (patch 0004)

Patch `0004` adds `CursorRenderElement` to `src/cursor.rs`. Its `Element`
methods `geometry`, `damage_since` and `opaque_regions` ignore the scale they
are given and use the scale fixed when the element was built from
`output_scale`. The only guard is `debug_assert_eq!(scale, self.output_scale)`,
which the `--release` build in `build()` compiles out:

```sh
grep -c 'debug_assert_eq!(scale, self.output_scale)' packaging/niri-emaki/0004-crisp-xcursor-at-fractional-scale.patch   # 3
```

If upstream starts rendering the cursor element at a scale other than the
output scale it was built with (a new capture or render path, or a changed
scale for an existing one), release builds still report geometry, damage and
opaque regions at the build-time scale, without any error. At each rebase,
re-check every place that builds the element and every path that renders it
against the scale passed to `CursorRenderElement::new`:

```sh
git grep -n 'CursorRenderElement' emaki-wallpaper -- src
```

On the `emaki-wallpaper` branch this shows, outside `src/cursor.rs`, only
`src/niri.rs`: its `use` line, the one `CursorRenderElement::new` call and the
`NamedPointer` entry of the render element enum.

#### Primary renderer detection (patch 0006)

Patch `0006` makes `Tty::init` report a missing primary renderer after the
device loop when the primary GPU was in the device list taken by `Tty::new`
and `self.dmabuf_global` is still `None` (`primary_renderer_missing`). A
primary GPU that is not in that list registered after the list was taken; its
udev "added" event reaches `device_added` later, so `init` keeps running, as
stock niri does. In v26.04 the dmabuf global is created in `device_added` in the
same block that initializes the primary renderer, so "dmabuf global exists" is
"primary renderer exists". If upstream creates the dmabuf global elsewhere, or
initializes the primary renderer lazily after `init`, the fork still builds
but exits with status 3 on every start (the greeter then shows the text
failure screen with working graphics). At each rebase, check that the global
is still created together with the primary renderer and nowhere else:

```sh
git grep -n 'dmabuf_global.replace\|dmabuf_global = Some' emaki-wallpaper -- src/backend/tty.rs   # 1 line, in device_added
```

The decision itself is tested by `emaki_no_primary_renderer_only_when_the_primary_gpu_was_listed_at_start`
(run by `check()` through `--lib emaki_`); that the list is taken before the
primary GPU is picked is read in `Tty::new` (`UdevBackend::new`, then
`udev::primary_gpu`) at each rebase.

Before `exit(3)`, `main` drops the event loop to close the seat, so that the
VT is back in text mode when the greeter prints its failure screen. Two
things make that work, and neither is checked by the compiler: smithay keeps
the only strong reference to the libseat session in `LibSeatSessionNotifier`
(`src/backend/session/libseat.rs`; `LibSeatSession` holds a `Weak`), and no
source in the loop holds a `LoopHandle`, which would keep the loop's sources
alive after the loop is dropped. The second is tested by
`emaki_no_primary_renderer_exit_drops_the_session_notifier_with_the_event_loop`
(headless backend); at each rebase or smithay update, re-read the first and
the closures that `Tty` inserts into the loop.

#### Locked-session capture isolation (patch 0007)

Both window-cast paths must protect the session: output redraw calls
`Niri::render_windows_for_screen_cast`, and PipeWire requests also call
`State::redraw_cast` directly. Each must blank the buffer before reading the
window, its dimensions or cursor. Keep cursor metadata clearing and damage-tracker
reset in `Cast::dequeue_buffer_and_clear`; verify streams resume after unlock.

Output casts render the pointer separately, then use central composition, as do
both wlr-screencopy paths, output screenshots and GNOME portal screenshots.
`render_inner` must return lock elements before any session overlay or transition.
`render_pointer` must exclude session drag icons and non-lock-client surface cursors.
Window screenshots bypass central composition and need their own lock check.
`is_locked()` must cover `Locking` as well as `Locked`; review any state-machine change.
The existing unconfirmed surface wait and already captured/queued pixels are outside
this rendering boundary. Screenshot-worker and GPU-fence completion can happen later.

The current version has no ext-image-copy-capture or export-dmabuf implementation.
Any new capture protocol or direct window render path needs another lock audit, as
well as the client filtering audit for patch `0001`. Run the delivery guard and then
the source guard against the fully patched tree:

```sh
make check-assumptions
NIRI_SOURCE_DIR=/absolute/patched-niri-source make check-assumptions
LIBGL_ALWAYS_SOFTWARE=1 cargo test --frozen --release --lib lock_capture
```

The source guard detects changes to audited entry points; it is not pixel or protocol
proof. Follow `tests/vm/README.md` for window/output portal streams, screenshots,
cursor metadata and unlock recovery before accepting the package.

## quickshell-emaki

Two patches on Quickshell `v0.3.1`, applied by `prepare()` in this order:

| Patch | Branch | Base |
|---|---|---|
| `0001-wlr-screencopy-raw-wl_output-for-transform.patch` | `emaki` | `v0.3.1` |
| `0002-network-nm-name-the-activation-a-connection-failure.patch` | `emaki-network-attempt` | `emaki` |

Each branch is a single commit on its base. `emaki-network-attempt` was cut on
2026-10-05 in a separate clone of the fork; until it is fetched into the fork
clone, it exists only there.

Verified 2026-10-04 for `0001`: `git format-patch --stdout v0.3.1..emaki` differs from the
tracked file only in the first line, the `From <commit>` line; the commit named
in the tracked file is not in the local clone. Both have the same
`git patch-id --stable`. Since 2026-10-05 the tracked file's `Date:` line also
differs: it gives the same instant in UTC (`+0000`), rewritten by hand as
described for niri-emaki above.

`0002` (2026-10-05) is `git format-patch --stdout emaki..emaki-network-attempt`
with two header lines rewritten by hand: the `From:` author line is
`Emaki contributors <emaki@localhost>`, as in niri-emaki's `0002` to `0006`, and
the `Date:` line gives the commit's instant in UTC. Its first line names the
branch commit.

After regenerating a patch, rewrite its header lines the same way, then update
`sha256sums` in the PKGBUILD (run for `0002`; `tests/test-make-public.py` checks
that every patch's `Date:` is UTC and its sha256 is in the PKGBUILD). The package
is dropped once upstream Quickshell contains both fixes (see
`quickshell-emaki/README.md`).

Applying both patches to an export of `v0.3.1` gives exactly the tree of
`emaki-network-attempt`, without an offset or fuzz (checked 2026-10-05):

```sh
# In the Quickshell fork clone. EMAKI is the Emaki checkout; base/ and tip/ are
# empty scratch directories.
git archive v0.3.1 | tar -x -C base
git archive emaki-network-attempt | tar -x -C tip
(cd base && for p in "$EMAKI"/packaging/quickshell-emaki/000*.patch; do patch -Np1 -s -i "$p"; done)
diff -r base tip
```

`0002` carries its own test, which only a build with `-D BUILD_TESTING=ON`
compiles (the package does not): `ctest -R connectionfailure` in that build
directory. The test feeds the NetworkManager signals by hand and never opens a
D-Bus connection. On `v0.3.1` the existing `popupwindow` test fails
(`moveWithParent`, `QT_QPA_PLATFORM=offscreen`) with and without the patches.

### Places where a change breaks patch 0002 without a build error

`connectionFailed` is emitted in one place, `NMNetwork::bindFrontend` in
`src/network/nm/network.cpp`, when the active connection reports `Deactivated`
with reason `DeviceDisconnected`. Three facts about NetworkManager make the
reported reason and `Network.activation` right; re-read them at each rebase and
each NetworkManager update (references are to NetworkManager 1.58.1):

- The device's `StateChanged(Failed, reason)` D-Bus signal reaches the client
  before the active connection's `StateChanged`: `_set_state_full` in
  `src/core/devices/nm-device.c` emits the D-Bus signal before the GObject
  `state-changed` signal that `device_state_changed` in
  `src/core/nm-act-request.c` turns into the active connection's state.
- Every activation enters the device state `Prepare`
  (`activate_stage1_device_prepare` in `nm-device.c`), where the fork clears the
  previous failure reason.
- Active connection paths come from a counter that only grows while
  NetworkManager runs (`nm-dbus-object.c`), so a path names one activation; after
  a NetworkManager restart the numbering starts again.

```sh
git grep -n 'emit frontend->connectionFailed' emaki-network-attempt -- src/network   # 1 line, in nm/network.cpp
```

### Qt 6.12 candidate (2026-10-05)

The active recipe is `0.3.1-5`, fenced to Qt `>=6.11.2`, `<6.12`.
`quickshell-emaki/qt-6.12/` holds a dormant recipe with `>=6.12.0`,
`<6.13` fences for base and declarative, plus patch `0003`. Its placeholder
`pkgrel=1` is replaced with the next active release number during activation;
patch rebuilds never reserve a future minor's release number. The build
and watch discover only active `packaging/*/PKGBUILD` files. The candidate is
an activation overlay, not a standalone makepkg directory: it reuses patches
`0001` and `0002` after being copied beside them. Do not activate it on the
stable packaging branch until the coordinated dependency switch.

Upstream commit `5d5d49873fe8cf1f99ddfd5006ceb2057c5c9b13` on `origin/master`
explicitly fixes linking and Qt 6.12 compatibility. Patch `0003` backports its
15 source files, omitting the changelog, and rebases hunk positions onto
`v0.3.1` plus our two patches. Its stable patch ID matches the upstream
source-only diff (`f94f186a16b389fc86d3152459e307c15a4bdcc4`). All three patches
apply in order with `patch --fuzz=0`, without offsets. This proves applicability,
not a successful Qt 6.12 build.

Audit evidence, with paths/lines in the Quickshell clone at `v0.3.1`:

| Location | Compatibility concern / action |
|---|---|
| `src/services/pipewire/peak.hpp:19` | Opaque QObject-derived `PwNodeIface*`; upstream includes the complete type. |
| `src/wayland/hyprland/ipc/connection.hpp:28` | Opaque workspace/monitor/toplevel pointers; upstream uses `Q_MOC_INCLUDE` and moves bindable accessors out of line. |
| `src/x11/i3/ipc/controller.hpp:23` | Same opaque workspace/monitor pointers; fixed by the same backport. |
| `src/core/incubator.cpp:3,30,50` | Private render-loop header, singleton, animation and incubation internals; compile/runtime check required. |
| `src/window/proxywindow.cpp:233,335,497` | Direct private `updatesEnabled` field and `polishItems()` calls. |
| `src/wayland/buffer/dmabuf.cpp:1261,1273` | Private native texture creation and `QVkTexture` access; exercise capture/rendering in the VM. |
| `src/wayland/buffer/shm.cpp:39,76,83` | Private SHM conversion, integration singleton and buffer construction. |
| `src/wayland/screencopy/wlr_screencopy/wlr_screencopy.cpp:174` | Protected `QWaylandScreen::m_outputId`; patch 0001 still depends on it while avoiding Qt's generated output wrapper. |

[Qt's metatype documentation](https://doc.qt.io/qt-6/qmetatype.html#Q_DECLARE_OPAQUE_POINTER)
warns against opaque QObject pointers and recommends `Q_MOC_INCLUDE`, consistent
with the upstream fix. The [Qt 6.12 changes](https://doc.qt.io/qt-6/whatsnew612.html)
and [release notes](https://code.qt.io/cgit/qt/qtreleasenotes.git/about/qt/6.12.0/release-note.md)
raise the CMake minimum to 3.25, add QTP0006 for generated Wayland protocol
visibility, and make missing required QML properties construction errors.
Quickshell's `CMakeLists.txt:1` declares 3.20; its Qt policy request at `:151`
is through 6.6, so it does not automatically opt into QTP0006 NEW. Use CMake
3.25 or later and check QML startup. Patch 0002 uses public QObject, bindable
and D-Bus interfaces; it adds no private Qt headers. Neither existing patch
is superseded by the MOC backport.

The Qt source endpoints could not be retrieved during this audit. The table
therefore inventories private API exposure, not a verified claim that these
headers or signatures remain unchanged in 6.12. Compile and runtime validation
against extra-testing remain release gates; do not remove the fences based on
this source audit alone.

#### Activate on the host, then build the reviewed commit in the disposable VM

On the host, start with a clean checkout on a release preparation branch. Stage
activation and verify the recipe, patch checksums, Qt fences and marker pin:

```sh
python3 packaging/activate-qt612.py
python3 tests/test-packaging.py
git add packaging/quickshell-emaki/PKGBUILD packaging/quickshell-emaki/0003-qt-6.12-moc-includes.patch packaging/emaki/PKGBUILD
git commit -m 'Build Quickshell against Qt 6.12'
```

The helper copies the candidate recipe and patch 0003, increments the active
`pkgrel`, and changes the `emaki` marker pin to that version. Repeated activation
is idempotent. The packaging tests exercise activation
in a temporary tree as well as checking the current active recipe. Have the
activation commit reviewed and made available through the normal release
process before any VM build. Record its full commit hash as `REVIEWED_COMMIT`.

In the disposable VM, fetch that reviewed commit and check it out without local
changes; do not create or amend a commit in the VM. The release engineer enables
`[core-testing]` and `[extra-testing]` above their stable counterparts and
performs a full VM upgrade first. Confirm Qt base, declarative, wayland, svg and
shadertools all belong to the 6.12 series. As the unprivileged build user:

```sh
# Set REVIEWED_COMMIT to the full hash approved on the host.
: "${REVIEWED_COMMIT:?Set the reviewed activation commit hash}"
git checkout --detach "$REVIEWED_COMMIT"
test "$(git rev-parse HEAD)" = "$REVIEWED_COMMIT"
test -z "$(git status --porcelain)"
python3 tests/test-packaging.py
pacman -Q qt6-base qt6-declarative qt6-wayland qt6-svg qt6-shadertools cmake
packaging/build.sh --only quickshell-emaki --out /tmp/emaki-qt612-out
```

The recorded `BUILDINFO` source commit must equal `REVIEWED_COMMIT`. The marker
package must also be rebuilt from that commit for the switch-day repository so
its exact Quickshell pin selects release 3.

The output directory must be absent or empty. Inspect the resulting package's
`.PKGINFO` fences and `.BUILDINFO` Qt versions, keep the build log and `BUILDINFO`,
and test the installed package in the VM. In a separate source build with the
same three patches, enable `-D BUILD_TESTING=ON`; run
`ctest --test-dir build -R connectionfailure --output-on-failure`. The existing
`popupwindow` offscreen baseline failure is documented above. Exercise actual
niri capture and greeter/shell/lock startup on Qt 6.12; the source-only checks
performed here cannot establish those results. Follow “A fenced dependency
moved” in `docs/updates-runbook.md` for switch-day testing publication,
acceptance, and stable promotion. No package build or publication was performed
as part of this preparation.


## Executable source review gates (2026-10-06)

Package preparation runs `niri-emaki/check-assumptions.py` after every patch and before
fetching build dependencies. It rejects source changes affecting own-layer capture (464),
wallpaper camera/layout (465), action classification (753), cursor output scales (754),
new cursor render files, and introduction of the image-copy capture protocol.
Run `python3 tests/test-fork-assumptions.py` for mutation coverage.

The full-file fingerprints intentionally also reject benign edits. On a rebase, follow
“Silent-break places” above, classify new actions/render paths and layout mutations, extend
or run the corresponding source tests, then update fingerprints and package checksums in
the same reviewed change. Do not automatically regenerate fingerprints to make a build
pass. This gate records review boundaries; it does not prove output pixels or replace
fractional-scale, wallpaper navigation and capture checks in a running session.

### Static blur and wallpaper visibility (2026-10-07)

Patch `0008` fills in xray effect opaque regions when the postprocess shader mixes
an opaque workspace background. Preserve effect subregions, cropped geometry and
rounded corners. The wallpaper scheduler consumes these regions from the rendered
output list: default xray blur hides trains, while plain translucent surfaces and
`xray false` framebuffer blur show them. Run the `emaki_wallpaper_` source tests;
the compositor regression compares pixels and visible train counts for each path.
The guard fingerprints `xray.rs` and `postprocess.frag` alongside the compositor source. Revisit both
when changing the xray shader's alpha composition or static wallpaper input.
