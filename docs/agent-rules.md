<!-- Copyright (C) 2026 Artur Yakymenko -->
<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Working on an Emaki system

Generated from the system map by scripts/render-system-map; do not edit the packaged copy.
These instructions apply when working on an installed Emaki system.
Emaki is an Arch-based distribution built around the niri compositor, with its own
desktop shell, settings, installer and recovery tools.

## Read before changing the system

Read the installed system map at `/usr/share/emaki/system-map.md` for this release.
Find the affected component and read its Files, Zone, Change, Never and Rollback
entries before acting. If a path is not covered, establish its owner before writing.
A copy from another machine or release is not evidence of the current system.

## File ownership

- `package`: installed by an Emaki package; never edit it, an update replaces it.
- `emaki-settings`: changed only with `emaki settings`, which keeps a history.
- `person`: the person's own files, theirs to change; no Emaki update writes them.

To find the owner of a path, run `emaki map --json`.
Its zone names differ: `managed` is `emaki-settings` and `yours` is `person`.

Use `emaki settings` for Emaki settings; never edit the canonical settings store,
generated configuration or history directly. Use the map's named interface for
machine settings. Keep personal overrides separate and preserve existing content.
Never write the person's `~/.config/niri/config.kdl` to change Emaki settings.
Back up personal files separately before changing them with the person's permission.

## Ask for state

Start with `emaki state --json`, then use the relevant command below in the installed
session. These commands print JSON, including those without a `--json` option.
Read status and reason as well as the exit code. Unavailable state does not prove
a component is absent or broken. Do not run every command for an unrelated change.

| State command | Components |
| --- | --- |
| `emaki niri snapshot --json` | compositor-configuration, compositor-runtime |
| `emaki settings get appearance.wallpaper --json` | settings-wallpaper, wallpaper |
| `emaki settings list --json` | command-and-settings-core, settings-input-pages, settings-notifications |
| `emaki-boot-order --json` | alongside-test-boot-safety |
| `emaki-machine-settings get --json` | machine-settings |
| `emaki-programs catalog --json; emaki-programs list --json; emaki-programs preview PACKAGE --json; emaki-programs aur --json; emaki-programs popular --json; emaki-programs ratings APPSTREAM-ID... --json; emaki-programs reviews APPSTREAM-ID... --json` | installed-programs |
| `emaki-rollback status --json` | snapshot-recovery, update-restore |
| `emaki-session-update state` | desktop-update-signal |
| `emaki-settings-about status --json` | settings-local-report |
| `emaki-settings-apps status --json` | settings-startup-apps |
| `emaki-settings-network status --json` | settings-network-pages, settings-network-provider, settings-wireless-password |
| `emaki-settings-power status --json` | battery-charge-limits, settings-power |
| `emaki-settings-privacy status --json` | privacy-recovery-status, settings-privacy |
| `emaki-settings-storage status --json` | settings-storage |
| `emaki-settings-users status --json` | settings-users |
| `emaki-shell call capture status` | capture-media, capture-runtime, capture-tool |
| `emaki-shell call settings status` | launcher-settings, settings-controls, settings-displays, settings-system-pages |
| `emaki-shell call system status` | desktop-shell, settings-sound, wifi-recovery |
| `emaki-shell-trusted status --json; niri-emaki msg --json trusted-shell-identity` | trusted-authentication-path |
| `emaki-sudo status --json` | sudo-authentication, sudo-authentication-runtime, sudo-key, sudo-turn-reporting |
| `emaki-update-channel status --json` | channels-and-mirrors |
| `emaki-update-manager --status` | update-reminder-state, updates |

No JSON state command yet: agent-rules, application-defaults, application-package-policy, authentication-prompt, autostart, boot-loader-and-initramfs, cursor-theme, desktop-package-policy, desktop-portals, install-paths, installer, lock-idle-and-power, login-and-authentication, nvidia-policy, package-transaction-safety, release-identity-and-licenses, release-marker, repository-mirror-data, repository-signing-keys, runtime-compatibility, session-startup, shell-runtime, snapshot-boot-menu, system-map, trusted-shell-generations, wallet-and-secrets.
Read each component's State entry in the map for the reason; do not invent a command.

## Make and verify a change

Choose the recovery path first. Follow the component's Change instruction, make
the smallest authorized change, then query state again and verify the result.
Managed settings have `emaki settings history --json` and `emaki settings undo ID`;
machine and personal settings may have different recovery paths in the map.
On Arch, package operations use the package backend and its package manager;
never replace package files by hand or bypass a failed protection check.
Never disable authentication, expose secrets in logs or weaken permissions to make
a command succeed. A rules file is guidance, not a security boundary or a sudo grant.

