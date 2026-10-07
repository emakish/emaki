# Emaki installer core

This package is for the Emaki live ISO. The root worker implements the frozen
v1 NDJSON contract at `/run/emaki-installer/sock`; the CLI uses that same socket.
Importing `emaki_installer` or running its unit tests does not import archinstall
or inspect/write a block device. The daemon checks **archinstall 4.5** before
opening its socket. Arch's package dependency is pinned to **4.5-1**.

During `copy_packages`, `progress.step` names the operation displayed below the bar:
`{"id":"copy-17","name":"initramfs","text":"Building the startup image (mkinitcpio: linux-lts: default).","detail":"linux-lts: default","state":"running"}`.
The ID identifies one running instance. A step transition is emitted immediately;
`step: null` ends it, and a phase state without a step clears it. The saved state
replays the current step after reconnect. Logs and elapsed time never advance it.
Pacman's padded hook counters name the current hook; nested image presets and
locale generation temporarily replace that line. Command exits clear the line.

The copy percentage counts work, not estimated seconds: package announcements
have 60 points, nine preparation checks per transaction have 10, the two hook
groups have 20, four image presets have 4, locale generation has 4, and the two
explicit keyring commands have 2. Checks and hooks advance when the next step or
successful command return proves completion. Each preset contributes half its
point when selected and half when its image succeeds. A repeated preset in the
second transaction is separate work. The value is monotonic and capped at 99
until the copy phase returns successfully; without a resolved package total the
bar remains indeterminate while the same running-step text is shown.

The installer offers two layouts: erase and manual. Erase supports btrfs and
ext4. Manual supports existing GPT partitions, explicit format flags, ESP
reuse, and btrfs subvolume assignments. Both share optional LUKS2 root
encryption and a RAM-sized hibernation file. BIOS installation is unsupported.

## Alongside Windows

**Not offered in 0.3.0.** Installing alongside Windows returns only after a check
on a real Windows (a real shrink of its partition, BitLocker); see DECISIONS,
2026-10-04 "No experimental options". One switch controls it: `ALONGSIDE` in
`emaki_installer/constants.py`, `False`. With it off the inventory does not
probe Windows partitions and publishes no shrink offer, so the Disk step never
shows the mode or its notes; a plan with `"mode": "alongside"` is refused with
`unsupported_mode` before any disk is probed; the worker and backend refuse a
plan made while the mode was offered before any write. Only a source change
turns it on; there is no environment, protocol or `emaki.test=1` override.

The code and its unit tests stay in the tree; the alongside tests turn the
switch on explicitly (`alongside_on()` in `tests/support.py`). The rest of this
section describes the code with the switch on. The btrfs and ext4 queue cycles
below passed before the switch was turned off: both shrank, preserved
NTFS/GPT/EFI data, booted encrypted Emaki, resumed the same kernel session and
chainloaded the diagnostic Windows EFI target. These are synthetic fixtures;
real Windows and hardware acceptance remain in the checklist below.

With the switch on, the Disk step offers "Install alongside Windows" only when the worker reports
a shrinkable Windows system volume. Read-only probes require an unambiguous
largest NTFS candidate, a readable `Windows/System32/config/SYSTEM`, exactly
one FAT32 ESP with at least 32 MiB free and the Microsoft EFI loader, and
matching GPT geometry and identity. BitLocker, dirty/check-needed NTFS,
hibernated/Fast Startup Windows, mounted/held/read-only devices, ambiguous
layouts, missing evidence and insufficient space are refused. The partition
start and size must be aligned to 1 MiB. A larger refused NTFS candidate never
causes a smaller data volume to be offered.

`ntfsresize --info --no-action` checks the clean volume and minimum size.
A separate `ntfs-3g -o ro,norecover,nodev,nosuid,noexec,show_sys_files` mount
inspects the system volume's `hiberfil.sys`. An absent file or a complete zero
4096-byte header passes this check; nonzero, truncated or unreadable headers
refuse. Probes never repair NTFS, clear its dirty flag or remove hibernation.

