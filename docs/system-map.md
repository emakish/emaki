# Emaki system map

Emaki is an Arch-based distribution built around the niri compositor, with its own
desktop shell, settings, installer and recovery tools. Read this map before changing
the system: each component names its files, ownership, state command and recovery path.

Generated from the `system-map` blocks of the Emaki sources by scripts/render-system-map;
do not edit. One section per component: what it is, the files it installs, whose files
they are, how to ask its state, how to change it, what never to do and how to go back.

Zones:

- `package`: installed by an Emaki package; never edit it, an update replaces it.
- `emaki-settings`: changed only with `emaki settings`, which keeps a history.
- `person`: the person's own files, theirs to change; no Emaki update writes them.

Start with `emaki state --json` for a system overview, then run the relevant component's
State command in the installed session. Read the JSON status and reason as well as the
exit code: unavailable state is not proof that a component is absent or broken.
"none yet" means no suitable JSON command exists today; its reason names the gap.

Follow each Change instruction. Use managed settings or the named settings page where
available, and keep personal overrides separate from package files. Never write the
person's `~/.config/niri/config.kdl` or edit generated files to change a setting.

Choose the rollback path before making a change. Managed settings use
`emaki settings history --json` and `emaki settings undo ID`; machine settings and other
personal state have separate recovery instructions. Before snapshot recovery, read
`emaki-rollback status --json`: `unsupported` means snapshots are unavailable on this root.
Ext4 has none; a supported btrfs root still needs an available pre-update snapshot.
A snapshot restores the whole root subvolume, not just one component. It excludes /home
and other separate mounts or subvolumes, including a separate /boot and the /efi system
partition. Emaki installs /boot inside root, so that layout includes its boot files.
Snapshot boots are temporary: follow snapshot-recovery to keep one, with the person's
decision. Without a suitable snapshot, recovery needs a separately saved system backup
or a compatible package repair through the update backend; no automatic rollback is promised.
Back up personal files separately. Keep the snapshot packages installed where supported.

On Arch, package changes go through `pacman`. Use the component's Rollback instruction
for a previous package version or a system snapshot; never replace package files by hand.

## application-defaults

Default application associations, launcher and terminal themes, file-manager preferences, toolkit appearance and desktop portal routing. Skeleton templates are package-owned; copies in a new account are personal files and are not updated in place.

- Files: `/etc/skel/.config/**`, `/etc/xdg/dolphinrc`, `/etc/xdg/fastfetch/config.jsonc`, `/etc/xdg/hypr/hyprlock.conf`, `/etc/xdg/kdeglobals`, `/etc/xdg/menus/emaki-applications.menu`, `/etc/xdg/mimeapps.list`, `/etc/xdg/qt6ct/qt6ct.conf`, `/etc/xdg/xdg-desktop-portal/niri-portals.conf`, `/usr/share/emaki/fuzzel/*`, `/usr/share/emaki/kitty/*`, `/usr/share/emaki/qt6ct/*`, `/usr/share/themes/Emaki/gtk-3.0/gtk.css`, `/usr/bin/emaki-terminal`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: none yet: no single JSON command reports toolkit overrides, associations and portal routing; `emaki settings list --json` reports only supported managed choices.
- Change: Use `emaki settings` for supported terminal and default-application choices. A person can override associations in ~/.config/mimeapps.list and maintain their own application configuration. Change packaged defaults with emaki-config.
- Never: Write /etc by hand to change a preference; replace personal files with skeleton templates; assume an update overwrites a personal association.
- Rollback: Undo managed choices with `emaki settings undo ID`; restore personal files from their own backups. For packaged defaults, follow snapshot-recovery with an available pre-update btrfs root snapshot; overrides in /home remain unchanged. Ext4 has no snapshot rollback.

```system-map
component: application-defaults
what: Default application associations, launcher and terminal themes, file-manager preferences, toolkit appearance and desktop portal routing. Skeleton templates are package-owned; copies in a new account are personal files and are not updated in place.
files:
  /etc/skel/.config/**
  /etc/xdg/dolphinrc
  /etc/xdg/fastfetch/config.jsonc
  /etc/xdg/hypr/hyprlock.conf
  /etc/xdg/kdeglobals
  /etc/xdg/menus/emaki-applications.menu
  /etc/xdg/mimeapps.list
  /etc/xdg/qt6ct/qt6ct.conf
  /etc/xdg/xdg-desktop-portal/niri-portals.conf
  /usr/share/emaki/fuzzel/*
  /usr/share/emaki/kitty/*
  /usr/share/emaki/qt6ct/*
  /usr/share/themes/Emaki/gtk-3.0/gtk.css
  /usr/bin/emaki-terminal
zone: package
state: none yet: no single JSON command reports toolkit overrides, associations and portal routing; `emaki settings list --json` reports only supported managed choices.
change: Use `emaki settings` for supported terminal and default-application choices. A person can override associations in ~/.config/mimeapps.list and maintain their own application configuration. Change packaged defaults with emaki-config.
never: Write /etc by hand to change a preference; replace personal files with skeleton templates; assume an update overwrites a personal association.
rollback: Undo managed choices with `emaki settings undo ID`; restore personal files from their own backups. For packaged defaults, follow snapshot-recovery with an available pre-update btrfs root snapshot; overrides in /home remain unchanged. Ext4 has no snapshot rollback.
```

## application-package-policy

Application-package integration: printer settings launcher, service presets and an autostart override that prevents a second update notifier.

- Files: `/etc/xdg/autostart/emaki-discover-notifier.desktop`, `/usr/lib/systemd/system-preset/45-emaki-apps.preset`, `/usr/share/applications/emaki-printers.desktop`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: none yet: no installed JSON command inventories the application-package policy and its effective overrides.
- Change: Open Printers from the app list for printer configuration. On Arch, use `pacman` for application installation and emaki-apps updates; the person can use personal autostart overrides.
- Never: Edit packaged launcher or preset files to configure one person's session; remove application dependencies recursively without checking the full plan.
- Rollback: For application integration and its compatible dependency set together, follow snapshot-recovery with an available pre-update btrfs root snapshot. Personal application data and autostart overrides in /home remain unchanged. Ext4 has no snapshot rollback.

```system-map
component: application-package-policy
what: Application-package integration: printer settings launcher, service presets and an autostart override that prevents a second update notifier.
files:
  /etc/xdg/autostart/emaki-discover-notifier.desktop
  /usr/lib/systemd/system-preset/45-emaki-apps.preset
  /usr/share/applications/emaki-printers.desktop
zone: package
state: none yet: no installed JSON command inventories the application-package policy and its effective overrides.
change: Open Printers from the app list for printer configuration. On Arch, use `pacman` for application installation and emaki-apps updates; the person can use personal autostart overrides.
never: Edit packaged launcher or preset files to configure one person's session; remove application dependencies recursively without checking the full plan.
rollback: For application integration and its compatible dependency set together, follow snapshot-recovery with an available pre-update btrfs root snapshot. Personal application data and autostart overrides in /home remain unchanged. Ext4 has no snapshot rollback.
```

## autostart

Starts desktop autostart entries and supplies the packaged device-integration entry. Per-person off switches live in ~/.config/emaki/autostart-disabled; these are personal files, not managed settings.

- Files: `/usr/bin/emaki-autostart`, `/usr/share/emaki/xdg/autostart/*`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: none yet: no installed JSON command inventories effective autostart entries and personal off switches.
- Change: The person can maintain ~/.config/autostart overrides and ~/.config/emaki/autostart-disabled off switches. Change packaged defaults with emaki-config.
- Never: Delete installed desktop entries to disable an application for one person; edit another person's autostart configuration.
- Rollback: Restore or remove the person's own override from its backup. For packaged startup entries, follow snapshot-recovery with an available pre-update btrfs root snapshot; overrides and disabled-entry lists in /home remain unchanged. Ext4 has no snapshot rollback.

```system-map
component: autostart
what: Starts desktop autostart entries and supplies the packaged device-integration entry. Per-person off switches live in ~/.config/emaki/autostart-disabled; these are personal files, not managed settings.
files:
  /usr/bin/emaki-autostart
  /usr/share/emaki/xdg/autostart/*
zone: package
state: none yet: no installed JSON command inventories effective autostart entries and personal off switches.
change: The person can maintain ~/.config/autostart overrides and ~/.config/emaki/autostart-disabled off switches. Change packaged defaults with emaki-config.
never: Delete installed desktop entries to disable an application for one person; edit another person's autostart configuration.
rollback: Restore or remove the person's own override from its backup. For packaged startup entries, follow snapshot-recovery with an available pre-update btrfs root snapshot; overrides and disabled-entry lists in /home remain unchanged. Ext4 has no snapshot rollback.
```

## battery-charge-limits

Applies supported battery thresholds through a fixed authorized helper, boot unit and discovery rule; machine state is /var/lib/emaki/charge-limits.json.

- Files: `/usr/libexec/emaki/emaki-charge-limit`, `/usr/lib/systemd/system/emaki-charge-limit.service`, `/usr/lib/systemd/system/multi-user.target.wants/emaki-charge-limit.service`, `/usr/lib/udev/rules.d/90-emaki-charge-limit.rules`, `/usr/share/polkit-1/actions/org.emaki.charge-limit.policy`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: `emaki-settings-power status --json` (prints JSON)
- Change: Choose a supported battery and charge limit in the power page; boot and device discovery reapply saved limits.
- Never: Accept caller-supplied paths, write arbitrary battery attributes or offer unsupported controls.
- Rollback: Set the supported limit to 100 percent; package removal disables boot/discovery integration and preserves machine state.

```system-map
component: battery-charge-limits
what: Applies supported battery thresholds through a fixed authorized helper, boot unit and discovery rule; machine state is /var/lib/emaki/charge-limits.json.
files:
  /usr/libexec/emaki/emaki-charge-limit
  /usr/lib/systemd/system/emaki-charge-limit.service
  /usr/lib/systemd/system/multi-user.target.wants/emaki-charge-limit.service
  /usr/lib/udev/rules.d/90-emaki-charge-limit.rules
  /usr/share/polkit-1/actions/org.emaki.charge-limit.policy
zone: package
state: emaki-settings-power status --json
change: Choose a supported battery and charge limit in the power page; boot and device discovery reapply saved limits.
never: Accept caller-supplied paths, write arbitrary battery attributes or offer unsupported controls.
rollback: Set the supported limit to 100 percent; package removal disables boot/discovery integration and preserves machine state.
```

## boot-loader-and-initramfs

Boot artwork, boot-loader refresh implementation, update return and boot-completion services, initramfs snapshot mounts and resume integration. Generated boot output describes this machine and must stay consistent with its kernels and root snapshots.

- Files: `/usr/bin/emaki-update-boot`, `/usr/lib/emaki/boot/**`, `/usr/lib/initcpio/hooks/emaki-*`, `/usr/lib/initcpio/install/emaki-*`, `/usr/lib/systemd/system-sleep/emaki-boot-resume`, `/usr/lib/systemd/system/emaki-boot-complete.service`, `/usr/lib/systemd/system/emaki-boot-refresh.service`, `/usr/lib/systemd/system/emaki-update-boot.service`, `/usr/lib/systemd/system/multi-user.target.wants/emaki-boot-complete.service`, `/usr/lib/systemd/system/multi-user.target.wants/emaki-boot-refresh.service`, `/usr/lib/systemd/system/multi-user.target.wants/emaki-update-boot.service`, `/usr/share/emaki/boot/*`, `/usr/share/emaki/grub/*`, `/usr/share/libalpm/hooks/04-emaki-update-boot.hook`, `/usr/share/libalpm/hooks/06-emaki-update-boot.hook`, `/usr/share/libalpm/hooks/95-emaki-boot-refresh.hook`, `/usr/share/libalpm/scripts/emaki_boot_defaults.py`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: none yet: boot publication and automatic return have no aggregate read-only JSON command; inspect the boot services' journal and /var/log/emaki-boot-refresh.log.
- Change: Update through the update manager. Package hooks and boot services regenerate boot output; use the boot menu to choose an available recovery entry.
- Never: Run boot-loader installation or menu-generation commands by hand; edit generated /boot files or /etc machine boot configuration by hand; remove a kernel or snapshot needed by a pending update.
- Rollback: Try another installed kernel, or follow snapshot-recovery with an available pre-update btrfs root snapshot. Only /boot inside the root subvolume is restored with its modules; a separate /boot and the /efi system partition need their own recovery. Ext4 has no snapshot rollback. Keep recovery entries until the recovered system is verified.

```system-map
component: boot-loader-and-initramfs
what: Boot artwork, boot-loader refresh implementation, update return and boot-completion services, initramfs snapshot mounts and resume integration. Generated boot output describes this machine and must stay consistent with its kernels and root snapshots.
files:
  /usr/bin/emaki-update-boot
  /usr/lib/emaki/boot/**
  /usr/lib/initcpio/hooks/emaki-*
  /usr/lib/initcpio/install/emaki-*
  /usr/lib/systemd/system-sleep/emaki-boot-resume
  /usr/lib/systemd/system/emaki-boot-complete.service
  /usr/lib/systemd/system/emaki-boot-refresh.service
  /usr/lib/systemd/system/emaki-update-boot.service
  /usr/lib/systemd/system/multi-user.target.wants/emaki-boot-complete.service
  /usr/lib/systemd/system/multi-user.target.wants/emaki-boot-refresh.service
  /usr/lib/systemd/system/multi-user.target.wants/emaki-update-boot.service
  /usr/share/emaki/boot/*
  /usr/share/emaki/grub/*
  /usr/share/libalpm/hooks/04-emaki-update-boot.hook
  /usr/share/libalpm/hooks/06-emaki-update-boot.hook
  /usr/share/libalpm/hooks/95-emaki-boot-refresh.hook
  /usr/share/libalpm/scripts/emaki_boot_defaults.py
zone: package
state: none yet: boot publication and automatic return have no aggregate read-only JSON command; inspect the boot services' journal and /var/log/emaki-boot-refresh.log.
change: Update through the update manager. Package hooks and boot services regenerate boot output; use the boot menu to choose an available recovery entry.
never: Run boot-loader installation or menu-generation commands by hand; edit generated /boot files or /etc machine boot configuration by hand; remove a kernel or snapshot needed by a pending update.
rollback: Try another installed kernel, or follow snapshot-recovery with an available pre-update btrfs root snapshot. Only /boot inside the root subvolume is restored with its modules; a separate /boot and the /efi system partition need their own recovery. Ext4 has no snapshot rollback. Keep recovery entries until the recovered system is verified.
```

