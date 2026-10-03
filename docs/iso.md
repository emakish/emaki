# Building and testing the Emaki ISO

The output is `emaki-0.1.0-x86_64.iso`, volume label `EMAKI_0.1.0`.
UEFI is supported. The releng BIOS loader is retained, but the installer requires
UEFI. English is the live system language. Proprietary NVIDIA drivers and
`broadcom-wl` are not included.

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
working Arch core/extra mirrors. These commands assume the checkout is `~/emaki`
in the build VM, reached through the existing port-2222 harness.

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
`$run_once_mode.$function`. The builder creates `iso._build_iso_image` to suppress
only mastering in pass one. After that succeeds, it copies the repository into
`<work>/mk/iso/emaki/repo`, removes `iso._build_iso_image` and
`build._build_buildmode_iso`, then invokes the same `mkarchiso` command again.
The base stages remain cached for pass two, with the recorded build date.
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
  'arch@127.0.0.1:/var/tmp/emaki-iso-test-out/emaki-0.1.0-x86_64.iso*' \
  "$HOME/VMs/iso/test/"
```

## Live and installer layout

| Path | Purpose |
|---|---|
| `/emaki/repo` on ISO | Signed, complete offline package repository outside squashfs |
| `/run/archiso/bootmnt/emaki/repo` | Repository location after live boot |
| `/etc/emaki-installer/pacman-offline.conf` | Pacstrap configuration containing only `[emaki-offline]` |
| `/etc/pacman.conf` in live root | Same offline-only repository configuration |
| `/etc/emaki-live/greetd.toml` | Normal Emaki greeter plus one-time initial `live` session |
| `/home/live/.config/emaki/niri-emaki.kdl` | Only `spawn-at-startup "emaki-install"` |
| `/home/live/.config/wpaperd/config.toml` | Installed `/usr/share/emaki/wallpaper/ring.png` |
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
setsid tests/vm/run-iso.sh --usb --offline --iso "$HOME/VMs/iso/test/emaki-0.1.0-x86_64.iso" \
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

`iso-shot.sh` uses guest grim in the active seat's Wayland session, with a monitor
`screendump` fallback. `--monitor` skips SSH for firmware/GRUB. Virgl may return
“no surface”; the full response is retained in the screenshot `.log`. Such a
missing GRUB capture is reported `BAD`, and no desktop screenshot is substituted
as GRUB evidence. Screenshots alone do not prove the menu text is correct.

Repeat with `erase-ext4` and a fresh directory. For `manual`, use GParted in the
live ISO first: create GPT, a FAT32 EFI System Partition as partition 1, and a root
partition as partition 2 on the disposable target. The installer's primary fixture currently
preserves the formatted ESP and formats partition 2 as btrfs; the fallback fixture
formats both and uses ext4. Inspect the selected fixture before running it.
A blank disk alone is not a manual-partition fixture. Rollback booting from a
snapshot and hardware testing remain separate acceptance steps.

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
