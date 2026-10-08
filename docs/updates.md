# Updating Emaki

## The one command

```
sudo emaki-update
```

Type your password, read the list, and press Enter at each question. Emaki's packages come from
`[emaki]` at `https://pkgs.emaki.sh/stable/x86_64`; everything else comes from Arch Linux as
usual. The command explains common update refusals in plain English and keeps the technical
details in brackets. It runs a full update with the same package checks and confirmation
questions [pacman]. Plain `sudo pacman -Syu` still works and keeps its native messages.
The desktop does not yet check whether updates are available.

After a desktop update, an on-screen notice asks you to save your work, sign out and sign in
again. The running panel keeps its own copy of its files until it restarts, so an update cannot
replace its helpers while it is running. Nothing restarts by itself. Restart the computer
after a kernel update.

## What is signed

Every Emaki package is signed and pacman refuses an unsigned or wrongly signed package. The
repository database is signed too. The packaged stable and testing server definitions require
its signature; custom server configurations retain their own signature policy. A
signature does not prove that a database is the newest one: someone who controlled the mirror
could serve an older, correctly signed database, and `pacman -Syu` would then simply find
nothing to update.

## Channels

`/etc/emaki/channel` selects the update source [update channel]:

```
Include = /usr/share/emaki/mirrors/stable.conf
# Include = /usr/share/emaki/mirrors/testing.conf
```

`testing` receives every release first; `stable` receives only releases that passed the
upgrade check. To change channel, swap which line is commented and run `sudo pacman -Syyu` once
(two `y`: the databases of the two channels are not in time order, and a plain `-Syu` can fail
once with a signature error after the switch). The package keeps a valid stable or testing choice unchanged [channel selector].
On installation or upgrade, it repairs a missing or invalid selector to stable; a missing
selector during migration inherits a recognized older channel.
Package-owned server definitions receive address updates [emaki-mirrorlist]. Recognized older GitHub and Emaki server lines migrate automatically, preserving the
channel; custom server configurations stay untouched [emaki-mirrorlist]. `emaki-update-channel` reports the
effective channel, or `custom`, `mixed`, `disabled`, or `unknown` when appropriate.

If the selector is missing or invalid and pacman cannot read it, recover the stable source
and reinstall the mirror definitions in one command:

```
echo 'Server = https://pkgs.emaki.sh/stable/$arch' | sudo tee /etc/pacman.d/emaki-mirrorlist && sudo pacman -Syyu emaki-mirrorlist
```

Pacman may announce a `.pacnew` before the migration script runs. For a recognized older
channel, the script merges that packaged file automatically, removes it, and reports that
there is nothing left to merge. Custom mirror lists keep their `.pacnew` for manual review.
Removing `emaki-mirrorlist` removes its Include from `[emaki]` and removes the channel selector;
other repositories and independent custom server lines remain configured.

## Installed from 0.1.0 or 0.1.1

These systems read `https://github.com/emakish/packages/releases/download/stable`. The next
`sudo pacman -Syu` installs the release that moves them: its `emaki-mirrorlist` migrates the
recognized old server line, including a selected testing channel, and the following update comes from `pkgs.emaki.sh`.
Releases keep being copied to the old address for a while. If your machine missed that period,
or you had edited the file, run once:

```
echo 'Server = https://pkgs.emaki.sh/stable/$arch' | sudo tee /etc/pacman.d/emaki-mirrorlist && sudo pacman -Syyu
```

## If an update breaks something

