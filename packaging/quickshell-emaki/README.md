# quickshell-emaki

Quickshell 0.3.1 from Arch + three patches and one upstream backport. The first: the wlr-screencopy backend no longer holds a second `wl_output` through the QtWayland wrapper, which QtWayland mistook for a screen, crashing the shell (SIGSEGV in `QPlatformScreen::screen()` ← `calculateScreenFromSurfaceEvents()`) when a new window appeared on niri/Smithay while ScreencopyView was active.
The second: `Network.activation` names the NetworkManager activation a `connectionFailed` belongs to, a new activation no longer inherits the previous one's failure reason, and failure reasons without a `ConnectionFailReason` of their own are reported as `Unknown` instead of not at all (the shell's Wi-Fi panel needs this to ignore a late failure of a join it has retried).
The backport (`0003`) carries upstream commit `5d5d498` (complete types for meta-object generation), which Quickshell needs to build against Qt 6.12; see `packaging/REBASE.md`.
The third patch (`0004`) repairs the polkit service for the authentication prompt: requests run one at a time and the active flow passes through null between them, the authentication cookie is not logged (not even by the polkit library's `POLKIT_DEBUG` output), requests with unsupported identities and sessions that cannot start are refused instead of stalling the queue, the UTF-8 copy of a reply is wiped after it reaches the helper, `AuthFlow` gains `details`, `subjectPid` and `callerPid`, and `cancelActiveRequest()` cancels the active request (for the session lock).
`0005` keeps the private `WAYLAND_SOCKET` out of child environments. `0006` makes `DesktopEntries.applications` honour the desktop entry specification's `OnlyShowIn` and `NotShowIn` against `XDG_CURRENT_DESKTOP`, as GLib does; `byId` still finds such entries, so the dock keeps their icons. `0007` makes the desktop entry watch follow application directories created after the shell started (the flatpak exports on the first flatpak install, `~/.local/share/applications`) and subdirectories at any depth, so a program installed while the shell runs reaches `DesktopEntries.applications` without a restart. `0008` makes `Quickshell.iconPath(name, true)` load the icon themes again on a miss (at most every two seconds), adding the data directories' icon directories created since startup (the first flatpak's export directory), so a program installed while the shell runs shows its own icon instead of a letter. A missed name is looked up again when those two seconds end and until two seconds after its miss, and `Quickshell.iconThemeRevision` changes when one is found, so a binding that reads it (the launcher tile) updates by itself.
A separate package (`provides`/`conflicts` quickshell) prevents pacman from overwriting the fix during a normal update.
Build: `makepkg -s` in this directory; install: `sudo pacman -U quickshell-emaki-0.3.1-9-x86_64.pkg.tar.zst`.
Once upstream Quickshell includes these fixes, remove this package and return to `quickshell` from extra.
Upstream report (2026-09-25): https://github.com/quickshell-mirror/quickshell/issues/1202.

Every Qt patch release needs a rebuild. Set `_qtver` and all four Qt dependency lower
bounds to the build release, increment the active `pkgrel`, and update the exact
Quickshell pin in `packaging/emaki/PKGBUILD`. Builds validate the Qt Core and QML
versions and record `emaki-quickshell-qt-build=x.y.z` in package metadata for the
repository watcher. Follow the testing and promotion procedure in `docs/updates.md`.
The dormant Qt 6.12 recipe reserves no package release: `packaging/activate-qt612.py`
assigns the next integer after the active release, including intervening patch rebuilds.