Plans use `partition_id` for Windows and `shrink_bytes` for **bytes freed for
Emaki**. The worker reserves the NTFS minimum plus 2 GiB for Windows and at
least 32 GiB for Emaki. Root must also fit the existing 20 GiB allowance, RAM
when hibernation is selected, and the LUKS2 header when encrypted. The slider
starts at half the available space, bounded by those limits. Since the disk
step precedes the encryption choice, its hibernation reservation also allows
16 MiB for a possible LUKS2 header. The review names both resulting sizes,
preservation behavior, encryption and hibernation, and requires the backup
acknowledgement before confirmation.

Immediately before writes, the worker repeats the probe after package
preflight and compares disk/partition identity, geometry, NTFS bounds, ESP
free space and loader hashes with the review. The transaction then:

1. Verifies GPT metadata and dry-runs the exact NTFS size without force.
2. Shrinks NTFS, requiring the tool's successful completion marker.
3. Changes only the Windows partition end, retaining its start, unique GUID,
   type, name and attributes, then refreshes the kernel partition table.
4. Verifies kernel partition size, NTFS primary and backup boot records,
   cluster-rounded filesystem size, bounded MFT/MFT mirror locations, and
   readable system/allocation metadata through a read-only non-recovering
   mount. It also checks every preserved GPT entry.
5. Creates root exactly in the freed extent, verifies the partition table,
   and formats only that new root, optionally through LUKS2.

