# quickshell-emaki

Quickshell 0.3.1 from Arch + two patches. The first: the wlr-screencopy backend no longer holds a second `wl_output` through the QtWayland wrapper, which QtWayland mistook for a screen, crashing the shell (SIGSEGV in `QPlatformScreen::screen()` ← `calculateScreenFromSurfaceEvents()`) when a new window appeared on niri/Smithay while ScreencopyView was active.
The second: `Network.activation` names the NetworkManager activation a `connectionFailed` belongs to, a new activation no longer inherits the previous one's failure reason, and failure reasons without a `ConnectionFailReason` of their own are reported as `Unknown` instead of not at all (the shell's Wi-Fi panel needs this to ignore a late failure of a join it has retried).
A separate package (`provides`/`conflicts` quickshell) prevents pacman from overwriting the fix during a normal update.
Build: `makepkg -s` in this directory; install: `sudo pacman -U quickshell-emaki-0.3.1-2-x86_64.pkg.tar.zst`.
Once upstream Quickshell includes both fixes, remove this package and return to `quickshell` from extra.
Upstream report (2026-09-25): https://github.com/quickshell-mirror/quickshell/issues/1202.