## channels-and-mirrors

Reports the effective update channel and refreshes repository mirrors through a scheduled service. Channel and mirror changes affect subsequent package transactions.

- Files: `/usr/bin/emaki-update-channel`, `/usr/share/polkit-1/actions/org.emaki.channel.policy`, `/usr/bin/emaki-refresh-mirrors`, `/usr/lib/systemd/system/emaki-refresh-mirrors.service`, `/usr/lib/systemd/system/emaki-refresh-mirrors.timer`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: `emaki-update-channel status --json` (prints JSON)
- Change: Use authorized emaki-update-channel set stable|testing --json or reset --json; the fixed /var/lib/emaki/channel state controls subsequent transactions; the switch removes the local [emaki] database copy so the next update reads the chosen channel. Use the packaged mirror-refresh service for maintenance; channel selection does not run an update.
- Never: Rewrite /etc repository configuration by hand; mix release channels by copying individual packages; disable signing verification.
- Rollback: Select the previous channel with emaki-update-channel; installed versions do not change. Use the pre-update snapshot for a consistent package rollback.

```system-map
component: channels-and-mirrors
what: Reports the effective update channel and refreshes repository mirrors through a scheduled service. Channel and mirror changes affect subsequent package transactions.
files:
  /usr/bin/emaki-update-channel
  /usr/share/polkit-1/actions/org.emaki.channel.policy
  /usr/bin/emaki-refresh-mirrors
  /usr/lib/systemd/system/emaki-refresh-mirrors.service
  /usr/lib/systemd/system/emaki-refresh-mirrors.timer
zone: package
state: emaki-update-channel status --json
change: Use authorized emaki-update-channel set stable|testing --json or reset --json; the fixed /var/lib/emaki/channel state controls subsequent transactions; the switch removes the local [emaki] database copy so the next update reads the chosen channel. Use the packaged mirror-refresh service for maintenance; channel selection does not run an update.
never: Rewrite /etc repository configuration by hand; mix release channels by copying individual packages; disable signing verification.
rollback: Select the previous channel with emaki-update-channel; installed versions do not change. Use the pre-update snapshot for a consistent package rollback.
```

## command-and-settings-core

The emaki command provides diagnostics, the file ownership map and the managed settings menu. Its installed executable is package-owned. Managed overrides are in $XDG_CONFIG_HOME/emaki/settings.toml; derived generations, history and locks are under $XDG_STATE_HOME/emaki, with ~/.config and ~/.local/state as defaults. These runtime files belong to the emaki-settings zone and are not package payloads.

- Files: `/usr/bin/emaki`, `/usr/bin/emaki-config-path`, `/usr/share/doc/emaki/ZONES.md`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: `emaki settings list --json` (prints JSON)
- Change: Use `emaki settings`, or `emaki settings set KEY VALUE`, `get`, `reset` and `history`. Use `emaki state --json` for service diagnostics and `emaki map --json` for ownership. Machine keyboard settings go through the machine-settings provider and are outside managed history.
- Never: Edit managed settings, generated generations or journals by hand; change ~/.config/niri/config.kdl; treat an unavailable service as permission to rewrite its configuration.
- Rollback: Read `emaki settings history --json`, then use `emaki settings undo ID` for a managed change. Set machine settings back through their provider. For package code, follow snapshot-recovery with an available pre-update btrfs root snapshot; this does not undo settings or history in /home. Ext4 has no snapshot rollback.

```system-map
component: command-and-settings-core
what: The emaki command provides diagnostics, the file ownership map and the managed settings menu. Its installed executable is package-owned. Managed overrides are in $XDG_CONFIG_HOME/emaki/settings.toml; derived generations, history and locks are under $XDG_STATE_HOME/emaki, with ~/.config and ~/.local/state as defaults. These runtime files belong to the emaki-settings zone and are not package payloads.
files:
  /usr/bin/emaki
  /usr/bin/emaki-config-path
  /usr/share/doc/emaki/ZONES.md
zone: package
state: emaki settings list --json
change: Use `emaki settings`, or `emaki settings set KEY VALUE`, `get`, `reset` and `history`. Use `emaki state --json` for service diagnostics and `emaki map --json` for ownership. Machine keyboard settings go through the machine-settings provider and are outside managed history.
never: Edit managed settings, generated generations or journals by hand; change ~/.config/niri/config.kdl; treat an unavailable service as permission to rewrite its configuration.
rollback: Read `emaki settings history --json`, then use `emaki settings undo ID` for a managed change. Set machine settings back through their provider. For package code, follow snapshot-recovery with an available pre-update btrfs root snapshot; this does not undo settings or history in /home. Ext4 has no snapshot rollback.
```

## compositor-configuration

Packaged niri defaults and the Emaki session includes. /etc/niri/config.kdl is a packaged fallback; a person may have a separate ~/.config/niri/config.kdl, which Emaki must never overwrite. The personal ~/.config/emaki/niri-emaki.kdl include is read last in the Emaki session.

- Files: `/etc/niri/config.kdl`, `/usr/share/emaki/niri/*`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: `emaki niri snapshot --json` (prints JSON)
- Change: Use `emaki settings` for supported gaps and shortcuts. A person can maintain their own ~/.config/emaki/niri-emaki.kdl for other compositor choices; validate changes with `niri-emaki validate -c "$(emaki settings session-config)"` before relying on them. Package defaults change through emaki-config.
- Never: Edit ~/.config/niri/config.kdl; put a machine-specific output, scale, user name or home path into shared defaults; modify packaged includes.
- Rollback: Undo managed changes with `emaki settings undo ID`. Restore the personal include from its own backup. For packaged defaults, follow snapshot-recovery with an available pre-update btrfs root snapshot; personal includes in /home remain unchanged. Ext4 has no snapshot rollback.

```system-map
component: compositor-configuration
what: Packaged niri defaults and the Emaki session includes. /etc/niri/config.kdl is a packaged fallback; a person may have a separate ~/.config/niri/config.kdl, which Emaki must never overwrite. The personal ~/.config/emaki/niri-emaki.kdl include is read last in the Emaki session.
files:
  /etc/niri/config.kdl
  /usr/share/emaki/niri/*
zone: package
state: emaki niri snapshot --json
change: Use `emaki settings` for supported gaps and shortcuts. A person can maintain their own ~/.config/emaki/niri-emaki.kdl for other compositor choices; validate changes with `niri-emaki validate -c "$(emaki settings session-config)"` before relying on them. Package defaults change through emaki-config.
never: Edit ~/.config/niri/config.kdl; put a machine-specific output, scale, user name or home path into shared defaults; modify packaged includes.
rollback: Undo managed changes with `emaki settings undo ID`. Restore the personal include from its own backup. For packaged defaults, follow snapshot-recovery with an available pre-update btrfs root snapshot; personal includes in /home remain unchanged. Ext4 has no snapshot rollback.
```

## compositor-runtime

The patched niri compositor executable used by the Emaki desktop and login compositor; its configuration belongs to the separate compositor-configuration component.

- Files: `/usr/bin/niri-emaki`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: `emaki niri snapshot --json` (prints JSON)
- Change: On Arch, update niri-emaki through a full package transaction; sign out and back in when requested. Change supported preferences through `emaki settings`.
- Never: Replace the running compositor binary by hand; remove the recovery compositor; modify personal niri configuration to hide a package failure.
- Rollback: For the compositor and its compatible root package set together, follow snapshot-recovery with an available pre-update btrfs root snapshot. Personal compositor configuration in /home remains unchanged. Ext4 has no snapshot rollback.

```system-map
component: compositor-runtime
what: The patched niri compositor executable used by the Emaki desktop and login compositor; its configuration belongs to the separate compositor-configuration component.
files:
  /usr/bin/niri-emaki
zone: package
state: emaki niri snapshot --json
change: On Arch, update niri-emaki through a full package transaction; sign out and back in when requested. Change supported preferences through `emaki settings`.
never: Replace the running compositor binary by hand; remove the recovery compositor; modify personal niri configuration to hide a package failure.
rollback: For the compositor and its compatible root package set together, follow snapshot-recovery with an available pre-update btrfs root snapshot. Personal compositor configuration in /home remains unchanged. Ext4 has no snapshot rollback.
```

## cursor-theme

The packaged Emaki cursor images, aliases and theme index used by the desktop and login screen.

- Files: `/usr/share/icons/Emaki/**`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: none yet: no installed JSON command reports both the compositor cursor and application cursor themes; read the compositor and toolkit settings.
- Change: Update emaki-config through the update manager. On Arch, package transactions use `pacman`.
- Never: Replace cursor images or aliases in the package directory to change a personal preference.
- Rollback: For packaged cursor assets, follow snapshot-recovery with an available pre-update btrfs root snapshot. Personal theme selections in /home remain unchanged. Ext4 has no snapshot rollback.

```system-map
component: cursor-theme
what: The packaged Emaki cursor images, aliases and theme index used by the desktop and login screen.
files:
  /usr/share/icons/Emaki/**
zone: package
state: none yet: no installed JSON command reports both the compositor cursor and application cursor themes; read the compositor and toolkit settings.
change: Update emaki-config through the update manager. On Arch, package transactions use `pacman`.
never: Replace cursor images or aliases in the package directory to change a personal preference.
rollback: For packaged cursor assets, follow snapshot-recovery with an available pre-update btrfs root snapshot. Personal theme selections in /home remain unchanged. Ext4 has no snapshot rollback.
```

## desktop-package-policy

The desktop package supplies lock authentication policy; its dependencies select the desktop services. The emaki metapackage installs no files of its own and keeps the tested desktop, compositor and shell-runtime versions together through dependencies.

- Files: `/etc/pam.d/emaki-lock`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: none yet: no installed JSON command reports the desktop dependency set and lock authentication policy.
- Change: On Arch, update emaki and emaki-desktop with a full `pacman` transaction.
- Never: Weaken or edit the packaged PAM policy; recursively remove desktop dependencies without reviewing the entire removal plan.
- Rollback: For the desktop dependency set and packaged authentication policy together, follow snapshot-recovery with an available pre-update btrfs root snapshot. Personal configuration in /home remains unchanged. Ext4 has no snapshot rollback.

```system-map
component: desktop-package-policy
what: The desktop package supplies lock authentication policy; its dependencies select the desktop services. The emaki metapackage installs no files of its own and keeps the tested desktop, compositor and shell-runtime versions together through dependencies.
files:
  /etc/pam.d/emaki-lock
zone: package
state: none yet: no installed JSON command reports the desktop dependency set and lock authentication policy.
change: On Arch, update emaki and emaki-desktop with a full `pacman` transaction.
never: Weaken or edit the packaged PAM policy; recursively remove desktop dependencies without reviewing the entire removal plan.
rollback: For the desktop dependency set and packaged authentication policy together, follow snapshot-recovery with an available pre-update btrfs root snapshot. Personal configuration in /home remains unchanged. Ext4 has no snapshot rollback.
```

## desktop-portals

The desktop portal backend serves application requests such as file selection and screen sharing. Emaki's package changes dependency policy; the session's portal routing is in the separate application-defaults component.

- Files: `/usr/lib/systemd/user/xdg-desktop-portal-gnome.service`, `/usr/lib/xdg-desktop-portal-gnome`, `/usr/share/applications/xdg-desktop-portal-gnome.desktop`, `/usr/share/dbus-1/services/org.freedesktop.impl.portal.desktop.gnome.service`, `/usr/share/glib-2.0/schemas/xdg-desktop-portal-gnome.gschema.xml`, `/usr/share/locale/*/LC_MESSAGES/xdg-desktop-portal-gnome.mo`, `/usr/share/xdg-desktop-portal/portals/gnome.portal`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: none yet: no installed Emaki JSON command reports portal routing and request health; inspect the portal services in the user journal.
- Change: Update the portal package through a full package transaction. Use the requesting application's portal dialog for permissions and selections; keep package routing intact.
- Never: Bypass screen-sharing permission dialogs; rewrite installed D-Bus activation or portal descriptors to work around a session-environment problem.
- Rollback: For portal packages and their dependencies together, follow snapshot-recovery with an available pre-update btrfs root snapshot. Restore personal portal overrides in /home from their own backups. Ext4 has no snapshot rollback.

```system-map
component: desktop-portals
what: The desktop portal backend serves application requests such as file selection and screen sharing. Emaki's package changes dependency policy; the session's portal routing is in the separate application-defaults component.
files:
  /usr/lib/systemd/user/xdg-desktop-portal-gnome.service
  /usr/lib/xdg-desktop-portal-gnome
  /usr/share/applications/xdg-desktop-portal-gnome.desktop
  /usr/share/dbus-1/services/org.freedesktop.impl.portal.desktop.gnome.service
  /usr/share/glib-2.0/schemas/xdg-desktop-portal-gnome.gschema.xml
  /usr/share/locale/*/LC_MESSAGES/xdg-desktop-portal-gnome.mo
  /usr/share/xdg-desktop-portal/portals/gnome.portal
zone: package
state: none yet: no installed Emaki JSON command reports portal routing and request health; inspect the portal services in the user journal.
change: Update the portal package through a full package transaction. Use the requesting application's portal dialog for permissions and selections; keep package routing intact.
never: Bypass screen-sharing permission dialogs; rewrite installed D-Bus activation or portal descriptors to work around a session-environment problem.
rollback: For portal packages and their dependencies together, follow snapshot-recovery with an available pre-update btrfs root snapshot. Restore personal portal overrides in /home from their own backups. Ext4 has no snapshot rollback.
```

## desktop-shell

The desktop bar, dock, launcher, notifications, clipboard, system panel, lock and welcome windows, plus their QML modules, shaders and helpers. The state command requires the running desktop shell. The launcher hosts Settings on the same surface, growing from its normal 720-pixel width to at most 1240 by 810 logical pixels within the output. Application, recent-item, notification, night-light and welcome state under $XDG_STATE_HOME/emaki is runtime state outside settings history.

