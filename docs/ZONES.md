# Who owns which file

Every file Emaki puts on a computer belongs to one of three zones. The
zone says who changes the file, how, and what an Emaki update does to it.

1. Package zone. Files of the Emaki packages.
   On update: replaced. An edit there is lost without a warning.
2. Managed settings. Settings changed with `emaki settings`.
   On update: never touched.
   NOT CONNECTED YET: in this release `emaki settings` does not change
   your settings or your session (see zone 2).
3. Your zone. Your files in your home, and the machine's own settings.
   On update: never written by Emaki in your home.

Change things in zone 3. Zone 1 is for reading.


## 1. Package zone

Owner: the Emaki packages, installed and updated by pacman.
How to change: not by hand. Override in your own file instead (zone 3).
History: none.

Files that an update replaces whole:

- /usr/share/emaki/**  (niri defaults, shell, themes, wallpaper)
- /usr/bin/emaki*, /usr/bin/niri-emaki-session
- Emaki units under /usr/lib/systemd/**, and /usr/lib/emaki*
- /usr/share/libalpm/hooks/*emaki*, /usr/share/libalpm/scripts/emaki-*
- /usr/share/doc/emaki/**  (this page)
- /usr/lib/initcpio/{hooks,install}/emaki-snapshot-fstab  (a boot hook
  for starting a snapshot from the boot menu)
- /etc/skel/.config/{kitty,qt6ct,wpaperd}/*  (templates that are
  copied into the home of a new account; see zone 3)

Files in /etc that pacman protects. If you never edited one, an update
replaces it. If you edited it and the package version did not change,
yours stays. If you edited it and the package changed it too, yours
stays and the new one is saved next to it as `<file>.pacnew`; pacman
prints a warning (see "HANDLING CONFIG FILES" in `man pacman`):

- /etc/niri/config.kdl
- /etc/xdg/mimeapps.list, /etc/xdg/kdeglobals, /etc/xdg/dolphinrc,
  /etc/xdg/qt6ct/qt6ct.conf, /etc/xdg/menus/emaki-applications.menu,
  /etc/xdg/xdg-desktop-portal/niri-portals.conf,
  /etc/xdg/fastfetch/config.jsonc, /etc/xdg/hypr/hyprlock.conf
- /etc/pam.d/emaki-lock, /etc/pacman.d/emaki-mirrorlist

Editing these needs sudo. A file of your own in zone 3 is the better
place for a change.

Files of other packages that an Emaki hook adjusts after every update
of the package named:

- /etc/grub.d/10_linux (grub, emaki-config): one line, the menu title.
- /etc/os-release (emaki-config, systemd): a link to
  /usr/lib/emaki/os-release.


## 2. Managed settings

NOT CONNECTED YET. In this release `emaki settings` works only in a
separate test profile (`--profile-root`). It does not read or change
your own settings and does not change the running session. Until it is
connected, change settings in your own files (zone 3).

Owner: Emaki, on your command.
How to change: only with `emaki settings`, never by hand.
On update: never touched. A changed Emaki default reaches every
setting you have not set yourself.

- ~/.config/emaki/settings.toml  (the settings you set; not created in
  this release)
- ~/.local/state/emaki/generations/**, ~/.local/state/emaki/history/**
  (files built from it, and the record of changes)

The shell keeps a few choices of its own, changed by clicking in the
shell (dock, night light, the welcome window). They are in this zone,
but they are not in the `emaki settings` history and have no undo:

- ~/.local/state/emaki/{dock,apps,recent,notifications,night-light,
  welcome}.json


## 3. Your zone

Owner: you. Emaki never writes these files in your home.
How to change: any way you like.
History: yours to keep; a system snapshot does not cover your home.

Your files:

- ~/.config/emaki/autostart-disabled/*  Your login off switches [autostart].
- ~/.config/niri/config.kdl  Your niri config. The installer may have
  created it with your keyboard layout; after that it is only yours. If
  you write one, its first line must be
  `include "/usr/share/emaki/niri/default.kdl"`.
- ~/.config/emaki/niri-emaki.kdl  Read after everything else in the
  "niri (Emaki)" session, if it exists.
- ~/.config/kitty/kitty.conf, ~/.config/qt6ct/qt6ct.conf,
  ~/.config/wpaperd/config.toml  Copied from /etc/skel when your account
  was made; they only point at Emaki's files in zone 1.
- ~/.config/mimeapps.list  Your default applications.
- ~/.config/fuzzel/fuzzel.ini, ~/.config/hypr/hyprlock.conf  If one of
  these exists, it replaces Emaki's file of the same name whole.
- everything else in your home.

The machine's own settings, initially written by the installer from your
answers. Emaki packages do not own these files; you change them with sudo
or with the system tool (`localectl`, `timedatectl`). Boot refresh migrates
recognized menu defaults to a packaged include, preserves your custom menu
values and disk arguments, and regenerates the boot menu and initramfs.
It also replaces the stock resume hook name with `emaki-resume`:

- /etc/vconsole.conf, /etc/locale.conf, /etc/locale.gen,
  /etc/localtime, /etc/hostname, /etc/conf.d/wireless-regdom
- /etc/fstab, /etc/default/grub, /boot/grub/grub.cfg,
  /etc/mkinitcpio.conf, /etc/mkinitcpio.d/*.preset,
  /etc/cryptsetup-keys.d/*
- /etc/snapper/configs/root, /etc/default/grub-btrfs/config
- /etc/sudoers.d/10-wheel, /etc/systemd/zram-generator.conf
- the [emaki] section of /etc/pacman.conf
- /etc/initcpio/{hooks,install}/emaki-snapshot-fstab, only where the
  Emaki 0.1.2 installer wrote them (btrfs installs): mkinitcpio uses
  these copies instead of the hook in zone 1.


## How your niri config meets Emaki's

Your niri file is read after Emaki's defaults, so it wins where niri
lets a later setting win:

- Your own shortcuts override Emaki defaults [niri binds].
  A bind on the same keys replaces Emaki's bind.
- Gaps and other `layout` values replace Emaki's one by one.
- A `touchpad { }` block replaces Emaki's touchpad block whole.
- An `xkb { }` block sets your keyboard layouts. With it, your session
  no longer follows the system keyboard layouts (`localectl`).
- An `output` block sets up a monitor; Emaki ships none. If two blocks
  name the same monitor, niri uses the first one it reads.

Your file cannot remove an Emaki shortcut, window rule or workspace [niri].
You can replace a shortcut with `spawn-sh "true"` to stop its action, but niri
still captures those keys; it does not pass them to the application.
For example, put `binds { Mod+V { spawn-sh "true"; }; }` after the include
line to stop the clipboard shortcut [niri binds].

### Turn off a login command [autostart]

Create an empty file in `~/.config/emaki/autostart-disabled/` to switch off
one Emaki login command [autostart].
These files belong to you: Emaki never creates, edits or removes them,
and updates leave them alone.
If you set `XDG_CONFIG_HOME` to an absolute path, use that directory instead
of `~/.config`.

| File name | Login command switched off |
| --- | --- |
| `wallpaper` | Desktop wallpaper [emaki-session-wallpaper] |
| `clipboard` | Clipboard history recording [wl-paste, cliphist] |
| `authentication` | Administrator password prompts [polkit-gnome] |
| `automount` | Drive mounting on request (udiskie) |
| `shell` | Panel, launcher and notifications [emaki-shell] |
| `sleep-guard` | Locking before sleep [emaki-sleep-guard] |

To turn off the clipboard history at login [cliphist]:

```sh
mkdir -p "${XDG_CONFIG_HOME:-$HOME/.config}/emaki/autostart-disabled"
touch "${XDG_CONFIG_HOME:-$HOME/.config}/emaki/autostart-disabled/clipboard"
```

To turn it back on at login [cliphist]:

```sh
rm "${XDG_CONFIG_HOME:-$HOME/.config}/emaki/autostart-disabled/clipboard"
```

Log out and back in for the change to take effect [autostart].
A switch does not stop a running command or prevent you from starting it yourself.


## Legacy snapshot limits

The maintenance update makes one narrow migration of `/etc/snapper/configs/root`: only a
regular root-btrfs configuration with the installer's `NUMBER_LIMIT="20"` is eligible, and
only individual limits still equal to the old defaults change. Other values stay intact.
A migration comment prevents repeating the change if you later choose old values again.
Package-owned cleanup code supplies this repair to existing systems; the configuration
remains machine-owned. A symlink or a customized number limit opts out of this migration.


## Old KDE passwords

At login, Emaki automatically asks KWallet to move old KDE passwords to the login keyring.
Wallets with an empty password move without a prompt.
If an old wallet needs a password, KWallet asks for it; Cancel skips that attempt.
Automatic attempts run at most once per login and stop after three logins with passwords still unmoved; interrupted attempts count too.
Each attempt lasts at most five minutes.
A single notification then gives the retry command: `emaki-wallet-migrate`.

To retry later, log out and back in, then run `emaki-wallet-migrate` in a terminal before opening KDE applications.
Enter the old wallet password in KWallet's dialog, then press Enter in the terminal after all dialogs close.
Run `emaki-wallet-migrate --help` for help.
Original wallet files are kept, and migration records belong to `~/.local/state/emaki/wallet-migration.json` (or `$XDG_STATE_HOME/emaki/wallet-migration.json`).
An empty-looking wallet in a KDE application does not mean the old passwords were deleted.