**Windows is expected to run chkdsk on its first boot after resizing. Let it
finish.** The deliberate dirty flag remains set. A second clean-volume
`ntfsresize --info` is inappropriate at this point: upstream's
[ntfsresize implementation](https://github.com/tuxera/ntfs-3g/blob/edge/ntfsprogs/ntfsresize.c)
sets that flag during resize and rounds the requested size to whole clusters
while retaining the backup boot sector. The post-check establishes geometry
and readable metadata, not Windows consistency or every file's integrity.
Failure stops before root creation/formatting and directs the user to Windows
and the installation log. There is no automatic rollback of a partial resize.

Only alongside installs add os-prober/ntfs-3g and enable os-prober in GRUB.
The generated menu must contain Windows Boot Manager; Emaki stays the default.
Existing Microsoft and foreign fallback EFI files must retain their hashes.
The ESP, MSR and other partitions are preserved; encryption covers Emaki root.
With encrypted root, GRUB asks for the Emaki disk password before showing its
menu, including its Windows choice. A separate Windows option in the firmware
boot menu can start Windows directly; verify that option on the real laptop.

The self-contained queue job is `tests/vm/iso-alongside-check.sh btrfs RUN`
or `tests/vm/iso-alongside-check.sh ext4 RUN`, with
`VMDIR="$HOME/VMs/n2-along"`. It uses one 6 GiB KVM guest, SSH port 2241,
and the ISO named by `EMAKI_CHECK_ISO` with Minimal software. It creates a
96 GiB disposable disk with an ESP, MSR and NTFS data, exercises refusals,
installs, verifies data/GPT/EFI preservation, boots encrypted Emaki,
checks RAM-sized swap and kernel resume, and selects the Windows GRUB entry.
Its Microsoft path contains a diagnostic EFI program with a serial marker,
not Windows. This tests chainloading only. All files and evidence stay under
the dedicated VM directory, and the guest is stopped on success or failure.
Before it touches the target disk, the job asks the ISO's worker for a plan
only; when the worker refuses the mode itself (`unsupported_mode`, as every
0.3.0 image does), it prints `NOT APPLICABLE`, writes `NOT-APPLICABLE` into the
run directory and exits 77: neither a pass nor a failure.

### First real Windows laptop checklist

1. Back up Windows files and make Windows recovery media. Record the disk's
   GPT layout, partition GUIDs, EFI files and hashes of a representative file
   set. Confirm UEFI boot and the intended disk; disconnect unrelated drives.
2. In Windows, verify BitLocker/device encryption status. An encrypted Windows
   volume must be refused; do not suspend protection as a substitute for a
   fully decrypted volume. Disable Fast Startup/hibernation, run `chkdsk /f`
   if Windows requests it, and finish a full shutdown.
3. Boot the new ISO. Confirm Windows appears only with verified shrink bounds.
   Check that encrypted, dirty, hibernated and insufficient-space states are
   refused before any write. Confirm the slider, RAM reservation, encryption
   choice and both final sizes in Review. Keep the laptop on external power.
4. Install alongside with a backed-up disk. Save the worker log. Verify GPT
   identities, Windows start/end, MSR/recovery/ESP preservation and EFI hashes.
5. Select Windows Boot Manager in GRUB. Let chkdsk finish without interruption;
   record its result. Log in, verify the files and applications, and run a
   Windows consistency check. Confirm Windows recovery still works.
6. Fully shut down Windows and boot Emaki. Verify both kernels, encryption
   unlock, desktop login, network, swap size and hibernate/resume. On btrfs,
   verify snapshots and snapshot boot. Then switch Windows/Emaki repeatedly.
7. Check Windows Update and a subsequent Emaki boot, firmware boot entries,
   and a final full Windows shutdown. Record hardware/firmware versions and
   all outcomes. Synthetic VM results do not replace these checks.

For steps 2 and 5, use an elevated Windows terminal. `manage-bde -status C:`
must report a fully decrypted volume; protection merely being suspended is
insufficient. Run `powercfg /hibernate off`, then `chkdsk C: /scan`. If repair
is required, run `chkdsk C: /f`, accept its scheduled boot-time check and let
Windows finish it before returning to the installer. Save work before
`shutdown /s /t 0`. Repeat `chkdsk C: /scan` after the first post-installation
Windows boot. See Microsoft's [BitLocker status](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/manage-bde-status),
[hibernation control](https://learn.microsoft.com/en-us/windows-hardware/design/device-experiences/powercfg-command-line-options#hibernate-or-h)
and [chkdsk](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/chkdsk)
documentation.

Save `emaki-install-cli --probe` before installation, export the installer log,
and record `lsblk --tree -b -o PATH,START,SIZE,PARTUUID,PARTTYPE,FSTYPE,MOUNTPOINTS`
before and after. In installed Emaki, compare `stat -c %s /swap/swapfile` with
the exact RAM reservation in Review, inspect `swapon --show --bytes` and
`cat /proc/cmdline`, and verify that the resume boot ID and an unsaved desktop
document survive hibernation. Compare the saved Windows file hashes after
chkdsk; do not treat a successful login alone as preservation evidence.

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

The graphical installer checks run without host service access. The render and
interaction runners create private XDG directories and use offscreen Qt:

```sh
python installer/ui/tests/check.py
python -m unittest discover -s installer/ui/tests -p 'test_*.py'
node installer/ui/tests/test-protocol.js
node installer/ui/tests/test-timezones.js
python installer/ui/tests/controller.py
python installer/ui/tests/interactions.py
python installer/ui/tests/render.py --repo-shell --output installer/ui/tests/artifacts/night
```

The render set includes every step, encryption choices and password states,
hibernation in every layout mode, a selected map region, active and empty
time zone searches, and both software choices, at the default 1024 × 700 size.
The `alongside*` screens replay a worker offer recorded with the alongside
switch on; a 0.3.0 worker never sends one.
Use `--width 960 --height 640` for the minimum window, and `--iso-fonts` to limit
the test to the ISO's Adwaita fonts. `controller.py --unix` additionally exercises
the real socket; it returns 77 when the sandbox cannot bind Unix sockets.

## Time zone, software and passwords

The order is Welcome → Keyboard → Network → Time zone → Disk → File system
(erase) → Encryption → You → Software → Review → Install → Done.

Time zone starts with the worker's `tz_guess`, or UTC. Choose a land region on
the map or search by city, country, region or IANA zone name. Search accepts
spaces in place of underscores. Tab reaches the search and list; Up/Down move
through results, Enter selects, and Escape returns from the list to search.
The map highlights the selected region, and its marker shows the representative
city. Map clicks clear the search and scroll the list to the selected zone.
For a small island or a border, use the list to confirm the city. The map source,
licence and reproducible generator are documented in [ui/assets/README.md](ui/assets/README.md).

Selecting a zone sends the additive v1 request:

```json
{"type":"set_timezone","id":"clock-1","timezone":"Europe/Berlin"}
```

The root worker replies with the usual `reply`, `id`, `for_id`, `seq` and `ok`,
plus `timezone`, `unix_ms` (current Unix time in milliseconds), `offset_seconds`
(including daylight saving) and `abbreviation`. Failures use the existing
`bad_config`, `bad_request`, `busy` or `command_failed` reply codes. The UI shows
an error and retry control and does not advance until the live clock succeeds.
Only the latest selection is applied after an outstanding request completes.
The time preview ticks each second and refreshes its offset every minute while
the page is open, including across daylight-saving changes.

The hello reply advertises `timezone_guess` support. `get_timezone_guess` returns
`tz_guess` and `pending` immediately from a shared background lookup. The lookup
starts once a route is available; disk probes never start or wait for it. The
window polls this cache until complete and applies a detected zone through
`set_timezone` before the time-zone page, unless the person chose a zone.

This operation is separate from disk installation and needs no installation
confirmation. It requires root, `/run/archiso/bootmnt`, and `IMAGE_ID=emaki` in
`/etc/os-release` on every request. The worker validates a bounded zone name
against the union of `/usr/share/zoneinfo/zone1970.tab` and `zone.tab`, plus UTC;
the resolved file must stay inside zoneinfo and contain TZif data. Only then
does it run `timedatectl set-timezone` with a fixed argument vector. This lets
systemd-timedated update the live clock outside the worker's private mount
namespace. Busy/stopping workers refuse the request; it invalidates any pending
installation token. The installed target still uses the plan's `timezone` field.

Software adds `software: "rich" | "minimal"` to the plan config. Omitted means
`"rich"`, for compatibility with older plans. Rich is preselected and adds
`emaki-apps` to the same signed offline pacstrap transaction as the base desktop.
Minimal uses only the desktop's Dolphin, Firefox and kitty application set.
The Review summary names the selection. Preflight resolves the selected set
before disk writes; missing Rich packages produce `offline_repo_incomplete`
with an explicit `emaki-apps` message. ISO assembly must include `emaki-apps`
and all its dependencies in the offline repository. The ext4 fixture exercises
Minimal; the btrfs and manual fixtures exercise Rich.

Every password field has a focusable eye control (Space or Enter toggles it).
Revealing changes only the field's display, and resets on leaving the step or
clearing passwords. The account password is retained only in memory between
You and Software, then discarded immediately after sending the plan. Back,
disconnect, window close and Hide discard it; returning to review after such
an action requires entering it again. Passwords never enter diagnostic output,
shared protocol state, command arguments or saved configuration.

## Encryption and hibernation

The Encryption step has no initial yes/no answer. Continue requires a choice.
After choosing encryption, the recommended option uses the account password;
the other option asks for a separate disk password and confirmation. Both
fields have the same eye controls as the account fields. Passwords stay in
private controller memory until the plan is sent, and are cleared on Back,
Hide, close, disconnect and submission. Changing an account password after
installation does not change the disk password. Startup uses the English (US)
keyboard layout, so disk passwords must use printable ASCII characters.

Plans must explicitly include `encryption: "none" | "account" | "separate"`.
The separate choice also needs `disk_password`; neither password appears in
the returned review or logs. `hibernation` is a boolean and defaults to false.
Its size comes from the worker's RAM inventory, never a client-supplied size.
RAM is the sum of installed SMBIOS memory devices, including firmware-reserved
memory; unavailable or invalid firmware sizes disable hibernation instead of
guessing. Root needs its existing 20 GiB minimum plus that amount and, when
encrypted, the 16 MiB LUKS2 header. The review names both choices and warns that
unencrypted hibernation writes memory, including passwords, to disk.

Only root is encrypted; the ESP remains plain at `/efi`. Manual encryption
requires formatting root and preserves the existing GPT and ESP. Any other
data partitions stay unencrypted, as stated in the UI and review. The common
`storage_layout` planner function applies these options after a layout builder;
the alongside builder (off in 0.3.0) calls it without duplicating storage policy.

The shipped Arch GRUB 2:2.16-1 includes `luks2`, `argon2`, `cryptodisk` and
`pbkdf2` modules, verified in the test ISO. Root uses LUKS2/Argon2id with a
64 MiB memory budget, one lane and a fixed 5 passes (`GRUB_ARGON2_PASSES`), not a time
calibration: GRUB's wait then depends on the machine, not on its load while installing.
Under KVM on 2026-10-05 GRUB took 1.0 s for 5 passes (about 0.17 s per pass on a quiet host);
reading the encrypted menu afterwards adds about 1.5 s before the picture changes.
Preflight requires the relevant GRUB modules before any disk mutation. Both
GRUB installs include the decryption modules. A random 64-byte secondary key
is stored in `/etc/cryptsetup-keys.d/emaki-root.key` (0600 in a 0700 directory)
and included in the initramfs, following the
[Arch encrypted-boot keyfile method](https://wiki.archlinux.org/title/Dm-crypt/Encrypting_an_entire_system#Avoiding_having_to_enter_the_passphrase_twice).
`/boot` is inside encrypted root and has mode 0700; the mkinitcpio configuration
sets umask 0077 for future rebuilds as well. GRUB asks once, then the busybox
`encrypt` hook uses the key. No passphrase reaches command arguments,
environment, archinstall, or a password file. Owned mappings close after all
target mounts are gone, including failed installations.

Before unlocking, the EFI image carries its own normal parser, script, blank terminal font
and committed Adwaita Sans pictures of the English instructions. `VARIANT` in
`emaki_installer/grub_screen.py` selects A, B or C in one line. GRUB's passphrase
prompt, progress and errors are hidden only after gfxterm and the initial picture
succeed; any graphics or displayed-picture failure switches to light console
instructions, cleared and reprinted on every attempt (GRUB's own diagnostics do not stay
on screen; any failure reads "Wrong password. Try again."). The password remains
inside cryptomount's input handling. One cryptomount
attempt per script iteration gives unlimited retries, including empty input and Escape,
and adds “Wrong password. Try again.” after failure. This still mislabels non-password
failures and can loop without input when the UUID is missing. GRUB 2.16 collapses
some backend failures into its password error; proper classification needs changes
inside GRUB. The checking artwork is prepared for all variants and sizes, but no stock
script hook can show it between password entry and key derivation. Actual video-mode
selection and centering on unknown sizes also remain unresolved. No GRUB source patch
is used. Before leaving the script after success, a polling sleep drains queued keys,
the picture is cleared, and an embedded 24 px visible font replaces the blank font as
the menu fallback. The optional installed menu background is not needed for this path.

`python3 tests/vm/grub-unlock-check.py --grub-root /path/to/unpacked/usr --output /tmp/unlock-check`
builds a disposable encrypted fixture and runs QEMU with OVMF. It captures the initial
screen, four completed wrong attempts, Escape, empty input and the menu after the right
password. A test-only serial mirror counts successive password prompts; pixel comparisons
check that the visible screens exactly match the generated artwork. It defaults to TCG;
`--accel kvm` opts into hardware acceleration. `--argon2-iterations 4` makes a faster fixture,
without changing installer encryption parameters. The secondary keyslot stays PBKDF2 with
1000 iterations. The fixture loads an actual encrypted ext4 `/boot/grub/grub.cfg`
with a minimal menu body and tests cleanup when its font and background are absent.
Full generated configuration, installed-root and initramfs checks remain the responsibility of
`iso-encrypt-check.sh` below. See the harness's `--help` for failure-injection cases.

Hibernation adds `/swap/swapfile`, exactly RAM-sized, mode 0600. Btrfs uses
the separate top-level `@swap` subvolume mounted at `/swap`, with NODATACOW;
root snapshots do not include it. `btrfs filesystem mkswapfile` creates the
file and `btrfs inspect-internal map-swapfile -r` validates it and supplies
the device-relative resume offset, as specified in the
[btrfs swapfile documentation](https://btrfs.readthedocs.io/en/latest/Swapfile.html).
Ext4 uses a preallocated swapfile and `filefrag -b4096` for its page offset.
GRUB gets the root filesystem UUID in `resume=` and the calculated offset in
`resume_offset=`. Busybox hooks run `block`, `encrypt` when needed, `resume`,
`filesystems`, then the existing snapshot overlay hooks. Zram has priority 100;
disk swap has priority 10. Installation does not activate swap in the live system.

The host-queue regression job is `tests/vm/iso-encrypt-check.sh btrfs RUN` or
`tests/vm/iso-encrypt-check.sh ext4 RUN`, with `VMDIR` set to the dedicated
test directory. It uses port 2231 and one 6 GiB KVM guest, uses the installer
packaged in the ISO named by `EMAKI_CHECK_ISO`, selects Minimal, installs,
reboots without the CD, unlocks GRUB through the monitor, logs in through greetd, checks
swap/key permissions/CanHibernate, then hibernates and resumes. A matching
boot ID and a retained file under `/run/user` distinguish resume from cold boot.
An optional final `manual` argument prepares GPT and a plain ESP first, then
tests manual assignment with ESP preservation. Btrfs also boots the generated
snapshot kernel/initramfs commands and verifies the writable recovery overlay.
For an already installed btrfs test disk, append `--snapshot-only` after the
layout argument to run that check independently, for example
`tests/vm/iso-encrypt-check.sh btrfs RUN manual --snapshot-only`.

## CLI and VM fixtures

`--probe` prints inventory. `--plan FILE` prints a reviewed plan without
confirming it. **Adding `--yes` authorizes disk writes.** `--plan FILE --yes`
streams the job; adding `--follow` is accepted as a no-op. `--follow` alone attaches to
the active job. A completed last job can be replayed with `--follow --job-id ID`;
`--since-seq N` resumes after a recorded sequence. Exit codes: 0 success/review,
2 request/job failure, 3 invalid plan, 4 busy. JSON event lines go to stdout;
neither the password nor the plan token is printed by the CLI.

Closed LUKS, BitLocker and FileVault 2 volumes appear in the selected disk's
`closed_encrypted` inventory list with a warning, device path, format and UUID.
Read each warning before confirming destruction. For Erase, explicitly add every
listed volume's `path`, `type` and `uuid` to the plan's `confirmed_encrypted` list;
for Manual, add only volumes on partitions the plan will format. For example:

```json
"confirmed_encrypted": [
  {"path": "/dev/vda2", "type": "crypto_LUKS", "uuid": "UUID-from-the-current-probe"}
]
```

This field records the same acknowledgement as typing `ERASE` in the window;
`--yes` alone does not supply it. Missing or changed confirmations produce
`encrypted_confirmation`. A volume without a readable UUID cannot be confirmed.
The worker checks the identities again after package verification, immediately
before disk writes. Open or held volumes and multi-device members remain refused.
Alongside preserves other encrypted partitions and requires no confirmation for them.

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
zram, the optional swapfile, fstab UUIDs and mount options. For btrfs verify that `/boot`
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
The window passes the active niri session's per-output scales as `output_scales`;
the target keeps these scales and compensates the shell gap for physical-pixel rounding.
Without a session no scales are invented. The legacy optional `scale_guess` config
extension still names connected DRM outputs, or warns and omits scale when none is identifiable. Unknown console layouts fall back to `us`.

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


## NVIDIA graphics

Detection reads cached PCI and DRM sysfs attributes, including when the live
medium uses basic display (`nomodeset`) and the card has no DRM node. Turing and
newer (PCI device ID at least `0x1e00`) select official Arch open modules for both
installed kernels. If the offline repository lacks either prebuilt module,
installation uses `nvidia-open-dkms`, DKMS and both sets of kernel headers.
Availability comes from the discovered repository, including alternate live-media
mounts. Online recovery selects the official prebuilt modules for both kernels.
The Review page names the selected packages.

Maxwell, Pascal, Volta, Kepler and older cards follow the same installation path
as 0.2, using nouveau or the integrated GPU. They do not add driver packages,
change plan hashes or refuse installation. Mixed old/open NVIDIA systems also
retain that path because installing `nvidia-utils` would blacklist nouveau.
All NVIDIA hardware acceptance remains **NOT TESTED**.

`emaki-nvidia` delivers graphics settings as package files. At each session start,
active display connectors determine automatic NVIDIA video/GLX library selection;
an Intel-driven hybrid display retains automatic selection. User profile settings
take precedence. The package does not prevent greetd from starting. The existing
graphical-session failure path provides the normal console recovery message.
The transaction hook diagnoses missing modules, and installation checks both
installed kernels before reporting completion. Inspect
`python3 -I /usr/lib/emaki/nvidia/runtime.py check` after repairing kernel, headers
or driver packages, then rebuild the initramfs normally. A failed DKMS hook is not
a transaction rollback. Custom kernels need their own headers and successful DKMS
build; the automatic check covers the installer kernels (`linux`, `linux-lts`).

The initramfs drop-in skips early NVIDIA loading when `emaki-resume` is in HOOKS,
preserving the hibernation path. This does not establish hardware resume acceptance.
The live image continues to use nouveau/Mesa. The “Emaki (basic display, text
console)” boot entry adds `nomodeset` for troubleshooting at a console.
No NVIDIA hardware has verified either live boot path.