- Files: `/usr/share/emaki/shell/AppCatalog.qml`, `/usr/share/emaki/shell/AppIdentity.qml`, `/usr/share/emaki/shell/AppLaunch.qml`, `/usr/share/emaki/shell/AuthController.qml`, `/usr/share/emaki/shell/BarPolicy.qml`, `/usr/share/emaki/shell/Calculator.js`, `/usr/share/emaki/shell/CalendarMonth.qml`, `/usr/share/emaki/shell/ClipboardHistory.qml`, `/usr/share/emaki/shell/ClockBody.qml`, `/usr/share/emaki/shell/ClockCompactRow.qml`, `/usr/share/emaki/shell/ClockIsland.qml`, `/usr/share/emaki/shell/ClockPanel.qml`, `/usr/share/emaki/shell/Dock.qml`, `/usr/share/emaki/shell/DockBackdrop.qml`, `/usr/share/emaki/shell/DockBackdropRegion.qml`, `/usr/share/emaki/shell/DockInputRegion.qml`, `/usr/share/emaki/shell/DockIpc.qml`, `/usr/share/emaki/shell/DockPolicy.qml`, `/usr/share/emaki/shell/DockStore.qml`, `/usr/share/emaki/shell/FocusRing.qml`, `/usr/share/emaki/shell/GlassCopy.qml`, `/usr/share/emaki/shell/GlassShape.qml`, `/usr/share/emaki/shell/GlassTarget.qml`, `/usr/share/emaki/shell/GlassText.qml`, `/usr/share/emaki/shell/GreeterAuth.qml`, `/usr/share/emaki/shell/GreeterSession.qml`, `/usr/share/emaki/shell/GreeterState.qml`, `/usr/share/emaki/shell/HoverTip.qml`, `/usr/share/emaki/shell/Icon.qml`, `/usr/share/emaki/shell/IslandGlass.qml`, `/usr/share/emaki/shell/Keyboard.js`, `/usr/share/emaki/shell/KeyboardFocusKeeper.qml`, `/usr/share/emaki/shell/KeyboardTarget.qml`, `/usr/share/emaki/shell/LauncherBody.qml`, `/usr/share/emaki/shell/LauncherItem.qml`, `/usr/share/emaki/shell/LeftIslands.qml`, `/usr/share/emaki/shell/Liquid.js`, `/usr/share/emaki/shell/LiquidPalette.qml`, `/usr/share/emaki/shell/LockAuth.qml`, `/usr/share/emaki/shell/LockCapture.qml`, `/usr/share/emaki/shell/LockCapturePanel.qml`, `/usr/share/emaki/shell/LockEnvironment.qml`, `/usr/share/emaki/shell/LockGeometry.qml`, `/usr/share/emaki/shell/LockGlass.qml`, `/usr/share/emaki/shell/LockInput.qml`, `/usr/share/emaki/shell/LockPam.qml`, `/usr/share/emaki/shell/LockSession.qml`, `/usr/share/emaki/shell/LockSurface.qml`, `/usr/share/emaki/shell/LockVisual.qml`, `/usr/share/emaki/shell/LockWordmark.js`, `/usr/share/emaki/shell/LockWordmark.qml`, `/usr/share/emaki/shell/LockWordmarkData.js`, `/usr/share/emaki/shell/MediaBlock.qml`, `/usr/share/emaki/shell/Metrics.qml`, `/usr/share/emaki/shell/MicMeter.qml`, `/usr/share/emaki/shell/NightLight.qml`, `/usr/share/emaki/shell/NiriService.qml`, `/usr/share/emaki/shell/NotificationReceiver.qml`, `/usr/share/emaki/shell/NotificationService.qml`, `/usr/share/emaki/shell/NotificationStore.qml`, `/usr/share/emaki/shell/Osd.qml`, `/usr/share/emaki/shell/OutputSelection.js`, `/usr/share/emaki/shell/PasswordToggle.qml`, `/usr/share/emaki/shell/PrivacyCompactRow.qml`, `/usr/share/emaki/shell/PrivacyPanel.qml`, `/usr/share/emaki/shell/PrivacyPill.qml`, `/usr/share/emaki/shell/PrivateJob.qml`, `/usr/share/emaki/shell/RecentFiles.qml`, `/usr/share/emaki/shell/RecentLog.qml`, `/usr/share/emaki/shell/SessionCoverState.qml`, `/usr/share/emaki/shell/SessionCoverSurface.qml`, `/usr/share/emaki/shell/SessionStartup.qml`, `/usr/share/emaki/shell/SessionUpdateNotice.qml`, `/usr/share/emaki/shell/ShellOutputs.qml`, `/usr/share/emaki/shell/ShellPalette.qml`, `/usr/share/emaki/shell/ShellRecoveryNotice.qml`, `/usr/share/emaki/shell/ShellScene.qml`, `/usr/share/emaki/shell/ShellServices.qml`, `/usr/share/emaki/shell/ShellTools.qml`, `/usr/share/emaki/shell/ShortcutSheet.qml`, `/usr/share/emaki/shell/SnapshotPrompt.qml`, `/usr/share/emaki/shell/SnapshotRecovery.qml`, `/usr/share/emaki/shell/StaticBar.qml`, `/usr/share/emaki/shell/Surfaces.qml`, `/usr/share/emaki/shell/SymbolIcon.qml`, `/usr/share/emaki/shell/SystemBackend.qml`, `/usr/share/emaki/shell/SystemBody.qml`, `/usr/share/emaki/shell/SystemCompactRow.qml`, `/usr/share/emaki/shell/SystemIcons.js`, `/usr/share/emaki/shell/SystemIsland.qml`, `/usr/share/emaki/shell/SystemMeterPolicy.qml`, `/usr/share/emaki/shell/SystemNative.qml`, `/usr/share/emaki/shell/SystemPanel.qml`, `/usr/share/emaki/shell/SystemService.qml`, `/usr/share/emaki/shell/TipTarget.qml`, `/usr/share/emaki/shell/TrayItems.qml`, `/usr/share/emaki/shell/TrayService.qml`, `/usr/share/emaki/shell/UpdateService.qml`, `/usr/share/emaki/shell/WallpaperSource.qml`, `/usr/share/emaki/shell/Welcome.qml`, `/usr/share/emaki/shell/WelcomeButton.qml`, `/usr/share/emaki/shell/WelcomeContent.js`, `/usr/share/emaki/shell/WelcomeController.qml`, `/usr/share/emaki/shell/WelcomeGlass.qml`, `/usr/share/emaki/shell/WelcomeStore.qml`, `/usr/share/emaki/shell/WelcomeWindow.qml`, `/usr/share/emaki/shell/WindowLabels.qml`, `/usr/share/emaki/shell/WorkspaceStrip.qml`, `/usr/share/emaki/shell/XkbCodes.js`, `/usr/share/emaki/shell/emaki-welcome.desktop`, `/usr/share/emaki/shell/greeter.qml`, `/usr/share/emaki/shell/helpers/app_scope.py`, `/usr/share/emaki/shell/helpers/child-guard.py`, `/usr/share/emaki/shell/helpers/clip-recorder.py`, `/usr/share/emaki/shell/helpers/clipboard_store.py`, `/usr/share/emaki/shell/helpers/greeter-auth.py`, `/usr/share/emaki/shell/helpers/greeter-handoff.py`, `/usr/share/emaki/shell/helpers/greeter-state.py`, `/usr/share/emaki/shell/helpers/launcher-tools.py`, `/usr/share/emaki/shell/helpers/lock-environment.py`, `/usr/share/emaki/shell/helpers/publish-wallpaper.py`, `/usr/share/emaki/shell/helpers/recent-files.py`, `/usr/share/emaki/shell/helpers/session-start.py`, `/usr/share/emaki/shell/helpers/shortcuts.py`, `/usr/share/emaki/shell/helpers/system-tools.py`, `/usr/share/emaki/shell/helpers/wallpaper.py`, `/usr/share/emaki/shell/lock.qml`, `/usr/share/emaki/shell/qmldir`, `/usr/share/emaki/shell/session-cover.qml`, `/usr/share/emaki/shell/shaders/dock.frag`, `/usr/share/emaki/shell/shaders/dock.frag.qsb`, `/usr/share/emaki/shell/shaders/down.frag`, `/usr/share/emaki/shell/shaders/down.frag.qsb`, `/usr/share/emaki/shell/shaders/gauss.frag`, `/usr/share/emaki/shell/shaders/gauss.frag.qsb`, `/usr/share/emaki/shell/shaders/session-drain.frag`, `/usr/share/emaki/shell/shaders/session-drain.frag.qsb`, `/usr/share/emaki/shell/shell.qml`, `/usr/bin/emaki-shell`, `/usr/bin/emaki-shell-health`, `/usr/share/applications/emaki-welcome.desktop`, `/usr/share/icons/hicolor/scalable/apps/emaki-welcome.svg`, `/usr/lib/systemd/user/emaki-shell*.service`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: `emaki-shell call system status` (prints JSON)
- Change: Use the shell panels for their actions and `emaki settings` for supported managed values. Package code changes through emaki-config updates. `emaki-shell restart` restarts the shell in the current session.
- Never: Patch installed QML or helper files; delete personal state to repair package code; assume settings undo restores clipboard, notifications or recent items.
- Rollback: Use settings history and undo for managed values. Restore separately backed-up personal state when needed. For packaged shell code, follow snapshot-recovery with an available pre-update btrfs root snapshot; clipboard, notifications and recent items in /home remain unchanged. Ext4 has no snapshot rollback.

```system-map
component: desktop-shell
what: The desktop bar, dock, launcher, notifications, clipboard, system panel, lock and welcome windows, plus their QML modules, shaders and helpers. The state command requires the running desktop shell. The launcher hosts Settings on the same surface, growing from its normal 720-pixel width to at most 1240 by 810 logical pixels within the output. Application, recent-item, notification, night-light and welcome state under $XDG_STATE_HOME/emaki is runtime state outside settings history.
files:
  /usr/share/emaki/shell/AppCatalog.qml
  /usr/share/emaki/shell/AppIdentity.qml
  /usr/share/emaki/shell/AppLaunch.qml
  /usr/share/emaki/shell/AuthController.qml
  /usr/share/emaki/shell/BarPolicy.qml
  /usr/share/emaki/shell/Calculator.js
  /usr/share/emaki/shell/CalendarMonth.qml
  /usr/share/emaki/shell/ClipboardHistory.qml
  /usr/share/emaki/shell/ClockBody.qml
  /usr/share/emaki/shell/ClockCompactRow.qml
  /usr/share/emaki/shell/ClockIsland.qml
  /usr/share/emaki/shell/ClockPanel.qml
  /usr/share/emaki/shell/Dock.qml
  /usr/share/emaki/shell/DockBackdrop.qml
  /usr/share/emaki/shell/DockBackdropRegion.qml
  /usr/share/emaki/shell/DockInputRegion.qml
  /usr/share/emaki/shell/DockIpc.qml
  /usr/share/emaki/shell/DockPolicy.qml
  /usr/share/emaki/shell/DockStore.qml
  /usr/share/emaki/shell/FocusRing.qml
  /usr/share/emaki/shell/GlassCopy.qml
  /usr/share/emaki/shell/GlassShape.qml
  /usr/share/emaki/shell/GlassTarget.qml
  /usr/share/emaki/shell/GlassText.qml
  /usr/share/emaki/shell/GreeterAuth.qml
  /usr/share/emaki/shell/GreeterSession.qml
  /usr/share/emaki/shell/GreeterState.qml
  /usr/share/emaki/shell/HoverTip.qml
  /usr/share/emaki/shell/Icon.qml
  /usr/share/emaki/shell/IslandGlass.qml
  /usr/share/emaki/shell/Keyboard.js
  /usr/share/emaki/shell/KeyboardFocusKeeper.qml
  /usr/share/emaki/shell/KeyboardTarget.qml
  /usr/share/emaki/shell/LauncherBody.qml
  /usr/share/emaki/shell/LauncherItem.qml
  /usr/share/emaki/shell/LeftIslands.qml
  /usr/share/emaki/shell/Liquid.js
  /usr/share/emaki/shell/LiquidPalette.qml
  /usr/share/emaki/shell/LockAuth.qml
  /usr/share/emaki/shell/LockCapture.qml
  /usr/share/emaki/shell/LockCapturePanel.qml
  /usr/share/emaki/shell/LockEnvironment.qml
  /usr/share/emaki/shell/LockGeometry.qml
  /usr/share/emaki/shell/LockGlass.qml
  /usr/share/emaki/shell/LockInput.qml
  /usr/share/emaki/shell/LockPam.qml
  /usr/share/emaki/shell/LockSession.qml
  /usr/share/emaki/shell/LockSurface.qml
  /usr/share/emaki/shell/LockVisual.qml
  /usr/share/emaki/shell/LockWordmark.js
  /usr/share/emaki/shell/LockWordmark.qml
  /usr/share/emaki/shell/LockWordmarkData.js
  /usr/share/emaki/shell/MediaBlock.qml
  /usr/share/emaki/shell/Metrics.qml
  /usr/share/emaki/shell/MicMeter.qml
  /usr/share/emaki/shell/NightLight.qml
  /usr/share/emaki/shell/NiriService.qml
  /usr/share/emaki/shell/NotificationReceiver.qml
  /usr/share/emaki/shell/NotificationService.qml
  /usr/share/emaki/shell/NotificationStore.qml
  /usr/share/emaki/shell/Osd.qml
  /usr/share/emaki/shell/OutputSelection.js
  /usr/share/emaki/shell/PasswordToggle.qml
  /usr/share/emaki/shell/PrivacyCompactRow.qml
  /usr/share/emaki/shell/PrivacyPanel.qml
  /usr/share/emaki/shell/PrivacyPill.qml
  /usr/share/emaki/shell/PrivateJob.qml
  /usr/share/emaki/shell/RecentFiles.qml
  /usr/share/emaki/shell/RecentLog.qml
  /usr/share/emaki/shell/SessionCoverState.qml
  /usr/share/emaki/shell/SessionCoverSurface.qml
  /usr/share/emaki/shell/SessionStartup.qml
  /usr/share/emaki/shell/SessionUpdateNotice.qml
  /usr/share/emaki/shell/ShellOutputs.qml
  /usr/share/emaki/shell/ShellPalette.qml
  /usr/share/emaki/shell/ShellRecoveryNotice.qml
  /usr/share/emaki/shell/ShellScene.qml
  /usr/share/emaki/shell/ShellServices.qml
  /usr/share/emaki/shell/ShellTools.qml
  /usr/share/emaki/shell/ShortcutSheet.qml
  /usr/share/emaki/shell/SnapshotPrompt.qml
  /usr/share/emaki/shell/SnapshotRecovery.qml
  /usr/share/emaki/shell/StaticBar.qml
  /usr/share/emaki/shell/Surfaces.qml
  /usr/share/emaki/shell/SymbolIcon.qml
  /usr/share/emaki/shell/SystemBackend.qml
  /usr/share/emaki/shell/SystemBody.qml
  /usr/share/emaki/shell/SystemCompactRow.qml
  /usr/share/emaki/shell/SystemIcons.js
  /usr/share/emaki/shell/SystemIsland.qml
  /usr/share/emaki/shell/SystemMeterPolicy.qml
  /usr/share/emaki/shell/SystemNative.qml
  /usr/share/emaki/shell/SystemPanel.qml
  /usr/share/emaki/shell/SystemService.qml
  /usr/share/emaki/shell/TipTarget.qml
  /usr/share/emaki/shell/TrayItems.qml
  /usr/share/emaki/shell/TrayService.qml
  /usr/share/emaki/shell/UpdateService.qml
  /usr/share/emaki/shell/WallpaperSource.qml
  /usr/share/emaki/shell/Welcome.qml
  /usr/share/emaki/shell/WelcomeButton.qml
  /usr/share/emaki/shell/WelcomeContent.js
  /usr/share/emaki/shell/WelcomeController.qml
  /usr/share/emaki/shell/WelcomeGlass.qml
  /usr/share/emaki/shell/WelcomeStore.qml
  /usr/share/emaki/shell/WelcomeWindow.qml
  /usr/share/emaki/shell/WindowLabels.qml
  /usr/share/emaki/shell/WorkspaceStrip.qml
  /usr/share/emaki/shell/XkbCodes.js
  /usr/share/emaki/shell/emaki-welcome.desktop
  /usr/share/emaki/shell/greeter.qml
  /usr/share/emaki/shell/helpers/app_scope.py
  /usr/share/emaki/shell/helpers/child-guard.py
  /usr/share/emaki/shell/helpers/clip-recorder.py
  /usr/share/emaki/shell/helpers/clipboard_store.py
  /usr/share/emaki/shell/helpers/greeter-auth.py
  /usr/share/emaki/shell/helpers/greeter-handoff.py
  /usr/share/emaki/shell/helpers/greeter-state.py
  /usr/share/emaki/shell/helpers/launcher-tools.py
  /usr/share/emaki/shell/helpers/lock-environment.py
  /usr/share/emaki/shell/helpers/publish-wallpaper.py
  /usr/share/emaki/shell/helpers/recent-files.py
  /usr/share/emaki/shell/helpers/session-start.py
  /usr/share/emaki/shell/helpers/shortcuts.py
  /usr/share/emaki/shell/helpers/system-tools.py
  /usr/share/emaki/shell/helpers/wallpaper.py
  /usr/share/emaki/shell/lock.qml
  /usr/share/emaki/shell/qmldir
  /usr/share/emaki/shell/session-cover.qml
  /usr/share/emaki/shell/shaders/dock.frag
  /usr/share/emaki/shell/shaders/dock.frag.qsb
  /usr/share/emaki/shell/shaders/down.frag
  /usr/share/emaki/shell/shaders/down.frag.qsb
  /usr/share/emaki/shell/shaders/gauss.frag
  /usr/share/emaki/shell/shaders/gauss.frag.qsb
  /usr/share/emaki/shell/shaders/session-drain.frag
  /usr/share/emaki/shell/shaders/session-drain.frag.qsb
  /usr/share/emaki/shell/shell.qml
  /usr/bin/emaki-shell
  /usr/bin/emaki-shell-health
  /usr/share/applications/emaki-welcome.desktop
  /usr/share/icons/hicolor/scalable/apps/emaki-welcome.svg
  /usr/lib/systemd/user/emaki-shell*.service
zone: package
state: emaki-shell call system status
change: Use the shell panels for their actions and `emaki settings` for supported managed values. Package code changes through emaki-config updates. `emaki-shell restart` restarts the shell in the current session.
never: Patch installed QML or helper files; delete personal state to repair package code; assume settings undo restores clipboard, notifications or recent items.
rollback: Use settings history and undo for managed values. Restore separately backed-up personal state when needed. For packaged shell code, follow snapshot-recovery with an available pre-update btrfs root snapshot; clipboard, notifications and recent items in /home remain unchanged. Ext4 has no snapshot rollback.
```

