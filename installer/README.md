# Emaki installer core

This package is for the Emaki live ISO. The root worker implements the frozen
v1 NDJSON contract at `/run/emaki-installer/sock`; the CLI uses that same socket.
Importing `emaki_installer` or running its unit tests does not import archinstall
or inspect/write a block device. The daemon checks **archinstall 4.5** before
opening its socket. Arch's package dependency is pinned to **4.5-1**.

Erase supports btrfs and ext4. Manual supports existing GPT partitions, explicit
format flags, ESP reuse, and btrfs subvolume assignments. Alongside returns
`unsupported_mode`; no NTFS resize path exists in this release. No encryption,
BIOS, swapfile, or hibernation is configured.

## Running the tests

From the repository root, with Python 3.14:

```sh
PYTHONPATH=installer python -m unittest discover -s installer/tests -v
python -m compileall -q installer/emaki_installer installer/tests
bash -n packaging/emaki-installer/PKGBUILD
```

The default suite includes the production framing/replay loop over in-memory
streams. Six additional real Unix-socket tests are opt-in because some
sandboxes block socket binds and cross-thread socket wakeups. Run these in the
build VM as an ordinary user, without disks or root:

```sh
EMAKI_TEST_UNIX_SOCKET=1 PYTHONPATH=installer python -m unittest discover -s installer/tests -v
python -m pyflakes installer/emaki_installer installer/tests
```

The lsblk fixtures are representative captured-output shapes: blank virtio,
Windows with ESP on 4Kn NVMe, and macOS. They are synthetic; no host disk was
probed to produce them. Rootless tests validate plans, generated configuration,
failure cleanup, secret redaction, framing, tokens, safety checks and command
ordering. They do not substitute for installing and booting a VM.

## CLI and VM fixtures

`--probe` prints inventory. `--plan FILE` prints a reviewed plan without
confirming it. **Adding `--yes` authorizes disk writes.** `--plan FILE --yes`
streams the job; adding `--follow` is accepted as a no-op. `--follow` alone attaches to
the active job. A completed last job can be replayed with `--follow --job-id ID`;
`--since-seq N` resumes after a recorded sequence. Exit codes: 0 success/review,
2 request/job failure, 3 invalid plan, 4 busy. JSON event lines go to stdout;
neither the password nor the plan token is printed by the CLI.

Fixtures are installed at `/usr/share/emaki-installer/fixtures/`. They assume a
disposable **40 GiB virtio disk with serial `emaki-target`**, ID
`/dev/disk/by-id/virtio-emaki-target`, and use the
public VM-only account `emaki` / `emaki-vm-test-only`. If `--probe` reports a
different by-id name, replace `disk_id` and manual `partition_id` fields with exactly the
reported IDs. The fixture repository override is `http://10.0.2.2:8765/stable`;
the default fixtures keep `online_update` false for deterministic offline tests.

Before any disk mutation, the worker resolves the full offline package set in a
temporary pacman database and checks package/signature files. Failure reports
`offline_repo_incomplete` with the target untouched. Manual root partitions must
be at least 20 GiB; reused ESPs need at least 32 MiB of verified free space.

Run these commands as root **inside the live ISO VM**, each erase fixture on a
fresh disposable disk:

```sh
emaki-install-cli --probe
emaki-install-cli --plan /usr/share/emaki-installer/fixtures/plan-erase-btrfs.json
emaki-install-cli --plan /usr/share/emaki-installer/fixtures/plan-erase-btrfs.json --yes
# On a separate fresh VM disk:
emaki-install-cli --plan /usr/share/emaki-installer/fixtures/plan-erase-ext4.json --yes
```

For `plan-manual.json`, use GParted in the VM first: GPT, p1 1 GiB FAT32 with
the ESP flag, p2 the remainder. Leave both unmounted. Put a sentinel file and a
foreign `EFI/BOOT/BOOTX64.EFI` on p1 and record its SHA256 and partition GUIDs
before unmounting. The fixture preserves p1 and formats only p2 as btrfs:

```sh
emaki-install-cli --probe
emaki-install-cli --plan /usr/share/emaki-installer/fixtures/plan-manual.json
emaki-install-cli --plan /usr/share/emaki-installer/fixtures/plan-manual.json --yes
```

Also test a manual preserved `/home` row, explicit btrfs names, and an
unformatted btrfs root with the prescribed subvolumes. Choose a new account
login when installing over an existing root; `useradd` refuses an existing user.
The installer does not erase preexisting data outside formatted filesystems.

After `done`, shut down and boot without the ISO while retaining OVMF VARS.
Verify both GRUB kernel entries and the Emaki background, login, NetworkManager,
zram (no swapfile), fstab UUIDs and mount options. For btrfs verify that `/boot`
and `/var/lib/pacman` are in the root subvolume, and the root snapper config,
initial snapshot, timers and grub-btrfs entries exist. Test booting a snapshot;
permanent rollback remains a separate recovery tool, not `snapper rollback`.

