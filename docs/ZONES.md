# Who owns which file

Every file Emaki puts on a computer belongs to one of three zones. The
zone says who changes the file, how, and what an Emaki update does to it.

1. Package zone. Files of the Emaki packages.
   On update: replaced. An edit there is lost without a warning.
2. Managed settings. Settings changed with `emaki settings`.
   On update: never touched.
   The CLI and keyboard menu use the installed store (see zone 2).
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

Owner: Emaki, on your command.
How to change: `emaki settings` opens settings inside the launcher; add a page name such as
`wifi` to open that page. `emaki settings --text` opens the keyboard menu.
`list`, `get`, `set`, `reset`, `history` and `undo` use the same core.
On update: never touched. A changed Emaki default reaches every
setting you have not set yourself.

- ~/.config/emaki/settings.toml (`$XDG_CONFIG_HOME/emaki/settings.toml`): versioned canonical overrides.
- ~/.local/state/emaki/generations/** (`$XDG_STATE_HOME/emaki/generations/**`): immutable derived files.
- ~/.local/state/emaki/history/** (`$XDG_STATE_HOME/emaki/history/**`): durable changes, undo and recovery journals.
- ~/.local/state/emaki/settings.lock (`$XDG_STATE_HOME/emaki/settings.lock`): serializes shell and CLI transactions.
- ~/.local/state/emaki/helper.lock: keeps interrupted helpers from racing with recovery.
- `$XDG_RUNTIME_DIR/emaki-settings/**`: generated session configuration, rebuilt as needed.
- ~/.local/state/emaki/defaults/mimeapps.list: derived default browser/file-manager
  associations. The session prepends this directory to `XDG_CONFIG_DIRS`, below
  personal configuration and above packaged defaults. Personal default choices win.
- ~/.config/niri-mimeapps.list: an older managed location, migrated only when
  carrying Emaki's managed header. Personal files are never adopted or overwritten.

Unset config/state variables use `~/.config` and `~/.local/state`. A missing
settings file means package defaults. New managed files are 0600 and directories
0700. Without an absolute runtime directory, staging uses
`$XDG_STATE_HOME/emaki/runtime/`. The canonical source is committed by atomic rename; history survives login
and reboot. Derived generations retain the newest 16 plus the selected generation if older;
semantic history remains available for undo. Installed reads create no files and do not
run pending recovery. Read the transaction and recovery contract in [settings.md](settings.md).

Gaps and the floating shortcut use an Emaki-owned runtime niri wrapper; wallpaper
and terminal choices use session helpers; bar and dock values use acknowledged
shell bindings. A failed application restores previous managed values. Emaki never
writes personal compositor configuration (see zone 3) or the personal fork include.

Keyboard remains machine-owned (zone 3). On an installed system `emaki settings`
reads and changes `keyboard.layouts` and `keyboard.switch_key` on the machine through
`emaki-machine-settings`, which asks the system's locale service; nothing of them is
stored in managed settings or their history. An explicit isolated test profile can
still store them without affecting a session.

The shell also keeps application use, recent items, notifications, night light and
welcome state under `$XDG_STATE_HOME/emaki/`; these are outside settings history:

- ~/.local/state/emaki/{dock,apps,recent,notifications,night-light,welcome}.json


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
or with the system tool (`localectl`, `timedatectl`); `emaki-machine-settings`
changes keyboard, time zone, automatic time and locale through the same system
services, after an administrator password. Boot refresh migrates
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
| `authentication` | Administrator password prompts [polkit-kde-agent] |
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


## Saved passwords after the KWallet transition

KWallet provides password storage, Secret Service and the Secret portal.
A typed graphical login password unlocks the default Blowfish wallet when its password matches.
Changing the account password does not change the wallet password.
Emaki does not ship a wallet password editor; an administrator can install `kwalletmanager` to change it separately.
Existing wallets need their original wallet password until then.

On the first password login after an update from 0.1.x, 0.2.0 or 0.3.0, Emaki migrates an isolated copy of the old login keyring before session applications start.
Generic items keep their attributes and labels.
Secret portal master keys keep their exact bytes in KWallet's `xdg-desktop-portal` folder, under the application id, so sandboxed applications can keep decrypting their data.
Every original file under `~/.local/share/keyrings/` (or `$XDG_DATA_HOME/keyrings/`) remains untouched.
Migration writes the destination `kwalletd/kdewallet.kwl`; other old wallet files remain available with their original password.
The private per-item record at `~/.local/state/emaki/wallet-migration-v1.json` (or `$XDG_STATE_HOME/emaki/wallet-migration-v1.json`) reports what was copied and what failed; a failure does not block login.
The desktop notice explains failures and how to recover.
Keep `gnome-keyring` installed until recovery is complete for every account.
If old keyrings exist and their application keys have not been safely accounted for,
the desktop still opens, but password storage and the Secret portal remain paused.
This prevents applications from generating replacement keys before recovery.

1. Keep a protected backup of old keyrings, `~/.local/share/kwalletd/`, and sandboxed application data, together with the old passwords.
2. Use `emaki-keyring-recover` in a private terminal to retry failed migration or unlock other collections with their own passwords.
   It reads isolated encrypted copies and leaves the originals untouched.
   Collections that remain locked cannot be migrated until you supply their password.
   Recovery leaves the live wallet unlocked.
   If password storage is paused, recovery uses a private destination and asks for
   its wallet password. It saves and stops that destination before returning.
   After recovery succeeds, log out and log in again to enable password storage.
3. Close and reopen affected applications, then verify saved passwords and encrypted data still work.
   A delivered notice or an empty-looking wallet does not prove migration succeeded.
4. Only after every account has verified every secret and affected application's data may an administrator review `pacman -R gnome-keyring`.
   Keep the original files even after removing the package.

If an application obtained a new master key after the update, ordinary recovery preserves it and reports the conflict.
To restore access to old encrypted application data, first close affected applications and back up both password stores and the application data.
Then run `emaki-keyring-recover --replace-portal-keys`.
This option replaces a conflicting application master key only when its old key is available in the old keyring.
Data encrypted with the newer key may become unreadable; keep the backups until both sets of data have been recovered.

Personal settings remain yours.
Modified `/etc/xdg/kwalletrc` and `/etc/xdg/xdg-desktop-portal/niri-portals.conf` files can leave `.pacnew` files after upgrade.
Review them together with personal `kwalletrc` settings: the native defaults enable `Wallet/Enabled`, `KSecretD/Enabled` and `org.freedesktop.secrets/apiEnabled`, disable `Migration/MigrateTo3rdParty`, and select `kwallet` for the Secret portal.
Arch globally enables `gnome-keyring-daemon.socket`; the old daemon can survive logout while the user manager remains alive, including during another session or with linger enabled.
The transition retires the old user's service and socket before the native provider starts; a competing owner is identified in the failure notice.
Runtime masks alone do not stop the package's direct D-Bus activation command.
Emaki retains its runtime activation overrides for Secret Service, the Secret portal and the compatibility service for the user manager's lifetime, including between graphical sessions.
Activation waits for the migration result; it cannot bypass paused password storage.
No original keyring files are removed.

The old polkit agent can be removed after the upgrade notice lists it as unused.
The GNOME screencast portal remains required for screen sharing.