## desktop-update-signal

Tells a running desktop session that the desktop under it was updated, so the panel can ask the person to sign out and sign in again. A package hook records each desktop update (which session components changed, when, and the action it needs); `state` answers in JSON: schema, busy (a system transaction is running), desktop (an opaque identity that changes with every installed, replaced or removed desktop package) and update (null, or id, at, components, action). A record from another boot or root counts as no update. Running sessions are never restarted.

- Files: `/usr/bin/emaki-session-update`, `/usr/libexec/emaki/emaki_session_arch.py`, `/usr/share/libalpm/hooks/zzz-emaki-session-update.hook`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: `emaki-session-update state` (prints JSON)
- Change: The files change only with an emaki-config update. The update record lives in /run/emaki-session-update and disappears at the next boot; signing out and in again ends the notice.
- Never: Write /run/emaki-session-update by hand or make the panel read the package database instead of this command; restart the panel or compositor for the person.
- Rollback: Boot the snapshot taken before the update from the boot menu.

```system-map
component: desktop-update-signal
what: Tells a running desktop session that the desktop under it was updated, so the panel can ask the person to sign out and sign in again. A package hook records each desktop update (which session components changed, when, and the action it needs); `state` answers in JSON: schema, busy (a system transaction is running), desktop (an opaque identity that changes with every installed, replaced or removed desktop package) and update (null, or id, at, components, action). A record from another boot or root counts as no update. Running sessions are never restarted.
files:
  /usr/bin/emaki-session-update
  /usr/libexec/emaki/emaki_session_arch.py
  /usr/share/libalpm/hooks/zzz-emaki-session-update.hook
zone: package
state: emaki-session-update state
change: The files change only with an emaki-config update. The update record lives in /run/emaki-session-update and disappears at the next boot; signing out and in again ends the notice.
never: Write /run/emaki-session-update by hand or make the panel read the package database instead of this command; restart the panel or compositor for the person.
rollback: Boot the snapshot taken before the update from the boot menu.
```

## install-paths

Tells Emaki's own programs where Emaki is installed: its data, helper and command directories and the system programs it starts. The same values exist once for shell scripts, once for Python programs and once for the desktop shell; all are written when the package is built and none is read from the person's settings.

- Files: `/usr/libexec/emaki/emaki_paths.py`, `/usr/libexec/emaki/paths`, `/usr/share/emaki/shell/Platform.qml`, `/usr/share/emaki/shell/helpers/emaki_paths.py`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: none yet: the files are shell KEY=VALUE, Python and QML text; read them with `cat /usr/libexec/emaki/paths`.
- Change: Only with an emaki-config update; each package build writes them again.
- Never: Edit or replace them by hand; a wrong value stops the login screen and the panel.
- Rollback: For generated package path modules, follow snapshot-recovery with an available pre-update btrfs root snapshot. Recover them with the matching package files and install layout; personal settings in /home remain unchanged. Ext4 has no snapshot rollback.

```system-map
component: install-paths
what: Tells Emaki's own programs where Emaki is installed: its data, helper and command directories and the system programs it starts. The same values exist once for shell scripts, once for Python programs and once for the desktop shell; all are written when the package is built and none is read from the person's settings.
files:
  /usr/libexec/emaki/emaki_paths.py
  /usr/libexec/emaki/paths
  /usr/share/emaki/shell/Platform.qml
  /usr/share/emaki/shell/helpers/emaki_paths.py
zone: package
state: none yet: the files are shell KEY=VALUE, Python and QML text; read them with `cat /usr/libexec/emaki/paths`.
change: Only with an emaki-config update; each package build writes them again.
never: Edit or replace them by hand; a wrong value stops the login screen and the panel.
rollback: For generated package path modules, follow snapshot-recovery with an available pre-update btrfs root snapshot. Recover them with the matching package files and install layout; personal settings in /home remain unchanged. Ext4 has no snapshot rollback.
```

## installer

The live installation window, command-line client, privileged worker, device planning code, UI and reference fixtures. The installer is available on live media and is not retained as an installed-system service.

- Files: `/usr/bin/emaki-install`, `/usr/bin/emaki-install-cli`, `/usr/bin/emaki-installerd`, `/usr/lib/python*/site-packages/emaki_installer/**`, `/usr/lib/systemd/system/emaki-installerd.service`, `/usr/lib/sysusers.d/emaki-installer.conf`, `/usr/lib/tmpfiles.d/emaki-installer.conf`, `/usr/share/applications/emaki-install.desktop`, `/usr/share/doc/emaki-installer/**`, `/usr/share/emaki-installer/**`, `/usr/share/icons/hicolor/scalable/apps/emaki-install.svg`, `/usr/share/licenses/emaki-installer/*`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: none yet: `emaki-install-cli --probe` prints JSON only on live media with its worker running; no installed-system installer state command exists.
- Change: Use the Install window on live media and review the explicit disk and partition choices before confirmation. Update installer code through a new image or emaki-installer package.
- Never: Confirm an installation plan just to inspect state; erase an unselected disk; write a fabricated successful-install record.
- Rollback: Disk installation has no automatic undo. Back up affected disks before confirming a plan; restore those backups if installation must be reversed.

```system-map
component: installer
what: The live installation window, command-line client, privileged worker, device planning code, UI and reference fixtures. The installer is available on live media and is not retained as an installed-system service.
files:
  /usr/bin/emaki-install
  /usr/bin/emaki-install-cli
  /usr/bin/emaki-installerd
  /usr/lib/python*/site-packages/emaki_installer/**
  /usr/lib/systemd/system/emaki-installerd.service
  /usr/lib/sysusers.d/emaki-installer.conf
  /usr/lib/tmpfiles.d/emaki-installer.conf
  /usr/share/applications/emaki-install.desktop
  /usr/share/doc/emaki-installer/**
  /usr/share/emaki-installer/**
  /usr/share/icons/hicolor/scalable/apps/emaki-install.svg
  /usr/share/licenses/emaki-installer/*
zone: package
state: none yet: `emaki-install-cli --probe` prints JSON only on live media with its worker running; no installed-system installer state command exists.
change: Use the Install window on live media and review the explicit disk and partition choices before confirmation. Update installer code through a new image or emaki-installer package.
never: Confirm an installation plan just to inspect state; erase an unselected disk; write a fabricated successful-install record.
rollback: Disk installation has no automatic undo. Back up affected disks before confirming a plan; restore those backups if installation must be reversed.
```

## launcher-settings

Hosts Settings inside the launcher, with navigation, a lazy page registry and shared core bridge; personal choices remain in provider or core state.

- Files: `/usr/share/emaki/shell/SettingsController.qml`, `/usr/share/emaki/shell/SettingsView.qml`, `/usr/share/emaki/shell/SettingsGlass.qml`, `/usr/share/emaki/shell/SettingsCorePage.qml`, `/usr/share/emaki/shell/SystemSettingsPage.qml`, `/usr/share/emaki/shell/SettingsPageRegistry.qml`, `/usr/share/emaki/shell/SettingsPageDefinition.qml`, `/usr/share/emaki/shell/SettingsBridge.qml`, `/usr/share/emaki/shell/SettingsCatalog.qml`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: `emaki-shell call settings status` (prints JSON)
- Change: Open with emaki settings or emaki-shell call settings open PAGE; close with emaki-shell call settings close.
- Never: Create a second settings IPC owner, probe devices in the navigation layer or edit installed QML.
- Rollback: Restore the previous package for launcher settings code; reset or undo managed choices through emaki settings.

```system-map
component: launcher-settings
what: Hosts Settings inside the launcher, with navigation, a lazy page registry and shared core bridge; personal choices remain in provider or core state.
files:
  /usr/share/emaki/shell/SettingsController.qml
  /usr/share/emaki/shell/SettingsView.qml
  /usr/share/emaki/shell/SettingsGlass.qml
  /usr/share/emaki/shell/SettingsCorePage.qml
  /usr/share/emaki/shell/SystemSettingsPage.qml
  /usr/share/emaki/shell/SettingsPageRegistry.qml
  /usr/share/emaki/shell/SettingsPageDefinition.qml
  /usr/share/emaki/shell/SettingsBridge.qml
  /usr/share/emaki/shell/SettingsCatalog.qml
zone: package
state: emaki-shell call settings status
change: Open with emaki settings or emaki-shell call settings open PAGE; close with emaki-shell call settings close.
never: Create a second settings IPC owner, probe devices in the navigation layer or edit installed QML.
rollback: Restore the previous package for launcher settings code; reset or undo managed choices through emaki settings.
```

## lock-idle-and-power

Coordinates screen locking, idle handling, sleep preparation and power actions with logind. The idle unit ships disabled and supports explicit personal timer enablement; the lock window is part of the shell payload. System policy also configures compressed swap and service presets.

- Files: `/usr/bin/emaki-lock`, `/usr/bin/emaki-idle`, `/usr/bin/emaki-power`, `/usr/bin/emaki-sleep-guard`, `/usr/lib/systemd/user/emaki-idle.service`, `/usr/lib/systemd/user/emaki-sleep-guard.service`, `/usr/lib/systemd/logind.conf.d/50-emaki.conf`, `/usr/lib/systemd/zram-generator.conf.d/00-emaki.conf`, `/usr/lib/systemd/system-preset/50-emaki.preset`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: none yet: `emaki-lock status` returns JSON only outside live media and exits nonzero when unlocked; it is not a general readiness command. Inspect the user journal for idle and sleep units.
- Change: Use the desktop lock and power controls. Change implementation and policy through emaki-config updates.
- Never: Suspend automatically while diagnosing; bypass the lock or sleep guard; treat a locker process as proof that the session is locked.
- Rollback: For packaged lock helpers and system power policy, follow snapshot-recovery with an available pre-update btrfs root snapshot. It does not restore a running lock or sleep state, or personal configuration in /home. Ext4 has no snapshot rollback.

