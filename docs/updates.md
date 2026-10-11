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
The desktop checks once a day and shows a panel indicator when updates are pending.

Before downloading packages, `emaki-update` checks the pending transaction's download
and installed sizes against free space on the system and package-cache filesystems.
On a btrfs root with snapper snapshots, it budgets the full installed size because
the pre-update snapshot retains replaced files; otherwise it uses positive net growth.
Subvolumes on the same filesystem share one combined budget. It adds
1 GiB of headroom for snapshot metadata and copy-on-write
changes. This is an estimate, not a reservation or a guarantee that every snapshot
will fit. A refusal reports the needed and available space; free some space and retry.
Repository metadata is downloaded into a temporary database for this check; a refusal
does not refresh the system package databases or change installed packages.
If the private probe cannot prepare the transaction, the command reports this and
hands over to the normal pacman update and its checks and questions.
Plain pacman retains its own `CheckSpace` check.

Once a package transaction reaches its pre-transaction hooks, Emaki blocks normal
suspend, hibernation and lid-triggered sleep until pacman exits, including after a
failure or Ctrl+C. This also covers plain `sudo pacman -Syu`, installs and removals.
Sleep is available again afterwards; the existing screen-lock sleep guard still runs.
Closing the lid while undocked and while the transaction holds the lid inhibitor
asks that guard to lock the session, without suspending the update. If both locks
fail, the session stays open and the next login shows a notice. Docked lid closures
do nothing.
The hook must already be installed: the update that first delivers it is not protected.
Installer and recovery chroots do not take a sleep inhibitor on their host.
If sleep protection cannot start, a notice is printed and the transaction continues.
This keeps package repair available when the system bus or login service is absent.

## The update window

Open **Emaki updates** from the launcher, run `emaki-update-manager`, or select the panel's
update indicator. **Check again** refreshes the list. The window groups repository updates by
source, with old and new versions and download sizes. It also lists installed foreign packages
that have newer versions in the AUR. AUR packages are not checked by Arch or Emaki; this window
does not build them. Update those separately with your AUR tool.

The window shows cached Arch news newer than the last recorded successful full update when
an item mentions manual intervention. Open its link and read the instructions before updating.
Warnings describe known conditions; the list does not certify that an update will work.
When a network request fails, cached information is marked as stale.

The update button requests administrator authorization and runs a full repository upgrade
through `emaki-update`, with the same refusal explanations and snapshot hooks as the terminal
command. Repository selection and individual package updates are not offered. The transaction
continues if the window closes; reopening it shows progress and the result. Save your work before
choosing **Restart now** after a kernel update, or choose **Later**.

Checking runs as your account with temporary package databases; it never refreshes the system's
`/var/lib/pacman/sync`. The installed package database is read without changing it. Applying
uses a fixed system service; it accepts no arbitrary command, package name or file path.

The window's appearance and wording are drafts awaiting owner approval.

After a desktop update, an on-screen notice asks you to save your work, sign out and sign in
again. The running panel keeps its own copy of its files until it restarts, so an update cannot
replace its helpers while it is running. Nothing restarts by itself. Restart the computer
after a kernel update.

## Program manager engine

`emaki-programs` also exposes a catalog and transaction engine; the existing windows keep their
current appearance. Catalogs contain application components from the platform's standard
AppStream files and enabled, verified Flathub remotes when Flatpak is installed. Metadata includes
name, summary, plain-text description, icons, categories, screenshot URLs and licence. Sizes are
integer bytes from the package backend, or JSON `null` when unavailable. Images are references;
the engine does not download them. Components missing from AppStream are not synthesized from
every system library. A partial source failure returns warnings with `partial: true`.

Installed metainfo uses the session language, then English, with one description language at
a time. Metadata input is capped at 64 MiB both on disk and after gzip expansion. XML and YAML
have depth 32, 500,000-node/event and 1 MiB scalar limits; catalogs have at most 50,000 components.
DTDs, entity declarations and YAML anchors/aliases are refused. YAML validation requires the
optional `python-yaml` package; its absence produces a parser-unavailable warning. Descriptions
are capped at 16 KiB of UTF-8, other strings at 2 KiB and lists at 64 entries. Oversized or unsafe
identifiers are rejected rather than shortened into different targets. Display strings, including
diagnostics and progress, omit Unicode format, bidi and control characters except newlines/tabs.
Local icons must resolve inside the metadata directory and are never accepted from remote catalogs.

