<!-- Copyright (C) 2026 Artur Yakymenko -->
<!-- SPDX-License-Identifier: GPL-3.0-or-later -->

# Installed boot delivery acceptance

These scripts deliberately mutate disposable QEMU guests. Never run them on an owner's
machine. Start from **separate copies of actual 0.2.0 installations**, encrypted btrfs and
plain ext4. Record the source disk SHA-256, candidate package SHA-256, QEMU command, OVMF
code/variable-store SHA-256, and host tool versions in each evidence directory. Copy the
guest helper and candidate packages into the guest before starting. Run guest commands as
root; the helper refuses other virtual environments and non-root execution. Keep the
worktree checkout and evidence outside the root snapshots that will be rolled back.

Host disks, overlays, raw conversions, screenshots and logs belong below the worktree's
`.cache/evidence/boot-delivery/`, never `/tmp`. Allocate enough host disk for a sparse raw
conversion and an ESP copy per crash trial. Use explicit qcow2 copies/overlays, not QEMU
`snapshot=on` (which can allocate under `/var/tmp`). Keep original images untouched.

## Candidate and ordinary boot

Inside each guest, substitute the checkout and package paths once:

```sh
TEST=/path/to/worktree/tests/vm/boot-delivery-guest.py
EVIDENCE=/path/to/worktree/.cache/evidence/boot-delivery/btrfs
python3 -B "$TEST" --disposable-vm --out "$EVIDENCE" install /path/to/candidate.pkg.tar.zst
systemctl reboot
# Reconnect after multi-user/graphical target and the boot-complete unit have finished.
python3 -B "$TEST" --disposable-vm --out "$EVIDENCE" collect
```

`collect` fails unless the running release exists under `/usr/lib/modules`, the kernel
command line contains exactly one generation tag, that generation is marked good, and the
fallback image matches its recorded checksum. Preserve `boot.json`, `esp.json` and
`commands.log`. Repeat using a distinct evidence directory for ext4.

At **2560×1600**, capture the entire encrypted unlock screen: initial, wrong passphrase,
empty input, Escape, successful unlock, and menu. Record actual framebuffer dimensions;
a requested QEMU mode is not proof. Judge full-screen coverage, readable instructions,
retry behavior and transition to the menu. Boot the Linux, Linux LTS, fallback initramfs
and btrfs snapshot entries individually; use `kernel` collection for snapshots, and
`collect` for normal current-root boots. Preserve each screenshot and verdict separately.
No unattended helper claims a visual pass.

The narrower rootless trial check exercises actual GRUB chainloading and writable
or read-only FAT counters under OVMF without Linux or an installed root:

```sh
python3 -B tests/vm/boot-trial-check.py --grub-root /path/to/extracted/grub \
  --mtools-root /path/to/extracted/mtools \
  --output "$PWD/.cache/evidence/boot-delivery/trial"
```

It requires counts `1, 2, 2` and ordinary recovery entries for a corrupt candidate,
then a valid candidate entry on its first trial, with `EFI/BOOT` recorded as source.
Missing, corrupt and truncated counters and a read-only ESP must reach the old entry.
A separately supplied disarmed menu must stop reading the counter and show no errors.
This does not establish Linux startup, boot-completion disarming, promotion, encrypted
root or visual acceptance.

For an installation that originally detected Windows, keep its Windows disk attached.
Save `os-prober` output and the generated generation `grub/grub.cfg`; require the same
Windows loader entry and actually chainload it. A synthetic Microsoft directory alone
is not proof that os-prober finds a Windows installation.

## Firmware that boots only the fallback path

Use separate original 0.2.0 guest copies for ext4 and encrypted btrfs. Install the
candidate package, then prepare this scenario before the first reboot:

```sh
python3 -B "$TEST" --disposable-vm --out "$EVIDENCE/fallback-only" fallback-prepare
# Power off. In firmware explicitly select EFI/BOOT/BOOTX64.EFI.
# Reconnect after boot completion, without running another refresh.
python3 -B "$TEST" --disposable-vm --out "$EVIDENCE/fallback-only" fallback-check
```