```system-map
component: lock-idle-and-power
what: Coordinates screen locking, idle handling, sleep preparation and power actions with logind. The idle unit ships disabled and supports explicit personal timer enablement; the lock window is part of the shell payload. System policy also configures compressed swap and service presets.
files:
  /usr/bin/emaki-lock
  /usr/bin/emaki-idle
  /usr/bin/emaki-power
  /usr/bin/emaki-sleep-guard
  /usr/lib/systemd/user/emaki-idle.service
  /usr/lib/systemd/user/emaki-sleep-guard.service
  /usr/lib/systemd/logind.conf.d/50-emaki.conf
  /usr/lib/systemd/zram-generator.conf.d/00-emaki.conf
  /usr/lib/systemd/system-preset/50-emaki.preset
zone: package
state: none yet: `emaki-lock status` returns JSON only outside live media and exits nonzero when unlocked; it is not a general readiness command. Inspect the user journal for idle and sleep units.
change: Use the desktop lock and power controls. Change implementation and policy through emaki-config updates.
never: Suspend automatically while diagnosing; bypass the lock or sleep guard; treat a locker process as proof that the session is locked.
rollback: For packaged lock helpers and system power policy, follow snapshot-recovery with an available pre-update btrfs root snapshot. It does not restore a running lock or sleep state, or personal configuration in /home. Ext4 has no snapshot rollback.
```

## login-and-authentication

Runs the login compositor and greeter, prepares their private runtime directories and holds the display during login handover. PAM configuration belongs to packages, while account credentials belong to the person and the system account tools.

- Files: `/usr/bin/emaki-drm-hold`, `/usr/bin/emaki-greeter-*`, `/usr/lib/pam.d/emaki-greetd*`, `/usr/lib/systemd/system/emaki-drm-hold.service`, `/usr/lib/systemd/system/greetd.service.d/emaki.conf`, `/usr/lib/tmpfiles.d/emaki-greeter.conf`, `/usr/share/emaki/greetd/**`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: none yet: greeter readiness and authentication have no installed read-only JSON status command; inspect `journalctl -b -u greetd`.
- Change: Update emaki-config through the update manager. On Arch, package transactions use `pacman`.
- Never: Edit PAM or greeter files by hand; log passwords; bypass authentication; copy personal credentials into greeter state.
- Rollback: For greeter and authentication integration, follow snapshot-recovery with an available pre-update btrfs root snapshot. Root account configuration also reverts; personal wallets and other files in /home do not. Ext4 has no snapshot rollback.

```system-map
component: login-and-authentication
what: Runs the login compositor and greeter, prepares their private runtime directories and holds the display during login handover. PAM configuration belongs to packages, while account credentials belong to the person and the system account tools.
files:
  /usr/bin/emaki-drm-hold
  /usr/bin/emaki-greeter-*
  /usr/lib/pam.d/emaki-greetd*
  /usr/lib/systemd/system/emaki-drm-hold.service
  /usr/lib/systemd/system/greetd.service.d/emaki.conf
  /usr/lib/tmpfiles.d/emaki-greeter.conf
  /usr/share/emaki/greetd/**
zone: package
state: none yet: greeter readiness and authentication have no installed read-only JSON status command; inspect `journalctl -b -u greetd`.
change: Update emaki-config through the update manager. On Arch, package transactions use `pacman`.
never: Edit PAM or greeter files by hand; log passwords; bypass authentication; copy personal credentials into greeter state.
rollback: For greeter and authentication integration, follow snapshot-recovery with an available pre-update btrfs root snapshot. Root account configuration also reverts; personal wallets and other files in /home do not. Ext4 has no snapshot rollback.
```

## machine-settings

Reads and changes the settings of the whole computer: keyboard layouts and the switch key, time zone, automatic time, system language and formats. It writes no file itself: the system's locale and time services own those files and ask for an administrator password. `emaki settings` uses it for keyboard.layouts and keyboard.switch_key.

- Files: `/usr/bin/emaki-machine-settings`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: `emaki-machine-settings get --json` (prints JSON)
- Change: `emaki-machine-settings set KEY VALUE --json` (keys and allowed values: `emaki-machine-settings capabilities --json`), or `emaki settings set keyboard.layouts us,ru`. A key with source "declared" is set by the machine's own configuration; change it there.
- Never: Edit the locale, keyboard or time zone files of the system by hand while the session runs, or copy them from another machine.
- Rollback: Set the previous value again; every change answers with its before value.

```system-map
component: machine-settings
what: Reads and changes the settings of the whole computer: keyboard layouts and the switch key, time zone, automatic time, system language and formats. It writes no file itself: the system's locale and time services own those files and ask for an administrator password. `emaki settings` uses it for keyboard.layouts and keyboard.switch_key.
files:
  /usr/bin/emaki-machine-settings
zone: package
state: emaki-machine-settings get --json
change: `emaki-machine-settings set KEY VALUE --json` (keys and allowed values: `emaki-machine-settings capabilities --json`), or `emaki settings set keyboard.layouts us,ru`. A key with source "declared" is set by the machine's own configuration; change it there.
never: Edit the locale, keyboard or time zone files of the system by hand while the session runs, or copy them from another machine.
rollback: Set the previous value again; every change answers with its before value.
```

## nvidia-policy

Optional hardware-specific session policy, application profile and kernel-module guard. It is separate from hardware-neutral desktop defaults.

- Files: `/etc/mkinitcpio.conf.d/60-emaki-nvidia.conf`, `/etc/nvidia/nvidia-application-profiles-rc.d/50-emaki-niri.json`, `/usr/lib/emaki/nvidia/*`, `/usr/lib/modprobe.d/emaki-nvidia.conf`, `/usr/share/emaki/nvidia/session.sh`, `/usr/share/libalpm/hooks/92-emaki-nvidia-guard.hook`, `/usr/share/licenses/emaki-nvidia/LICENSE`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: none yet: the runtime policy helper has no installed read-only JSON health command covering driver, modules and compositor startup.
- Change: Let the installer select the hardware policy. On Arch, update emaki-nvidia and its matching driver/kernel packages in one consistent transaction; retain the recovery kernel.
- Never: Put device-specific environment or output settings in shared desktop defaults; bypass the module guard or hand-edit generated boot files.
- Rollback: For matching policy, drivers and kernel modules together, follow snapshot-recovery with an available pre-update btrfs root snapshot. /boot is recovered only when inside that root; separate /boot and /efi files need their own recovery. Another installed kernel may help only if it has working matching modules. Ext4 has no snapshot rollback.

```system-map
component: nvidia-policy
what: Optional hardware-specific session policy, application profile and kernel-module guard. It is separate from hardware-neutral desktop defaults.
files:
  /etc/mkinitcpio.conf.d/60-emaki-nvidia.conf
  /etc/nvidia/nvidia-application-profiles-rc.d/50-emaki-niri.json
  /usr/lib/emaki/nvidia/*
  /usr/lib/modprobe.d/emaki-nvidia.conf
  /usr/share/emaki/nvidia/session.sh
  /usr/share/libalpm/hooks/92-emaki-nvidia-guard.hook
  /usr/share/licenses/emaki-nvidia/LICENSE
zone: package
state: none yet: the runtime policy helper has no installed read-only JSON health command covering driver, modules and compositor startup.
change: Let the installer select the hardware policy. On Arch, update emaki-nvidia and its matching driver/kernel packages in one consistent transaction; retain the recovery kernel.
never: Put device-specific environment or output settings in shared desktop defaults; bypass the module guard or hand-edit generated boot files.
rollback: For matching policy, drivers and kernel modules together, follow snapshot-recovery with an available pre-update btrfs root snapshot. /boot is recovered only when inside that root; separate /boot and /efi files need their own recovery. Another installed kernel may help only if it has working matching modules. Ext4 has no snapshot rollback.
```

## package-transaction-safety

Package hooks enforce snapshot retention, inhibit unsafe concurrent power operations, maintain release identity and refresh initramfs and menu integration. The migration helper adopts only recognized older defaults.

- Files: `/usr/bin/emaki-transaction-inhibit`, `/usr/share/libalpm/hooks/00-emaki-*`, `/usr/share/libalpm/hooks/01-emaki-config-release.hook`, `/usr/share/libalpm/hooks/02-emaki-config-remove.hook`, `/usr/share/libalpm/hooks/50-emaki-os-release*.hook`, `/usr/share/libalpm/hooks/89-emaki-*.hook`, `/usr/share/libalpm/hooks/90-emaki-grub-title.hook`, `/usr/share/libalpm/hooks/93-emaki-initramfs-refresh.hook`, `/usr/share/libalpm/hooks/99-emaki-rollback-holds.hook`, `/usr/share/libalpm/scripts/emaki-grub-title`, `/usr/share/libalpm/scripts/emaki-initramfs-refresh`, `/usr/share/libalpm/scripts/emaki-os-release`, `/usr/share/libalpm/scripts/emaki-rollback-holds`, `/usr/share/libalpm/scripts/emaki-snapshot-policy`, `/usr/share/libalpm/scripts/emaki-system-migrate`, `/usr/share/libalpm/scripts/emaki_initramfs.py`, `/etc/sudoers.d/10-emaki-wheel`, `/usr/share/emaki/defaults/wheel`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: none yet: no read-only JSON command reports all package hooks and transaction inhibitors; inspect the package transaction log and system journal.
- Change: Update emaki-config through the update manager. On Arch, package transactions use `pacman`.
- Never: Disable transaction or snapshot hooks; remove snapshot packages; write sudoers or other /etc files by hand; interrupt a running package transaction. Before recursive removal on Arch, inspect the entire `pacman -Rs --print` result and preserve needed dependencies explicitly.
- Rollback: For hooks and the root package database together, follow snapshot-recovery with an available pre-update btrfs root snapshot. Separate /boot and /efi output is not restored; follow boot-loader-and-initramfs for those boundaries. Ext4 has no snapshot rollback.

```system-map
component: package-transaction-safety
what: Package hooks enforce snapshot retention, inhibit unsafe concurrent power operations, maintain release identity and refresh initramfs and menu integration. The migration helper adopts only recognized older defaults.
files:
  /usr/bin/emaki-transaction-inhibit
  /usr/share/libalpm/hooks/00-emaki-*
  /usr/share/libalpm/hooks/01-emaki-config-release.hook
  /usr/share/libalpm/hooks/02-emaki-config-remove.hook
  /usr/share/libalpm/hooks/50-emaki-os-release*.hook
  /usr/share/libalpm/hooks/89-emaki-*.hook
  /usr/share/libalpm/hooks/90-emaki-grub-title.hook
  /usr/share/libalpm/hooks/93-emaki-initramfs-refresh.hook
  /usr/share/libalpm/hooks/99-emaki-rollback-holds.hook
  /usr/share/libalpm/scripts/emaki-grub-title
  /usr/share/libalpm/scripts/emaki-initramfs-refresh
  /usr/share/libalpm/scripts/emaki-os-release
  /usr/share/libalpm/scripts/emaki-rollback-holds
  /usr/share/libalpm/scripts/emaki-snapshot-policy
  /usr/share/libalpm/scripts/emaki-system-migrate
  /usr/share/libalpm/scripts/emaki_initramfs.py
  /etc/sudoers.d/10-emaki-wheel
  /usr/share/emaki/defaults/wheel
zone: package
state: none yet: no read-only JSON command reports all package hooks and transaction inhibitors; inspect the package transaction log and system journal.
change: Update emaki-config through the update manager. On Arch, package transactions use `pacman`.
never: Disable transaction or snapshot hooks; remove snapshot packages; write sudoers or other /etc files by hand; interrupt a running package transaction. Before recursive removal on Arch, inspect the entire `pacman -Rs --print` result and preserve needed dependencies explicitly.
rollback: For hooks and the root package database together, follow snapshot-recovery with an available pre-update btrfs root snapshot. Separate /boot and /efi output is not restored; follow boot-loader-and-initramfs for those boundaries. Ext4 has no snapshot rollback.
```

## release-identity-and-licenses

Packaged operating-system identity, the terminal system-information layout and artwork, and the licenses for emaki-config and its font data.

- Files: `/usr/lib/emaki/os-release`, `/usr/share/emaki/fetch/*`, `/usr/share/licenses/emaki-config/*`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: none yet: release identity and license files have no combined JSON command; `emaki version` and the files provide text.
- Change: Update emaki-config through the update manager. On Arch, package transactions use `pacman`.
- Never: Edit release metadata to claim a different installed version; delete attribution files.
- Rollback: For release metadata and its matching root package set together, follow snapshot-recovery with an available pre-update btrfs root snapshot. Ext4 has no snapshot rollback.

```system-map
component: release-identity-and-licenses
what: Packaged operating-system identity, the terminal system-information layout and artwork, and the licenses for emaki-config and its font data.
files:
  /usr/lib/emaki/os-release
  /usr/share/emaki/fetch/*
  /usr/share/licenses/emaki-config/*
zone: package
state: none yet: release identity and license files have no combined JSON command; `emaki version` and the files provide text.
change: Update emaki-config through the update manager. On Arch, package transactions use `pacman`.
never: Edit release metadata to claim a different installed version; delete attribution files.
rollback: For release metadata and its matching root package set together, follow snapshot-recovery with an available pre-update btrfs root snapshot. Ext4 has no snapshot rollback.
```

## release-marker

Names the installed Emaki release: VERSION and LABEL from packaging/emaki-config/emaki-release, and EMAKI_COMMIT, the source commit appended when the package is built. The terminal greeting (`emaki` without arguments, fastfetch) and the release and rollback image checks read it.

- Files: `/usr/lib/emaki-release`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: none yet: the file is shell KEY=VALUE text and `emaki version` prints text; read it with `cat /usr/lib/emaki-release`.
- Change: Only by installing another emaki-config version (`emaki-update` or `sudo pacman -Syu`).
- Never: Edit or replace the file by hand to claim another version; source it as root from an untrusted copy.
- Rollback: Boot the snapshot taken before the update from the boot menu, or install the previous emaki-config from the pacman cache while it is still there.