```
emaki-programs catalog --json
emaki-programs search paint --source arch --category Graphics --json
emaki-programs search paint --include-aur --json
emaki-programs details KEY --json
emaki-programs plan install KEY --json
emaki-programs execute install KEY TOKEN --json
emaki-programs plan remove KEY --json
emaki-programs execute remove KEY TOKEN --json
emaki-programs plan update --json
emaki-programs execute update TOKEN --json
emaki-programs updates --json
```

Use the returned opaque `key`, never a display name. Read the entire plan before executing its
token. Plans are recomputed on execution. Native removals retain the existing exact-set preview
and system/Emaki/dependency refusals, including the privileged recheck. Updates are full updates
through the same backend as the update window, with its repository-first Flatpak sequence.
Repository installation also includes a full update; as with the update window, its final fresh
package list can change. It uses the existing space check and package hooks and a system-owned
worker, so a started repository installation survives a closed client. Its details are in the
system journal under `emaki-programs-install.service`.

Native and system Flatpak authentication headlines name the program first. The full helper
command and preview token remain in folded Details. The polkit display rule rejects
unsafe command text, but never grants authorization: valid calls still require `auth_admin`.
Helper argument validation and privileged preview checks remain authoritative.

Flatpak plans resolve dependencies and related extensions through libflatpak. Execution compares
the operation list, commits, current deployment and remote identity before deployment. New installs
use the default system installation through a fixed root helper and an authentication prompt,
including when the distribution supplies only a system Flathub remote. Its rows and plans expose
`managementScope: "system"` and `requiresAuthentication: true`. Existing user deployments can be
removed without root, with `managementScope: "user"` and `requiresAuthentication: false`; new user
installs are refused. Additional system installations remain read-only. A single row per app ref
lists its `installedCopies` (installation and origin), preferring an existing user deployment
until it is removed, then an installed system deployment, then the available default system
edition. Uninstalled user-remote editions are omitted. No remotes are added,
no unused-runtime cleanup is requested and personal data is
retained. Flatpak transactions have their own deployment behavior and are outside root snapshots.
AUR search is opt-in (at least two characters); results offer only build-in-a-terminal advice.
The old `list`, `preview` and `remove` commands used by Settings retain their protocol.

For integrations, `emaki-programs serve` reads one JSON object per line (maximum 64 KiB). Each
request supplies `version: 1`, optional scalar `id`, `method`, and an object `params`:

```json
{"version":1,"id":"lookup","method":"search","params":{"query":"paint","source":"arch","category":"Graphics","installed":false,"offset":0,"limit":100}}
```

Methods are `catalog`, `search`, `details`, `plan`, `execute` and `updates`. Search/catalog parameters
are `query`, `source` (`arch`, `flatpak`, `aur`, or empty), `category`, optional boolean `installed`,
`offset`, `limit` (1–500), `includeAur` and `refresh`. Details takes `key`; plan takes `action`
(`install`, `remove`, `update`) and `key` (empty for update); execute also requires `token`.
Updates takes no parameters and reports existing update status. Responses carry the same `id`
and `version`, `type: "result"`, and `ok`; progress messages use `type: "progress"`. Errors have a
machine-readable `code`; current explanatory messages marked `[PLACEHOLDER]` await text approval.
Malformed requests fail individually and do not end the stream. Transactions are sequential
within a stream. Separate repository clients share the existing privileged transaction lock.

Ordinary catalog discovery uses installed repository databases and cached Flatpak summaries.
`--refresh` refreshes configured Flathub summaries in the library's unprivileged cache and updates
user AppStream metadata; system AppStream remains cached. This can enumerate a newly configured
system remote before its application metadata has been cached.
It never refreshes the live repository
database as a side effect of browsing. Standard Flatpak library bookkeeping may initialize its
own installation state. After an external package operation, refresh or reopen the engine before
requesting details; execute always reloads state. Network failures are reported without replacing
successful sources with an empty success. Upstream metadata is data, not trusted interface markup.

On Arch, `appstream` and `archlinux-appstream-data` ship with the engine. Repository metadata and
application catalogs are refreshed by the normal full update path. A package with no AppStream
application component is still available through the ordinary terminal package manager.

## What is signed

Every Emaki package is signed and pacman refuses an unsigned or wrongly signed package. The
repository database is signed too. The packaged stable and testing server definitions require
its signature; custom server configurations retain their own signature policy. A
signature does not prove that a database is the newest one: someone who controlled the mirror
could serve an older, correctly signed database, and `pacman -Syu` would then simply find
nothing to update.

## Channels