Configure a dedicated OVMF variable store to boot only the fallback file, and preserve
its configuration and a firmware screenshot. Do not infer the loaded EFI path from the
kernel generation tag. `fallback-only.json` records distinct Linux boot IDs, trial
environment and `emaki_trial_source`; the latter must name `EFI/BOOT` when promotion
succeeds. If the first boot reports pending, cold boot the same path once more and rerun
`fallback-check`. A second unpromoted boot fails. Keep both boot observations even if
the second succeeds. Do not change loader inputs between these boots; an unchanged
refresh must preserve the trial budget.

Refresh publishes `EFI/Emaki/loader-<generation>.efi` and a two-attempt menu entry while
keeping both firmware entry points intact. Boot completion promotes the proven image to
the primary and fallback paths. This scenario specifically checks that firmware selecting
the old fallback can reach and confirm the candidate within at most two cold boots.

## Bounded storage and refusal after a kernel update

After one successful boot:

```sh
python3 -B "$TEST" --disposable-vm --out "$EVIDENCE" bounded
python3 -B "$TEST" --disposable-vm --out "$EVIDENCE" refusal /path/to/newer-linux.pkg.tar.zst
systemctl reboot
python3 -B "$TEST" --disposable-vm --out "$EVIDENCE" collect
```

The first command performs 45 real refreshes, keeps the same booted-good generation,
keeps newest unchanged, checks exactly good/newest directories after each refresh, checks for leaked backup
sidecars and frozen kernel artifacts, and limits variation in the total file bytes to
less than 1 MiB. It saves all measurements, not merely the final size.

The refusal command requires a strictly newer `linux` package. It hides `normal.mod`,
removes `linux-lts` without cascading dependency removal, installs the new kernel, and
requires a refused refresh with a byte-identical ESP. Stock package hooks still maintain
the initramfs. The module is restored in `finally`; an interrupted test leaves its saved
copy in the evidence directory for recovery. Reboot collection requires the newly
installed kernel release, so booting the old kernel cannot satisfy this test. A dependency
conflict is a failed fixture: do not force removal. Also inspect the successful generation's
menu to confirm all Linux/initramfs operands point to live `/boot` paths.

## Publication crash matrix

Run the matrix for both the **first refresh from original 0.2.0** and a refresh after
a candidate was confirmed good. Determine each transaction's actual rename count on
one sacrificial copy, with candidate files installed but the refresh not yet performed:

```sh
python3 -B "$TEST" --disposable-vm --out "$EVIDENCE/trace" trace
cat "$EVIDENCE/trace/publication-renames.json"
```

Preparing the first-refresh baseline must suppress only that package installation's
boot-refresh hook inside the disposable guest, then restore the hook before tracing or
cutting; preserve those preparation commands. Merely installing the candidate normally
already runs its refresh hook and cannot represent the first publication. Never use the
post-trace disk as the cut baseline.

For **each recorded N**, and for **both `--phase enter` and `--phase exit`**, start from a
new disposable copy of the matching baseline and its OVMF variable store. Include the
intent publication and state windows identified by their target filenames in the trace;
do not carry forward a hard-coded rename count from an older implementation.
Launch QEMU with an explicit `-name`, QMP Unix socket,
absolute qcow2 path, and test-only SSH access. Record its PID. On the host:

```sh
python3 -B tests/vm/boot-delivery-powercut.py \
  --out "$PWD/.cache/evidence/boot-delivery/cut-1" \
  --disk "$PWD/.cache/evidence/boot-delivery/cut-1/guest.qcow2" \
  --qemu-pid 12345 --qmp "$PWD/.cache/evidence/boot-delivery/cut-1/qmp.sock" \
  --vm-name boot-delivery-cut-1 \
  --ssh '["ssh","-p","2222","root@127.0.0.1"]' \
  --guest-helper /path/to/worktree/tests/vm/boot-delivery-guest.py \
  --guest-out /path/to/worktree/.cache/evidence/boot-delivery/cut-1 --rename 1 --phase enter
```