| Install | What you can do |
|---|---|
| btrfs (the default) | Restart; in the boot menu open **Emaki snapshots** and pick the snapshot taken before the update. If that snapshot already contains `emaki-rollback` (it arrives with the release that moves a machine to `pkgs.emaki.sh`), the desktop offers **Keep this state**, which makes the snapshot the system for good. |
| btrfs, snapshot without the tool | Snapshots of systems that had not yet received that release (0.1.0, 0.1.1 and the first 0.1.2 build) can be booted but not kept from inside. If the updated system still starts, keep the older snapshot from there with `sudo emaki-rollback snapshot <number>` (`sudo snapper list` shows the numbers). If it does not start, follow the [manual recovery USB commands](iso.md#keep-an-older-snapshot-without-the-tool); that path still needs a VM check. |
| btrfs with disk encryption | As above, after typing the disk passphrase at the first prompt. Keeping a snapshot on an encrypted install has not been checked in a VM yet. |
| ext4 | There are no snapshots. If a new kernel is the problem, choose the `linux-lts` entry in the boot menu. Otherwise reinstall the previous package from the cache: `sudo pacman -U /var/cache/pacman/pkg/<file of the previous version>`. |
| Any, desktop does not start | Ctrl+Alt+F3 opens a text console; log in there and run `sudo pacman -Syu` again once a fix is announced. |

After `emaki-rollback`, only package versions that went backwards are held:
packages added after the snapshot are not held. An update or local install that
would install exactly a held version stops before installing anything (pacman).
The message names the package and prints commands to continue. A full upgrade
that includes a held version stops as a whole. To upgrade the other packages,
run `sudo pacman -Syu --ignore NAME`, replacing `NAME` with the held package name;
separate multiple names with commas. Other versions remain available; a newer
version releases the hold automatically during the next transaction, without a
background timer.

To allow a held version again, run `sudo emaki-rollback release NAME`, replacing
`NAME` with the package name printed in the refusal. To release every hold, run
`sudo emaki-rollback release --all`; this also resets an unreadable holds file.
Restart into the restored system before releasing holds. If the transaction's
exact versions cannot be checked, it is refused with a release command. If the
holds file cannot be read or the protection cannot start because its interpreter
is broken (Python), the transaction continues so the system can be repaired.
If hold preparation fails, the rollback continues without holds and prints a
warning. The manual recovery-USB path does not install this protection.

When a release of Emaki itself is bad, Emaki withdraws it from the mirror, so that machines that
have not updated yet do not receive it. A machine that already installed it keeps it: pacman
never goes back to an older version by itself. Go back with a snapshot, as above; the next
update then brings the fixed release.

Quickshell must be rebuilt for every Qt release, including patch releases. Qt patch updates
within the supported minor series remain allowed by `sudo pacman -Syu`; this avoids holding
back other updates, including security fixes, but a mismatched shell can crash until the
matching rebuild arrives. The dependency watcher alerts the release engineer when Arch's
stable or testing Qt version differs from the version used to build the published shell.

A new Qt minor series is blocked by the dependency bounds. In that case,
`sudo pacman -Syu` stops with a message like
`breaks dependency 'qt6-base<6.12' required by quickshell-emaki` and changes nothing. Your system
keeps its installed packages; wait for the rebuilt Emaki release and update then.

`emaki-qt-check` also compares the shell's exact build version with both installed Qt
packages. Pacman runs it after Qt or shell updates, and the panel runs it before loading
Quickshell. A mismatch prints recovery instructions and prevents a repeated panel crash;
the text recovery terminal remains available. Keep a working session open and install the
matching Emaki rebuild with a full update. Do not downgrade individual Qt packages.
The package stores `/usr/share/quickshell-emaki/qt-build-version`; older packages use their
installed `emaki-quickshell-qt-build` metadata. Package-release and epoch changes alone do
not change the upstream Qt ABI version being compared.

### When the Qt alarm fires (release engineer)

1. Check the alarm for both Emaki channels. Before Arch's stable move, rehearse in a
   disposable Arch environment with both testing repositories enabled and a full upgrade.
   Wait for the required Qt components to carry the same upstream `x.y.z` version.
   Keep this rehearsal package offline; it is not a publishable release bundle.
2. Set Quickshell's `_qtver` and every dependency lower bound to that version (for example
   `>=6.11.3`): two Qt bounds in the active recipe, four after Qt 6.12 activation.
   Retain the minor upper bounds, increment `pkgrel`, and update the release marker's exact
   Quickshell pin and increment the marker's `pkgrel`. The build records its actual Qt version as
   `emaki-quickshell-qt-build=x.y.z` in package metadata and refuses a different build version.
   Prepare and review this change before the stable move. For Qt 6.12, use
   `packaging/activate-qt612.py`; it assigns the next active package release and increments
   the marker release instead of reserving release numbers that a patch rebuild could
   consume. Re-running activation does not increment either release again.
3. After all required dependencies reach Arch stable, build the reviewed release commit
   in a fresh, fully upgraded stable build VM that never had testing repositories enabled.
   Run the packaging and upgrade checks and check greeter, shell and lock startup on the
   exact release packages. Publish the complete rebuilt set to Emaki `testing` through the
   normal release approval process. See `docs/updates-runbook.md`, “Qt 6.12 day zero”, for
   package selection, source records, signing and acceptance requirements.
4. After the required VM and hardware acceptance, promote the same tested Emaki snapshot
   through the normal release approval process. Rerun `packaging/arch-watch` to confirm
   each channel's recorded build version matches Arch and refresh the watch's source pin.


## Disk upkeep

Weekly cache cleanup keeps the newest three package versions [paccache].
The first migration enables cleanup on older installations unless the timer is masked.
Later updates preserve local disables; the former vendor timer link is carried over unless masked.
Disable cleanup with `sudo systemctl disable --now paccache.timer`.

Btrfs snapshot cleanup uses ranges so it can thin snapshots when free space is low. Existing
installer defaults are migrated once; locally changed limits, other configurations and
symlinked configuration files are preserved. Cleanup does not guarantee free space and never
removes the separately retained roots made by `emaki-rollback`.

## Arch mirrors

Online installation ranks recent HTTPS mirrors by measured speed, retaining the USB's list
if probing fails. Managed lists refresh weekly [reflector].
A local edit stops automatic refresh; unknown older offline lists stay unchanged.
To refresh Arch mirrors explicitly, run:

```
sudo reflector --protocol https --latest 10 --sort rate --connection-timeout 3 --download-timeout 3 --save /etc/pacman.d/mirrorlist
sudo pacman -Syu
```

This replaces the machine's Arch mirror list. It does not change the Emaki channel.

Package updates preserve edited installer settings and apply new service presets once.
Disable the mirror timer with `sudo systemctl disable --now emaki-refresh-mirrors.timer`.