Set `online_update` true in a fixture copy and test success with the repository
reachable, then failure with an unreachable mirror. The pacman failure is a
warning; independent final boot verification can still fail if an interrupted
transaction left an unusable boot configuration. Disconnect/reconnect the CLI
during installation, replay its job, and test cancel only at a phase boundary.

Test mode is enabled only by `emaki.test=1` on the live kernel command line.
It installs openssh and grim in settings, enables sshd, and copies the ISO's
`/etc/emaki-test/authorized_keys` to the created user's private `.ssh` directory.
It never installs passwordless sudo or unlocks root.

## Adapter and safety boundaries

The operation order is `FilesystemHandler.perform_filesystem_operations()` →
`Installer` → `mount_ordered_layout()` → `sanity_check(skip_ntp=True,
skip_wkd=True)` → `minimal_installation(mkinitcpio=False)` → offline packages →
our mkinitcpio/GRUB/fstab → account/settings → snapper → optional update → finish.
No archinstall bootloader, swap or snapshot helper is used; its context manager
is not relied on for cleanup.

The 4.5 default device handler performs eager btrfs probes with writable mounts,
and `MODIFY` recreates GPT partition entries. Emaki suppresses that import-time
device discovery, injects a job-owned device handler into FilesystemHandler,
uses sfdisk only for erase, and formats manual rows in place. The handler
verifies the written erase geometry before any mkfs. Mounts are flattened and
ordered by parent path, rather than grouped by partition. This is essential
when manual subvolumes belong to multiple filesystems.

`minimal_installation` is the upstream method. A narrow `PacmanConfig` adapter
prevents it editing the live pacman configuration or saving USB URLs in the
target. Its `Pacman.strap` is replaced by a logged, noninteractive
`pacstrap -C /etc/emaki-installer/pacman-offline.conf -K ...`. Both signed keys
are populated in the target. `mkinitcpio` is an explicit additional package so
the kernel dependency cannot select a different initramfs generator.

Only the current/last job is retained for resume during one daemon lifetime;
daemon restarts require a new plan. Cleanup unmounts only the target and its
private temporary btrfs mount, in reverse order; startup uses lazy unmount for
leftovers. After pacman-key, GnuPG helpers are stopped in the chroot. Normal
cleanup stops only processes rooted inside the target, syncs, settles udev,
and attempts each unmount up to ten times with 0.5–2 second backoff. Exhausted
retries log fuser and target-rooted processes and remain fatal. The service has a private mount
namespace and defers SIGTERM to a phase boundary.

Safety revalidation checks UEFI, the boot-medium ancestor, mount/holder/readonly
state, disk size, identity, partition UUIDs/geometry and kernel disk sequence.
It also checks exclusive-open availability just before filesystem operations.
Do not run external partitioning tools while a job is active. Manual mountpoints
that separate boot, system configuration, userspace or the pacman database are
rejected. Preserved btrfs must be verifiably single-device; explicit subvolume
names are top-level and must provide the five required root-related mounts.

GRUB retries failed NVRAM registration with `--no-nvram` and installs the removable
loader unless a foreign BOOTX64.EFI is present. Failed NVRAM registration with no
usable removable path is fatal. Existing ESP free space must be verified and at
least 32 MiB. Ext4 deliberately disables snapshot services after presets.
Scale uses an optional `scale_guess` config extension; niri requires an output
name, so the worker names connected DRM outputs, or warns and omits scale when
none is identifiable. Unknown console layouts fall back to `us`.

The live log is root-only. Log export requires a new file on actual mounted
removable/USB media below `/run/media/live`, rejects symlinks and never
overwrites a file. The target log copy is taken before unmount; the live log
also contains the final cleanup lines. Non-mutating archinstall import-time
diagnostics may appear only in the system journal. Passwords are never supplied
to archinstall, argv, environment or a file, and chpasswd input/output is private.

## Packaging

The PKGBUILD archives a local Git commit and has no source download. Commit the
installer files before building it; HEAD is resolved each run. To select a commit,
use `EMAKI_SOURCE_COMMIT=<sha> makepkg` instead of exporting `_emaki_commit`.
It explicitly lists every installed module, launcher, service, sysusers,
tmpfiles, fixture, documentation and license file. The installer-window block
installs the graphical installer window from `installer/ui/`.

Sources checked against tag 4.5:

- [Installer](https://raw.githubusercontent.com/archlinux/archinstall/4.5/archinstall/lib/installer.py)
- [Device models](https://raw.githubusercontent.com/archlinux/archinstall/4.5/archinstall/lib/models/device.py)
- [Filesystem handler](https://raw.githubusercontent.com/archlinux/archinstall/4.5/archinstall/lib/disk/filesystem.py)
- [Device handler](https://raw.githubusercontent.com/archlinux/archinstall/4.5/archinstall/lib/disk/device_handler.py)
- [pacstrap manual](https://man.archlinux.org/man/pacstrap.8.en)
- [mkinitcpio manual](https://man.archlinux.org/man/mkinitcpio.8.en)