Change all trial-specific values for the next N. The helper runs the real refresh under
`strace -e inject=rename:delay_enter=120s:when=N` (or `delay_exit` for a cut after the
rename executes). A Python wrapper observes parent
`os.replace`/`os.rename` immediately before the syscall; it does not replace the actual
rename. Only the publisher process is traced, excluding generator child renames. Before
tracing, `PYTHONDONTWRITEBYTECODE=1` and `python3 -B` disable cold-import cache writes,
so the syscall count matches the publication count even on the first run. The guest
helper also disables bytecode writes for direct invocation and inherited subprocesses.
Before SIGKILL, the host requires the selected marker, ptrace stop, and x86_64 syscall 82
(`rename`) in `/proc`. Unsupported strace/proc behavior, an unreachable N, or a timeout
fails without a claimed power-cut pass. Parent calls must continue using `rename`; a
future implementation using `renameat` requires updating this helper explicitly.
The host pins QEMU with a process descriptor and waits for the whole thread group
to exit before converting the image. A zombie leader alone is insufficient. Conversion
retains normal image locking; a remaining lock fails inspection rather than bypassing it.

The host extracts the ESP from a sparse raw conversion of the killed qcow2 image and
runs **`fsck.fat -n`** against that copy. It records the complete output and exit code.
Exit 1 records filesystem discrepancies; it is not a boot pass. Exit codes other than 0/1
fail inspection. Never repair the only crashed image before preserving its evidence.

Keep the crashed disk immutable. Make two separate copies plus separate variable stores:
use firmware's file picker/boot entry to launch `EFI/Emaki/grubx64.efi` on one and
`EFI/BOOT/BOOTX64.EFI` on the other. Collect kernel/module evidence and firmware-path
screenshots for each. Attempt the initial boot without fsck repair. If repair is needed,
record that failure, then repair only an additional copy and collect the diagnostic boot.
If a path is missing, corrupt, or cannot boot, record FAIL rather than selecting another
path silently. For each N/phase report FAT findings, primary boot, fallback boot, and release
matching modules. On each booted copy run recovery without manually editing ownership,
state, intent, or generation files:

```sh
python3 -B "$TEST" --disposable-vm --out "$EVIDENCE/cut-N-PHASE-PATH" recover
systemctl reboot
# Boot through the same firmware path, then reconnect after boot completion.
python3 -B "$TEST" --disposable-vm --out "$EVIDENCE/cut-N-PHASE-PATH" collect
```

`recover` preserves the pre-refresh inventory and boot-completion journal, requires three
consecutive successful refreshes, and checks that each clears `boot-intent.json`, leaves
a reachable newest generation and candidate image, and preserves a checksummed fallback.
The final reboot/collection proves promotion after recovery. A boot-completion unit may
already recover the intent before the helper starts; the journal and before inventory
distinguish that from recovery by the explicit refresh. A crash before intent publication
can require no recovery; still run the same checks. A permanent ownership refusal fails.

This helper cannot establish real power-loss behavior on Apple firmware;
VM evidence is a release prerequisite, not hardware acceptance.

## Corruption, snapshots and refusal matrix

From another already-successful copy, install a package with changed loader inputs
to leave a real pending candidate before preparing corruption:

```sh
python3 -B "$TEST" --disposable-vm --out "$EVIDENCE/corrupt" corrupt
# Cold boot EFI/BOOT/BOOTX64.EFI; repeat this boot/check pair three times.
python3 -B "$TEST" --disposable-vm --out "$EVIDENCE/corrupt" corrupt-check
```

