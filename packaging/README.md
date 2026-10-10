# Emaki 0.4.2 packages

Copyright (C) 2026 Artur Yakymenko. Emaki packaging is GPL-3.0-or-later;
the Quickshell fork retains its upstream LGPL-3.0-only license.

`emaki` pins the six tested release packages. `emaki-config` owns the CLI,
shell, session files, greeter, themes, cursors, wallpaper, GRUB background,
release marker, os-release, system preset and skel defaults. Its alpm hook
links the unowned `/etc/os-release` to `/usr/lib/emaki/os-release` and links
it back to Arch's file on removal; `/usr/lib/os-release` stays as `filesystem`
ships it, so `pacman -Qkk` reports nothing for it. `emaki-desktop` adds the
Minimal applications (Dolphin, Firefox, kitty), the firmware and graphics drivers of the
live image (so an install keeps what worked from the stick) and password-only lock PAM
configuration.
`xdg-desktop-portal-gnome-emaki` follows Arch's 50.0-1 recipe and sources, removes
Nautilus from its dependencies and replaces the upstream package. Its source tag is
signed and makepkg does not import keys by itself: import the two maintainer keys in
`xdg-desktop-portal-gnome-emaki/keys/pgp/` with `gpg --import` before makepkg. `emaki-config`
installs `/etc/xdg/xdg-desktop-portal/niri-portals.conf`, which takes precedence over
niri's `/usr/share` default while preserving user overrides. FileChooser uses GTK;
ScreenCast stays on GNOME. The ISO builder rejects Nautilus in the resolved closure.
`emaki-apps` adds the Rich KDE application set, LibreOffice Fresh, archive backends
and codecs. It depends on `emaki-desktop`; the release marker does not depend on it,
so Minimal stays reversible with `pacman -S emaki-apps`. Its own preset file
`45-emaki-apps.preset` enables CUPS (`cups.service`, `.socket`, `.path`) when the
installer runs `systemctl preset-all`, so only Rich installs print from the first boot;
on a running system its scriptlet enables nothing and prints the command to switch
printing on (`emaki-apps.install`). The `niri-emaki`
package alone owns the fork executable. Qt minor-version ranges and niri's
stock-version/soname constraints require rebuilding the forks before those
dependencies can advance.

Build `emaki-config` from a committed repository checkout (a detached HEAD
from a bundle is supported). `_emaki_commit` defaults to HEAD and is exported
across makepkg/fakeroot; set it explicitly to build another committed revision.
`prepare()` archives that revision and fetches the locked Cargo dependencies.
`build()` uses `make build`, including offline locked Cargo and qsb. `check()`
uses `make check`: stock niri validates the base configs; fork-only rules are
also checked when `niri-emaki` is available. An installed but outdated fork
is an error. `package()` stages without host package/service queries and
diffs the payload against `emaki-config/expected-files.list`. Commit a reviewed
manifest update when changing installed files. A source-only makepkg tarball
is a recipe export; rebuilding still requires the adjacent repository/bundle.

The nine system configs are pacman backups. `/etc/skel` supplies only a
wallpaper path and kitty/qt6ct references to package defaults; user creation
copies them once. wpaperd reads `~/.config/wpaperd/config.toml` directly, with
the ring image centered and cropped as the stock-session fallback. The fork
draws its living ring separately. The installer provisions each account's
greeter publishing directory explicitly and applies `50-emaki.preset`.
Package staging does not run tmpfiles or enable services: Arch's pacman hooks
process the installed tmpfiles and unit files. Developer `make install`
still creates greeter tmpfiles; `make enable-greeter GREETER_USER=<login>`
requires an explicit account.

`emaki-sleep-guard.service` starts with `emaki-shell.service` from the niri
session config and stops with `graphical-session.target`. Its isolated Python
helper uses Gio on the system bus to hold a logind `sleep` delay inhibitor.
On `PrepareForSleep(true)`, it runs `emaki-lock --wait` with an 18-second
timeout (12 seconds when `EMAKI_SLEEP_LOCK_FAILURE` selects any policy other
than the default `sleep`, which leaves time to act after a failed lock), then
closes the inhibitor descriptor; resume reacquires it. `50-emaki.conf` sets
`InhibitDelayMaxSec=20`. The locker lives in its own scope, so the guard's
short-lived client does not own its lifetime. A failed or timed-out lock is
logged and handled by that policy (unset: `sleep`, the machine sleeps unlocked;
the policies are listed in `/usr/bin/emaki-sleep-guard`); a delay inhibitor
cannot veto sleep beyond logind's deadline. Every policy, the default included,
leaves a flag that the shell turns into a one-time notice and deletes:
`$XDG_RUNTIME_DIR/emaki-sleep-lock-failed` holds the policy name, or
`end-session-failed` when `end-session` could not end the session.
`end-session` judges that by niri's IPC socket disappearing (niri's reply to
quit cannot tell) and keeps the sleep delay while it waits; a session it ended
gets `$XDG_STATE_HOME/emaki/sleep-lock-failed` with that boot and niri socket,
so only a shell of a later session shows it. This covers lid/direct sleep
requests without enabling an idle policy or using a system-sleep hook (where
user sessions are frozen).
VM acceptance must cover successful lock, slow/failing lock, direct suspend,
resume and a second suspend, plus logind restart recovery.

