# Emaki

Emaki is an Arch-based desktop built around **niri-emaki**, a fork of the
[niri](https://github.com/niri-wm/niri) scrollable-tiling Wayland compositor, and its own
[Quickshell](https://quickshell.org) shell. Warm colours, glass panels, and a living
pixel-art wallpaper: a looping valley at sunset that scrolls with your workspaces, with
steam trains running through it.

**Status: 0.1.0 — the first working version, not a stable release.** It has been tested in
QEMU with UEFI firmware (OVMF), not yet on a range of real hardware.

![The Emaki desktop](docs/screenshots/desktop.png)

| | |
|---|---|
| ![Live session](docs/screenshots/live-session.png) | ![Installer](docs/screenshots/installer.png) |
| The live session from the USB stick | The installer, last step before writing to disk |
| ![Boot menu](docs/screenshots/boot-menu.png) | ![Login screen](docs/screenshots/login.png) |
| GRUB: two kernels and the btrfs snapshots | The login screen |

## What's inside

- **niri-emaki** (`packaging/niri-emaki/`) — niri 26.04 with five patches: the living
  wallpaper, glass capture for the shell, a seamless handoff from the login screen, an
  animated overview backdrop and crisp cursors at fractional scale.
- **The shell** (`shell/`) — bar islands (workspaces, clock, system, privacy), dock,
  launcher, notifications and on-screen display on liquid glass. The login screen (a greetd
  greeter) and the lock screen are drawn by the same shell.
- **The `emaki` command** (`crates/`) — system map and state, and niri window/workspace
  commands from the terminal.
- **The installer** (`installer/`) — a root worker with a JSON protocol, a command-line client
  and a graphical window.
- **The ISO profile** (`iso/`) — archiso 91 releng with the Emaki overlay.
- **Packages** (`packaging/`) — `emaki`, `emaki-config`, `emaki-desktop`, `emaki-installer`,
  `emaki-keyring`, `emaki-mirrorlist`, `niri-emaki`, `quickshell-emaki`.
- **Art** (`art/`, `cursors/`, `fetch/`, `boot/`) — the wallpaper, logo, cursors, GRUB
  background and boot splash, with the scripts that make them.

## The ISO

`emaki-0.1.0-x86_64.iso` boots (UEFI only) into a live Emaki session with the installer open.
The installer offers:

- **Erase disk** with **btrfs** (recommended: snapper snapshots that you can boot from the GRUB
  menu) or **ext4** (no system snapshots).
- **Manual partitioning**: prepare partitions in GParted, then assign mount points in the
  installer; an existing EFI system partition can be reused.
- **Offline installation** from the signed package repository on the USB stick. When the
  machine is online, the installer can also update Emaki at the end.

The installed system gets GRUB with `linux` and `linux-lts`, zram (no swap file) and
NetworkManager. Not in 0.1.0: disk encryption, BIOS boot, installing alongside Windows.

## Updates

An installed system updates with `pacman -Syu`. Emaki's own packages come from the signed
`[emaki]` repository, published as GitHub Releases of
[`emakish/packages`](https://github.com/emakish/packages): `emaki-keyring` installs the signing
key and `emaki-mirrorlist` selects the `stable` channel. Everything else comes from Arch.

## Building

- **Packages**: each one builds with `makepkg` in its directory under `packaging/`;
  `emaki-config` and `emaki-installer` archive the committed checkout, so commit first.
  Details and local checks: [`packaging/README.md`](packaging/README.md).
- **ISO**: `iso/build.sh` runs inside a disposable Arch build VM with archiso 91, from a signed
  repository of the packages above. The build and the QEMU/OVMF test cycle
  (`tests/vm/run-iso.sh` and the `tests/vm/iso-*` scripts) are described in
  [`docs/iso.md`](docs/iso.md); the installer in [`installer/README.md`](installer/README.md).
- **Checks**: `make check` (configs and fast tests), `python3 tests/test-packaging.py`,
  `PYTHONPATH=installer python -m unittest discover -s installer/tests`, `iso/check.sh`.

## License

GPL-3.0-or-later, see [LICENSE](LICENSE). Copyright (C) 2026 Artur Yakymenko.

Patches to other projects keep those projects' licences: the niri patches in
`packaging/niri-emaki/` are GPL-3.0-or-later like niri; the Quickshell patch in
`packaging/quickshell-emaki/` is LGPL-3.0-only like Quickshell.
