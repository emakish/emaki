# Updating Emaki

## The one command

```
sudo pacman -Syu
```

Type your password, read the list, and press Enter at each question. Emaki's packages come from
`[emaki]` at `https://pkgs.emaki.sh/stable/x86_64`; everything else comes from Arch Linux as
usual. Nothing on the screen tells you that updates exist yet; a notice and an `emaki update`
command come later, as an ordinary update. Plain `sudo pacman -Syu` keeps working after that.

When it has finished, restart the computer: the desktop that is running keeps using the old
shell and compositor until you do. Nothing restarts by itself.

## What is signed

Every Emaki package is signed and pacman refuses an unsigned or wrongly signed package. The
repository database is signed too, and pacman checks that signature when it is there. A
signature does not prove that a database is the newest one: someone who controlled the mirror
could serve an older, correctly signed database, and `pacman -Syu` would then simply find
nothing to update.

## Channels

`/etc/pacman.d/emaki-mirrorlist` names one channel:

```
Server = https://pkgs.emaki.sh/stable/$arch
# Server = https://pkgs.emaki.sh/testing/$arch
```

`testing` receives every release first; `stable` receives only releases that passed the
upgrade check. To change channel, swap which line is commented and run `sudo pacman -Syyu` once
(two `y`: the databases of the two channels are not in time order, and a plain `-Syu` can fail
once with a signature error after the switch). An edited mirrorlist is kept when the package
changes it; pacman then writes the new version beside it as `emaki-mirrorlist.pacnew`.

## Installed from 0.1.0 or 0.1.1

These systems read `https://github.com/emakish/packages/releases/download/stable`. The next
`sudo pacman -Syu` installs the release that moves them: its `emaki-mirrorlist` replaces the
old file (if you never edited it), and the following update comes from `pkgs.emaki.sh`.
Releases keep being copied to the old address for a while. If your machine missed that period,
or you had edited the file, run once:

```
echo 'Server = https://pkgs.emaki.sh/stable/$arch' | sudo tee /etc/pacman.d/emaki-mirrorlist && sudo pacman -Syyu
```

## If an update breaks something

| Install | What you can do |
|---|---|
| btrfs (the default) | Restart; in the boot menu open **Emaki snapshots** and pick the snapshot taken before the update. If that snapshot already contains `emaki-rollback` (it arrives with the release that moves a machine to `pkgs.emaki.sh`), the desktop offers **Keep this state**, which makes the snapshot the system for good. |
| btrfs, snapshot without the tool | Snapshots of systems that had not yet received that release (0.1.0, 0.1.1 and the first 0.1.2 build) can be booted but not kept from inside. If the updated system still starts, keep the older snapshot from there with `sudo emaki-rollback snapshot <number>` (`sudo snapper list` shows the numbers). If it does not start, no tested procedure exists yet. |
| btrfs with disk encryption | As above, after typing the disk passphrase at the first prompt. Keeping a snapshot on an encrypted install has not been checked in a VM yet. |
| ext4 | There are no snapshots. If a new kernel is the problem, choose the `linux-lts` entry in the boot menu. Otherwise reinstall the previous package from the cache: `sudo pacman -U /var/cache/pacman/pkg/<file of the previous version>`. |
| Any, desktop does not start | Ctrl+Alt+F3 opens a text console; log in there and run `sudo pacman -Syu` again once a fix is announced. |

When a release of Emaki itself is bad, Emaki withdraws it from the mirror, so that machines that
have not updated yet do not receive it. A machine that already installed it keeps it: pacman
never goes back to an older version by itself. Go back with a snapshot, as above; the next
update then brings the fixed release.

When Arch releases a library that Emaki's compositor or shell must be rebuilt for (for example a
new Qt), `sudo pacman -Syu` stops with a message like
`breaks dependency 'qt6-base<6.12' required by quickshell-emaki` and changes nothing. Your system
is fine; wait for the rebuilt Emaki release and update then.