Choose the update source in Settings, Updates & Recovery, Channel, or with
`sudo emaki-update-channel set stable --json` (or `set testing`) [update channel].
`testing` receives every release first; `stable` receives only releases that passed the
upgrade check. The command changes only `/var/lib/emaki/channel`, confirms the effective
server and removes the local copy of the `[emaki]` database, so the next ordinary update
downloads the chosen channel's database (the two channels' databases are not in time order,
and pacman keeps a newer copy from the other channel). The command refuses while another
package operation runs.

The chain is fixed: `/etc/pacman.d/emaki-mirrorlist` includes `/etc/emaki/channel`, which
includes `/var/lib/emaki/channel`, which names one packaged definition:

```
Include = /usr/share/emaki/mirrors/stable.conf
```

Releases 0.3.0 to 0.4.2 kept that line in `/etc/emaki/channel` itself. Installing or
upgrading the repository package moves it into `/var/lib/emaki/channel`, keeping stable or
testing; a choice edited by hand into `/etc/emaki/channel` later is moved the same way,
because it is the one pacman reads [channel selector]. The package keeps a valid choice
unchanged and repairs a missing or invalid selector or state to stable; a missing selector
during migration inherits a recognized older channel.
Package-owned server definitions receive address updates [emaki-mirrorlist]. Recognized older GitHub and Emaki server lines migrate automatically, preserving the
channel; custom server configurations stay untouched and Settings does not offer the switch
for them [emaki-mirrorlist]. `emaki-update-channel` reports the
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

On btrfs installations with automatic update recovery installed, package changes
are protected by their pre-update snapshot, including `pacman -Syu` in a terminal.
The first subsequent start must reach a running greeter or a graphical user
session within five minutes after mounting the system. If it does not, restart:
the next start selects the snapshot automatically, once. Encrypted disks still
need their usual disk password. After login, the recovery prompt explains the
return and offers **Keep this state** to make that snapshot the system again.
Home files are on a separate subvolume and are not rolled back.

Protection starts with the transaction **after** this feature is installed; a new
pre-transaction hook cannot protect the transaction that first installs it. The
automatic path still requires the encrypted and unencrypted VM checks described
in [the boot acceptance procedure](iso.md#automatic-update-return-acceptance).
Until those checks pass, it is not a hardware recovery guarantee. A failed
preparation never stops the package transaction: it continues without update
recovery protection, and the terminal output names the missing snapshot or boot
preparation step.
Several updates before an accepted restart retain the earliest snapshot and
do not reset the one-return limit. Hibernation resume does not accept an update.
On ext4 there are no snapshots: these hooks do nothing and automatic return is
unavailable. The manual paths below remain available.

| Install | What you can do |
|---|---|
| btrfs (the default) | Restart; in the boot menu open **Emaki snapshots** and pick the snapshot taken before the update. If that snapshot already contains `emaki-rollback` (it arrives with the release that moves a machine to `pkgs.emaki.sh`), the desktop offers **Keep this state**, which makes the snapshot the system for good. |
| btrfs, snapshot without the tool | Snapshots of systems that had not yet received that release (0.1.0, 0.1.1 and the first 0.1.2 build) can be booted but not kept from inside. If the updated system still starts, keep the older snapshot from there with `sudo emaki-rollback snapshot <number>` (`sudo snapper list` shows the numbers). If it does not start, follow the [manual recovery USB commands](iso.md#keep-an-older-snapshot-without-the-tool); that path still needs a VM check. |
| btrfs with disk encryption | As above, after typing the disk passphrase at the first prompt. Keeping a snapshot on an encrypted install has not been checked in a VM yet. |
| ext4 | There are no snapshots. If a new kernel is the problem, choose the `linux-lts` entry in the boot menu. Otherwise reinstall the previous package from the cache: `sudo pacman -U /var/cache/pacman/pkg/<file of the previous version>`. |
| Any, desktop does not start | Ctrl+Alt+F3 opens a text console; log in there and run `sudo pacman -Syu` again once a fix is announced. |

[PLACEHOLDER] After `emaki-rollback`, only package versions that went backwards are held:
packages added after the snapshot are not held. An update or local install that
would install exactly a held version stops before installing anything (pacman).
The message names the package and prints commands to continue. A plain
`pacman -Syu` that includes a held version stops as a whole. The update window and
`sudo emaki-update` refuse the remaining update as a whole and name the held
packages. Declared package dependencies do not establish that newer programs can
use an older held library. `archlinux-keyring` and `emaki-keyring` are never held,
and `emaki-update` updates available keyring versions in their own transaction
first. If the available versions cannot be checked, the update stops before any
package changes. Other versions remain available; a newer version releases the
hold automatically during the next transaction, without a background timer.

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
