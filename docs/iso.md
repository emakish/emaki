# Building and testing the Emaki ISO

The output is `emaki-<version>-x86_64.iso`, volume label `EMAKI_<version>`, where `<version>`
is the content of `iso/VERSION` (read by `iso/build.sh` and `iso/profile/profiledef.sh`).
UEFI is supported. The releng BIOS loader is retained, but the installer requires
UEFI. English is the live system language. The offline target repository includes
official Arch NVIDIA open-module drivers for Turing and newer. The live desktop
uses nouveau/Mesa; older NVIDIA cards retain that same installation path.
`broadcom-wl` is not included.

## Source profile and current integration status

`iso/profile/` contains the vendored [archiso v91](https://raw.githubusercontent.com/archlinux/archiso/v91/archiso/mkarchiso)
releng profile with the Emaki overlay. If the vendoring manifest is absent,
`iso/build.sh` imports `/usr/share/archiso/configs/releng` from the build VM,
after checking that its installed archiso package is version 91.
`iso/import-releng.py` copies every upstream file and symlink into the staged
profile, preserves upstream bootmodes/image options, applies the overlay, and
records the original file hashes in `RELENG-SHA256SUMS`. It never substitutes a
handwritten approximation of the loaders or initramfs configuration.

To refresh the vendored profile, copy the releng profile out of the build VM,
then run on the host:

```bash
tests/vm/ssh.sh 'tar -C /usr/share/archiso/configs -cf - releng' > /tmp/emaki-releng-v91.tar
mkdir -p /tmp/emaki-releng-source
tar -C /tmp/emaki-releng-source -xf /tmp/emaki-releng-v91.tar
python3 iso/import-releng.py /tmp/emaki-releng-source/releng /tmp/emaki-profile-complete
cp -a /tmp/emaki-profile-complete/. iso/profile/
iso/check.sh
```

The importer requires a new destination. Use a new `/tmp` destination on repeat
imports. Importing does not install packages or start a VM. The runtime fallback
means an unavailable source download on the host need not block the build VM.

Install with `emaki-install-cli --plan FILE --yes`; it streams until done/error.
`--follow` alone reattaches to a running job; adding it to `--plan FILE --yes` is
also accepted. The installer requires archinstall 4.5 (packaged as 4.5-1).
Release builds require the `emaki-install` launcher/window; test builds warn and
continue with CLI installation when it is absent.

`copytoram=n` keeps the USB mounted so the installer and offline package repo remain available.
Archiso v91 supports `kernel_params_x86_64` (`mkarchiso`, lines 1599–1618);
Emaki keeps this parameter explicit in every loader entry and in the importer.

## Build in the disposable build VM

The following steps need the signed Emaki packages.
The VM needs archiso 91 and its dependencies, Python, OpenSSH tools, enough disk
space for the package cache, live root, squashfs and ISO (allow tens of GiB), and
access to the recorded Arch Archive snapshot. These commands assume the checkout is `~/emaki`
in the build VM, reached through the existing port-2222 harness.

Build inputs are recorded beside `iso/VERSION` in `iso/ARCH-SNAPSHOT`: the Arch
snapshot date, the audited archiso upstream version and the exact archinstall
package version. The build also requires the installed archiso package release
to match the snapshot, and records that full version in `BUILDINFO`.
To advance the date deliberately, record the reviewed versions in one command:

```sh
python3 iso/snapshot.py advance YYYY-MM-DD --archiso 91 --archinstall 4.5-1
```

Replace the date and installer version with the reviewed values, then run
`python3 tests/test-arch-snapshot.py` and validate a clean build in the disposable
VM. A different archiso upstream version requires a review of image staging
before this command accepts it. Archive mirrors can be slower than current Arch
mirrors; the build has no rolling mirror fallback. Installed systems keep their
normal Arch mirrors.

All eight names in `iso/emaki-packages.txt` must be in `.cache/repo/testing/emaki.db`
with their `.pkg.tar.zst` and `.sig` files. The build VM's **pacman verification
keyring** must already trust both Arch and Emaki signing keys. Provision Emaki's
public keyring plus trusted/revoked metadata from `packaging/emaki-keyring/`; no private
signing key belongs in the VM. The build deliberately fails on untrusted packages.

Rebuild `emaki-config` from the new committed checkout without exporting
`_emaki_commit`: the PKGBUILD resolves HEAD on each invocation and records
`EMAKI_COMMIT=<sha>` in `/usr/lib/emaki-release`. To select an older source
explicitly, use `EMAKI_SOURCE_COMMIT=<sha> makepkg`; inspect the installed release
file before building the ISO. Rebuild, sign and re-add `emaki-installer` after changing the installer.

Transfer the checkout and the signed repository to the build VM. Transfer only
the SSH **public** test key:

```bash
scp -P 2222 -i "$HOME/VMs/emaki-vm/id_vm" \
  -o UserKnownHostsFile="$HOME/VMs/emaki-vm/known_hosts" \
  "$HOME/VMs/emaki-vm/id_vm.pub" arch@127.0.0.1:emaki-test.pub
```

Inside the VM:

```bash
cd ~/emaki
sudo iso/build.sh --repo .cache/repo/testing \
  --work /var/tmp/emaki-iso-test --out /var/tmp/emaki-iso-test-out \
  --test-key "$HOME/emaki-test.pub"
```

`--test-key` implies test mode; `--test` is accepted only together with a public
key. Release builds omit both. Defaults are `.cache/iso-work-{test,release}` and
`.cache/iso-out-{test,release}` under the checkout. Never share a work directory
or output directory between modes. Old `emaki-*.iso*` outputs are removed before
building. The script marks its directories, refuses unowned nonempty work
ones, takes an exclusive lock, and reconstructs the resolver databases, generated
profile and archiso stages on every invocation. Downloaded packages can be reused;
packages outside the newly resolved transaction are removed before `repo-add`.

The dependency transaction includes releng minus `iwd`, `packages-extra.txt`
(including the desktop runtime dependencies), and `target-packages.txt`.
A new empty pacman DB avoids relying on packages installed in the builder.
The supplied `emaki.db` is copied and aliased as `emaki-offline.db` in private
build staging, so `[emaki-offline]` can precede core/extra during resolution.
The Emaki quickshell provider is explicitly selected. No source repository files
are changed. Package signatures are retained, or recovered from the repository's
embedded `PGPSIG` when pacman did not leave a detached file. Every package is
verified; a second empty database resolves the same transaction against **only**
the offline repository before image creation. `closure.txt` records exact package
filenames. There is no signature-check bypass.

Archiso v91 has no profile-root copy hook for arbitrary external ISO files.
Its `_build_iso_base` sets `isofs_dir=$work_dir/iso`; `_run_once` names stamps with
`$run_once_mode.$function`. The builder creates `iso._build_iso_image` and
`base._prepare_airootfs_image` to suppress mastering and packing the live root in
pass one. After that succeeds, it removes the mkinitcpio wrapper that emaki-config
wrote into the live `/etc/pacman.d/hooks` (pacstrap runs that directory for the
target; any other live hook fails the build), copies the repository into
`<work>/mk/iso/emaki/repo`, removes `iso._build_iso_image`,
`build._build_buildmode_iso` and `base._prepare_airootfs_image`, then invokes the
same `mkarchiso` command again. The other base stages remain cached for pass two,
with the recorded build date; pass two packs the live root and masters the image.
`-r` means **remove work**, not resume, and is intentionally absent. There is no
`customize_airootfs.sh` hook and no patch to the system's mkarchiso.

The final path, byte size and SHA-256 are printed. A `.iso.sha256` sidecar is
written using the image basename. The finished image is checked with xorriso
extraction (including the appended EFI boot image), mcopy and `unsquashfs -l`.
Every live entry must retain `copytoram=n`; release loaders must have no `emaki.test`
and airootfs must have neither `/home/live/.ssh` nor `/etc/emaki-test`. Test builds
require the test parameter and key directories. Failed verification removes the ISO. Copy the test
image out to a separate directory:

```bash
mkdir -p "$HOME/VMs/iso/test"
scp -P 2222 -i "$HOME/VMs/emaki-vm/id_vm" \
  -o UserKnownHostsFile="$HOME/VMs/emaki-vm/known_hosts" \
  'arch@127.0.0.1:/var/tmp/emaki-iso-test-out/emaki-<version>-x86_64.iso*' \
  "$HOME/VMs/iso/test/"
```

## Live and installer layout

mkarchiso v91 replaces profile `HookDir` settings with the live root's
`etc/pacman.d/hooks/` path before pacstrap runs. Build-host admin hooks are not
used. After pass one, `build.sh` asserts that the generated `iso.pacman.conf`
contains exactly this single `HookDir`; a changed setting stops the build.

| Path | Purpose |
|---|---|
| `/emaki/repo` on ISO | Signed, complete offline package repository outside squashfs |
| `/run/archiso/bootmnt/emaki/repo` | Repository location after live boot |
| `/etc/emaki-installer/pacman-offline.conf` | Validated input containing only `[emaki-offline]`; pacstrap uses a generated private configuration with an empty `HookDir` |
| `/etc/pacman.conf` in live root | Same offline-only repository configuration |
| `/etc/emaki-live/greetd.toml` | Normal Emaki greeter plus one-time initial `live` session |
| `/home/live/.config/emaki/niri-emaki.kdl` | Only `spawn-at-startup "emaki-install"` |
| `/home/live/.config/wpaperd/config.toml` | Installed `/usr/share/emaki/wallpaper/fallback.png` (centred) |
| `/etc/emaki-test/authorized_keys` | Test builds only; installer uses it for target SSH |

The `live` account has UID/GID 1000, no password, Bash, membership in `wheel`,
`video`, `input`, `emaki-install`, and ISO-only passwordless sudo. Root is locked.
The ISO boots graphical.target with greetd. `10-live.conf` resets greetd's command;
the later-sorting packaged `emaki.conf` is masked so it cannot undo that override.
Its DRM-hold dependencies are retained. `niri-emaki-session` sets up the user
systemd environment itself; no alternative compositor command is used.

NetworkManager uses wpa_supplicant and systemd-resolved. Inherited networkd/iwd,
root autologin, mirror-selection and SSH enablement are removed or masked.
The releng pacman-init service is kept, with `--populate emaki` after Arch keyring
initialization; the installer waits for this. Releng’s time-wait-sync enablement
is removed so offline startup cannot wait forever for NTP. Live snapshot timers are disabled.

The release contains the OpenSSH tools inherited from releng, but has no SSH
listener, live authorized key, `/etc/emaki-test`, or test kernel parameter.
`sshd.service` and its socket are masked. The separate `emaki-test-ssh.service`
requires both `emaki.test=1` and the test key file, then starts sshd directly with
public-key-only access for `live`. Test preparation appends the parameter to
Linux entries in every enabled loader. Test access to the installed system is set up
by the installer; live sudo and service masks are not copied to it.

## UEFI test cycle on the host

Use a new VM directory for each filesystem scenario. The scripts never reset an
existing disk. Missing disks are created as 40-GiB qcow2; `--no-cd` requires an
existing disk. OVMF VARS are copied once and persist across boots. SSH defaults
to port 2223 and `$HOME/VMs/emaki-vm/id_vm`. Pass `--dir`/`--ssh-port`, or set
`EMAKI_ISO_VM_DIR`/`EMAKI_ISO_SSH_PORT`. `--user` selects the installed account;
`EMAKI_ISO_SSH_KEY` overrides the key. Trust files are isolated by user and live
boot generation, so newly generated live host keys do not require disabling
SSH host-key checks. The default runner uses KVM, q35, six CPUs, 6 GiB and virgl
on `/dev/dri/renderD128`; `--outputs 2` enables a second output.

Start the live ISO. Include `--usb --offline` for the erase-btrfs USB/offline
acceptance run: USB storage exercises boot-medium protection, and restricted
user networking blocks internet while retaining the SSH host forward:

```bash
export EMAKI_ISO_VM_DIR="$HOME/VMs/iso-vm-btrfs"
mkdir -p "$EMAKI_ISO_VM_DIR"
setsid tests/vm/run-iso.sh --usb --offline --iso "$HOME/VMs/iso/test/emaki-<version>-x86_64.iso" \
  >"$EMAKI_ISO_VM_DIR/qemu.log" 2>&1 </dev/null &
tests/vm/iso-wait-ssh.sh
tests/vm/iso-shot.sh "$EMAKI_ISO_VM_DIR/live.png"
tests/vm/iso-install.sh erase-btrfs
```

Fixtures are read from `installer/fixtures/plan-FIXTURE.json`, falling back to
`tests/vm/fixtures/plan-FIXTURE.json`. Fallback fixtures target QEMU's stable
`/dev/disk/by-id/virtio-emaki-target`, created with serial `emaki-target`.
Both fixture sets use these stable IDs. `iso-install.sh` verifies a test ISO
and QEMU/KVM, then waits up to 300 seconds for the worker socket before submitting the plan. It saves the exact fixture privately
(mode 0600, including its disposable password), CLI stderr, NDJSON stream and
exit code under the VM's `runs/` directory. It exits with the CLI status, including
validation errors. Protect those run directories if changing the fixture password.

To test installed boot, stop QEMU and start the checker **before** starting the
no-CD VM. The checker sends Up repeatedly across firmware handoff so GRUB's
five-second countdown stops, records frames, selects the first entry with Home,
and presses Enter. It never starts or stops QEMU itself:

```bash
tests/vm/iso-stop.sh
tests/vm/iso-boot-check.sh erase-btrfs >"$EMAKI_ISO_VM_DIR/boot-check.log" 2>&1 &
check_pid=$!
setsid tests/vm/run-iso.sh --no-cd >"$EMAKI_ISO_VM_DIR/qemu-installed.log" 2>&1 </dev/null &
wait "$check_pid"
cat "$EMAKI_ISO_VM_DIR/boot-check.log"
```

`iso-stop.sh` asks the monitor to quit, then waits up to 180 seconds. A timeout
fails without killing QEMU or changing the disk. The runner holds a directory
lock for its lifetime to prevent overlapping launches.

Boot checks print `OK:`/`BAD:` for failed units, boot journal errors, package version and ownership,
NetworkManager connectivity, the Emaki repository, a real `pacman -Syu`, GRUB
entries (including the exact `Emaki, with Linux linux-lts` title), and btrfs snapshots.
The journal error allowlist contains only the exact QEMU `i8042: PNP: No PS/2
controller found.` probe failure; all other error lines fail acceptance and are printed.
The installed-system update check requires online networking; omit `--offline`
when restarting the installed system. The update requires the fixture's configured Emaki
repository to be reachable and signed. The installer fixtures currently use `/stable`, while
the fallback fixtures use `/testing`; serve the matching directories below the
repository root. Use an existing HTTP fixture server or start one
bound only to localhost:

```bash
python3 -m http.server 8765 --bind 127.0.0.1 --directory .cache/repo
```

The installed account's password comes from the saved fixture and travels only
through SSH stdin into `sudo -k -S`. No password is placed in command arguments.
The checker uses the existing `guest-login.py` with `niri-emaki-session`; that
helper discovers the greetd socket from guest greeter processes. This is a raw
protocol login, **not acceptance of the greeter UI**. Screenshots and user service
checks follow it. Review GRUB titles/background, greeter, wallpaper ring and shell
visually; run the separate installer UI acceptance as well.

`iso-shot.sh` captures the host display through the VM's local `vnc.sock`, exposed
by both `run-iso.sh` and `night/vm.sh`. When VNC is unavailable, or with `--monitor`,
it uses QEMU `screendump`; it never captures through the guest session. Virgl may
return “no surface”; the response is retained in the screenshot `.log`. Missing,
undecodable or black captures fail, and no desktop screenshot is substituted as
GRUB evidence. Decoded frames are reported as `SHOT`, with an unjudged `.json`
sidecar. Screenshots alone do not prove the menu text is correct.

Repeat with `erase-ext4` and a fresh directory. For `manual`, use GParted in the
live ISO first: create GPT, a FAT32 EFI System Partition as partition 1, and a root
partition as partition 2 on the disposable target. The installer's primary fixture currently
preserves the formatted ESP and formats partition 2 as btrfs; the fallback fixture
formats both and uses ext4. Inspect the selected fixture before running it.
A blank disk alone is not a manual-partition fixture. Rollback booting from a
snapshot and hardware testing remain separate acceptance steps.

## Encryption and alongside-Windows VM checks

Two host-queue checks cover the encryption step and installing next to Windows:
`tests/vm/iso-encrypt-check.sh` (described in `installer/README.md`, "Encryption and
hibernation") and `tests/vm/iso-alongside-check.sh` (`installer/README.md`, "Alongside
Windows"). The alongside check uses a synthetic Windows disk: its Microsoft
path holds a diagnostic EFI program, so it tests chainloading only, not Windows. The last
encrypted run on the full 0.1.2 test ISO ended rc=1.

Install alongside Windows is not offered in 0.5.0: the 0.5.0 installer has no experimental
options. On a 0.5.0 ISO the alongside check asks the worker for a plan only, sees the mode
refused, prints `NOT APPLICABLE` and exits 77 without touching the target disk; treat 77 as
"not run", never as a pass.

## Release build and gates

Inside the VM, after test acceptance:

```bash
cd ~/emaki
sudo iso/build.sh --repo .cache/repo/testing \
  --work /var/tmp/emaki-iso-release --out /var/tmp/emaki-iso-release-out
```

Copy the release ISO and checksum to `$HOME/VMs/iso/release/` using the test scp
command with the release paths. Boot it separately and verify that the installer
opens and SSH remains unavailable. Automated SSH acceptance applies only to the
test image.

`iso/check.sh` runs Bash syntax, ShellCheck when present, Python parsing, sorted
unique package lists, cached `pacman -Si` name checks, and offline regression
checks. It dry-parses a vendored profile when present; otherwise it explicitly
reports that gate unverified and exits nonzero. Transitive closure, package signatures, exact Arch
versions, service ordering, rendering, physical UEFI boot and installation still
need the build/VM gates. The host `make check` currently rejects the existing
fork-only `emaki-wallpaper` node with its installed niri-emaki binary; the live
session's one-line autostart KDL validates successfully with stock niri.

## Permanent snapshot rollback

On installed btrfs systems, `emaki-rollback snapshot NUMBER` (as root) prepares a
writable copy of a Snapper root snapshot and atomically exchanges it with `@`.
From a snapshot recovery boot, `emaki-rollback keep` selects the original booted
snapshot. The shell offers **Keep this state** through polkit with an
administrator password, or **Not now**. Changes in the temporary recovery overlay
are discarded; home files, logs, the package cache and snapshot history stay on
their shared subvolumes. Restart after promotion before changing the system again.
Ext4 has no snapshots: the tool explains this and changes nothing.

The old root remains at a printed, dated `@emaki-kept-…` name. After restarting,
`emaki-rollback list` lists kept roots and `emaki-rollback restore NAME` prepares
an undo in the same way, preserving the currently selected root too. After each
successful rollback, automatic cleanup keeps the two newest previous systems
[retained roots], plus pinned or mounted copies. Older mounted copies are retried
at the next rollback. The list marks each copy as newest two, pinned, pending
cleanup, or an incomplete preparation. Use `emaki-rollback pin NAME` to keep an
older copy and `emaki-rollback unpin NAME` to allow its cleanup. Snapper snapshots
and manually created copies are never part of this cleanup. Explicit deletion
with `emaki-rollback delete NAME --yes` requires a normal writable boot and also
preserves pins, mounted copies and the two newest retained roots.
Snapshots predating installation of the tool can lack it after restoration; use
recovery media and the printed top-level mount instructions in that case.

The tool requires the installer's UUID-based fstab and shared subvolumes, boot
files inside `@`, and GRUB entries targeting that same `@`. It preserves the
snapshot's matching kernels, initramfs, package database and GRUB configuration;
it clears the copied one-shot GRUB selection. It does not regenerate GRUB against
the recovery overlay or reinstall EFI loaders. New snapshots continue to update
the menu through grub-btrfsd after reboot. Unsupported layouts, populated nested
subvolumes, active package transactions and mismatching boot paths are refused.
Only systemd's empty `var/lib/machines` and `var/lib/portables` subvolumes are
allowed as nested roots. They are retained with the previous root; their empty
snapshot placeholders remain empty in the restored root.

[Snapper's rollback](https://snapper.io/manpages/snapper.html) sets the filesystem's
default subvolume. Emaki explicitly mounts `subvol=@` in fstab and GRUB, so changing
that default does not select the next root. The name exchange preserves the
installed boot contract, and keeps `@` present even if interrupted at commit.
A root-only record outside the roots is written before the copy is made and identifies
both subvolume IDs before the exchange. `emaki-rollback list` shows a copy that never
became `@` as an incomplete preparation; `emaki-rollback delete NAME --yes` removes it,
or a record whose copy was never made. Nothing removes them automatically. A staging
copy without a record is not touched; this version never leaves one, an earlier build could.

### Keep an older snapshot without the tool

Snapshots made before the rollback tool was installed, including 0.1.0 and 0.1.1,
do not contain **Keep this state**. If the updated system still starts and has the
tool, run `sudo snapper list`, then `sudo emaki-rollback snapshot NUMBER`, replacing
`NUMBER` with the chosen snapshot number, and restart.

Otherwise boot a current Emaki or Arch recovery USB and open a terminal. The
following manual path is for an unencrypted Emaki btrfs install with boot files
inside `@`; it has not yet been verified in a VM. Do not use it while the installed
system is running or hibernating. Start a root shell and identify the installed
btrfs partition and its UUID:

```sh
sudo -i
lsblk -f
```

In that shell, replace `YOUR-BTRFS-UUID` below with that UUID. Stop if a command
fails. The recovery USB must provide `mv --exchange` and `grub-editenv`.

```sh
mv --help | grep -- --exchange
command -v grub-editenv
recovery=/mnt/emaki-recovery
mkdir -p "$recovery"
mount -t btrfs -o rw,subvolid=5 /dev/disk/by-uuid/YOUR-BTRFS-UUID "$recovery"
btrfs subvolume list "$recovery"
ls "$recovery/@snapshots"
```

Replace `7` below with the chosen snapshot number. Read its mount table and boot
entries: `/` must use `subvol=@`, the shared mounts must use `@home`, `@log`, `@pkg`
and `@snapshots`, and normal boot entries must use `/@/boot/` and `rootflags=subvol=@`
(or `subvol=/@`) with the installed filesystem UUID. Stop if the layout differs.

```sh
number=7
source="$recovery/@snapshots/$number/snapshot"
btrfs property get -ts "$source" ro
cat "$source/etc/fstab"
cat "$source/boot/grub/grub.cfg"
cat "$recovery/@snapshots/$number/info.xml"
```

The snapshot must report `ro=true`. Prepare a writable copy, clear its old boot
selection, and remove a copied package lock only for a transaction-boundary
snapshot (`pre` or `post`):

```sh
kept="$recovery/@manual-kept-$(date -u +%Y%m%dT%H%M%SZ)"
test ! -e "$kept"
btrfs subvolume snapshot "$source" "$kept"
if test -e "$kept/var/lib/pacman/db.lck"; then
    grep -Eq '<type>(pre|post)</type>' "$recovery/@snapshots/$number/info.xml"
    rm -- "$kept/var/lib/pacman/db.lck"
fi
grub-editenv "$kept/boot/grub/grubenv" create
btrfs filesystem sync "$recovery"
mv --exchange --no-copy -T -- "$recovery/@" "$kept"
btrfs filesystem sync "$recovery"
printf 'Previous system: %s\n' "$kept"
umount "$recovery"
reboot
```

The exchange leaves the previous system under the printed `@manual-kept-…` name.
Write that name down before restarting. Home files and the shared subvolumes stay
unchanged. This manual copy is not managed or deleted by `emaki-rollback`.
To undo, boot the recovery USB again, mount the same filesystem as above, set
`kept` to the printed path, then run:

```sh
mv --exchange --no-copy -T -- "$recovery/@" "$kept"
btrfs filesystem sync "$recovery"
umount "$recovery"
reboot
```

The restored old system still lacks the rollback tool and its update protection;
wait for a fixed package release before updating it again.

`tests/vm/rollback-check.sh` is the host-queue acceptance job. With the session's
`VMDIR` set to a disposable directory below the VM work root, pass `--base DIR`,
`--iso IMAGE`, `--candidate EMAKI_COMMIT`, `--provenance JSON`, `--plan PLAN` and `--identity KEY`.
The candidate must be the full 40-character `EMAKI_COMMIT` recorded in the image's
`/usr/lib/emaki-release`; abbreviated commits and free-form build IDs are not accepted.
The base must be a separate installed candidate fixture; provenance binds the ISO,
disk, NVRAM and plan by SHA256 and records installed package versions. The checker
copies the fixture, verifies installed packages before damage, uses KVM and SSH
port 2251, then tests recovery, promotion, reboot, Snapper/menu updates, undo and
cleanup. It uploads no production payload replacements. All images and logs stay
below `VMDIR`; the base is read-only and the test VM is stopped in a `finally` block.
For encrypted Btrfs with hibernation, use a fresh installation instead of `--base`:

```sh
VMDIR="$PWD/.cache/evidence/u7d/rollback-runs" python3 tests/vm/rollback-check.py --install-encrypted-hibernation --work-root "$PWD/.cache/evidence/u7d" --iso "$ISO" --candidate "$EMAKI_COMMIT" --iso-sha256 "$ISO_SHA256" --package "emaki=$EMAKI_VERSION" --package "emaki-config=$CONFIG_VERSION" --package "emaki-desktop=$DESKTOP_VERSION" --identity "$VM_KEY"
```

Supply the digest, full 40-character `EMAKI_COMMIT` and package versions from the candidate's independently
verified build records. This mode builds its plan from `plan-erase-btrfs.json`, enables
account-password encryption and hibernation, selects minimal software and disables online
updates. Packages come from the ISO's offline repository. It assesses each boot's unlock,
menu and greeter frames and compares the real swap block device, offset and initramfs
configuration across rollback and undo. It does not hibernate the guest. Actual resume and
the recovery/authorization dialog appearance still need separate acceptance. Other
encrypted or hibernating fixtures remain `NOT TESTED` (exit 77).


The release gate (`tests/vm/release-gate.sh`) includes `rollback` and `boot-menu`
jobs. Missing evidence is `NOT TESTED` and blocks a successful script result. The final
summary names functional captures still requiring human review; a functional script pass
does not accept their appearance.
For rollback, supply `--candidate EMAKI_COMMIT --rollback-base DIR --rollback-provenance JSON
--rollback-plan PLAN --rollback-identity KEY`, using the full 40-character image commit.
The gate validates the candidate
manifest against the test ISO, disk, NVRAM and plan, then runs the rollback check;
an old PASS marker cannot substitute for that run.

For boot-menu acceptance, first capture the release ISO with
`tests/vm/check-boot-menu.py --iso IMAGE --sha256 DIGEST --out DIR`, then pass
`--boot-menu DIR` to the release gate. The capture command leaves the pictures
unjudged. Review each native size in `evidence.json`: 1280x800, 1366x768,
1920x1080, 2560x1600 and 3840x2160. At least one frame at each size must record
`judgment: "PASS"`, `reviewer`, and an ISO timestamp with timezone in `reviewed_at`.
A `CONFUSING` frame also requires `waiver` naming a nonempty file inside that
capture directory. Preserve each frame's recorded `sha256`; the gate checks it,
the decoded dimensions and the release ISO digest. Missing or unjudged sizes
remain `NOT TESTED`; broken or mismatched evidence fails. This boot-menu evidence
is additional to the separately signed release walk.

## Installed boot update acceptance

For automatic return after a failed updated system, also run the separate
[automatic update return procedure](#automatic-update-return-acceptance).

`emaki-config` installs `emaki-boot-refresh`, `95-emaki-boot-refresh.hook`,
`emaki-boot-complete.service` and `emaki-boot-refresh.service`. Refresh stages
the GRUB image, modules, artwork and menu, with normal entries pointing at live
`/boot` kernels/initramfs. Stock
mkinitcpio owns initramfs creation. `emaki-boot-refresh --check` reports disk
identity without changing boot files; the regular command retries a refusal.
Unchanged loader inputs regenerate only the menu. Kernel and initramfs updates do not
start another trial. An exhausted candidate keeps the old loader while its menu is
updated; changed loader inputs or a missing root manifest can offer a new candidate.
Refresh requires the installed writable ext4/btrfs root and the fstab ESP. Secure
Boot, BIOS and missing fallback loaders refuse before publication; foreign fallback
loaders and ESPs smaller than 256 MiB keep the existing loader. Plain roots require
an empty GRUB abstraction and GPT partition map. The root UUID's `blkid` device
set must match `grub-probe --target=device /boot` after resolving device aliases:
multiple btrfs members are valid, but an additional cloned device refuses refresh.
An encrypted root's LUKS UUID must identify exactly one device, including closed
containers. These storage checks run only on the refresh path; `--check` and
`--mark-good` do not scan all devices or reject confirmation because of a clone.
Live images, installation roots and chroots skip silently.

A real hook or manual refresh writes `/var/lib/emaki/boot-refresh-pending` before
discovery and resets its budget to three boot attempts. Fresh installation skips
create no marker, so the first installed boot does not stage an unsolicited trial.
After multi-user.target and boot completion, `emaki-boot-refresh.service` runs
`emaki-boot-refresh --retry` with that marker or a package lock. Without the marker,
it only recovers a recognized previous-boot refresh lock. Each boot attempt consumes its
budget before refresh work, including package-lock contention or interruption.
A held run lock defers retry with exit zero and leaves the marker and attempt
count unchanged. Success, a kept
loader or a permanent refusal clears pending retry; kept/refused outcomes are
recorded and reported once, without a failed unit for permanent refusal. Transient
command or I/O failures get at most three boot attempts, then stop. A subsequent
hook or manual refresh can start a new budget. Both retry and boot completion have
a five-minute start timeout and a 90-second stop timeout. A start timeout always
leaves the unit failed with `Result=timeout`, even if SIGTERM cleanup exits zero.
Retry exclusively creates pacman's `/var/lib/pacman/db.lck` for its refresh work,
writes and fsyncs `emaki-boot-refresh:<boot UUID>`, and removes its lock on normal
exit. After a crash, a later boot removes only a recognized previous-boot token
with same-file checks, even when the retry budget is exhausted or the marker has
already been cleared. Manual and hook refreshes run the same recovery before work.
Lock removal is synced before clearing pending retry. Foreign and
current-boot locks remain untouched. Rollback also recovers an exact previous-boot
refresh token in the live root before acquiring its own package lock. In snapshots,
it removes the copied token only from the prepared writable root. Refresh checks
staged modules against packaged modules again after `grub-mkimage`.

Each image carries matching modules in its memdisk and reads the current canonical
menu, including after root restoration. Refresh keeps both firmware paths and stages
a separate ESP candidate. Older loaders chainload it with at most two attempts,
counted in the FAT `EFI/Emaki/trial.env`, inside the ordinary default entry. An old
completed boot with a zero counter disarms the trial; the next refresh reports that
the old loader was kept. Missing or damaged counters also exhaust the trial.
After multi-user.target, the boot-complete
service certifies `emaki.generation` and promotes it to both firmware paths. Until
promotion encrypted boots ask twice, first in the old loader and then the candidate.
NVRAM is untouched. Cleanup retains only booted-good/newest GRUB generations, even
after a refusal. The ESP's `EFI/Emaki/boot-state.json` records those generations and
the fallback checksum; `boot-intent.json` records interrupted publication for recovery
on the next refresh or completed boot. Do not manually remove these files or retained
generations. Detailed update output is in `/var/log/emaki-boot-refresh.log`,
retaining at most the latest 1 MiB.

Run the complete [installed boot delivery matrix](../tests/vm/boot-delivery.md),
including 45 refreshes, kernel upgrade plus refusal, rename power cuts with
`fsck.fat -n`, both firmware paths and snapshot rollback. FAT rename durability
must be measured on killed guest copies; a successful ordinary rename test does
not establish power-loss safety. The following older walkthrough also covers the
visible installer and unlock sequence. Store every image under `.cache/evidence`.

The following acceptance run needs a disposable VM host with KVM and a usable
display. The release 0.2.0 ISO has no SSH service. Its real upgrade path starts
with a GUI installation from that release ISO; a 0.1.x test ISO is not a substitute.
Use a separate guest for encrypted and unencrypted installs. All `sudo` commands
below belong inside those disposable guests.

Build the candidate from a clean committed tree in the existing build VM and
create a read-only transfer ISO on the host:

```sh
packaging/build.sh --only emaki-config --out .cache/evidence/t5g-packages
xorriso -as mkisofs -o .cache/evidence/t5g-packages.iso .cache/evidence/t5g-packages
export EMAKI_ISO_VM_DIR=.cache/evidence/t5g-encrypted
mkdir -p "$EMAKI_ISO_VM_DIR"
tests/vm/run-iso.sh --iso ~/VMs/iso/release-0.2.0/emaki-0.2.0-x86_64.iso
```

Install 0.2.0 with encrypted btrfs, keep the disk password, boot the installed
system, and save the old unlock-screen capture. Record `pacman -Q emaki-config
grub`, `findmnt /`, `findmnt /efi`, `/etc/default/grub`, and hashes of both EFI
loaders and `/boot/grub/grub.cfg`. Shut down this QEMU before starting the same
disk with a 2560×1600 display preference and the package transfer CD:

```sh
qemu-system-x86_64 -machine q35 -enable-kvm -cpu host -smp 4 -m 6G \
  -drive if=pflash,format=raw,readonly=on,file=/usr/share/edk2/x64/OVMF_CODE.4m.fd \
  -drive if=pflash,format=raw,file=.cache/evidence/t5g-encrypted/OVMF_VARS.4m.fd \
  -drive file=.cache/evidence/t5g-encrypted/target.qcow2,if=none,id=target,format=qcow2,discard=unmap \
  -device virtio-blk-pci,drive=target,serial=emaki-target \
  -drive file=.cache/evidence/t5g-packages.iso,media=cdrom,readonly=on \
  -device VGA,xres=2560,yres=1600 -display gtk \
  -netdev user,id=net0 -device virtio-net-pci,netdev=net0 \
  -monitor unix:.cache/evidence/t5g-encrypted/mon.sock,server=on,wait=off \
  -serial file:.cache/evidence/t5g-encrypted/serial-firmware.log
```

Use the guest console to transfer and install the candidate:

```sh
sudo mkdir -p /mnt/packages
sudo mount -o ro /dev/sr0 /mnt/packages
sudo pacman -U /mnt/packages/emaki-config-*.pkg.tar.zst
sudo emaki-boot-refresh --check
sudo sha256sum /efi/EFI/Emaki/grubx64.efi /efi/EFI/BOOT/BOOTX64.EFI /boot/grub/grub.cfg
sudo cat /boot/emaki/*/manifest.json
```

Capture the complete package transaction output. It must show the refresh hook
and its success message. Do not run a manual refresh before the first reboot:
that would hide a broken package hook. The 0.5.0 packages must upgrade the installed
0.2.0 packages so ordinary `pacman -Syu` selects them. No publication is part of this check.

Reboot and capture the actual framebuffer:

```sh
python3 tests/vm/iso-monitor.py --dir .cache/evidence/t5g-encrypted \
  screendump .cache/evidence/t5g-encrypted/unlock-after.png -f png
```

The screenshot itself must be 2560×1600. An EDID preference alone does not prove
the active GOP mode; configure the firmware display setting if it chooses another
mode. Require the full-screen current artwork, four wrong attempts without
`grub rescue>`, then a successful unlock and normal installed-system boot. Check
both normal and LTS entries, and fallback initramfs entries. In the guest, record
`uname -r`, `/proc/cmdline`, `findmnt /`, `systemctl --failed`, and the manifest.
Select `/EFI/BOOT/BOOTX64.EFI` explicitly in firmware's Boot From File screen and
repeat unlock/boot to cover the Mac path. Repeat the entire update and reboot
scenario from an unencrypted ext4 0.2.0 installation under `.cache/evidence/t5g-plain`.

On the encrypted btrfs guest, create a root marker before a new snapshot and
change it afterwards. Record the snapshot number:

```sh
echo before-boot-update | sudo tee /etc/emaki-boot-marker
sudo snapper -c root create --print-number --description boot-update-acceptance
echo current-root | sudo tee /etc/emaki-boot-marker
```

Select that snapshot through the visible GRUB submenu on 20 boots, saving the
console capture, `/proc/cmdline`, `findmnt /`, `emaki-rollback status --json`, and
the marker each time. The marker must be `before-boot-update`, and the selected
snapshot must match the mounted recovery root. A return to the timed main menu
or ordinary root is a failure even if login works. This is the unresolved audit
85 acceptance; direct injected kernel commands do not test menu selection.
Also restore a snapshot from before the refresh and boot it, then restore a
snapshot containing a generation. Verify that each uses its own kernel/module
package set. The refresh must refuse to run from the temporary snapshot overlay.

Failure checks belong on VM clones: unmount the ESP and reinstall the candidate;
the hook must warn and leave both original loaders unchanged after remounting.
Repeat with insufficient free ESP space, an unavailable required GRUB module,
and a foreign fallback file. Every refusal must leave both loaders unchanged.
Compare hashes before and after; retain the guest images and transaction logs.

The rootless supplementary fixture exercises actual unlock and module loading
without an installed kernel. It is useful where KVM is unavailable, but does not
replace the release-to-package upgrade above:

```sh
python3 tests/vm/grub-unlock-check.py --boot-refresh \
  --output .cache/evidence/boot-refresh-unlock --video-size 2560x1600 \
  --native-only --wrong-attempts 4 --accel tcg
```

It requires GRUB build tools, mtools, sgdisk, QEMU, OVMF, clang and lld-link;
`--grub-root /path/to/extracted/usr` supports an unpacked GRUB package. Its
`RESULT.txt` distinguishes the synthetic disk menu from an installed-system boot.

## Automatic update return acceptance

Not run for this change. Run each case on a disposable installed btrfs VM twice:
once unencrypted, once encrypted, with a working baseline built from the tested
commit. Install the feature, reboot, and confirm a usable greeter before the
fixture transaction. Keep the same virtual disk, firmware variables and display
size across the failure and return; record the image hash, package versions,
source commit and every host-side power action. Do not substitute a new install
for recovery of the failed disk.

1. Save `findmnt / /.snapshots /efi`, `/proc/cmdline`, `snapper -c root list`,
   `systemctl show greetd -p ActiveState -p SubState -p MainPID`, and a host-frame
   greeter screenshot. Confirm that `systemctl list-dependencies multi-user.target`
   includes `emaki-update-boot.service`, and inspect
   `systemctl show emaki-update-boot.service -p LoadState -p ActiveState -p Result`
   plus `journalctl -b -u emaki-update-boot.service`. The package supplies the
   target dependency; `systemctl is-enabled` may print `disabled` because its
   link is under `/usr/lib/systemd/system`, rather than an administrator enablement.
2. Build the following unsigned local fixture inside the disposable VM. Verify
   `/etc/pam.d/greetd-greeter` does not exist first; if it does, create the fixture
   from a clean disk without an administrator override. This package deliberately
   denies the greeter's PAM session while leaving text-console access available.

   ```sh
   test ! -e /etc/pam.d/greetd-greeter
   mkdir -p /tmp/n1-fixture/etc/pam.d
   cat > /tmp/n1-fixture/.PKGINFO <<'PKG'
   pkgname = emaki-n1-greeter-failure
   pkgbase = emaki-n1-greeter-failure
   pkgver = 1-1
   pkgdesc = Disposable automatic recovery acceptance fixture
   builddate = 1791331200
   size = 100
   arch = any
   license = GPL-3.0-or-later
   PKG
   cat > /tmp/n1-fixture/etc/pam.d/greetd-greeter <<'PAM'
   auth required pam_permit.so
   account required pam_permit.so
   session required pam_deny.so
   PAM
   bsdtar -C /tmp/n1-fixture -caf /tmp/emaki-n1-greeter-failure-1-1-any.pkg.tar.zst .PKGINFO etc
   sudo pacman -U /tmp/emaki-n1-greeter-failure-1-1-any.pkg.tar.zst
   sudo cat /efi/EFI/Emaki/update.json
   sudo grub-editenv /efi/EFI/Emaki/update.env list
   ```

3. Before restarting, prove `update.json.snapshot` is this transaction's snap-pac
   pre snapshot (`info.xml` has type `pre` and the matching number), its cleanup
   algorithm is empty, `emaki_attempt=0`, and its captured PAM stack is healthy.
   Save `update.cfg`, the canonical GRUB menu and the raw 1024-byte environment.
4. Restart normally. On the encrypted VM, type the disk password and count its
   prompts. Capture greetd restarting/start-limit-hit from the text console or
   serial journal. Wait over five minutes from root mounting; prove the marker
   remains and `emaki_attempt=1`, `next_entry=emaki-auto-recovery`. Capture the
   visible failed start as a host frame. Do not repair PAM or select a snapshot.
5. Use the host monitor's `quit` command to terminate the VM, then start the same
   VM command/disk/firmware again. Do not use an in-guest graceful reboot for this
   power-cut check. Leave GRUB's default selected. Encrypted recovery must ask
   for its usual disk password once; the recovery entry adds no unlock prompt.
6. Prove `/proc/cmdline` names `rootflags=subvol=/@snapshots/NUMBER/snapshot`,
   `emaki.auto_return=TRANSACTION`, `emaki.snapshot_date=DATE` and `noresume`.
   `findmnt /` must show the snapshot's writable overlay; the original pre
   snapshot remains read-only. Prove a sentinel created in home before the update
   still has its original hash. After login capture the full recovery prompt,
   its matching date, the exact return explanation and **Keep this state**.
   `emaki-rollback status --json` must report that snapshot and automatic return.
   The environment must show attempt `2` with an empty `next_entry`.
7. Choose **Keep this state**, authenticate, restart and prove a normal writable
   `subvol=@`, healthy greeter, absent failure-fixture package and preserved home
   sentinel. The old system must remain in `emaki-rollback list`. A usable boot
   clears `update.json` and releases the pre snapshot's cleanup retention.
8. Perform a good package transaction from the terminal (`sudo pacman -Syu`, with
   a real package change in the controlled test repository). Record the new pre
   number. Restart, prove fifteen seconds of stable greeter or graphical-session
   evidence, then no `update.json`/recovery selector. Power-cycle again and prove
   it still boots `@`, with no automatic-return message.

Repeat the failed fixture from a clean baseline with host power cuts (a) after
its package transaction but before any restart and (b) immediately after the
first updated kernel/initramfs starts, before userspace. The next appropriate
boot must use the same recorded pre snapshot. Repeat without **Keep this state**:
a later restart must not automatically return a second time for that update.
Delete the recorded snapshot from the text console before a return and prove the
recovery attempt is consumed once, then the normal entry is attempted, without
an automatic cycle. Preserve the console evidence of the missing snapshot.

Also test two transactions before acceptance (same retained pre number, no
counter reset), hibernation before the first updated start and during the pending
acceptance window (resume keeps its boot ID and trial budget; the next cold boot
is still checked), and a cold start after a deliberately unavailable hibernation
image. Finally, perform a terminal package transaction on an ext4 VM: no update
marker or automatic recovery entry becomes armed. These VM/hardware checks are
additional to the rootless state, GRUB fixture and decision-mutation tests.