```system-map
component: release-marker
what: Names the installed Emaki release: VERSION and LABEL from packaging/emaki-config/emaki-release, and EMAKI_COMMIT, the source commit appended when the package is built. The terminal greeting (`emaki` without arguments, fastfetch) and the release and rollback image checks read it.
files:
  /usr/lib/emaki-release
zone: package
state: none yet: the file is shell KEY=VALUE text and `emaki version` prints text; read it with `cat /usr/lib/emaki-release`.
change: Only by installing another emaki-config version (`emaki-update` or `sudo pacman -Syu`).
never: Edit or replace the file by hand to claim another version; source it as root from an untrusted copy.
rollback: Boot the snapshot taken before the update from the boot menu, or install the previous emaki-config from the pacman cache while it is still there.
```

## repository-mirror-data

Packaged server definitions and the mirror-list include used by the channel reader. The machine channel selector is outside this payload and is preserved by package migration.

- Files: `/etc/pacman.d/emaki-mirrorlist`, `/usr/share/emaki/mirrors/*`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: none yet: `emaki-update-channel` reports the effective channel as text; there is no JSON reader for repository source and signature policy.
- Change: On Arch, update emaki-mirrorlist through `pacman`; the package migrates recognized old server definitions and preserves custom configurations. Leave machine channel selection to the owner using the update documentation.
- Never: Replace custom server configuration with a packaged template; bypass signature checks or switch channel by installing isolated packages.
- Rollback: For packaged mirrors and root repository configuration, follow snapshot-recovery with an available pre-update btrfs root snapshot. To reverse only a channel selection, follow channels-and-mirrors; switching channels does not downgrade packages. Ext4 has no snapshot rollback.

```system-map
component: repository-mirror-data
what: Packaged server definitions and the mirror-list include used by the channel reader. The machine channel selector is outside this payload and is preserved by package migration.
files:
  /etc/pacman.d/emaki-mirrorlist
  /usr/share/emaki/mirrors/*
zone: package
state: none yet: `emaki-update-channel` reports the effective channel as text; there is no JSON reader for repository source and signature policy.
change: On Arch, update emaki-mirrorlist through `pacman`; the package migrates recognized old server definitions and preserves custom configurations. Leave machine channel selection to the owner using the update documentation.
never: Replace custom server configuration with a packaged template; bypass signature checks or switch channel by installing isolated packages.
rollback: For packaged mirrors and root repository configuration, follow snapshot-recovery with an available pre-update btrfs root snapshot. To reverse only a channel selection, follow channels-and-mirrors; switching channels does not downgrade packages. Ext4 has no snapshot rollback.
```

## repository-signing-keys

Public signing keys and trust/revocation lists for Emaki packages. These are public verification material, not private signing keys.

- Files: `/usr/share/pacman/keyrings/emaki*`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: none yet: no installed JSON command reports the packaged keyring and effective package trust.
- Change: On Arch, update emaki-keyring through a verified package transaction; follow keyring recovery instructions if signature validation fails.
- Never: Disable signature verification, trust an unverified key or overwrite keyring files to bypass an error.
- Rollback: Restore a previous verified keyring package only if its trust and revocation data still permits the intended signed packages. An available pre-update btrfs root snapshot can recover the keyring with its package set through snapshot-recovery; recheck trust afterward. Ext4 has no snapshot rollback.

```system-map
component: repository-signing-keys
what: Public signing keys and trust/revocation lists for Emaki packages. These are public verification material, not private signing keys.
files:
  /usr/share/pacman/keyrings/emaki*
zone: package
state: none yet: no installed JSON command reports the packaged keyring and effective package trust.
change: On Arch, update emaki-keyring through a verified package transaction; follow keyring recovery instructions if signature validation fails.
never: Disable signature verification, trust an unverified key or overwrite keyring files to bypass an error.
rollback: Restore a previous verified keyring package only if its trust and revocation data still permits the intended signed packages. An available pre-update btrfs root snapshot can recover the keyring with its package set through snapshot-recovery; recheck trust afterward. Ext4 has no snapshot rollback.
```

## runtime-compatibility

Checks that the shell runtime matches its Qt build version after package changes, before an incompatible graphical session is started.

- Files: `/usr/bin/emaki-qt-check`, `/usr/share/libalpm/hooks/99-emaki-qt-check.hook`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: none yet: `emaki-qt-check` is a text diagnostic with meaningful failure status, not a JSON state command.
- Change: Update emaki-config through the update manager. On Arch, package transactions use `pacman`.
- Never: Bypass an ABI mismatch or replace only one library to make the shell start; force a partial upgrade.
- Rollback: For the runtime, Qt libraries and compatibility checks together, follow snapshot-recovery with an available pre-update btrfs root snapshot. Ext4 has no snapshot rollback; never restore an isolated library into a different package set.

```system-map
component: runtime-compatibility
what: Checks that the shell runtime matches its Qt build version after package changes, before an incompatible graphical session is started.
files:
  /usr/bin/emaki-qt-check
  /usr/share/libalpm/hooks/99-emaki-qt-check.hook
zone: package
state: none yet: `emaki-qt-check` is a text diagnostic with meaningful failure status, not a JSON state command.
change: Update emaki-config through the update manager. On Arch, package transactions use `pacman`.
never: Bypass an ABI mismatch or replace only one library to make the shell start; force a partial upgrade.
rollback: For the runtime, Qt libraries and compatibility checks together, follow snapshot-recovery with an available pre-update btrfs root snapshot. Ext4 has no snapshot rollback; never restore an isolated library into a different package set.
```

## session-startup

Starts the Emaki compositor session, imports its environment and pins session files to a coherent generation across package updates. Includes the graphical session entry, systemd integration and an emergency text-session command.

- Files: `/usr/bin/niri-emaki-session`, `/usr/bin/emaki-session-files`, `/usr/bin/emaki-session-import-environment`, `/usr/bin/emaki-text-session`, `/usr/bin/emaki-migrate-installer-config`, `/usr/libexec/emaki/emaki_session_state.py`, `/usr/lib/environment.d/60-emaki-xdg.conf`, `/usr/lib/systemd/user/niri-emaki.service`, `/usr/share/wayland-sessions/niri-emaki.desktop`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: none yet: session-file generations and pending sign-out have no read-only JSON command; inspect the user session journal and the update notice.
- Change: Update emaki-config through the update manager. On Arch, package transactions use `pacman`. Sign out and back in when the update manager requests it; session migration runs through the provided startup path.
- Never: Mix files from different package generations; delete the active runtime generation; replace the running compositor executable or force a sign-out during a transaction.
- Rollback: For packaged startup files, follow snapshot-recovery with an available pre-update btrfs root snapshot. Session generations and personal files in /home are not restored by it. Ext4 has no snapshot rollback.

```system-map
component: session-startup
what: Starts the Emaki compositor session, imports its environment and pins session files to a coherent generation across package updates. Includes the graphical session entry, systemd integration and an emergency text-session command.
files:
  /usr/bin/niri-emaki-session
  /usr/bin/emaki-session-files
  /usr/bin/emaki-session-import-environment
  /usr/bin/emaki-text-session
  /usr/bin/emaki-migrate-installer-config
  /usr/libexec/emaki/emaki_session_state.py
  /usr/lib/environment.d/60-emaki-xdg.conf
  /usr/lib/systemd/user/niri-emaki.service
  /usr/share/wayland-sessions/niri-emaki.desktop
zone: package
state: none yet: session-file generations and pending sign-out have no read-only JSON command; inspect the user session journal and the update notice.
change: Update emaki-config through the update manager. On Arch, package transactions use `pacman`. Sign out and back in when the update manager requests it; session migration runs through the provided startup path.
never: Mix files from different package generations; delete the active runtime generation; replace the running compositor executable or force a sign-out during a transaction.
rollback: For packaged startup files, follow snapshot-recovery with an available pre-update btrfs root snapshot. Session generations and personal files in /home are not restored by it. Ext4 has no snapshot rollback.
```

## settings-controls

Contains the shared Settings rows, light palette, search index and section artwork; these files hold no personal state.

- Files: `/usr/share/emaki/shell/settings/*.qml`, `/usr/share/emaki/shell/settings/qmldir`, `/usr/share/emaki/shell/settings/SettingsIndex.js`, `/usr/share/emaki/shell/settings/icons/*.svg`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: `emaki-shell call settings status` (prints JSON)
- Change: Use Settings controls and search; update these assets only through emaki-config.
- Never: Edit installed controls or treat search metadata as proof a setting is available.
- Rollback: Restore the previous package; use the provider or core history to undo changes made through a row.

```system-map
component: settings-controls
what: Contains the shared Settings rows, light palette, search index and section artwork; these files hold no personal state.
files:
  /usr/share/emaki/shell/settings/*.qml
  /usr/share/emaki/shell/settings/qmldir
  /usr/share/emaki/shell/settings/SettingsIndex.js
  /usr/share/emaki/shell/settings/icons/*.svg
zone: package
state: emaki-shell call settings status
change: Use Settings controls and search; update these assets only through emaki-config.
never: Edit installed controls or treat search metadata as proof a setting is available.
rollback: Restore the previous package; use the provider or core history to undo changes made through a row.
```

## settings-displays

Controls outputs through one shared service and a fixed helper; preferences and recovery records live in the personal Emaki config/state directories.

- Files: `/usr/share/emaki/shell/DisplaysSettingsPage.qml`, `/usr/share/emaki/shell/DisplaysService.qml`, `/usr/share/emaki/shell/helpers/displays.py`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: `emaki-shell call settings status` (prints JSON)
- Change: Open Displays to inspect outputs and apply supported mode, scale, position, rotation, power and main-screen choices; confirm risky changes.
- Never: Edit personal niri configuration, disable the last active output or persist output-off preferences or unconfirmed risky changes.
- Rollback: Reject or let the confirmation expire to revert a pending change; use row reset for saved overrides and restore the package for code.

```system-map
component: settings-displays
what: Controls outputs through one shared service and a fixed helper; preferences and recovery records live in the personal Emaki config/state directories.
files:
  /usr/share/emaki/shell/DisplaysSettingsPage.qml
  /usr/share/emaki/shell/DisplaysService.qml
  /usr/share/emaki/shell/helpers/displays.py
zone: package
state: emaki-shell call settings status
change: Open Displays to inspect outputs and apply supported mode, scale, position, rotation, power and main-screen choices; confirm risky changes.
never: Edit personal niri configuration, disable the last active output or persist output-off preferences or unconfirmed risky changes.
rollback: Reject or let the confirmation expire to revert a pending change; use row reset for saved overrides and restore the package for code.
```

## settings-input-pages

Presents keyboard and pointer settings through the shared settings core and the machine provider for supported layouts.

- Files: `/usr/share/emaki/shell/KeyboardSettingsPage.qml`, `/usr/share/emaki/shell/MouseSettingsPage.qml`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: `emaki settings list --json` (prints JSON)
- Change: Open keyboard or mouse in Settings; use emaki settings set KEY VALUE --json or reset KEY --json for supported personal keys.
- Never: Edit personal niri files, write system files from a page or assume machine layouts have a universal default.
- Rollback: Reset or undo affected personal keys; restore machine layouts explicitly through their provider.

```system-map
component: settings-input-pages
what: Presents keyboard and pointer settings through the shared settings core and the machine provider for supported layouts.
files:
  /usr/share/emaki/shell/KeyboardSettingsPage.qml
  /usr/share/emaki/shell/MouseSettingsPage.qml
zone: package
state: emaki settings list --json
change: Open keyboard or mouse in Settings; use emaki settings set KEY VALUE --json or reset KEY --json for supported personal keys.
never: Edit personal niri files, write system files from a page or assume machine layouts have a universal default.
rollback: Reset or undo affected personal keys; restore machine layouts explicitly through their provider.
```

## settings-local-report

Reads local system information and writes private reviewable reports beneath $XDG_STATE_HOME/emaki/reports.

- Files: `/usr/bin/emaki-settings-about`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: `emaki-settings-about status --json` (prints JSON)
- Change: Run emaki-settings-about report --json to create a report for local review.
- Never: Upload reports, collect passwords or claim unavailable diagnostic sections succeeded.
- Rollback: Delete an unwanted report; no system configuration changes.

```system-map
component: settings-local-report
what: Reads local system information and writes private reviewable reports beneath $XDG_STATE_HOME/emaki/reports.
files:
  /usr/bin/emaki-settings-about
zone: package
state: emaki-settings-about status --json
change: Run emaki-settings-about report --json to create a report for local review.
never: Upload reports, collect passwords or claim unavailable diagnostic sections succeeded.
rollback: Delete an unwanted report; no system configuration changes.
```

## settings-network-pages

Presents wireless, pairing, DNS and VPN controls through existing session services and fixed network helpers.

- Files: `/usr/share/emaki/shell/SettingsWifiPage.qml`, `/usr/share/emaki/shell/SettingsBluetoothPage.qml`, `/usr/share/emaki/shell/SettingsNetworkPage.qml`, `/usr/share/emaki/shell/SettingsNetworkService.qml`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: `emaki-settings-network status --json` (prints JSON)
- Change: Open wifi, bluetooth or network in Settings; apply supported DNS or VPN changes through the page.
- Never: Create a second radio service, retain revealed passwords or edit system connection files from QML.
- Rollback: Restore previous connection values through their provider; restore the package for page code.

```system-map
component: settings-network-pages
what: Presents wireless, pairing, DNS and VPN controls through existing session services and fixed network helpers.
files:
  /usr/share/emaki/shell/SettingsWifiPage.qml
  /usr/share/emaki/shell/SettingsBluetoothPage.qml
  /usr/share/emaki/shell/SettingsNetworkPage.qml
  /usr/share/emaki/shell/SettingsNetworkService.qml
zone: package
state: emaki-settings-network status --json
change: Open wifi, bluetooth or network in Settings; apply supported DNS or VPN changes through the page.
never: Create a second radio service, retain revealed passwords or edit system connection files from QML.
rollback: Restore previous connection values through their provider; restore the package for page code.
```

## settings-network-provider

Reads saved connections without secrets and applies supported connection changes through NetworkManager.