## Snapshot before risky actions

Before a risky system action, establish a recovery path and take a snapshot with snapguard where it is installed and configured for this root; confirm that protection succeeded before proceeding.

Change: Read the rules and setup guide, then explicitly add the instructions to your own configuration if wanted. For risky actions, consult the installed snapguard documentation and use its supported interface; never guess its commands. Check snapshot coverage and keep separate backups of personal files before making the authorized change.

Never: Enable instructions without the person's choice; overwrite existing personal rules; bypass snapguard, assume it is installed because a prompt mentions it, or equate a sudo key with permission to skip a snapshot or the person's decision.

Recovery: If snapguard is absent or not working, say plainly that rollback protection for this risky action needs snapguard; do not promise an automatic undo. On ext4 snapguard keeps only file archives; a system rollback needs a btrfs root. Explain the recovery gap before proceeding and let the person decide whether to arrange protection or defer. Existing root snapshots and independent backups may support manual recovery only when verified for this change. Remove only the rules you added to turn this integration off.

## Snapshot coverage and recovery

Reports whether the current root is normal, a snapshot or a pending recovery, and provides the authenticated path to keep a selected snapshot. Snapshots cover only the btrfs root subvolume; ext4 has none. /home, separate mounts and subvolumes, a separate /boot and /efi are excluded. The installer's /boot is inside root and is included. Recovery reverts the whole root, including its package database and configuration, not only the component being repaired.

Change: First read `emaki-rollback status --json`; `unsupported` means this root cannot use snapshot recovery. A supported root does not guarantee a pre-update snapshot exists. If one is available, choose it in the boot menu for a temporary recovery session. With the person's decision, use the desktop recovery prompt to keep it, then restart as requested to make recovery permanent.

Never: Remove snapper, snap-pac or grub-btrfs; delete /.snapshots by hand; keep a snapshot without the person's decision; promise that root recovery restores home files.

Recovery: Keep the recovery receipt and previous-root entry until the recovered system is verified. Use the boot menu for another available root and restore home files from separate backups.

## Authentication for a command

Routes non-terminal sudo authentication through a root service and the shell prompt while terminal requests use the standard sudo PAM stack.

Change: Use ordinary sudo for an authorized command. Non-terminal authentication uses the desktop card when available; the person reviews the request and enters a password there. Terminal sudo keeps its standard PAM conversation and ordinary password memory. Cancel, Esc, expiry or locking cancels a new request. Update emaki-config for matching PAM policy, module, root service and shell transport; do not change authentication policy yourself.

Never: Ask the person to send a password to an agent; send passwords through command arguments, environment, files or general shell IPC; retry a cancelled request through a bypass. sudo -n cannot consume a key; -S and -A keep stock password handling and are not substitutes for the desktop card. Never remove the selected PAM service while keeping its sudoers selector.

Recovery: Restore the matching emaki-config package; remove the sudoers selector together with its PAM service when removing this integration. Root login policy is unchanged.

## Optional sudo key

The optional package lets the person give an agent a sudo key: a temporary extension of ordinary sudo for that agent's process tree, not a stored password. Only the person grants a key, after its warning screen; ordinary approval creates none. Ordinary sudo stays stock with its own 5-minute password memory. When a key ends, its served parents lose their cached sudo authority too; the person's terminal memory is untouched. Keys survive screen lock, suspend and shell restarts; they end on expiry, logout, the agent's exit, the panel indicator or Super+Ctrl+Esc.

Change: Install or remove emaki-sudo-key through package management. The person types the amount in the prompt: a number of minutes, at least 5 (the service takes 5–1440); a number of the agent's turns (1–1000, with a four-hour hard cap), only for agents that report turns; or the agent's whole session. There are no preset amounts; never ask for fewer than 5 minutes. Continue using ordinary sudo within the authorized scope. Inspect emaki-sudo status --json; revoke one key from the panel indicator or with emaki-sudo revoke ID --json. Revoke all with Super+Ctrl+Esc or emaki-sudo revoke-all --json. Turn reporting is a separate opt-in integration.

Never: Grant a key or acknowledge its warning for the person, create the marker manually, request a wider grant than needed, describe a grant as a stored password, or promise that revocation undoes commands already run or clears the person's terminal sudo password memory. Do not treat screen lock, suspend or a shell restart as revocation.

Recovery: Remove emaki-sudo-key to remove its marker and packaged binding; keys and their served parents' cached sudo authority end, while the person's terminal memory remains unchanged. A running compositor retains its captured binding until the session ends.