This requires a pending generation, corrupts only `loader-<generation>.efi`, and preserves
the confirmed firmware loaders. Each cold boot must reach the old generation and its
installed kernel modules. The recorded persistent trial counters must be **1, 2, 2**;
the third boot proves the bad candidate is no longer retried. Record the visible return
to the old menu on the first two failures, as well as successful unattended old-system
boot after the timeout. A hang or a third retry fails even if a manually selected entry
boots. Never run refresh between these boots. `corrupt.json` requires unique boot IDs,
unchanged good/fallback and fallback firmware trial origin; preserve firmware screenshots
because the guest cannot establish the firmware configuration by itself.

Repeat in another copy using the vendor firmware path to check its bounded trial behavior;
the fallback-specific automated `corrupt-check` does not claim this additional path passes.

On encrypted btrfs, create named Snapper snapshots before candidate installation and after
a successful refresh (`snapper -c root create --print-number --description ...`). Preserve
the numbers outside the snapshotted root. For each snapshot use a separate disposable VM
copy, run `emaki-rollback snapshot NUMBER`, follow its requested reboot, then run `kernel`
with a fresh evidence directory. Record `emaki-rollback status --json`, `/proc/cmdline`,
`uname -r`, and `/usr/lib/modules`. Also boot each snapshot directly from the GRUB snapshot
submenu. Pre-candidate roots may lack generation tags or the boot-complete unit: use
`kernel`, not `collect`, and require the restored kernel's module directory. A restored
root's old kernel is expected; a current kernel with missing modules is a failure.

Prepare each expected refusal on a separate disposable copy, then run `negative`:
- Foreign `EFI/BOOT/BOOTX64.EFI`, with its pre-test checksum preserved.
- An actual 100 MB Windows ESP, containing Windows files and its existing boot loader.

The helper hashes `/boot`, the ESP and `/etc/default`, invokes the package-hook entry
point, and requires exit zero, one plain line and unchanged files. Preserve Windows
checksums explicitly. Repeat the 100 MB row with matching owned Emaki firmware files.

Secure Boot (verified SecureBoot=1), BIOS, and a full ESP are operational failure rows:
run the hook directly, require nonzero status with the log location, and compare the
same file inventory. Use a firmware-trusted initial system for Secure Boot; an image
that never reaches Linux cannot test its hook. Remove only the disposable full-ESP
filler before rebooting the existing menu. Test ISO build-chroot silent skip separately: capture
`emaki-boot-refresh --hook` during the package-install stage and require success with no
refusal warning. The VM helper intentionally does not claim that a running ISO guest
is the build chroot.

A run is complete only when every applicable row has command evidence and the requested
visual/firmware verdict. These scripts do not turn unexecuted checks into a PASS marker.


## Recovery and unchanged-input follow-up rows

For encrypted btrfs and plain ext4, retain a confirmed baseline and a pending candidate.
Repeat interrupted publication with a snapshot submenu rewrite, root rollback deleting
sidecars, a failed trial followed by rollback deleting its manifest, and a lost FAT
replacement name. Each row must boot both firmware paths, complete three later refreshes,
and then boot again with the installed kernel's module directory. Preserve FAT inspection
and the original crash image; the rootless suite cannot establish physical durability.

After confirming a loader, upgrade linux and linux-lts independently and reinstall GRUB
and emaki-config with identical loader inputs. Require unchanged good/newest, firmware
hashes and a current menu, with one encrypted unlock. Repeat after exhausting a candidate;
none of these unchanged-loader transactions may reset its counter. A package with changed
EFI modules, boot code or artwork must offer a fresh candidate.

With a pending candidate, deny GRUB writes to the counter and boot the old system.
Boot completion must disarm the zero-counter trial. The next refresh must return zero
with the old-loader sentence, and the following cold boot must have no counter error or
extra fallback delay. Repeat with missing, corrupt, short and oversized trial.env files.
Capture the full menu during a pending trial: its first ordinary entry stays selected,
with no additional Emaki entry. A readonly-GRUB fixture alone does not prove Linux can
persist the disarmed state; collect the boot-completion journal and resulting menu.