`emaki-keyring` installs the public signing key and trust/revocation lists;
its install/upgrade hook runs `pacman-key --populate emaki`. The installer
initializes pacman's keyring first. Add `[emaki]` to pacman.conf with
`Include = /etc/pacman.d/emaki-mirrorlist`. The mirror list includes `/etc/emaki/channel` [channel selector].
This file selects `/usr/share/emaki/mirrors/stable.conf` by default; select
`testing.conf` and run `sudo pacman -Syyu` once to switch [update channel].
The package creates the selector once and never replaces it [emaki-mirrorlist].
Run `emaki-update-channel` to read the effective update source [update channel].
`/usr/lib/emaki-release` records the release version and label [release metadata].

Local checks: `make build`, `make check`, `python3 tests/test-packaging.py`.
The packaging tests stage into a temporary root, reject any host package or
service command, check local source checksums, package metadata, payload,
skel/presets and inhibitor descriptor lifetime. They run `systemd-analyze
--user verify` when available; a sandbox denial of its manager sockets is
reported as a skip and requires a VM rerun. No real sleep, lock or service
start occurs in these tests.

Application defaults live in `/etc/xdg/mimeapps.list`. Missing Rich handlers are
skipped by MIME resolvers; Firefox is the explicit Minimal PDF/common-image fallback.
The system Qt palette reference and token-generated `kdeglobals` cover Qt and KDE
applications without creating user files. `/etc/xdg/dolphinrc` holds only
`[General] ShowStatusBar=FullWidth` (Dolphin's own default status bar elides its
text); a person's `~/.config/dolphinrc` is read after it and wins.
`make render` regenerates KDE colors.
The session exports `XDG_MENU_PREFIX=emaki-`, selecting the packaged
`/etc/xdg/menus/emaki-applications.menu`; this supplies KService's application tree
for Dolphin's Open With dialog outside Plasma. User MIME and color settings retain
their usual XDG precedence. `python3 tests/test-kde-defaults.py` exercises real
KService/KColorScheme APIs with isolated configuration and cache directories.

The live image installs only the Minimal desktop. `iso/target-packages.txt` seeds
`emaki-apps` into the signed offline repository, and `iso/emaki-packages.txt` requires
the new package before building. `scripts/check-arch-apps.py` checks current Arch
core/extra names and records API package sizes in `emaki-apps/arch-packages.json`.
Its size calculation is an estimate; `iso/build.sh` resolves the complete transaction
with pacman and checks it again with only the offline repository available.


### Optional NVIDIA policy and offline inputs

`emaki-nvidia` is built with the release packages but is not a desktop metapackage
dependency. The installer selects it for Turing and newer NVIDIA hardware only.
It archives its shared detector from `installer/emaki_installer/graphics.py` and
ships configuration, session policy, module checks, an application profile and notices.

Only `emaki-nvidia` must come from the signed Emaki input repository. All driver
packages come from the official Arch repositories at the pinned ISO snapshot:
`nvidia-open`, `nvidia-open-lts`, `nvidia-open-dkms`, `nvidia-utils`,
`libva-nvidia-driver`, DKMS and the two kernel headers. Installed systems receive
these packages through normal Arch updates. No legacy proprietary driver is seeded.

`iso/nvidia-seeds.py` selects prebuilt modules when both kernels are available,
otherwise DKMS with both sets of headers. It repeats the selected transaction
against only the staged target repository with a fresh database and empty cache. Missing inputs
or unresolved dependencies fail the build. Prebuilt modules need no DKMS build;
the fallback uses DKMS for both installed kernels and checks module availability
before installation completes. Custom kernels are not selected by the installer;
manual custom-kernel use needs matching headers and a successful DKMS build.