- Files: `/usr/bin/emaki-settings-network`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: `emaki-settings-network status --json` (prints JSON)
- Change: Use dns UUID SERVERS, dns UUID reset or import-vpn TYPE FILE; the provider returns JSON and NetworkManager authorizes changes.
- Never: Edit resolver or connection files directly, or promise that a saved proxy field changes application traffic.
- Rollback: Restore previous DNS values or reset to automatic; remove an imported connection through NetworkManager by UUID.

```system-map
component: settings-network-provider
what: Reads saved connections without secrets and applies supported connection changes through NetworkManager.
files:
  /usr/bin/emaki-settings-network
zone: package
state: emaki-settings-network status --json
change: Use dns UUID SERVERS, dns UUID reset or import-vpn TYPE FILE; the provider returns JSON and NetworkManager authorizes changes.
never: Edit resolver or connection files directly, or promise that a saved proxy field changes application traffic.
rollback: Restore previous DNS values or reset to automatic; remove an imported connection through NetworkManager by UUID.
```

## settings-notifications

Presents do-not-disturb timing and application rules; the existing notification store owns identities and history.

- Files: `/usr/share/emaki/shell/NotificationsSettingsPage.qml`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: `emaki settings list --json` (prints JSON)
- Change: Open notifications in Settings or set notifications.dnd, notifications.until, notifications.schedule and notifications.rules through the core.
- Never: Register another notification server, export message bodies in status or edit generated settings state.
- Rollback: Reset the affected key or undo its settings history entry; removing the package preserves personal notification state.

```system-map
component: settings-notifications
what: Presents do-not-disturb timing and application rules; the existing notification store owns identities and history.
files:
  /usr/share/emaki/shell/NotificationsSettingsPage.qml
zone: package
state: emaki settings list --json
change: Open notifications in Settings or set notifications.dnd, notifications.until, notifications.schedule and notifications.rules through the core.
never: Register another notification server, export message bodies in status or edit generated settings state.
rollback: Reset the affected key or undo its settings history entry; removing the package preserves personal notification state.
```

## settings-power

Stores personal screen timers in $XDG_CONFIG_HOME/emaki/power.json; activates the existing idle service after an explicit supported change.

- Files: `/usr/bin/emaki-settings-power`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: `emaki-settings-power status --json` (prints JSON)
- Change: Use set KEY VALUE --json and reset KEY --json; supported keys are blank_battery, blank_ac and lock_delay.
- Never: Automatically suspend, overwrite machine power policy or create another sleep-lock owner.
- Rollback: Reset each changed key or disable the personal emaki-idle.service; the person owns saved state.

```system-map
component: settings-power
what: Stores personal screen timers in $XDG_CONFIG_HOME/emaki/power.json; activates the existing idle service after an explicit supported change.
files:
  /usr/bin/emaki-settings-power
zone: package
state: emaki-settings-power status --json
change: Use set KEY VALUE --json and reset KEY --json; supported keys are blank_battery, blank_ac and lock_delay.
never: Automatically suspend, overwrite machine power policy or create another sleep-lock owner.
rollback: Reset each changed key or disable the personal emaki-idle.service; the person owns saved state.
```

## settings-sound

Shares existing native audio devices and the peak monitor with the quick sound panel; it starts no second device service.

- Files: `/usr/share/emaki/shell/SoundSettingsPage.qml`, `/usr/share/emaki/shell/SoundSettingsBackend.qml`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: `emaki-shell call system status` (prints JSON)
- Change: Open Sound to choose devices and change volume, mute or stream levels through the native backend.
- Never: Write device configuration files, start another mixer service or keep microphone monitoring after both views close.
- Rollback: Use row reset for automatic device policy, 100 percent level and unmuted state; restore the package for code.

```system-map
component: settings-sound
what: Shares existing native audio devices and the peak monitor with the quick sound panel; it starts no second device service.
files:
  /usr/share/emaki/shell/SoundSettingsPage.qml
  /usr/share/emaki/shell/SoundSettingsBackend.qml
zone: package
state: emaki-shell call system status
change: Open Sound to choose devices and change volume, mute or stream levels through the native backend.
never: Write device configuration files, start another mixer service or keep microphone monitoring after both views close.
rollback: Use row reset for automatic device policy, 100 percent level and unmuted state; restore the package for code.
```

## settings-startup-apps

Manages only owned personal autostart desktop entries; default applications remain in the shared core.

- Files: `/usr/bin/emaki-settings-apps`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: `emaki-settings-apps status --json` (prints JSON)
- Change: Use add DESKTOP-ID, set DESKTOP-ID true|false or reset DESKTOP-ID, each with --json.
- Never: Modify system entries, follow redirected personal paths or execute entries while listing them.
- Rollback: Reset the owned entry or remove an added entry; use settings history for defaults.* changes.

```system-map
component: settings-startup-apps
what: Manages only owned personal autostart desktop entries; default applications remain in the shared core.
files:
  /usr/bin/emaki-settings-apps
zone: package
state: emaki-settings-apps status --json
change: Use add DESKTOP-ID, set DESKTOP-ID true|false or reset DESKTOP-ID, each with --json.
never: Modify system entries, follow redirected personal paths or execute entries while listing them.
rollback: Reset the owned entry or remove an added entry; use settings history for defaults.* changes.
```

## settings-system-pages

Power, startup apps, region, system information and maintenance pages use fixed providers and the shared settings core.

- Files: `/usr/share/emaki/shell/SettingsPowerPage.qml`, `/usr/share/emaki/shell/SettingsAppsPage.qml`, `/usr/share/emaki/shell/SettingsRegionPage.qml`, `/usr/share/emaki/shell/SettingsAboutPage.qml`, `/usr/share/emaki/shell/SettingsMaintenancePage.qml`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: `emaki-shell call settings status` (prints JSON)
- Change: Open battery, apps, region, about, updates or lock through Settings; use each supported row.
- Never: Write machine configuration from QML, upload diagnostic reports or expose unfinished recovery actions.
- Rollback: Reset the affected provider or core setting; restore the previous package for page code.

```system-map
component: settings-system-pages
what: Power, startup apps, region, system information and maintenance pages use fixed providers and the shared settings core.
files:
  /usr/share/emaki/shell/SettingsPowerPage.qml
  /usr/share/emaki/shell/SettingsAppsPage.qml
  /usr/share/emaki/shell/SettingsRegionPage.qml
  /usr/share/emaki/shell/SettingsAboutPage.qml
  /usr/share/emaki/shell/SettingsMaintenancePage.qml
zone: package
state: emaki-shell call settings status
change: Open battery, apps, region, about, updates or lock through Settings; use each supported row.
never: Write machine configuration from QML, upload diagnostic reports or expose unfinished recovery actions.
rollback: Reset the affected provider or core setting; restore the previous package for page code.
```

## settings-wallpaper

Offers packaged and personal wallpapers plus a portal chooser; wallpaper preferences use the shared settings history.

- Files: `/usr/share/emaki/shell/SettingsWallpaperPage.qml`, `/usr/share/emaki/shell/helpers/wallpaper_chooser.py`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: `emaki settings get appearance.wallpaper --json` (prints JSON)
- Change: Select a wallpaper in Settings or use emaki settings set appearance.wallpaper PATH --json.
- Never: Write personal compositor files, offer frame sheets as complete wallpapers or save a cancelled selection.
- Rollback: Reset appearance.wallpaper or undo its history entry; dismissing the chooser changes nothing.

```system-map
component: settings-wallpaper
what: Offers packaged and personal wallpapers plus a portal chooser; wallpaper preferences use the shared settings history.
files:
  /usr/share/emaki/shell/SettingsWallpaperPage.qml
  /usr/share/emaki/shell/helpers/wallpaper_chooser.py
zone: package
state: emaki settings get appearance.wallpaper --json
change: Select a wallpaper in Settings or use emaki settings set appearance.wallpaper PATH --json.
never: Write personal compositor files, offer frame sheets as complete wallpapers or save a cancelled selection.
rollback: Reset appearance.wallpaper or undo its history entry; dismissing the chooser changes nothing.
```

## settings-wireless-password

Provides narrowly authorized read-only access to saved WPA personal passwords through the existing authentication prompt.

- Files: `/usr/libexec/emaki/emaki-wifi-password`, `/usr/share/polkit-1/actions/org.emaki.wifi-password.policy`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: `emaki-settings-network status --json` (prints JSON)
- Change: Reveal a saved password in Wi-Fi Settings; the fixed helper returns JSON only after administrator authentication.
- Never: Use broad privileged commands, log passwords or retain revealed text after navigation.
- Rollback: Restore the previous package; revealing a password changes no durable state.

```system-map
component: settings-wireless-password
what: Provides narrowly authorized read-only access to saved WPA personal passwords through the existing authentication prompt.
files:
  /usr/libexec/emaki/emaki-wifi-password
  /usr/share/polkit-1/actions/org.emaki.wifi-password.policy
zone: package
state: emaki-settings-network status --json
change: Reveal a saved password in Wi-Fi Settings; the fixed helper returns JSON only after administrator authentication.
never: Use broad privileged commands, log passwords or retain revealed text after navigation.
rollback: Restore the previous package; revealing a password changes no durable state.
```

## shell-runtime

The Quickshell runtime and its QML modules execute the Emaki shell and installer windows. Its recorded Qt build version is checked by the separate runtime-compatibility component.

- Files: `/usr/bin/qs`, `/usr/bin/quickshell`, `/usr/lib/qt6/qml/Quickshell/**`, `/usr/share/applications/org.quickshell.desktop`, `/usr/share/icons/hicolor/scalable/apps/org.quickshell.svg`, `/usr/share/licenses/quickshell-emaki/LICENSE`, `/usr/share/quickshell-emaki/qt-build-version`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: none yet: no installed JSON command reports runtime ABI and module integrity; `emaki-qt-check` provides a text compatibility check.
- Change: On Arch, update quickshell-emaki and Qt through a full package transaction. Restart the session when the update manager requests it.
- Never: Mix runtime modules and Qt libraries from different package versions; bypass the ABI check; patch installed QML module metadata to hide a missing dependency.
- Rollback: For the shell runtime and Qt libraries together, follow snapshot-recovery with an available pre-update btrfs root snapshot. Personal shell state in /home remains unchanged. Ext4 has no snapshot rollback.

```system-map
component: shell-runtime
what: The Quickshell runtime and its QML modules execute the Emaki shell and installer windows. Its recorded Qt build version is checked by the separate runtime-compatibility component.
files:
  /usr/bin/qs
  /usr/bin/quickshell
  /usr/lib/qt6/qml/Quickshell/**
  /usr/share/applications/org.quickshell.desktop
  /usr/share/icons/hicolor/scalable/apps/org.quickshell.svg
  /usr/share/licenses/quickshell-emaki/LICENSE
  /usr/share/quickshell-emaki/qt-build-version
zone: package
state: none yet: no installed JSON command reports runtime ABI and module integrity; `emaki-qt-check` provides a text compatibility check.
change: On Arch, update quickshell-emaki and Qt through a full package transaction. Restart the session when the update manager requests it.
never: Mix runtime modules and Qt libraries from different package versions; bypass the ABI check; patch installed QML module metadata to hide a missing dependency.
rollback: For the shell runtime and Qt libraries together, follow snapshot-recovery with an available pre-update btrfs root snapshot. Personal shell state in /home remains unchanged. Ext4 has no snapshot rollback.
```

## snapshot-boot-menu

Keeps the boot menu's list of snapshots bootable. After each package transaction a hook checks the grub-btrfs menu with Emaki's renderer and publishes only a validated fragment to /boot/grub/grub-btrfs.cfg. After boot, emaki-snapshot-menu.service runs `emaki-boot-refresh --repair-snapshots`, which rebuilds the fragment only when it is missing, empty or names snapshots that no longer exist; a healthy boot writes nothing. The same emaki-boot-refresh program also refreshes the Emaki boot loader.

- Files: `/usr/bin/emaki-boot-refresh`, `/usr/bin/emaki-snapshot-menu-check`, `/usr/lib/systemd/system/emaki-snapshot-menu.service`, `/usr/lib/systemd/system/multi-user.target.wants/emaki-snapshot-menu.service`, `/usr/share/libalpm/hooks/91-emaki-snapshot-menu.hook`, `/usr/share/libalpm/scripts/emaki-snapshot-menu`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: none yet: emaki-boot-refresh has no read-only status mode. Read `systemctl status emaki-snapshot-menu.service` and `journalctl -b -u emaki-snapshot-menu.service` (text) and /var/log/emaki-boot-refresh.log.
- Change: The files change only with an emaki-config update. A snapshot menu is rebuilt by the next package transaction or the next boot; snapper settings belong to the machine (/etc/snapper/configs/root).
- Never: Edit /boot/grub/grub-btrfs.cfg or the files under /boot/grub/.emaki-snapshots by hand; run grub-mkconfig or grub-install by hand; delete snapshots under /.snapshots to repair the menu.
- Rollback: Try another installed kernel or follow snapshot-recovery with an available pre-update btrfs root snapshot. The loader keeps the last booted-good generation, but a root snapshot does not restore /efi or a separate /boot. Ext4 has no snapshot rollback.

```system-map
component: snapshot-boot-menu
what: Keeps the boot menu's list of snapshots bootable. After each package transaction a hook checks the grub-btrfs menu with Emaki's renderer and publishes only a validated fragment to /boot/grub/grub-btrfs.cfg. After boot, emaki-snapshot-menu.service runs `emaki-boot-refresh --repair-snapshots`, which rebuilds the fragment only when it is missing, empty or names snapshots that no longer exist; a healthy boot writes nothing. The same emaki-boot-refresh program also refreshes the Emaki boot loader.
files:
  /usr/bin/emaki-boot-refresh
  /usr/bin/emaki-snapshot-menu-check
  /usr/lib/systemd/system/emaki-snapshot-menu.service
  /usr/lib/systemd/system/multi-user.target.wants/emaki-snapshot-menu.service
  /usr/share/libalpm/hooks/91-emaki-snapshot-menu.hook
  /usr/share/libalpm/scripts/emaki-snapshot-menu
zone: package
state: none yet: emaki-boot-refresh has no read-only status mode. Read `systemctl status emaki-snapshot-menu.service` and `journalctl -b -u emaki-snapshot-menu.service` (text) and /var/log/emaki-boot-refresh.log.
change: The files change only with an emaki-config update. A snapshot menu is rebuilt by the next package transaction or the next boot; snapper settings belong to the machine (/etc/snapper/configs/root).
never: Edit /boot/grub/grub-btrfs.cfg or the files under /boot/grub/.emaki-snapshots by hand; run grub-mkconfig or grub-install by hand; delete snapshots under /.snapshots to repair the menu.
rollback: Try another installed kernel or follow snapshot-recovery with an available pre-update btrfs root snapshot. The loader keeps the last booted-good generation, but a root snapshot does not restore /efi or a separate /boot. Ext4 has no snapshot rollback.
```

