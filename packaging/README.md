# Emaki 0.1.1 packages

Copyright (C) 2026 Artur Yakymenko. Emaki packaging is GPL-3.0-or-later;
the Quickshell fork retains its upstream LGPL-3.0-only license.

`emaki` pins the six tested release packages. `emaki-config` owns the CLI,
shell, session files, greeter, themes, cursors, wallpaper, GRUB background,
release marker, os-release, system preset and skel defaults. Its alpm hook
links the unowned `/etc/os-release` to `/usr/lib/emaki/os-release` and links
it back to Arch's file on removal; `/usr/lib/os-release` stays as `filesystem`
ships it, so `pacman -Qkk` reports nothing for it. `emaki-desktop` adds the
applications and password-only lock PAM configuration. The `niri-emaki`
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

The three system configs are pacman backups. `/etc/skel` supplies only a
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
timeout, then closes the inhibitor descriptor; resume reacquires it.
`50-emaki.conf` sets `InhibitDelayMaxSec=20`. The locker lives in its own
scope, so the guard's short-lived client does not own its lifetime. A failed
or timed-out lock is logged; a delay inhibitor cannot veto sleep beyond
logind's deadline. This covers lid/direct sleep requests without enabling an
idle policy or using a system-sleep hook (where user sessions are frozen).
VM acceptance must cover successful lock, slow/failing lock, direct suspend,
resume and a second suspend, plus logind restart recovery.

`emaki-keyring` installs the public signing key and trust/revocation lists;
its install/upgrade hook runs `pacman-key --populate emaki`. The installer
initializes pacman's keyring first. Add `[emaki]` to pacman.conf with
`Include = /etc/pacman.d/emaki-mirrorlist`. The mirrorlist defaults to
`https://github.com/emakish/packages/releases/download/stable`; switch its
single active server to the commented `testing` URL for that channel. The
package-owned `/usr/lib/emaki-release` describes the default release channel.

Local checks: `make build`, `make check`, `python3 tests/test-packaging.py`.
The packaging tests stage into a temporary root, reject any host package or
service command, check local source checksums, package metadata, payload,
skel/presets and inhibitor descriptor lifetime. They run `systemd-analyze
--user verify` when available; a sandbox denial of its manager sockets is
reported as a skip and requires a VM rerun. No real sleep, lock or service
start occurs in these tests.