## snapshot-recovery

Reports whether the current root is normal, a snapshot or a pending recovery, and provides the authenticated path to keep a selected snapshot. Snapshots cover only the btrfs root subvolume; ext4 has none. /home, separate mounts and subvolumes, a separate /boot and /efi are excluded. The installer's /boot is inside root and is included. Recovery reverts the whole root, including its package database and configuration, not only the component being repaired.

- Files: `/usr/bin/emaki-rollback`, `/usr/share/polkit-1/actions/org.emaki.rollback.policy`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: `emaki-rollback status --json` (prints JSON)
- Change: First read `emaki-rollback status --json`; `unsupported` means this root cannot use snapshot recovery. A supported root does not guarantee a pre-update snapshot exists. If one is available, choose it in the boot menu for a temporary recovery session. With the person's decision, use the desktop recovery prompt to keep it, then restart as requested to make recovery permanent.
- Never: Remove snapper, snap-pac or grub-btrfs; delete /.snapshots by hand; keep a snapshot without the person's decision; promise that root recovery restores home files.
- Rollback: Keep the recovery receipt and previous-root entry until the recovered system is verified. Use the boot menu for another available root and restore home files from separate backups.

```system-map
component: snapshot-recovery
what: Reports whether the current root is normal, a snapshot or a pending recovery, and provides the authenticated path to keep a selected snapshot. Snapshots cover only the btrfs root subvolume; ext4 has none. /home, separate mounts and subvolumes, a separate /boot and /efi are excluded. The installer's /boot is inside root and is included. Recovery reverts the whole root, including its package database and configuration, not only the component being repaired.
files:
  /usr/bin/emaki-rollback
  /usr/share/polkit-1/actions/org.emaki.rollback.policy
zone: package
state: emaki-rollback status --json
change: First read `emaki-rollback status --json`; `unsupported` means this root cannot use snapshot recovery. A supported root does not guarantee a pre-update snapshot exists. If one is available, choose it in the boot menu for a temporary recovery session. With the person's decision, use the desktop recovery prompt to keep it, then restart as requested to make recovery permanent.
never: Remove snapper, snap-pac or grub-btrfs; delete /.snapshots by hand; keep a snapshot without the person's decision; promise that root recovery restores home files.
rollback: Keep the recovery receipt and previous-root entry until the recovered system is verified. Use the boot menu for another available root and restore home files from separate backups.
```

## system-map

This page: one section per Emaki component, with its files, their zone, how to ask its state, how to change it, what never to do and how to go back. Rendered from the Emaki sources; each section ends with the same facts as a fenced system-map block for programs.

- Files: `/usr/share/emaki/system-map.md`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: none yet: the page is Markdown; read it with `cat /usr/share/emaki/system-map.md`.
- Change: Only with an emaki-config update; each release renders it again.
- Never: Edit the page by hand or trust a copy of another release.
- Rollback: If a pre-update btrfs root snapshot exists, follow snapshot-recovery to recover the packaged map with its matching system version. Ext4 has no snapshot rollback.

```system-map
component: system-map
what: This page: one section per Emaki component, with its files, their zone, how to ask its state, how to change it, what never to do and how to go back. Rendered from the Emaki sources; each section ends with the same facts as a fenced system-map block for programs.
files:
  /usr/share/emaki/system-map.md
zone: package
state: none yet: the page is Markdown; read it with `cat /usr/share/emaki/system-map.md`.
change: Only with an emaki-config update; each release renders it again.
never: Edit the page by hand or trust a copy of another release.
rollback: If a pre-update btrfs root snapshot exists, follow snapshot-recovery to recover the packaged map with its matching system version. Ext4 has no snapshot rollback.
```

## updates

The update manager window, terminal update command, privileged update backend and service. The backend decides whether a restart or sign-out is needed and explains refused transactions.

- Files: `/usr/bin/emaki-update`, `/usr/bin/emaki-update-manager`, `/usr/lib/systemd/system/emaki-update.service`, `/usr/libexec/emaki/emaki-update-apply`, `/usr/libexec/emaki/emaki_update_*.py`, `/usr/libexec/emaki/update-manager-backend`, `/usr/libexec/emaki/update_catalog.py`, `/usr/share/applications/emaki-update-manager.desktop`, `/usr/share/emaki-update-manager/**`, `/usr/share/polkit-1/actions/org.emaki.update.policy`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: none yet: the window backend has a private streaming protocol, not an installed read-only JSON status command; use the update manager to inspect update state.
- Change: Open `emaki-update-manager` or use `emaki-update` for a full update. Let the backend perform the transaction and follow its restart or sign-out request.
- Never: Run concurrent package transactions; bypass snapshot or space checks; decide restart requirements from package names; use a partial upgrade to fix a dependency mismatch.
- Rollback: For the update backend and installed root package set together, follow snapshot-recovery with an available pre-update btrfs root snapshot. Personal state in /home and separate boot files are not restored. Ext4 has no snapshot rollback.

```system-map
component: updates
what: The update manager window, terminal update command, privileged update backend and service. The backend decides whether a restart or sign-out is needed and explains refused transactions.
files:
  /usr/bin/emaki-update
  /usr/bin/emaki-update-manager
  /usr/lib/systemd/system/emaki-update.service
  /usr/libexec/emaki/emaki-update-apply
  /usr/libexec/emaki/emaki_update_*.py
  /usr/libexec/emaki/update-manager-backend
  /usr/libexec/emaki/update_catalog.py
  /usr/share/applications/emaki-update-manager.desktop
  /usr/share/emaki-update-manager/**
  /usr/share/polkit-1/actions/org.emaki.update.policy
zone: package
state: none yet: the window backend has a private streaming protocol, not an installed read-only JSON status command; use the update manager to inspect update state.
change: Open `emaki-update-manager` or use `emaki-update` for a full update. Let the backend perform the transaction and follow its restart or sign-out request.
never: Run concurrent package transactions; bypass snapshot or space checks; decide restart requirements from package names; use a partial upgrade to fix a dependency mismatch.
rollback: For the update backend and installed root package set together, follow snapshot-recovery with an available pre-update btrfs root snapshot. Personal state in /home and separate boot files are not restored. Ext4 has no snapshot rollback.
```

## wallet-and-secrets

Starts the session wallet and migrates older secrets with per-item receipts under $XDG_STATE_HOME/emaki. Recovery is an explicit operation; diagnostic output and wallet material can contain private information.

- Files: `/usr/bin/emaki-keyring-recover`, `/usr/bin/emaki-wallet-*`, `/usr/lib/systemd/user/emaki-wallet-*.service`, `/usr/lib/systemd/user/niri.service.d/50-emaki-wallet.conf`, `/etc/xdg/kwalletrc`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: none yet: wallet migration receipts are JSON files but no installed command provides a read-only aggregate JSON state; inspect the user wallet service journal without exposing secrets.
- Change: Use the wallet prompts for unlock and `emaki-keyring-recover` for interactive recovery when needed. Package integration changes through emaki-config.
- Never: Delete a source wallet before verifying migration; print secret contents for routine diagnosis; reset or overwrite a personal wallet to fix a login problem.
- Rollback: Restore personal wallets from a separate secure backup; settings undo does not restore secrets. For packaged wallet integration, follow snapshot-recovery with an available pre-update btrfs root snapshot; wallets and migration receipts in /home remain unchanged. Ext4 has no snapshot rollback.

```system-map
component: wallet-and-secrets
what: Starts the session wallet and migrates older secrets with per-item receipts under $XDG_STATE_HOME/emaki. Recovery is an explicit operation; diagnostic output and wallet material can contain private information.
files:
  /usr/bin/emaki-keyring-recover
  /usr/bin/emaki-wallet-*
  /usr/lib/systemd/user/emaki-wallet-*.service
  /usr/lib/systemd/user/niri.service.d/50-emaki-wallet.conf
  /etc/xdg/kwalletrc
zone: package
state: none yet: wallet migration receipts are JSON files but no installed command provides a read-only aggregate JSON state; inspect the user wallet service journal without exposing secrets.
change: Use the wallet prompts for unlock and `emaki-keyring-recover` for interactive recovery when needed. Package integration changes through emaki-config.
never: Delete a source wallet before verifying migration; print secret contents for routine diagnosis; reset or overwrite a personal wallet to fix a login problem.
rollback: Restore personal wallets from a separate secure backup; settings undo does not restore secrets. For packaged wallet integration, follow snapshot-recovery with an available pre-update btrfs root snapshot; wallets and migration receipts in /home remain unchanged. Ext4 has no snapshot rollback.
```

## wallpaper

Packaged desktop artwork, wallpaper application and publication of the login background. The person selects a wallpaper through managed settings; publication services provide a copy readable by the greeter.

- Files: `/usr/bin/emaki-session-wallpaper`, `/usr/bin/emaki-settings-wallpaper`, `/usr/share/emaki/wallpaper/*`, `/usr/lib/systemd/user/emaki-greeter-wallpaper*`, `/usr/lib/systemd/user/graphical-session.target.wants/emaki-greeter-wallpaper*`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: `emaki settings get appearance.wallpaper --json` (prints JSON)
- Change: Use `emaki settings set appearance.wallpaper PATH` or the wallpaper controls that use the settings store. Packaged fallback artwork changes with emaki-config.
- Never: Replace packaged artwork to select a wallpaper; hand-edit the published greeter copy or grant the greeter access to the whole home directory.
- Rollback: Use `emaki settings undo ID` for the selection; restore a deleted personal image from its own backup. For packaged artwork, follow snapshot-recovery with an available pre-update btrfs root snapshot; personal images in /home remain unchanged. Ext4 has no snapshot rollback.

```system-map
component: wallpaper
what: Packaged desktop artwork, wallpaper application and publication of the login background. The person selects a wallpaper through managed settings; publication services provide a copy readable by the greeter.
files:
  /usr/bin/emaki-session-wallpaper
  /usr/bin/emaki-settings-wallpaper
  /usr/share/emaki/wallpaper/*
  /usr/lib/systemd/user/emaki-greeter-wallpaper*
  /usr/lib/systemd/user/graphical-session.target.wants/emaki-greeter-wallpaper*
zone: package
state: emaki settings get appearance.wallpaper --json
change: Use `emaki settings set appearance.wallpaper PATH` or the wallpaper controls that use the settings store. Packaged fallback artwork changes with emaki-config.
never: Replace packaged artwork to select a wallpaper; hand-edit the published greeter copy or grant the greeter access to the whole home directory.
rollback: Use `emaki settings undo ID` for the selection; restore a deleted personal image from its own backup. For packaged artwork, follow snapshot-recovery with an available pre-update btrfs root snapshot; personal images in /home remain unchanged. Ext4 has no snapshot rollback.
```

## wifi-recovery

Restarts a Wi-Fi adapter whose kernel scans keep failing. A root watcher, started by udev when a wireless device appears, rebinds only an idle, empty wireless PCI device or USB interface, with one three-attempt budget per boot kept in /run/emaki-wifi-recovery (root only). The panel's Restart Wi-Fi action runs the same helper through polkit. The state command's services object carries wifi_recovery_available, wifi_recovery_action and wifi_recovery_running; it answers only inside the desktop session.

- Files: `/usr/lib/emaki/emaki-wifi-recover`, `/usr/lib/systemd/system/emaki-wifi-recovery.service`, `/usr/lib/udev/rules.d/90-emaki-wifi-recovery.rules`, `/usr/share/polkit-1/actions/org.emaki.wifi-recovery.policy`
- Zone: package, installed by an Emaki package; never edit it, an update replaces it.
- State: `emaki-shell call system status` (prints JSON)
- Change: The files change only with an emaki-config update. To stop automatic recovery on one machine, `sudo systemctl mask emaki-wifi-recovery.service` (udev starts it even when disabled). Logs: `journalctl -b -t emaki-wifi-recovery`.
- Never: Edit or copy these files by hand; restart NetworkManager, unload wireless modules or toggle the radio instead of this helper; delete /run/emaki-wifi-recovery to reset the budget.
- Rollback: `sudo systemctl unmask emaki-wifi-recovery.service` undoes a mask. For the packaged recovery service, follow snapshot-recovery with an available pre-update btrfs root snapshot; root network profiles also revert. Ext4 has no snapshot rollback.

```system-map
component: wifi-recovery
what: Restarts a Wi-Fi adapter whose kernel scans keep failing. A root watcher, started by udev when a wireless device appears, rebinds only an idle, empty wireless PCI device or USB interface, with one three-attempt budget per boot kept in /run/emaki-wifi-recovery (root only). The panel's Restart Wi-Fi action runs the same helper through polkit. The state command's services object carries wifi_recovery_available, wifi_recovery_action and wifi_recovery_running; it answers only inside the desktop session.
files:
  /usr/lib/emaki/emaki-wifi-recover
  /usr/lib/systemd/system/emaki-wifi-recovery.service
  /usr/lib/udev/rules.d/90-emaki-wifi-recovery.rules
  /usr/share/polkit-1/actions/org.emaki.wifi-recovery.policy
zone: package
state: emaki-shell call system status
change: The files change only with an emaki-config update. To stop automatic recovery on one machine, `sudo systemctl mask emaki-wifi-recovery.service` (udev starts it even when disabled). Logs: `journalctl -b -t emaki-wifi-recovery`.
never: Edit or copy these files by hand; restart NetworkManager, unload wireless modules or toggle the radio instead of this helper; delete /run/emaki-wifi-recovery to reset the budget.
rollback: `sudo systemctl unmask emaki-wifi-recovery.service` undoes a mask. For the packaged recovery service, follow snapshot-recovery with an available pre-update btrfs root snapshot; root network profiles also revert. Ext4 has no snapshot rollback.
```
