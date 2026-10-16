# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
# Emaki — installation.
#
#   make prepare-core            fetch the locked Rust dependencies before an offline build
#   make build                   build the core and shaders (as your user, not root)
#   sudo make install            install system files (does not build; see “Build”)
#   sudo make enable-greeter     enable the login screen — a separate, explicit step
#   sudo make enable-services    enable networking (if unmanaged) and Bluetooth for the shell
#   sudo make uninstall          remove files
#   make render                  regenerate configs from tokens.toml
#   make install DESTDIR=/tmp/x  install into a separate root (used to build the package)
#   sudo make install-niri-emaki optional “niri (Emaki)” desktop on the niri fork
#
# Prerequisites (programs called by Emaki and build tools)
# are listed in one place: packaging/emaki-desktop/PKGBUILD (run makepkg -si there).
#
# File destinations:
#   scripts          $(PREFIX)/bin              not /usr/local: pacman does not write there
#   niri defaults    $(PREFIX)/share/emaki/niri
#   niri entry       /etc/niri/config.kdl       read until the user has their own config
#                                               ~/.config/niri/config.kdl (niri/system.kdl);
#                                               the niri package does not own /etc/niri
#   kitty, Qt themes $(PREFIX)/share/emaki/{kitty,qt6ct}
#   shell service    $(PREFIX)/lib/systemd/user/emaki-shell.service
#   fuzzel           $(PREFIX)/share/emaki/fuzzel   emaki-power passes it via --config
#   lock screen      /etc/xdg/hypr/hyprlock.conf    no package owns this file
#   lock screen PAM  /etc/pam.d/emaki-lock          owned by emaki-desktop (backup);
#                                               make install/uninstall leave it alone
#   cursors          $(PREFIX)/share/icons/Emaki    pixel xcursor theme (scripts/build-cursors)
#   login            $(PREFIX)/share/emaki/greetd   greetd/niri for Quickshell + ReGreet fallback
#                    /usr/lib/pam.d/emaki-greetd    dedicated PAM service (libpam reads /usr/lib/pam.d)
#                    $(PREFIX)/lib/systemd/system/greetd.service.d/emaki.conf
#                                                   greetd --config <our config>
#
# Installation never touches user files (~/.config/...). It also leaves other
# packages' files alone (directives 2 and 6): /etc/greetd/config.toml and /etc/pam.d/greetd
# belong to greetd, /etc/xdg/fuzzel to fuzzel.

.DEFAULT_GOAL := render

PREFIX  ?= /usr
DESTDIR ?=
DATADIR ?= $(PREFIX)/share/emaki
LIBEXECDIR ?= $(PREFIX)/libexec/emaki
LOCALSTATEDIR ?= /var/lib/emaki
# Development installs include the optional setup; the base package disables it.
INSTALL_SUDO_TURN_INTEGRATION ?= 1
EMAKI_PYTHON ?= $(PREFIX)/bin/python3

# Other packages' files and system defaults: where the host installs them, not under PREFIX.
EMAKI_XKB_RULES  ?= /usr/share/X11/xkb/rules/evdev.lst
EMAKI_PAM_KWALLET_INIT ?= /usr/lib/pam_kwallet_init
EMAKI_XDG_DATA_DIRS_DEFAULT ?= /usr/local/share:/usr/share
EMAKI_TRUSTED_PATH ?= /usr/bin
EMAKI_QS ?= /usr/bin/qs
EMAKI_QMLDIR ?= /usr/lib/qt6/qml
EMAKI_QT_PLUGINSDIR ?= /usr/lib/qt6/plugins

PATH_ARGS = --prefix "$(PREFIX)" --datadir "$(DATADIR)" --libexecdir "$(LIBEXECDIR)" \
	--xkb-rules "$(EMAKI_XKB_RULES)" --pam-kwallet-init "$(EMAKI_PAM_KWALLET_INIT)" \
	--xdg-data-dirs-default "$(EMAKI_XDG_DATA_DIRS_DEFAULT)" \
	--trusted-path "$(EMAKI_TRUSTED_PATH)" \
	--localstatedir "$(LOCALSTATEDIR)" --python "$(EMAKI_PYTHON)" --qs "$(EMAKI_QS)" \
	--qmldir "$(EMAKI_QMLDIR)" --qt-pluginsdir "$(EMAKI_QT_PLUGINSDIR)"

BIN     = $(DESTDIR)$(PREFIX)/bin
SHARE   = $(DESTDIR)$(DATADIR)
XDG     = $(DESTDIR)/etc/xdg
ICONS   = $(DESTDIR)$(PREFIX)/share/icons
SYSTEMD = $(DESTDIR)$(PREFIX)/lib/systemd
# The vendor PAM directory is compiled into libpam (/usr/lib/pam.d on Arch),
# so it does not depend on PREFIX.
PAMDIR  = $(DESTDIR)/usr/lib/pam.d
BOOTLIB = $(DESTDIR)$(PREFIX)/lib/emaki/boot/emaki_boot
BOOT_SHARED = boot errors grub_screen secureboot
BOOT_ARTWORK = $(wildcard installer/emaki_installer/grub_artwork/*/*.png)
GRUB_THEME_SCALES = 100 150 200 300
GRUB_THEME_FILES = $(wildcard grub/theme/theme-*.txt grub/theme/*.pf2)
# Startup logo: a Plymouth two-step theme. Plymouth reads themes only from its own fixed
# directory, whatever PREFIX is (like the initcpio hooks below).
PLYMOUTH_THEME = $(DESTDIR)/usr/share/plymouth/themes/emaki
PLYMOUTH_THEME_FILES = boot/plymouth/emaki.plymouth $(wildcard boot/plymouth/*.png)
# niri only looks here for the system config (src/main.rs); PREFIX does not affect the path.
NIRI_ETC = $(DESTDIR)/etc/niri/config.kdl
# Header string from niri/system.kdl: install and uninstall use it to recognize our file
# and leave any other /etc/niri/config.kdl alone.
NIRI_ETC_MARK = Emaki — system niri config

SCRIPTS = scripts/emaki-boot-order scripts/emaki-settings-network scripts/emaki-settings-wallpaper scripts/emaki-wallet-stage scripts/emaki-wallet-start scripts/emaki-keyring-recover scripts/emaki-qt-check scripts/emaki-autostart scripts/emaki-wallet-migrate scripts/emaki-boot-refresh scripts/emaki-migrate-installer-config scripts/emaki-rollback scripts/emaki-drm-hold scripts/emaki-greeter-compositor scripts/emaki-greeter-run scripts/emaki-session-import-environment scripts/emaki-idle scripts/emaki-config-path scripts/emaki-power scripts/emaki-shell scripts/emaki-shell-trusted scripts/emaki-lock scripts/emaki-greeter-provision scripts/emaki-session-wallpaper scripts/emaki-sleep-guard scripts/emaki-text-session scripts/emaki-shell-health scripts/emaki-settings-power scripts/emaki-settings-apps scripts/emaki-settings-about scripts/emaki-settings-storage scripts/emaki-settings-privacy
# Shell: QML, helpers, shader — copied as is to $(SHARE)/shell. Core: release binary
# from `make build` (prefix/datadir paths are compiled in; see CORE_ENV below).
SHELL_SRC = $(shell find shell -type f ! -path '*/__pycache__/*' ! -name '*.pyc' ! -name '*.pyo')
SCRIPTS += scripts/emaki-update-boot
SCRIPTS += scripts/emaki-terminal scripts/emaki-session-files scripts/emaki-session-update scripts/emaki-update
SCRIPTS += scripts/emaki-transaction-inhibit scripts/emaki-shell-generations
# Machine settings (keyboard, time zone, locale) through localed and timedated.
SCRIPTS += scripts/emaki-machine-settings
# Users: the accounts of the people on this computer through AccountsService.
SCRIPTS += scripts/emaki-settings-users
SCRIPTS += scripts/emaki-report
QSB      ?= $(or $(shell command -v qsb),/usr/lib/qt6/bin/qsb)
CORE_BIN  = $(CARGO_TARGET_DIR)/release/emaki
CAPTURE_BUILD ?= $(CURDIR)/.cache/capture-build
CAPTURE_BIN = $(CAPTURE_BUILD)/emaki-capture-recorder
SUDO_BUILD = $(CURDIR)/.cache/sudo-build
SUDO_SRC = $(wildcard sudo/*.cpp sudo/*.hpp sudo/*.in) sudo/CMakeLists.txt sudo/qmldir sudo/plugins.qmltypes
SUDO_ARTIFACTS = $(SUDO_BUILD)/pam_emaki_sudo.so $(SUDO_BUILD)/emaki-sudo-broker $(SUDO_BUILD)/emaki-sudo $(SUDO_BUILD)/qml/Emaki/Sudo/libemakisudoplugin.so $(SUDO_BUILD)/emaki-sudo-pam $(SUDO_BUILD)/emaki-sudo.service $(SUDO_BUILD)/emaki-sudo-cleanup.service

.PHONY: paths
paths:
	python3 scripts/render-paths $(PATH_ARGS) --source "$(CURDIR)"

.PHONY: render install uninstall check check-assumptions check-deps build

render: paths
	./scripts/render-theme
	python scripts/render-shell-palette
	python3 scripts/render-kde-theme
	python3 scripts/render-system-map

check: paths check-assumptions check-core
	python3 tests/test-rust-notices.py
	python3 tests/test-shim-notices.py
	python3 tests/test-licence-headers.py
	python3 tests/test-quickshell-fork.py
	python3 tests/test-quickshell-fork-guard.py
	python3 tests/test-dispersion.py
	python3 tests/test-backdrop-first-frame.py
	node tests/test-appearance.mjs
	node tests/test-appearance-icons.mjs
	node tests/test-switcher-logic.mjs
	node tests/test-switcher-animation.mjs
	python3 tests/test-theme-palette.py
	python3 tests/test-kitty-look.py
	python3 tests/test-report-icon.py
	python3 tests/test-shared-canvas.py --static
	python3 tests/test-capture.py
	python3 tests/test-capture-native.py
	python3 tests/test-capture-import.py
	python3 tests/test-capture-controller.py
	python3 tests/test-capture-ui.py
	python3 tests/test-sudo-broker.py
	python3 tests/test-sudo-prompt.py
	python3 tests/test-sudo-packaging.py
	python3 tests/test-sudo-turn-integration.py
	python3 tests/sudo-key-vm.py --self-test
	python3 tests/test-settings-terminal.py
	python3 tests/test-settings-wallpaper.py
	python3 tests/test-publish-source-additions.py
	python3 tests/test-settings-apps.py
	python3 tests/test-settings-terminal.py
	python3 tests/test-settings-wallpaper.py
	python3 tests/test-settings-about.py
	python3 tests/test-report-redaction.py
	python3 tests/test-report-redaction-corpus.py
	python3 tests/test-report.py
	python3 tests/test-settings-storage.py
	python3 tests/test-storage-arch.py
	python3 tests/test-package-lock.py
	python3 tests/test-settings-privacy.py
	python3 tests/test-privacy-recovery.py
	python3 tests/test-privacy-portals.py
	python3 tests/test-settings-power.py
	python3 tests/test-settings-channel.py
	python3 tests/test-paths.py
	python3 tests/test-polkit-lock-state.py
	python3 tests/test-polkit-prompt.py
	python3 tests/test-polkit-details.py
	python3 tests/test-prompt-queue.py
	python3 tests/test-prompt-morph.py
	python3 tests/test-launch-handoff.py
	python3 tests/check-portability.py --self-test
	python3 tests/check-portability.py
	python3 tests/check-system-map.py --self-test
	python3 tests/check-system-map.py
	python3 tests/test-publish-acceptance.py
	python3 tests/test-iso-boot-map.py
	python3 tests/test-alongside-boot-wait.py
	python3 tests/test-alongside-docs.py
	python3 tests/test-wifi-recovery.py
	python3 tests/test-machine-settings.py
	python3 tests/test-settings-users.py
	python3 tests/test-settings-users-home.py
	python3 tests/test-settings-users-root.py
	python3 update-manager/tests/test_updates.py
	python3 update-manager/tests/test_flatpaks.py
	python3 update-manager/tests/test_reminder.py
	python3 update-manager/tests/test_restore.py
	python3 update-manager/tests/test_protocol.py
	python3 update-manager/tests/test_programs.py
	node tests/test-programs-auth.mjs
	node tests/test-programs-logic.mjs
	python3 update-manager/tests/test_appstream.py
	python3 update-manager/tests/test_programs_engine.py
	python3 update-manager/tests/test_programs_report_log.py
	python3 update-manager/tests/test_programs_shelf.py
	python3 update-manager/tests/test_programs_install.py
	python3 update-manager/tests/test_programs_private_db.py
	python3 update-manager/tests/test_programs_flatpak.py
	python3 update-manager/tests/test_programs_flatpak_apply.py
	python3 update-manager/tests/test_programs_aur.py
	python3 update-manager/tests/test_programs_aur_build.py
	python3 update-manager/tests/test_programs_aur_pacman.py
	python3 update-manager/tests/test_programs_aur_consent.py
	python3 update-manager/tests/test_programs_aur_snapshot_cleanup.py
	python3 update-manager/tests/test_programs_snapshot_guard.py
	python3 update-manager/tests/test_programs_receipts.py
	python3 update-manager/tests/test_programs_reviews.py
	python3 update-manager/tests/test_programs_nvidia_pacman.py
	python3 update-manager/tests/mutants.py
	python3 tests/test-runtime-fixture.py
	python3 tests/test-vm-runtime.py
	python3 tests/test-wait-fixture.py
	python3 scripts/check-delivery.py
	python3 tests/test-delivery.py
	bash iso/preflight.sh
	python3 tests/test-iso-preflight.py
	python3 tests/test-qt-abi.py
	python3 tests/test-qmldir.py
	python3 tests/test-session-state.py
	python3 tests/test-displays.py
	python3 tests/test-displays-idle.py
	python3 tests/test-displays-mutants.py
	python3 tests/test-session-files.py
	python3 tests/test-shell-generations.py
	python3 tests/test-desktop-state.py
	python3 tests/test-session-update-notice.py
	python3 tests/test-session-update-hook.py
	python3 tests/test-update-errors.py
	python3 tests/test-auth-lockout.py
	python3 tests/test-shell-recovery.py
	python3 tests/test-trusted-shell-launch.py
	python3 tests/test-login-handoff.py
	python3 tests/test-shell-heartbeat.py
	python3 tests/test-lock-fallback-heartbeat.py
	python3 tests/test-lock-frozen-recovery.py
	python3 tests/test-application-environment.py
	python3 tests/test-shell-health.py
	python3 tests/test-shell-recovery-notice.py
	python3 tests/test-notification-store.py
	python3 tests/test-web-open.py
	python3 tests/test-report-window.py
	niri validate -c niri/default.kdl
	niri validate -c greetd/niri.kdl
	@if command -v niri-emaki >/dev/null 2>&1; then \
		niri-emaki validate -c niri/fork-rules.kdl; \
	else \
		echo 'Skipping fork-rules.kdl: niri-emaki is not installed (stock niri lacks fork-only nodes).'; \
	fi
	sh tests/test-niri-entry.sh
	python3 tests/test-autostart.py
	sh tests/test-power.sh
	python3 tests/test-uninstall.py
	python3 tests/test-zsh-defaults.py
	python3 tests/test-boot-refresh.py
	python3 tests/test-boot-order.py
	python3 tests/test-shared-esp.py
	python3 tests/test-update-boot.py
	python3 tests/test-sleep-lock.py
	python3 tests/test-update-menu.py
	python3 tests/test-update-usable.py
	python3 tests/test-boot-refresh-attacks.py
	python3 tests/test-boot-refresh-secureboot.py
	python3 tests/test-boot-slots.py
	python3 tests/test-boot-repair.py
	python3 tests/test-esp-records.py
	python3 tests/test-boot-delivery-guest.py
	python3 tests/test-boot-refresh-real.py
	python3 tests/test-grub-escape-real.py
	python3 tests/test-secureboot-tool.py
	node tests/test-secureboot-logic.mjs
	python3 tests/test-menu-font.py
	python3 tests/test-grub-menu-layout.py
	python3 tests/test-resume.py
	python3 tests/test-nvidia.py
	python3 tests/test-boot-splash.py
	python3 tests/test-plymouth-theme.py
	python3 tests/test-fetch.py
	python3 tests/test-cursors.py
	python3 tests/test-iso-check-env.py
	python3 tests/test-ci-forks.py ForkChecks
	python3 tests/test-iso-boot-health.py
	python3 tests/test-iso-shot.py
	python3 tests/test-boot-menu-check.py
	python3 tests/test-rollback-check.py
	python3 tests/test-encrypt-check.py
	python3 tests/test-install-progress-check.py
	python3 tests/test-release-notes.py
	python3 tests/test-status-words.py
	python3 tests/test-iso-install-harness.py
	python3 tests/test-iso-vm-owner.py
	python3 tests/test-windows-fixture.py
	python3 tests/test-windows-fixture-loader.py
	EMAKI_TEST_UNIX_SOCKET=1 PYTHONPATH=installer python3 -m unittest discover -s installer/tests
	python3 iso/test-offline-repo.py
	python3 tests/test-sleep-guard-unit.py
	python3 tests/test-sleep-guard.py
	sh tests/test-idle.sh
	python3 tests/test-keyboard-single-source.py
	python3 tests/test-desktop-bindings.py
	python3 tests/test-keyboard-defaults.py
	python3 tests/test-shell-smalls.py
	python3 tests/test-wallpaper-fallback.py
	python3 tests/test-wallpaper-policy.py
	python3 tests/test-reaper.py
	python3 tests/test-arch-watch.py
	python3 tests/test-vm-memory.py
	python3 tests/test-vm-secboot.py
	python3 tests/test-secureboot-release-key.py
	python3 tests/test-no-private-keys.py
	python3 tests/test-vm-suite-target.py
	python3 tests/test-vm-greeter-target.py
	python3 tests/test-vm-lock-target.py
	python3 tests/test-vm-glass-target.py
	python3 tests/test-clipboard-runtime.py
	python3 tests/test-live-session.py
	python3 tests/test-kdeconnect-autostart.py
	python3 tests/test-desktop-theme.py
	python3 tests/test-secret-service.py
	python3 tests/test-wallet-login.py
	python3 tests/test-wallet-stage.py
	python3 tests/test-keyring-recover.py

check-assumptions:
	python3 tests/test-fork-assumptions.py
	python3 tests/test-lock-capture-assumptions.py

# Every metapackage dependency is in the official repositories (requires the pacman -Sy database).
check-deps:
	bash tests/test-desktop-deps.sh

# The installer window: lint, helpers, protocol, controller over a real unix socket,
# pointer/key interactions and offscreen renders.
.PHONY: check-installer check-iso check-updates check-all
check-installer: paths
	python3 installer/assets/grub/generate-screens.py --check
	python3 grub/theme/generate-theme.py --check
	python3 tests/test-grub-artwork.py
	python3 installer/ui/tests/check.py
	python3 -m unittest discover -s installer/ui/tests -p 'test_*.py'
	node installer/ui/tests/test-protocol.js
	node installer/ui/tests/test-timezones.js
	python3 installer/ui/tests/map.py
	python3 installer/ui/tests/handshake.py
	python3 installer/ui/tests/controller.py --unix
	python3 installer/ui/tests/interactions.py
	python3 installer/ui/tests/render.py

# Static ISO profile checks and the offline tests of the release walk tools (no VM).
check-iso:
	bash iso/check.sh
	python3 tests/test-installed-defaults.py
	python3 tests/test-frame-assessment.py
	python3 tests/test-rollback-check.py
	python3 tests/vm/eyes/test_eyes.py
	python3 tests/test-release-gate.py
	python3 tests/test-graphics-fallback-check.py

# The package mirror: publisher, R2 client, pointer Worker, key-backup check, upgrade-check
# helpers (no network, no Cloudflare; see docs/mirror.md).
check-updates:
	python3 tests/test-transaction-inhibit.py
	python3 tests/test-update-space.py
	python3 tests/test-update-space-alpm.py
	python3 tests/test-upkeep-transactions.py
	python3 tests/test-apps-removal.py
	python3 tests/test-update-pins.py
	python3 tests/test-upkeep.py
	python3 tests/test-system-migrate.py
	python3 tests/test-boot-defaults.py
	python3 tests/test-initramfs-refresh.py
	python3 tests/test-initramfs-native.py
	python3 tests/test-mirror-refresh.py
	python3 tests/test-discover-autostart.py
	python3 tests/test-rollback-holds.py
	python3 tests/test-update-channel.py
	python3 tests/test-channel-transactions.py
	python3 tests/test-legacy-app-notice.py
	python3 tests/test-snapshot-menu.py
	python3 tests/test-snapshot-repair.py
	python3 tests/test-channel-detection.py
	python3 tests/test-arch-snapshot.py
	python3 tests/test-qt-runtime-pins.py
	python3 tests/test-arch-source-fallback.py
	python3 tests/test-historical-sources.py
	python3 tests/test-cargo-sources.py
	python3 tests/test-go-sources.py
	python3 tests/test-source-routes.py
	python3 tests/test-source-keys.py
	python3 tests/test-savannah-git.py
	python3 tests/test-unused-sources.py
	python3 tests/test-iso-sources.py
	python3 tests/test-audit-sources.py
	python3 tests/test-collect-sources.py
	python3 tests/test-iso-source-signatures.py
	python3 tests/test-source-archives.py
	python3 tests/test-release-inputs.py
	python3 tests/test-publish.py
	python3 tests/test-publish-immutability.py
	python3 tests/test-publish-transactions.py
	python3 tests/test-publish-iso-transactions.py
	python3 tests/test-publish-source-additions.py
	python3 tests/test-publish-exceptions.py
	python3 tests/test-r2-client.py
	python3 tests/test-upgrade-check.py
	node tests/test-pointer-worker.mjs
	bash tests/test-key-backup.sh

# Everything a release candidate passes on the host before any VM job (tests/vm/release-gate.sh).
check-all: check check-deps check-shell check-installer check-iso check-updates check-leaks

.PHONY: install-upkeep
install-upkeep:
	install -Dm644 upkeep/00-emaki-update-space.hook $(DESTDIR)$(PREFIX)/share/libalpm/hooks/00-emaki-update-space.hook
	sed -i 's|@PREFIX@|$(PREFIX)|g' $(DESTDIR)$(PREFIX)/share/libalpm/hooks/00-emaki-update-space.hook
	install -Dm755 upkeep/emaki-update-space $(DESTDIR)$(PREFIX)/share/libalpm/scripts/emaki-update-space
	sed -i -e 's|@LIBEXECDIR@|$(LIBEXECDIR)|g' -e 's|@PYTHON@|$(EMAKI_PYTHON)|g' $(DESTDIR)$(PREFIX)/share/libalpm/scripts/emaki-update-space
	install -Dm644 update-manager/emaki_update_space.py $(DESTDIR)$(LIBEXECDIR)/emaki_update_space.py
	install -Dm644 update-manager/emaki_package_lock.py $(DESTDIR)$(LIBEXECDIR)/emaki_package_lock.py
	for hook in 01-emaki-config-release 02-emaki-config-remove; do install -Dm644 upkeep/$$hook.hook $(DESTDIR)$(PREFIX)/share/libalpm/hooks/$$hook.hook; done
	install -Dm644 upkeep/99-emaki-qt-check.hook $(DESTDIR)$(PREFIX)/share/libalpm/hooks/99-emaki-qt-check.hook
	install -Dm755 upkeep/emaki-rollback-holds $(DESTDIR)$(PREFIX)/share/libalpm/scripts/emaki-rollback-holds
	install -Dm644 upkeep/00-emaki-package-lock.hook $(DESTDIR)$(PREFIX)/share/libalpm/hooks/00-emaki-package-lock.hook
	sed -i 's|@PREFIX@|$(PREFIX)|g' $(DESTDIR)$(PREFIX)/share/libalpm/hooks/00-emaki-package-lock.hook
	install -Dm644 upkeep/00-emaki-rollback-holds.hook $(DESTDIR)$(PREFIX)/share/libalpm/hooks/00-emaki-rollback-holds.hook
	install -Dm644 upkeep/99-emaki-rollback-holds.hook $(DESTDIR)$(PREFIX)/share/libalpm/hooks/99-emaki-rollback-holds.hook
	install -Dm755 upkeep/emaki-snapshot-policy $(DESTDIR)$(PREFIX)/share/libalpm/scripts/emaki-snapshot-policy
	install -Dm755 upkeep/emaki-initramfs-refresh $(DESTDIR)$(PREFIX)/share/libalpm/scripts/emaki-initramfs-refresh
	install -Dm644 upkeep/89-emaki-system-migrate.hook $(DESTDIR)$(PREFIX)/share/libalpm/hooks/89-emaki-system-migrate.hook
	install -Dm644 upkeep/93-emaki-initramfs-refresh.hook $(DESTDIR)$(PREFIX)/share/libalpm/hooks/93-emaki-initramfs-refresh.hook
	install -Dm755 upkeep/emaki-system-migrate $(DESTDIR)$(PREFIX)/share/libalpm/scripts/emaki-system-migrate
	sed -i 's|@EMAKI_LIBEXECDIR@|$(LIBEXECDIR)|g' $(DESTDIR)$(PREFIX)/share/libalpm/scripts/emaki-system-migrate
	install -Dm755 upkeep/emaki_boot_defaults.py $(DESTDIR)$(PREFIX)/share/libalpm/scripts/emaki_boot_defaults.py
	install -Dm755 upkeep/emaki_initramfs.py $(DESTDIR)$(PREFIX)/share/libalpm/scripts/emaki_initramfs.py
	install -Dm644 upkeep/89-emaki-initramfs-begin.hook $(DESTDIR)$(PREFIX)/share/libalpm/hooks/89-emaki-initramfs-begin.hook
	for file in linux.preset linux-lts.preset grub-btrfs.conf; do install -Dm644 boot/defaults/$$file $(SHARE)/boot/$$file; done
	install -Dm644 upkeep/defaults/00-emaki-zram.conf $(SYSTEMD)/zram-generator.conf.d/00-emaki.conf
	install -dm750 $(DESTDIR)/etc/sudoers.d
	install -Dm440 upkeep/defaults/10-emaki-wheel $(DESTDIR)/etc/sudoers.d/10-emaki-wheel
	install -Dm440 upkeep/defaults/wheel $(SHARE)/defaults/wheel
	install -d $(DESTDIR)/etc/emaki/sudoers.d
	install -Dm755 upkeep/emaki-refresh-mirrors $(BIN)/emaki-refresh-mirrors
	for unit in emaki-refresh-mirrors.service emaki-refresh-mirrors.timer; do install -Dm644 systemd/$$unit $(SYSTEMD)/system/$$unit; done
	for unit in emaki-refresh-appstream.service emaki-refresh-appstream.timer; do install -Dm644 systemd/$$unit $(SYSTEMD)/system/$$unit; done

.PHONY: install-sudo-policy
# C archive order installs the /etc PAM stack before the selector. The vendor
# copy survives a deleted local override. A fail-closed pre-transaction hook drops
# the selector before package removal can remove either service copy.
install-sudo-policy:
	install -Dm644 packaging/emaki-config/00-emaki-sudo-remove.hook $(DESTDIR)$(PREFIX)/share/libalpm/hooks/00-emaki-sudo-remove.hook
	install -Dm644 $(SUDO_BUILD)/emaki-sudo-pam $(DESTDIR)/etc/pam.d/emaki-sudo
	install -Dm644 $(SUDO_BUILD)/emaki-sudo-pam $(PAMDIR)/emaki-sudo
	install -Dm440 sudo/20-emaki-pam $(DESTDIR)/etc/sudoers.d/20-emaki-pam

install: install-upkeep
	@if [ -e "$(XDG)/fastfetch/config.jsonc" ] && ! grep -qF 'Emaki fetch default.' "$(XDG)/fastfetch/config.jsonc"; then \
		echo 'make install: existing non-Emaki fastfetch system config; not overwriting.' >&2; exit 1; fi
	@if [ -e "$(NIRI_ETC)" ] && ! grep -qF "$(NIRI_ETC_MARK)" "$(NIRI_ETC)"; then \
		echo "make install: $(NIRI_ETC) already exists and is not from Emaki — refusing to overwrite." >&2; \
		echo "Move your settings to ~/.config/niri/config.kdl and remove this file, then retry." >&2; \
		exit 1; fi
	install -Dm644 polkit/org.emaki.rollback.policy $(DESTDIR)$(PREFIX)/share/polkit-1/actions/org.emaki.rollback.policy
	install -Dm755 scripts/emaki-wifi-recover $(DESTDIR)$(PREFIX)/lib/emaki/emaki-wifi-recover
	install -Dm644 polkit/org.emaki.wifi-recovery.policy $(DESTDIR)$(PREFIX)/share/polkit-1/actions/org.emaki.wifi-recovery.policy
	install -Dm644 scripts/users_home.py $(DESTDIR)$(LIBEXECDIR)/users_home.py
	install -Dm755 scripts/emaki-settings-users-root $(DESTDIR)$(LIBEXECDIR)/emaki-settings-users-root
	install -Dm644 polkit/org.emaki.settings-users.policy $(DESTDIR)$(PREFIX)/share/polkit-1/actions/org.emaki.settings-users.policy
	install -Dm755 scripts/emaki-wifi-password $(DESTDIR)$(LIBEXECDIR)/emaki-wifi-password
	install -Dm644 polkit/org.emaki.wifi-password.policy $(DESTDIR)$(PREFIX)/share/polkit-1/actions/org.emaki.wifi-password.policy
	install -Dm644 systemd/emaki-wifi-recovery.service $(SYSTEMD)/system/emaki-wifi-recovery.service
	install -Dm644 systemd/90-emaki-wifi-recovery.rules $(DESTDIR)$(PREFIX)/lib/udev/rules.d/90-emaki-wifi-recovery.rules
	install -Dm755 -t $(BIN) $(SCRIPTS)
	install -Dm755 update-manager/emaki-update-manager $(BIN)/emaki-update-manager
	install -Dm755 update-manager/backend.py $(DESTDIR)$(LIBEXECDIR)/update-manager-backend
	install -Dm644 update-manager/catalog.py $(DESTDIR)$(LIBEXECDIR)/update_catalog.py
	install -Dm644 update-manager/emaki_update_root.py $(DESTDIR)$(LIBEXECDIR)/emaki_update_root.py
	install -Dm644 update-manager/flatpaks.py $(DESTDIR)$(LIBEXECDIR)/update_flatpaks.py
	install -Dm644 update-manager/reminder.py $(DESTDIR)$(LIBEXECDIR)/update_reminder.py
	install -Dm755 update-manager/apply.py $(DESTDIR)$(LIBEXECDIR)/emaki-update-apply
	install -Dm755 update-manager/restore.py $(DESTDIR)$(LIBEXECDIR)/emaki-update-restore
	install -Dm755 update-manager/privacy_recovery.py $(DESTDIR)$(LIBEXECDIR)/emaki-privacy-recovery
	install -Dm644 update-manager/org.emaki.privacy.recovery.policy $(DESTDIR)$(PREFIX)/share/polkit-1/actions/org.emaki.privacy.recovery.policy
	install -Dm644 scripts/emaki_privacy_portals.py $(DESTDIR)$(LIBEXECDIR)/emaki_privacy_portals.py
	install -Dm644 update-manager/emaki-update.service $(SYSTEMD)/system/emaki-update.service
	install -Dm644 update-manager/emaki-restore-index.service $(SYSTEMD)/system/emaki-restore-index.service
	install -Dm644 update-manager/emaki-restore-index.timer $(SYSTEMD)/system/emaki-restore-index.timer
	install -Dm644 upkeep/zzz-emaki-restore-index.hook $(DESTDIR)$(PREFIX)/share/libalpm/hooks/zzz-emaki-restore-index.hook
	install -d $(SYSTEMD)/system/timers.target.wants
	ln -sfn ../emaki-restore-index.timer $(SYSTEMD)/system/timers.target.wants/emaki-restore-index.timer
	install -Dm644 update-manager/org.emaki.update.policy $(DESTDIR)$(PREFIX)/share/polkit-1/actions/org.emaki.update.policy
	install -Dm644 update-manager/org.emaki.update.restore.policy $(DESTDIR)$(PREFIX)/share/polkit-1/actions/org.emaki.update.restore.policy
	install -Dm644 update-manager/emaki-update-manager.desktop $(DESTDIR)$(PREFIX)/share/applications/emaki-update-manager.desktop
	install -Dm644 -t $(DESTDIR)$(PREFIX)/share/emaki-update-manager/ui update-manager/ui/*.qml update-manager/ui/*.js
	install -Dm644 update-manager/storefront.json $(DESTDIR)$(PREFIX)/share/emaki-update-manager/storefront.json
	install -Dm644 update-manager/emaki-shelf.json $(DESTDIR)$(PREFIX)/share/emaki-update-manager/emaki-shelf.json
	install -Dm644 update-manager/emaki-programs-updates.svg $(DESTDIR)$(PREFIX)/share/icons/hicolor/scalable/apps/emaki-programs-updates.svg
	install -Dm644 installer/emaki_installer/redaction.py $(DESTDIR)$(LIBEXECDIR)/emaki_redaction.py
	install -Dm644 installer/emaki_installer/update_errors.py $(DESTDIR)$(LIBEXECDIR)/emaki_update_errors.py
	install -Dm644 scripts/emaki_session_state.py $(DESTDIR)$(LIBEXECDIR)/emaki_session_state.py
	install -Dm644 scripts/emaki_session_arch.py $(DESTDIR)$(LIBEXECDIR)/emaki_session_arch.py
	install -Dm644 scripts/emaki_shell_tree.py $(DESTDIR)$(LIBEXECDIR)/emaki_shell_tree.py
	install -Dm644 scripts/emaki_lock_fallback.py $(DESTDIR)$(LIBEXECDIR)/emaki_lock_fallback.py
	install -Dm644 scripts/emaki_storage.py $(DESTDIR)$(LIBEXECDIR)/emaki_storage.py
	install -Dm644 scripts/emaki_report_redact.py $(DESTDIR)$(LIBEXECDIR)/emaki_report_redact.py
	install -Dm644 update-manager/emaki_package_lock.py $(BOOTLIB)/package_lock.py
	install -Dm644 update-manager/storage_arch.py $(DESTDIR)$(LIBEXECDIR)/storage_arch.py
	install -Dm644 polkit/org.emaki.storage.policy $(DESTDIR)$(PREFIX)/share/polkit-1/actions/org.emaki.storage.policy
	install -Dm755 update-manager/programs.py $(BIN)/emaki-programs
	install -Dm644 update-manager/programs_catalog.py $(DESTDIR)$(LIBEXECDIR)/programs_catalog.py
	install -Dm644 -t $(DESTDIR)$(LIBEXECDIR) update-manager/programs_appstream.py update-manager/programs_arch.py update-manager/programs_engine.py update-manager/programs_flatpak.py update-manager/programs_aur.py update-manager/programs_receipts.py update-manager/programs_reviews.py
	install -Dm644 update-manager/restore.py $(DESTDIR)$(LIBEXECDIR)/restore.py
	install -Dm755 update-manager/programs_remove.py $(DESTDIR)$(LIBEXECDIR)/emaki-programs-remove
	install -Dm755 update-manager/programs_flatpak_apply.py $(DESTDIR)$(LIBEXECDIR)/emaki-programs-flatpak
	install -Dm644 update-manager/org.emaki.programs.flatpak.policy $(DESTDIR)$(PREFIX)/share/polkit-1/actions/org.emaki.programs.flatpak.policy
	install -Dm644 installer/emaki_installer/graphics.py $(DESTDIR)$(LIBEXECDIR)/programs_graphics.py
	install -Dm755 update-manager/programs_install.py $(DESTDIR)$(LIBEXECDIR)/emaki-programs-install
	install -Dm755 update-manager/programs_aur_build.py $(DESTDIR)$(LIBEXECDIR)/emaki-programs-aur
	install -Dm755 update-manager/programs_undo.py $(DESTDIR)$(LIBEXECDIR)/emaki-programs-undo
	install -Dm644 update-manager/org.emaki.programs.aur.policy $(DESTDIR)$(PREFIX)/share/polkit-1/actions/org.emaki.programs.aur.policy
	install -Dm644 update-manager/org.emaki.programs.undo.policy $(DESTDIR)$(PREFIX)/share/polkit-1/actions/org.emaki.programs.undo.policy
	install -Dm644 update-manager/org.emaki.programs.policy $(DESTDIR)$(PREFIX)/share/polkit-1/actions/org.emaki.programs.policy
	install -Dm644 update-manager/org.emaki.programs.install.policy $(DESTDIR)$(PREFIX)/share/polkit-1/actions/org.emaki.programs.install.policy
	install -Dm644 update-manager/00-emaki-programs.rules $(DESTDIR)$(PREFIX)/share/polkit-1/rules.d/00-emaki-programs.rules
	install -Dm644 packaging/emaki-config/00-emaki-transaction-inhibit.hook $(DESTDIR)$(PREFIX)/share/libalpm/hooks/00-emaki-transaction-inhibit.hook
	rm -f $(BIN)/emaki-session-cover
	install -Dm755 $(CORE_BIN) $(BIN)/emaki
	for f in $(SHELL_SRC); do install -Dm644 "$$f" "$(SHARE)/$$f"; done
	install -Dm644 shell/emaki-welcome.desktop $(DESTDIR)$(PREFIX)/share/applications/emaki-welcome.desktop
	install -Dm644 shell/emaki-report.desktop $(DESTDIR)$(PREFIX)/share/applications/emaki-report.desktop
	install -Dm644 art/icons/emaki-welcome.svg $(ICONS)/hicolor/scalable/apps/emaki-welcome.svg
	install -Dm644 art/icons/emaki-report.svg $(ICONS)/hicolor/scalable/apps/emaki-report.svg
	install -Dm644 docs/ZONES.md $(DESTDIR)$(PREFIX)/share/doc/emaki/ZONES.md
	# Rendered from MANIFEST.md by `make render`; `make check` refuses a stale copy.
	install -Dm644 docs/system-map.md $(SHARE)/system-map.md
	install -Dm644 docs/agent-rules.md $(SHARE)/agent-rules.md
	install -Dm644 docs/agent-rules-setup.md $(SHARE)/agent-rules-setup.md
	# Cancellation now belongs to the attempt's existing greetd connection.
	rm -f "$(SHARE)/shell/helpers/greeter-cancel.py"
	# Dock glass shaders: built by `make build` in .cache/shell-shaders; only copied here.
	for f in $(SHADERS); do install -Dm644 "$$f" "$(SHARE)/shell/shaders/$$(basename "$$f")"; done
	install -Dm644 -t $(SHARE)/niri niri/default.kdl niri/theme.kdl niri/shell.kdl
	install -Dm644 niri/system.kdl $(NIRI_ETC)
	install -Dm644 -t $(SYSTEMD)/user systemd/emaki-shell.service systemd/emaki-shell-heal.service systemd/emaki-shell-recovery.service systemd/emaki-sleep-guard.service systemd/emaki-idle.service systemd/emaki-wallet-migrate.service systemd/emaki-wallet-start.service
	install -Dm644 -t $(SHARE)/kitty kitty/theme.conf
	install -Dm644 -t $(SHARE)/qt6ct qt6ct/emaki.conf
	install -Dm644 packaging/emaki-config/kdeglobals $(XDG)/kdeglobals
	install -Dm644 systemd/niri-wallet.conf $(SYSTEMD)/user/niri.service.d/50-emaki-wallet.conf
	install -Dm644 packaging/emaki-config/kwalletrc $(XDG)/kwalletrc
	install -Dm644 packaging/emaki-config/60-emaki-xdg.conf $(DESTDIR)$(PREFIX)/lib/environment.d/60-emaki-xdg.conf
	install -Dm644 packaging/emaki-config/org.kde.kdeconnect.daemon.desktop $(SHARE)/xdg/autostart/org.kde.kdeconnect.daemon.desktop
	install -Dm644 packaging/emaki-config/dolphinrc $(XDG)/dolphinrc
	install -Dm644 packaging/emaki-config/niri-portals.conf $(XDG)/xdg-desktop-portal/niri-portals.conf
	install -Dm644 packaging/emaki-config/mimeapps.list $(XDG)/mimeapps.list
	install -Dm644 packaging/emaki-config/emaki-applications.menu $(XDG)/menus/emaki-applications.menu
	install -Dm644 etc-skel/.config/qt6ct/qt6ct.conf $(XDG)/qt6ct/qt6ct.conf
	install -Dm644 -t $(SHARE)/fuzzel fuzzel/fuzzel.ini
	# swayosd removed 2026-09-27 (the shell provides OSD): remove its old installed style.
	rm -rf $(SHARE)/swayosd
	install -Dm644 -t $(XDG)/hypr hypr/hyprlock.conf
	install -Dm644 -t $(SHARE)/greetd greetd/config.toml greetd/niri.kdl greetd/graphics-failed.txt
	install -Dm644 greetd/data/wayland-sessions/niri.desktop $(SHARE)/greetd/data/wayland-sessions/niri.desktop
	install -Dm644 gtk/Emaki/gtk-3.0/gtk.css $(DESTDIR)$(PREFIX)/share/themes/Emaki/gtk-3.0/gtk.css
	install -Dm644 art/grub/background.png $(SHARE)/grub/background.png
	install -Dm644 grub/defaults.cfg $(SHARE)/grub/defaults.cfg
	install -Dm644 -t $(PLYMOUTH_THEME) $(PLYMOUTH_THEME_FILES)
	install -Dm644 boot/plymouth/NOTICE-Plymouth.txt $(DESTDIR)$(PREFIX)/share/licenses/emaki-config/NOTICE-Plymouth.txt
	# Turn the startup logo on: the plymouth hook (mkinitcpio drop-in) and "splash" (GRUB drop-in).
	install -Dm644 boot/plymouth/mkinitcpio.conf $(DESTDIR)/etc/mkinitcpio.conf.d/50-emaki-splash.conf
	install -Dm644 boot/plymouth/grub.cfg $(DESTDIR)/etc/default/grub.d/50-emaki-splash.cfg
	install -Dm644 installer/assets/grub/unlock-24.pf2 $(SHARE)/grub/unlock-24.pf2
	install -Dm644 -t $(SHARE)/grub/fonts grub/fonts/menu-24.pf2 grub/fonts/menu-32.pf2 grub/fonts/menu-46.pf2
	install -Dm644 art/grub/background.png $(SHARE)/grub/theme/background.png
	install -Dm644 -t $(SHARE)/grub/theme $(GRUB_THEME_FILES)
	for scale in $(GRUB_THEME_SCALES); do install -Dm644 -t $(SHARE)/grub/theme/$$scale grub/theme/$$scale/*.png; done
	install -Dm644 installer/assets/grub/LICENSE-DejaVu.txt $(DESTDIR)$(PREFIX)/share/licenses/emaki-config/LICENSE-DejaVu.txt
	install -Dm644 grub/theme/LICENSE-Noto.txt $(DESTDIR)$(PREFIX)/share/licenses/emaki-config/LICENSE-Noto.txt
	install -Dm644 -t $(BOOTLIB) grub/emaki_boot/__init__.py grub/emaki_boot/esp_records.py grub/emaki_boot/refresh.py grub/emaki_boot/shared.py grub/emaki_boot/update.py grub/emaki_boot/update_menu.py grub/emaki_boot/usable.py
	for module in $(BOOT_SHARED); do install -Dm644 "installer/emaki_installer/$$module.py" "$(BOOTLIB)/$$module.py"; done
	for picture in $(BOOT_ARTWORK); do install -Dm644 "$$picture" "$(BOOTLIB)/$${picture#installer/emaki_installer/}"; done
	install -Dm644 grub/00-emaki-alongside.hook $(DESTDIR)$(PREFIX)/share/libalpm/hooks/00-emaki-alongside.hook
	install -Dm644 systemd/emaki-boot-order.service $(SYSTEMD)/system/emaki-boot-order.service
	install -Dm644 grub/95-emaki-boot-refresh.hook $(DESTDIR)$(PREFIX)/share/libalpm/hooks/95-emaki-boot-refresh.hook
	for hook in 04-emaki-update-boot.hook 06-emaki-update-boot.hook; do install -Dm644 grub/$$hook $(DESTDIR)$(PREFIX)/share/libalpm/hooks/$$hook; done
	install -Dm755 systemd/emaki-boot-resume $(SYSTEMD)/system-sleep/emaki-boot-resume
	install -Dm644 systemd/emaki-boot-complete.service $(SYSTEMD)/system/emaki-boot-complete.service
	install -Dm644 systemd/emaki-boot-refresh.service $(SYSTEMD)/system/emaki-boot-refresh.service
	install -Dm644 systemd/emaki-snapshot-menu.service $(SYSTEMD)/system/emaki-snapshot-menu.service
	install -Dm644 systemd/emaki-update-boot.service $(SYSTEMD)/system/emaki-update-boot.service
	mkdir -p $(SYSTEMD)/system/multi-user.target.wants
	ln -sfn ../emaki-boot-complete.service $(SYSTEMD)/system/multi-user.target.wants/emaki-boot-complete.service
	ln -sfn ../emaki-boot-refresh.service $(SYSTEMD)/system/multi-user.target.wants/emaki-boot-refresh.service
	ln -sfn ../emaki-snapshot-menu.service $(SYSTEMD)/system/multi-user.target.wants/emaki-snapshot-menu.service
	ln -sfn ../emaki-update-boot.service $(SYSTEMD)/system/multi-user.target.wants/emaki-update-boot.service
	# Secure Boot: the machine tool, its pacman hooks, the mkinitcpio post hook (mkinitcpio's
	# own directory, fixed whatever PREFIX is), the boot-time status and the re-offer policy.
	install -Dm644 grub/emaki_boot/secureboot_tool.py $(BOOTLIB)/secureboot_tool.py
	install -Dm755 grub/emaki-secureboot $(BIN)/emaki-secureboot
	install -Dm644 -t $(DESTDIR)$(PREFIX)/share/libalpm/hooks grub/03-emaki-secureboot-check.hook grub/94-emaki-secureboot-sign.hook
	install -Dm755 initcpio/post/emaki-secureboot $(DESTDIR)/usr/lib/initcpio/post/emaki-secureboot
	install -Dm644 systemd/emaki-secureboot-status.service $(SYSTEMD)/system/emaki-secureboot-status.service
	ln -sfn ../emaki-secureboot-status.service $(SYSTEMD)/system/multi-user.target.wants/emaki-secureboot-status.service
	install -Dm644 polkit/org.emaki.secureboot.policy $(DESTDIR)$(PREFIX)/share/polkit-1/actions/org.emaki.secureboot.policy
	install -Dm644 initcpio/hooks/emaki-resume $(DESTDIR)/usr/lib/initcpio/hooks/emaki-resume
	install -Dm644 initcpio/install/emaki-resume $(DESTDIR)/usr/lib/initcpio/install/emaki-resume
	install -Dm644 grub/90-emaki-grub-title.hook $(DESTDIR)$(PREFIX)/share/libalpm/hooks/90-emaki-grub-title.hook
	install -Dm644 grub/91-emaki-snapshot-menu.hook $(DESTDIR)$(PREFIX)/share/libalpm/hooks/91-emaki-snapshot-menu.hook
	install -Dm755 grub/emaki-snapshot-menu $(DESTDIR)$(PREFIX)/share/libalpm/scripts/emaki-snapshot-menu
	install -Dm755 grub/emaki-snapshot-menu-check $(BIN)/emaki-snapshot-menu-check
	install -Dm755 scripts/emaki-update-channel $(BIN)/emaki-update-channel
	install -Dm644 systemd/90-emaki-charge-limit.rules $(DESTDIR)$(PREFIX)/lib/udev/rules.d/90-emaki-charge-limit.rules
	install -Dm755 scripts/emaki-charge-limit $(DESTDIR)$(LIBEXECDIR)/emaki-charge-limit
	install -Dm644 systemd/emaki-charge-limit.service $(SYSTEMD)/system/emaki-charge-limit.service
	ln -sfn ../emaki-charge-limit.service $(SYSTEMD)/system/multi-user.target.wants/emaki-charge-limit.service
	install -Dm644 polkit/org.emaki.charge-limit.policy $(DESTDIR)$(PREFIX)/share/polkit-1/actions/org.emaki.charge-limit.policy
	install -Dm644 polkit/org.emaki.channel.policy $(DESTDIR)$(PREFIX)/share/polkit-1/actions/org.emaki.channel.policy
	install -Dm755 grub/emaki-grub-title $(DESTDIR)$(PREFIX)/share/libalpm/scripts/emaki-grub-title
	# Snapshot boot hook, named in HOOKS on btrfs installs. mkinitcpio's own directory, fixed
	# whatever PREFIX is; a copy in /etc/initcpio is the administrator's and is read first.
	install -Dm644 initcpio/hooks/emaki-snapshot-fstab $(DESTDIR)/usr/lib/initcpio/hooks/emaki-snapshot-fstab
	install -Dm644 initcpio/install/emaki-snapshot-fstab $(DESTDIR)/usr/lib/initcpio/install/emaki-snapshot-fstab
	# System identity: /etc/os-release is linked to this file by the hook (no package owns
	# /etc/os-release; Arch's /usr/lib/os-release stays untouched).
	install -Dm644 os-release/os-release $(DESTDIR)$(PREFIX)/lib/emaki/os-release
	install -Dm644 -t $(DESTDIR)$(PREFIX)/share/libalpm/hooks os-release/50-emaki-os-release.hook os-release/50-emaki-os-release-remove.hook
	install -Dm755 os-release/emaki-os-release $(DESTDIR)$(PREFIX)/share/libalpm/scripts/emaki-os-release
	install -Dm644 packaging/emaki-config/emaki-release $(DESTDIR)$(PREFIX)/lib/emaki-release
	install -Dm644 packaging/emaki-config/zzz-emaki-session-update.hook $(DESTDIR)$(PREFIX)/share/libalpm/hooks/zzz-emaki-session-update.hook
	rm -f $(DESTDIR)$(PREFIX)/share/libalpm/hooks/95-emaki-session-update.hook
	@commit='$(if $(EMAKI_COMMIT),$(EMAKI_COMMIT),$(shell git rev-parse --verify HEAD))'; \
		printf '%s\n' "$$commit" | grep -Eq '^[0-9a-f]{40}$$' || exit 1; \
		printf 'EMAKI_COMMIT=%s\n' "$$commit" >> $(DESTDIR)$(PREFIX)/lib/emaki-release
	install -Dm644 systemd/50-emaki.preset $(SYSTEMD)/system-preset/50-emaki.preset
	install -Dm644 systemd/logind.conf.d/50-emaki.conf $(SYSTEMD)/logind.conf.d/50-emaki.conf
	for f in wpaperd/config.toml kitty/kitty.conf qt6ct/qt6ct.conf; do \
		install -Dm644 "etc-skel/.config/$$f" "$(DESTDIR)/etc/skel/.config/$$f"; done
	# zsh's system-wide files (zsh reads only /etc/zsh): defaults for people without their own
	# ~/.zshrc, and no first-run questionnaire for new accounts.
	install -Dm644 zsh/emaki.zsh $(DESTDIR)/etc/zsh/emaki.zsh
	install -Dm644 zsh/zshenv $(DESTDIR)/etc/zsh/zshenv
	# Cursor theme: files plus alias symlinks, replaced whole so a renamed cursor doesn't linger.
	rm -rf $(ICONS)/Emaki
	install -Dm644 cursors/Emaki/index.theme $(ICONS)/Emaki/index.theme
	install -dm755 $(ICONS)/Emaki/cursors
	cp -P --no-preserve=ownership cursors/Emaki/cursors/* $(ICONS)/Emaki/cursors/
	# Living wallpaper for niri-emaki (emaki-wallpaper in both fork entry points): ring, front layer, train.
	install -Dm644 -t $(SHARE)/wallpaper art/wallpaper/ring.png art/wallpaper/fallback.png art/wallpaper/ring-front.png art/wallpaper/train-frames.png art/wallpaper/train.json
	install -Dm644 -t $(SHARE)/fetch fetch/config.jsonc fetch/details.jsonc fetch/render.py fetch/wordmark.png fetch/wordmark.txt
	install -Dm644 fetch/config.jsonc $(XDG)/fastfetch/config.jsonc
	install -Dm644 greetd/pam $(PAMDIR)/emaki-greetd
	install -Dm644 greetd/pam-auth $(PAMDIR)/emaki-greetd-auth
	$(MAKE) install-sudo-policy
	install -Dm644 $(SUDO_BUILD)/pam_emaki_sudo.so $(DESTDIR)$(PREFIX)/lib/security/pam_emaki_sudo.so
	install -Dm755 $(SUDO_BUILD)/emaki-sudo-broker $(DESTDIR)$(LIBEXECDIR)/emaki-sudo-broker
	install -Dm755 $(CAPTURE_BIN) $(DESTDIR)$(LIBEXECDIR)/emaki-capture-recorder
	install -Dm644 capture/dmabuf.py $(DESTDIR)$(LIBEXECDIR)/dmabuf.py
	install -Dm755 $(SUDO_BUILD)/emaki-sudo $(BIN)/emaki-sudo
ifeq ($(INSTALL_SUDO_TURN_INTEGRATION),1)
	$(MAKE) install-sudo-turn-integration
endif
	install -Dm644 $(SUDO_BUILD)/emaki-sudo.service $(SYSTEMD)/system/emaki-sudo.service
	install -dm755 $(SYSTEMD)/system/multi-user.target.wants
	ln -sfn ../emaki-sudo.service $(SYSTEMD)/system/multi-user.target.wants/emaki-sudo.service
	install -Dm644 $(SUDO_BUILD)/emaki-sudo-cleanup.service $(SYSTEMD)/system/emaki-sudo-cleanup.service
	install -Dm644 sudo/emaki-sudo-cleanup.timer $(SYSTEMD)/system/emaki-sudo-cleanup.timer
	install -dm755 $(SYSTEMD)/system/timers.target.wants
	ln -sfn ../emaki-sudo-cleanup.timer $(SYSTEMD)/system/timers.target.wants/emaki-sudo-cleanup.timer
	install -Dm644 -t $(SHARE)/shell/Emaki/Sudo $(SUDO_BUILD)/qml/Emaki/Sudo/libemakisudoplugin.so sudo/qmldir sudo/plugins.qmltypes
	install -Dm644 systemd/emaki-drm-hold.service $(SYSTEMD)/system/emaki-drm-hold.service
	install -Dm644 systemd/greetd.service.d/emaki.conf $(SYSTEMD)/system/greetd.service.d/emaki.conf
	install -Dm644 -t $(SYSTEMD)/user systemd/emaki-greeter-wallpaper.path systemd/emaki-greeter-wallpaper.service systemd/emaki-greeter-wallpaper-watch.service
	install -dm755 $(SYSTEMD)/user/graphical-session.target.wants
	ln -sfn ../emaki-greeter-wallpaper.path $(SYSTEMD)/user/graphical-session.target.wants/emaki-greeter-wallpaper.path
	ln -sfn ../emaki-greeter-wallpaper-watch.service $(SYSTEMD)/user/graphical-session.target.wants/emaki-greeter-wallpaper-watch.service
	install -Dm644 greetd/emaki-greeter.conf $(DESTDIR)$(PREFIX)/lib/tmpfiles.d/emaki-greeter.conf
	python3 scripts/render-paths $(PATH_ARGS) --install "$(or $(DESTDIR),/)"
# Staging packages must not require a greeter account inside DESTDIR.
# On this machine, apply the os-release link now instead of at the next pacman transaction.
ifeq ($(DESTDIR),)
	systemd-tmpfiles --create $(PREFIX)/lib/tmpfiles.d/emaki-greeter.conf
	$(PREFIX)/share/libalpm/scripts/emaki-os-release
endif

# Explicit opt-in after installation: provision fixed private greeter directories
# and this account's publishing directory, then enable greetd for the next boot.
# No user config/image is read as root and no greeter is started or restarted here.
# GREETER_USER must explicitly name the account receiving wallpaper publishing access.
# DESTDIR enables only inside that root. A conflicting display-manager link is refused.
GREETER_FILES = /usr/bin/niri $(PREFIX)/bin/emaki-greeter-compositor $(PREFIX)/bin/emaki-greeter-run /usr/bin/greetd /usr/bin/regreet /usr/bin/qs $(PREFIX)/share/emaki/greetd/config.toml \
	$(PREFIX)/share/emaki/shell/greeter.qml $(PREFIX)/bin/emaki-greeter-provision \
	/usr/lib/pam.d/emaki-greetd /usr/lib/pam.d/emaki-greetd-auth $(PREFIX)/lib/systemd/system/greetd.service.d/emaki.conf
GREETER_USER ?=

.PHONY: enable-greeter
enable-greeter:
	@test -n "$(GREETER_USER)" || { echo 'enable-greeter: set GREETER_USER explicitly.' >&2; exit 1; }
	@for f in $(GREETER_FILES); do [ -e "$(DESTDIR)$$f" ] || { \
		echo "enable-greeter: missing $$f — run make install, then enable-greeter" >&2; \
		exit 1; }; done
	python3 -I "$(BIN)/emaki-greeter-provision" --root "$(if $(DESTDIR),$(DESTDIR),/)" --user "$(GREETER_USER)"
ifeq ($(DESTDIR),)
	systemctl daemon-reload
	systemctl enable greetd.service
else
	systemctl --root="$(DESTDIR)" enable greetd.service
endif

# Services used by the shell's right island: networking (NetworkManager) and Bluetooth.
# Another separate, explicit step after make install, like enable-greeter.
# Enable NetworkManager only if networking is unmanaged: two network managers
# fight over Wi-Fi and break connectivity. Leave it alone if already enabled. Nothing is disabled
# or started now: the service starts after reboot, so networking stays up during
# installation (which may itself use the network). Always enable bluetooth.
OTHER_NET = iwd.service systemd-networkd.service connman.service dhcpcd.service
SYSTEMCTL = systemctl $(if $(DESTDIR),--root="$(DESTDIR)")

.PHONY: enable-services
enable-services:
	@if [ "$$($(SYSTEMCTL) is-enabled NetworkManager.service 2>/dev/null)" = enabled ]; then \
		echo "enable-services: NetworkManager is already enabled"; \
	else \
		busy=; for s in $(OTHER_NET); do \
			if [ "$$($(SYSTEMCTL) is-enabled $$s 2>/dev/null)" = enabled ] || \
				{ [ -z "$(DESTDIR)" ] && systemctl -q is-active $$s; }; then busy="$$busy $$s"; fi; \
		done; \
		if [ -n "$$busy" ]; then \
			echo "enable-services: networking is already managed by$$busy — leaving NetworkManager disabled." >&2; \
			echo "  Wi-Fi in the shell panel will work when NetworkManager manages networking." >&2; \
		else \
			$(SYSTEMCTL) enable NetworkManager.service; \
		fi; \
	fi
	$(SYSTEMCTL) enable bluetooth.service

# The fork package owns /usr/bin/niri-emaki. This target installs only session
# files, without inspecting packages or executing a compositor on the build host.
.PHONY: install-niri-emaki uninstall-niri-emaki
install-niri-emaki:
	install -Dm755 scripts/niri-emaki-session $(BIN)/niri-emaki-session
	install -Dm644 -t $(DESTDIR)$(PREFIX)/lib/systemd/user systemd/niri-emaki.service
	install -Dm644 -t $(SHARE)/niri niri/fork.kdl niri/fork-system.kdl niri/fork-rules.kdl
	install -Dm644 -t $(DESTDIR)$(PREFIX)/share/wayland-sessions niri/niri-emaki.desktop
	python3 scripts/render-paths $(PATH_ARGS) --install "$(or $(DESTDIR),/)"

uninstall-niri-emaki:
	rm -f $(BIN)/niri-emaki-session
	rm -f $(SHARE)/niri/fork.kdl $(SHARE)/niri/fork-system.kdl $(SHARE)/niri/fork-rules.kdl
	rm -f $(DESTDIR)$(PREFIX)/lib/systemd/user/niri-emaki.service
	rm -f $(DESTDIR)$(PREFIX)/share/wayland-sessions/niri-emaki.desktop

# Remove only our files. Without the drop-in, greetd returns to its package config
# (/etc/greetd/config.toml, agreety text login), so login remains available.
# Without /etc/niri/config.kdl, niri creates its stock config at next login if no user config exists.
uninstall:
	@case "$(DESTDIR)" in ""|/*) ;; *) echo "uninstall: DESTDIR must be empty or an absolute path; nothing changed." >&2; exit 1 ;; esac; \
		case "/$(DESTDIR)/" in */../*) echo "uninstall: DESTDIR must not contain '..' components; nothing changed." >&2; exit 1 ;; esac
	# Remove our selector before its PAM service so ordinary sudo remains available.
	@if [ -e $(DESTDIR)/etc/sudoers.d/20-emaki-pam ] && ! cmp -s sudo/20-emaki-pam $(DESTDIR)/etc/sudoers.d/20-emaki-pam; then \
		echo 'uninstall: sudo PAM selector differs from the shipped file; restore ordinary sudo routing before removal.' >&2; exit 1; fi
	rm -f $(DESTDIR)$(PREFIX)/share/libalpm/hooks/99-emaki-qt-check.hook
	rm -f $(DESTDIR)$(PREFIX)/share/themes/Emaki/gtk-3.0/gtk.css
	rm -f $(DESTDIR)$(PREFIX)/lib/environment.d/60-emaki-xdg.conf
	rm -f $(DESTDIR)$(PREFIX)/share/libalpm/hooks/91-emaki-snapshot-menu.hook $(DESTDIR)$(PREFIX)/share/libalpm/scripts/emaki-snapshot-menu $(BIN)/emaki-update-channel $(BIN)/emaki-snapshot-menu-check
	rm -f $(DESTDIR)$(PREFIX)/share/libalpm/hooks/90-emaki-grub-title.hook $(DESTDIR)$(PREFIX)/share/libalpm/scripts/emaki-grub-title
	rm -f $(SYSTEMD)/system/emaki-boot-complete.service $(SYSTEMD)/system/multi-user.target.wants/emaki-boot-complete.service
	rm -f $(SYSTEMD)/system/emaki-boot-refresh.service $(SYSTEMD)/system/multi-user.target.wants/emaki-boot-refresh.service
	rm -f $(SYSTEMD)/system/emaki-snapshot-menu.service $(SYSTEMD)/system/multi-user.target.wants/emaki-snapshot-menu.service
	rm -f $(SYSTEMD)/system/emaki-update-boot.service $(SYSTEMD)/system/multi-user.target.wants/emaki-update-boot.service
	rm -f $(BOOTLIB)/secureboot_tool.py $(BIN)/emaki-secureboot $(DESTDIR)/usr/lib/initcpio/post/emaki-secureboot
	rm -f $(DESTDIR)$(PREFIX)/share/libalpm/hooks/03-emaki-secureboot-check.hook $(DESTDIR)$(PREFIX)/share/libalpm/hooks/94-emaki-secureboot-sign.hook
	rm -f $(SYSTEMD)/system/emaki-secureboot-status.service $(SYSTEMD)/system/multi-user.target.wants/emaki-secureboot-status.service
	rm -f $(DESTDIR)$(PREFIX)/share/polkit-1/actions/org.emaki.secureboot.policy
	rm -f $(BOOTLIB)/__init__.py $(BOOTLIB)/esp_records.py $(BOOTLIB)/package_lock.py $(BOOTLIB)/refresh.py $(BOOTLIB)/shared.py $(BOOTLIB)/update.py $(BOOTLIB)/update_menu.py $(BOOTLIB)/usable.py $(addprefix $(BOOTLIB)/,$(addsuffix .py,$(BOOT_SHARED)))
	for picture in $(BOOT_ARTWORK); do rm -f "$(BOOTLIB)/$${picture#installer/emaki_installer/}"; done
	rm -f $(DESTDIR)$(PREFIX)/share/licenses/emaki-config/LICENSE-DejaVu.txt $(DESTDIR)$(PREFIX)/share/licenses/emaki-config/LICENSE-Noto.txt
	rm -rf $(SHARE)/grub/theme
	rm -rf $(PLYMOUTH_THEME)
	rm -f $(DESTDIR)$(PREFIX)/share/licenses/emaki-config/NOTICE-Plymouth.txt
	rm -f $(DESTDIR)/etc/mkinitcpio.conf.d/50-emaki-splash.conf $(DESTDIR)/etc/default/grub.d/50-emaki-splash.cfg
	rm -f $(DESTDIR)/usr/lib/initcpio/hooks/emaki-resume $(DESTDIR)/usr/lib/initcpio/install/emaki-resume
	rm -f $(DESTDIR)/usr/lib/initcpio/hooks/emaki-snapshot-fstab $(DESTDIR)/usr/lib/initcpio/install/emaki-snapshot-fstab
	# Point /etc/os-release back at Arch's file before removing ours (source helper, as below).
	bash os-release/emaki-os-release --restore "$(if $(DESTDIR),$(DESTDIR),/)"
	rm -f $(DESTDIR)$(PREFIX)/share/libalpm/hooks/50-emaki-os-release.hook $(DESTDIR)$(PREFIX)/share/libalpm/hooks/50-emaki-os-release-remove.hook
	rm -f $(DESTDIR)$(PREFIX)/share/libalpm/hooks/zzz-emaki-session-update.hook $(DESTDIR)$(PREFIX)/share/libalpm/hooks/95-emaki-session-update.hook
	rm -f $(DESTDIR)$(PREFIX)/share/libalpm/scripts/emaki-os-release $(DESTDIR)$(PREFIX)/lib/emaki/os-release
	[ ! -d $(DESTDIR)$(PREFIX)/lib/emaki ] || rmdir --ignore-fail-on-non-empty $(DESTDIR)$(PREFIX)/lib/emaki
	# Use this source helper: older installed provisioners may not support purging.
	@if ! python3 -I scripts/emaki-greeter-provision --root "$(if $(DESTDIR),$(DESTDIR),/)" --purge-published; then \
		echo "uninstall: WARNING: published wallpaper cleanup incomplete at $(if $(DESTDIR),$(DESTDIR),)/var/lib/emaki-greeter; retained copies need administrator cleanup. Continuing removal." >&2; \
	fi
	# Remove our selector before its PAM service so ordinary sudo remains available (checked unchanged above).
	rm -f $(DESTDIR)/etc/sudoers.d/20-emaki-pam
	rm -f $(DESTDIR)$(PREFIX)/share/libalpm/hooks/00-emaki-sudo-remove.hook
	rm -f $(PAMDIR)/emaki-sudo $(DESTDIR)/etc/pam.d/emaki-sudo $(DESTDIR)$(PREFIX)/lib/security/pam_emaki_sudo.so
	rm -f $(BIN)/emaki-sudo $(BIN)/emaki-sudo-turn $(DESTDIR)$(LIBEXECDIR)/emaki-sudo-broker
	rm -f $(DESTDIR)$(LIBEXECDIR)/emaki-capture-recorder $(DESTDIR)$(LIBEXECDIR)/dmabuf.py
	rm -f $(SYSTEMD)/system/emaki-sudo.service $(SYSTEMD)/system/multi-user.target.wants/emaki-sudo.service
	rm -f $(SYSTEMD)/system/emaki-sudo-cleanup.service $(SYSTEMD)/system/emaki-sudo-cleanup.timer $(SYSTEMD)/system/timers.target.wants/emaki-sudo-cleanup.timer
	rm -f $(DESTDIR)$(LIBEXECDIR)/users_home.py $(DESTDIR)$(LIBEXECDIR)/emaki-settings-users-root $(DESTDIR)$(PREFIX)/share/polkit-1/actions/org.emaki.settings-users.policy
	rm -f $(BIN)/emaki-settings-network $(DESTDIR)$(LIBEXECDIR)/emaki-wifi-password $(DESTDIR)$(PREFIX)/share/polkit-1/actions/org.emaki.wifi-password.policy
	rm -f $(DESTDIR)$(LIBEXECDIR)/emaki_paths.py $(DESTDIR)$(LIBEXECDIR)/paths
	rm -f $(DESTDIR)$(LIBEXECDIR)/emaki_lock_fallback.py
	rm -f $(BIN)/emaki-transaction-inhibit $(DESTDIR)$(PREFIX)/share/libalpm/hooks/00-emaki-transaction-inhibit.hook $(DESTDIR)$(LIBEXECDIR)/emaki_update_space.py
	rm -f $(BIN)/emaki-update-boot $(DESTDIR)$(PREFIX)/share/libalpm/hooks/04-emaki-update-boot.hook $(DESTDIR)$(PREFIX)/share/libalpm/hooks/06-emaki-update-boot.hook
	rm -f $(BIN)/emaki-update-manager $(DESTDIR)$(LIBEXECDIR)/update-manager-backend $(DESTDIR)$(LIBEXECDIR)/update_catalog.py $(DESTDIR)$(LIBEXECDIR)/emaki_update_root.py $(DESTDIR)$(LIBEXECDIR)/emaki-update-apply
	rm -f $(DESTDIR)$(LIBEXECDIR)/update_flatpaks.py
	rm -f $(DESTDIR)$(LIBEXECDIR)/update_reminder.py
	rm -f $(DESTDIR)$(LIBEXECDIR)/emaki-update-restore $(DESTDIR)$(PREFIX)/share/polkit-1/actions/org.emaki.update.restore.policy
	rm -f $(BIN)/emaki-settings-privacy $(DESTDIR)$(LIBEXECDIR)/emaki-privacy-recovery $(DESTDIR)$(LIBEXECDIR)/emaki_privacy_portals.py
	rm -f $(DESTDIR)$(PREFIX)/share/polkit-1/actions/org.emaki.privacy.recovery.policy
	rm -f $(SYSTEMD)/system/emaki-restore-index.service $(SYSTEMD)/system/emaki-restore-index.timer $(SYSTEMD)/system/timers.target.wants/emaki-restore-index.timer
	rm -f $(DESTDIR)$(PREFIX)/share/libalpm/hooks/zzz-emaki-restore-index.hook
	rm -f $(SYSTEMD)/system/emaki-update.service $(DESTDIR)$(PREFIX)/share/polkit-1/actions/org.emaki.update.policy $(DESTDIR)$(PREFIX)/share/applications/emaki-update-manager.desktop
	rm -f $(DESTDIR)$(PREFIX)/share/icons/hicolor/scalable/apps/emaki-programs-updates.svg
	rm -rf $(DESTDIR)$(PREFIX)/share/emaki-update-manager
	rm -f $(BIN)/emaki-programs $(DESTDIR)$(LIBEXECDIR)/programs_catalog.py $(DESTDIR)$(LIBEXECDIR)/emaki-programs-remove $(DESTDIR)$(PREFIX)/share/polkit-1/actions/org.emaki.programs.policy
	rm -f $(DESTDIR)$(LIBEXECDIR)/programs_graphics.py $(DESTDIR)$(LIBEXECDIR)/programs_appstream.py $(DESTDIR)$(LIBEXECDIR)/programs_arch.py $(DESTDIR)$(LIBEXECDIR)/programs_engine.py $(DESTDIR)$(LIBEXECDIR)/programs_flatpak.py $(DESTDIR)$(LIBEXECDIR)/emaki-programs-install $(DESTDIR)$(PREFIX)/share/polkit-1/actions/org.emaki.programs.install.policy
	rm -f $(DESTDIR)$(LIBEXECDIR)/emaki-programs-flatpak $(DESTDIR)$(PREFIX)/share/polkit-1/actions/org.emaki.programs.flatpak.policy
	rm -f $(DESTDIR)$(LIBEXECDIR)/programs_aur.py $(DESTDIR)$(LIBEXECDIR)/programs_receipts.py $(DESTDIR)$(LIBEXECDIR)/programs_reviews.py $(DESTDIR)$(LIBEXECDIR)/emaki-programs-aur $(DESTDIR)$(LIBEXECDIR)/emaki-programs-undo
	rm -f $(DESTDIR)$(LIBEXECDIR)/restore.py
	rm -f $(DESTDIR)$(PREFIX)/share/polkit-1/actions/org.emaki.programs.aur.policy $(DESTDIR)$(PREFIX)/share/polkit-1/actions/org.emaki.programs.undo.policy
	rm -f $(DESTDIR)$(PREFIX)/share/polkit-1/rules.d/00-emaki-programs.rules
	rm -f $(BIN)/emaki-shell-generations $(DESTDIR)$(LIBEXECDIR)/emaki_shell_tree.py $(BIN)/emaki-session-files $(BIN)/emaki-session-update $(BIN)/emaki-update $(DESTDIR)$(LIBEXECDIR)/emaki_update_errors.py $(DESTDIR)$(LIBEXECDIR)/emaki_redaction.py $(DESTDIR)$(LIBEXECDIR)/emaki_session_state.py $(DESTDIR)$(LIBEXECDIR)/emaki_session_arch.py
	rm -f $(BIN)/emaki-wallet-stage $(BIN)/emaki-wallet-start $(BIN)/emaki-keyring-recover $(BIN)/emaki-wallet-migrate $(BIN)/emaki-rollback $(DESTDIR)$(PREFIX)/share/polkit-1/actions/org.emaki.rollback.policy
	rm -f $(DESTDIR)$(PREFIX)/lib/emaki/emaki-wifi-recover $(DESTDIR)$(PREFIX)/share/polkit-1/actions/org.emaki.wifi-recovery.policy $(SYSTEMD)/system/emaki-wifi-recovery.service
	rm -f $(DESTDIR)$(PREFIX)/lib/udev/rules.d/90-emaki-wifi-recovery.rules
	rm -f $(BIN)/emaki-boot-order $(SYSTEMD)/system/emaki-boot-order.service $(SYSTEMD)/system/multi-user.target.wants/emaki-boot-order.service $(DESTDIR)$(PREFIX)/share/libalpm/hooks/00-emaki-alongside.hook
	rm -f $(BIN)/emaki-boot-refresh $(DESTDIR)$(PREFIX)/share/libalpm/hooks/95-emaki-boot-refresh.hook
	rm -f $(BIN)/emaki-drm-hold $(BIN)/emaki-greeter-compositor $(BIN)/emaki-greeter-run $(BIN)/emaki-text-session $(BIN)/emaki-session-import-environment $(SYSTEMD)/system/emaki-drm-hold.service
	rm -f $(BIN)/emaki-settings-power $(BIN)/emaki-settings-apps $(BIN)/emaki-settings-about $(DESTDIR)$(LIBEXECDIR)/emaki-charge-limit
	rm -f $(BIN)/emaki-report $(DESTDIR)$(LIBEXECDIR)/emaki_report_redact.py
	rm -f $(BIN)/emaki-settings-storage $(DESTDIR)$(LIBEXECDIR)/emaki_storage.py $(DESTDIR)$(LIBEXECDIR)/storage_arch.py $(DESTDIR)$(LIBEXECDIR)/emaki_package_lock.py $(DESTDIR)$(PREFIX)/share/polkit-1/actions/org.emaki.storage.policy
	rm -f $(DESTDIR)$(PREFIX)/lib/udev/rules.d/90-emaki-charge-limit.rules
	rm -f $(SYSTEMD)/system/emaki-charge-limit.service $(SYSTEMD)/system/multi-user.target.wants/emaki-charge-limit.service
	rm -f $(DESTDIR)$(PREFIX)/share/polkit-1/actions/org.emaki.charge-limit.policy $(DESTDIR)$(PREFIX)/share/polkit-1/actions/org.emaki.channel.policy
	rm -f $(BIN)/emaki-autostart $(BIN)/emaki-idle $(BIN)/emaki-config-path $(BIN)/emaki-power $(BIN)/emaki-shell $(BIN)/emaki-shell-trusted $(BIN)/emaki-shell-health $(BIN)/emaki-lock $(BIN)/emaki-session-cover $(BIN)/emaki-session-wallpaper $(BIN)/emaki-machine-settings $(BIN)/emaki-settings-users $(BIN)/emaki
	# The fork session (install-niri-emaki) goes too: its wrapper reads configs from $(SHARE).
	rm -f $(BIN)/niri-emaki-session
	rm -f $(SHARE)/niri/fork.kdl $(SHARE)/niri/fork-system.kdl $(SHARE)/niri/fork-rules.kdl
	rm -f $(DESTDIR)$(PREFIX)/lib/systemd/user/niri-emaki.service
	rm -f $(DESTDIR)$(PREFIX)/share/wayland-sessions/niri-emaki.desktop
	rm -rf $(SHARE)
	rm -f $(DESTDIR)$(PREFIX)/share/applications/emaki-welcome.desktop $(DESTDIR)$(PREFIX)/share/applications/emaki-report.desktop $(ICONS)/hicolor/scalable/apps/emaki-welcome.svg $(ICONS)/hicolor/scalable/apps/emaki-report.svg
	rm -rf $(ICONS)/Emaki
	rm -f $(SYSTEMD)/user/emaki-shell.service $(SYSTEMD)/user/emaki-shell-heal.service $(SYSTEMD)/user/emaki-shell-recovery.service $(SYSTEMD)/user/emaki-sleep-guard.service $(SYSTEMD)/user/emaki-idle.service $(SYSTEMD)/user/emaki-wallet-migrate.service $(SYSTEMD)/user/emaki-wallet-start.service $(SYSTEMD)/user/niri.service.d/50-emaki-wallet.conf
	rm -f $(SYSTEMD)/system-sleep/emaki-boot-resume
	rm -f $(BIN)/emaki-sleep-guard $(DESTDIR)$(PREFIX)/lib/emaki-release
	rm -f $(DESTDIR)$(PREFIX)/share/libalpm/scripts/emaki-snapshot-policy $(DESTDIR)$(PREFIX)/share/libalpm/scripts/emaki-system-migrate $(DESTDIR)$(PREFIX)/share/libalpm/scripts/emaki_boot_defaults.py
	rm -f $(DESTDIR)$(PREFIX)/share/libalpm/hooks/01-emaki-config-release.hook $(DESTDIR)$(PREFIX)/share/libalpm/hooks/02-emaki-config-remove.hook
	rm -f $(DESTDIR)$(PREFIX)/share/libalpm/scripts/emaki_initramfs.py $(DESTDIR)$(PREFIX)/share/libalpm/hooks/89-emaki-initramfs-begin.hook
	rm -f $(DESTDIR)$(PREFIX)/share/libalpm/hooks/89-emaki-system-migrate.hook
	rm -f $(DESTDIR)$(PREFIX)/share/libalpm/scripts/emaki-initramfs-refresh $(DESTDIR)$(PREFIX)/share/libalpm/hooks/93-emaki-initramfs-refresh.hook
	rm -f $(BIN)/emaki-refresh-mirrors $(SYSTEMD)/system/emaki-refresh-mirrors.service $(SYSTEMD)/system/emaki-refresh-mirrors.timer $(SYSTEMD)/zram-generator.conf.d/00-emaki.conf
	rm -f $(SYSTEMD)/system/emaki-refresh-appstream.service $(SYSTEMD)/system/emaki-refresh-appstream.timer
	for pair in upkeep/defaults/10-emaki-wheel:sudoers.d/10-emaki-wheel; do \
		source=$${pair%%:*}; target=$(DESTDIR)/etc/$${pair#*:}; if cmp -s "$$source" "$$target"; then rm -f "$$target"; fi; done
	rm -f $(DESTDIR)$(PREFIX)/share/libalpm/hooks/00-emaki-package-lock.hook
	rm -f $(DESTDIR)$(PREFIX)/share/libalpm/hooks/00-emaki-update-space.hook
	rm -f $(DESTDIR)$(PREFIX)/share/libalpm/scripts/emaki-update-space
	rm -f $(DESTDIR)$(PREFIX)/share/libalpm/scripts/emaki-rollback-holds $(DESTDIR)$(PREFIX)/share/libalpm/hooks/00-emaki-rollback-holds.hook $(DESTDIR)$(PREFIX)/share/libalpm/hooks/99-emaki-rollback-holds.hook
	rm -f $(SYSTEMD)/system-preset/50-emaki.preset $(SYSTEMD)/logind.conf.d/50-emaki.conf
	for f in wpaperd/config.toml kitty/kitty.conf qt6ct/qt6ct.conf; do \
		if cmp -s "etc-skel/.config/$$f" "$(DESTDIR)/etc/skel/.config/$$f"; then \
			rm -f "$(DESTDIR)/etc/skel/.config/$$f"; fi; done
	# System defaults in /etc: remove only the file as shipped. An edited copy is the
	# administrator's (the package keeps it too, backup=), so it stays and is named.
	for pair in packaging/emaki-config/niri-portals.conf:xdg-desktop-portal/niri-portals.conf \
		hypr/hyprlock.conf:hypr/hyprlock.conf packaging/emaki-config/kdeglobals:kdeglobals \
		packaging/emaki-config/kwalletrc:kwalletrc \
		packaging/emaki-config/dolphinrc:dolphinrc packaging/emaki-config/mimeapps.list:mimeapps.list \
		packaging/emaki-config/emaki-applications.menu:menus/emaki-applications.menu \
		etc-skel/.config/qt6ct/qt6ct.conf:qt6ct/qt6ct.conf; do \
		src="$${pair%%:*}"; dst="$(XDG)/$${pair#*:}"; \
		if cmp -s "$$src" "$$dst"; then rm -f "$$dst"; \
		elif [ -e "$$dst" ]; then echo "uninstall: kept $$dst (it differs from the shipped file)"; fi; done
	for f in emaki.zsh zshenv; do \
		dst="$(DESTDIR)/etc/zsh/$$f"; \
		if cmp -s "zsh/$$f" "$$dst"; then rm -f "$$dst"; \
		elif [ -e "$$dst" ]; then echo "uninstall: kept $$dst (it differs from the shipped file)"; fi; done
	if [ -f $(XDG)/fastfetch/config.jsonc ] && grep -qF 'Emaki fetch default.' $(XDG)/fastfetch/config.jsonc; then rm -f $(XDG)/fastfetch/config.jsonc; fi
	rm -f $(PAMDIR)/emaki-greetd $(PAMDIR)/emaki-greetd-auth $(SYSTEMD)/system/greetd.service.d/emaki.conf
	rm -f $(BIN)/emaki-migrate-installer-config $(BIN)/emaki-greeter-provision $(DESTDIR)$(PREFIX)/lib/tmpfiles.d/emaki-greeter.conf
	rm -f $(SYSTEMD)/user/emaki-greeter-wallpaper.path $(SYSTEMD)/user/emaki-greeter-wallpaper.service $(SYSTEMD)/user/emaki-greeter-wallpaper-watch.service
	rm -f $(SYSTEMD)/user/graphical-session.target.wants/emaki-greeter-wallpaper.path $(SYSTEMD)/user/graphical-session.target.wants/emaki-greeter-wallpaper-watch.service
	[ ! -d $(SYSTEMD)/user/graphical-session.target.wants ] || rmdir --ignore-fail-on-non-empty $(SYSTEMD)/user/graphical-session.target.wants
	[ ! -d $(SYSTEMD)/system/greetd.service.d ] || rmdir --ignore-fail-on-non-empty $(SYSTEMD)/system/greetd.service.d
	if python3 scripts/render-paths $(PATH_ARGS) --text niri/system.kdl | cmp -s - "$(NIRI_ETC)"; then rm -f "$(NIRI_ETC)"; \
	elif [ -e "$(NIRI_ETC)" ] && grep -qF "$(NIRI_ETC_MARK)" "$(NIRI_ETC)"; then \
		echo "uninstall: kept $(NIRI_ETC) (it differs from the shipped file)"; fi
	[ ! -d $(DESTDIR)/etc/niri ] || rmdir --ignore-fail-on-non-empty $(DESTDIR)/etc/niri
	rm -f $(DESTDIR)$(PREFIX)/share/doc/emaki/ZONES.md
	[ ! -d $(DESTDIR)$(PREFIX)/share/doc/emaki ] || rmdir --ignore-fail-on-non-empty $(DESTDIR)$(PREFIX)/share/doc/emaki

# The emaki CLI binary (crates/), built reproducibly by scripts/core-package.py.
CARGO_HOME      ?= $(CURDIR)/.cache/cargo-home
CARGO_TARGET_DIR ?= $(CURDIR)/.cache/target
EMAKI_PREFIX     ?= $(PREFIX)
EMAKI_DATADIR    ?= $(DATADIR)
EMAKI_LIBEXECDIR ?= $(LIBEXECDIR)
EMAKI_SYSCONFDIR ?= /etc
EMAKI_BUSCTL     ?= busctl
EMAKI_PW_CLI     ?= pw-cli
EMAKI_NIRI       ?= niri

CORE_ENV = CARGO_HOME="$(CARGO_HOME)" CARGO_TARGET_DIR="$(CARGO_TARGET_DIR)" \
	EMAKI_PREFIX="$(EMAKI_PREFIX)" EMAKI_DATADIR="$(EMAKI_DATADIR)" \
	EMAKI_LIBEXECDIR="$(EMAKI_LIBEXECDIR)" EMAKI_SYSCONFDIR="$(EMAKI_SYSCONFDIR)" \
	EMAKI_BUSCTL="$(EMAKI_BUSCTL)" EMAKI_PW_CLI="$(EMAKI_PW_CLI)" EMAKI_NIRI="$(EMAKI_NIRI)" EMAKI_XKB_RULES="$(EMAKI_XKB_RULES)"

.PHONY: prepare-core build-core check-core

# Fetch only in the preparation phase; compilation and installation stay offline.
# Use the same cache for standalone checks and makepkg's prepare/build functions.
prepare-core:
	env $(CORE_ENV) cargo fetch --locked --target "$$(rustc --print host-tuple)"

# No live desktop or external services; socket peers and system helpers are fixtures.
check-core: paths
	env $(CORE_ENV) cargo test --locked -p emaki-cli -p emaki-core
	env $(CORE_ENV) cargo test --locked -p emaki-cli --features test-hooks --test settings

build-core:
	env $(CORE_ENV) python scripts/core-package.py build

# ── Build ──────────────────────────────────────────────────────────────
# Build only as a regular user: cargo and qsb write to .cache/, and building as root
# would leave root-owned files in the repo. Thus `sudo make install` does not build:
# if the core or shaders are missing or older than their sources, it stops and explains
# what to do. As a regular user (make install DESTDIR=…), missing artifacts are built.
# Rules follow CARGO_TARGET_DIR: target paths are expanded when the file is read.
CORE_SRC   = Cargo.toml Cargo.lock $(wildcard crates/*/Cargo.toml) \
	$(shell find crates -path '*/src/*' -type f) \
	tokens.toml niri/default.kdl niri/theme.kdl niri/shell.kdl \
	hypr/hyprlock.conf qt6ct/emaki.conf packaging/emaki-config/kdeglobals
SHADER_SRC = $(wildcard shell/shaders/*.frag)
SHADERS    = $(patsubst shell/shaders/%,.cache/shell-shaders/%.qsb,$(SHADER_SRC))
NOT_ROOT   = if [ "$$(id -u)" = 0 ]; then \
	echo "make: $@ is missing or older than its sources. Build as your regular user:" >&2; \
	echo "    make build     — then rerun sudo make install" >&2; exit 1; fi

build: paths $(CORE_BIN) $(SHADERS) build-sudo build-capture
install: $(CORE_BIN) $(SHADERS) check-sudo-build $(CAPTURE_BIN)

$(CORE_BIN): $(CORE_SRC)
	@$(NOT_ROOT)
	$(MAKE) build-core
	touch $@

$(SHADERS) &: $(SHADER_SRC)
	@$(NOT_ROOT)
	@[ -x "$(QSB)" ] || { echo "make: qsb is missing ($(QSB)); install qt6-shadertools (packaging/emaki-config)." >&2; exit 1; }
	QSB="$(QSB)" python scripts/build-shell-shaders

# No live Wayland; fixtures use .cache/, with short temporary Unix-socket paths. The qs in PATH
# must carry the packaged quickshell-emaki patches (tests/test-quickshell-fork.py).
.PHONY: check-shell shell-shots shell-shaders
check-shell: paths shell-shaders check-sudo-build
	python3 tests/test-quickshell-fork.py
	python3 tests/test-quickshell-fork-guard.py
	python3 tests/test-window-switcher-lifecycle.py
	python3 tests/test-dispersion.py
	python3 tests/test-backdrop-first-frame.py
	python3 tests/dispersion-frames.py --regression
	python3 tests/test-dispersion-frames.py
	python3 tests/test-settings-appearance.py
	python3 tests/test-settings-navigation.py
	python3 tests/test-settings-read-recovery.py
	python3 tests/test-settings-live-policy.py
	python3 tests/test-settings-snapshot-recovery.py
	python3 update-manager/ui/tests/palette.py
	python3 tests/test-settings-privacy-page.py
	python3 tests/test-settings-system-pages.py
	python3 tests/test-settings-apps-page.py
	python3 tests/test-settings-region-about.py
	python3 tests/test-report-window.py
	python3 tests/test-settings-maintenance-page.py
	python3 tests/test-secureboot-settings.py
	python3 tests/test-secureboot-notice.py
	python3 tests/test-settings-storage-page.py
	python3 tests/test-settings-users-page.py
	python3 tests/test-settings-chooser-lifetime.py
	node tests/test-settings-index.mjs
	node tests/test-notification-policy.mjs
	python3 tests/test-notification-store.py
	python3 tests/test-notification-reach.py
	python3 tests/test-settings-sound-backend.py
	python3 tests/test-settings-sound-page.py
	python3 tests/test-notifications-settings.py
	python3 tests/test-clock-notification-text.py
	python3 tests/test-settings-controls.py
	python3 tests/test-settings-launcher.py
	python3 tests/test-keyboard-settings-page.py
	python3 tests/test-mouse-settings-page.py
	python3 tests/test-settings-desktop.py
	python3 tests/test-wallpaper-chooser.py
	python3 tests/test-settings-visual.py
	python3 tests/test-system-settings-page.py
	python3 tests/test-displays-page.py
	python3 tests/test-displays-lifecycle.py
	python3 tests/test-night-schedule.py
	python3 tests/test-settings-bluetooth.py
	python3 tests/test-settings-wifi-page.py
	python3 tests/test-wifi-password.py
	python3 tests/test-settings-network.py
	python3 tests/test-settings-network-page.py
	python3 tests/test-wifi-recovery-shell.py
	python3 update-manager/ui/tests/interactions.py
	python3 update-manager/ui/tests/controller.py
	python3 update-manager/ui/tests/programs_controller.py
	python3 update-manager/ui/tests/icons.py
	python3 update-manager/ui/tests/reviews.py
	python3 update-manager/ui/tests/layout.py
	python3 tests/test-render-fonts.py
	mkdir -p "$(CURDIR)/.cache/tmp"
	env $(CORE_ENV) TMPDIR="$(CURDIR)/.cache/tmp" cargo build -p emaki-cli --locked
	python3 tests/test-settings-catalog.py "$(CARGO_TARGET_DIR)/debug/emaki"
	python3 tests/test-appearance-live.py "$(CARGO_TARGET_DIR)/debug/emaki"
	python3 tests/test-accent-apps.py "$(CARGO_TARGET_DIR)/debug/emaki"
	python3 update-manager/ui/tests/appearance.py "$(CARGO_TARGET_DIR)/debug/emaki"
	python tests/test-shell.py
	python3 tests/test-polkit-prompt.py
	python3 tests/test-shortcuts.py
	python3 tests/test-launcher-keyboard.py
	python3 tests/test-prompt-morph.py
	python3 tests/test-launch-handoff.py
	python3 tests/test-launcher-refresh.py
	python3 tests/test-launcher-icon-refresh.py
	python3 tests/test-spatial-keyboard.py
	python3 tests/test-surface-spatial.py
	python3 tests/test-system-keyboard.py
	python3 tests/test-shell-keyboard.py
	python3 tests/test-keyboard-gaps.py
	python3 tests/test-shell-outputs.py
	python3 tests/test-clipboard-recorder.py
	python3 tests/test-shell-recovery.py
	python3 tests/test-trusted-shell-launch.py
	python3 tests/test-login-handoff.py
	python3 tests/test-application-environment.py
	python3 tests/test-shell-health.py
	python3 tests/test-shell-recovery-notice.py
	python3 tests/test-session-state.py
	python3 tests/test-displays.py
	python3 tests/test-displays-mutants.py
	python3 tests/test-session-files.py
	python3 tests/test-shell-generations.py
	python3 tests/test-desktop-state.py
	python3 tests/test-session-update-notice.py
	python3 tests/test-session-update-hook.py
	python3 tests/test-update-errors.py
	python3 tests/test-auth-lockout.py
	node tests/test-battery-policy.mjs
	python3 tests/test-battery-layout.py
	python3 tests/test-system-warmup.py
	node tests/test-notification-text.mjs
	node tests/test-pairing-policy.mjs --mutations
	node tests/test-screen-share.mjs
	python3 tests/test-pairing-layout.py
	python tests/test-app-scope.py
	python tests/test-allocator-env.py
	python tests/test-app-scope-dbus.py
	python tests/test-recent-files.py
	python tests/test-wallpaper.py
	python tests/test-backdrop.py
	python tests/test-dock-region.py
	python3 tests/test-dock-drop.py
	python tests/test-greeter-wallpaper.py
	python tests/test-greeter-state.py
	python tests/test-greeter-compositor.py
	python tests/test-greeter-visual.py
	python tests/test-greeter-auth.py
	python tests/test-greeter-grab.py
	python tests/test-greeter-entry.py
	python tests/test-greeter-harness.py
	python tests/test-greeter-users.py
	python tests/test-drm-hold.py
	python tests/test-session-environment.py
	python tests/test-session-start.py
	python tests/test-session-start-cover.py
	python tests/test-session-start-shell.py
	python tests/test-session-start-harness.py
	python tests/test-notifications.py
	python tests/test-launcher-tools.py
	python3 tests/test-private-job-lists.py
	python tests/test-launcher-details.py
	python tests/check-launcher-inventory.py --self-test
	python tests/test-system.py
	python3 tests/test-account-identity.py
	python tests/test-sound-meter.py
	python tests/test-measure-shell.py
	python tests/test-lock-supervisor-unit.py
	python3 tests/test-lock-fallback-heartbeat.py
	python3 tests/test-lock-frozen-recovery.py
	python3 tests/test-lock-layout.py
	python tests/test-lock-supervisor.py
	python tests/test-lock-wiring.py
	python tests/test-lock-auth.py
	python3 tests/test-rollback.py
	python3 tests/test-rollback-shell.py
	python tests/test-password-toggle.py
	python tests/test-welcome.py
	python tests/test-lock-glass.py
	python tests/test-lock-capture.py
	python tests/test-lock-session.py
	python tests/test-lock-avatar.py
	python tests/test-lock-avatar-startup.py
	python tests/test-lock-wordmark.py
	python tests/test-bar.py
	python3 tests/test-panel-dock-policy.py
	python tests/test-strip.py
	python tests/test-shell-niri-actions.py "$(CARGO_TARGET_DIR)/debug/emaki"
	python tests/test-shell-close.py "$(CARGO_TARGET_DIR)/debug/emaki"
	python tests/test-dock.py "$(CARGO_TARGET_DIR)/debug/emaki"
	python tests/test-window-switcher.py "$(CARGO_TARGET_DIR)/debug/emaki"
	# Package payloads and metadata; prepares the selected cache, then builds the core offline.
	python3 tests/test-packaging.py
	python3 tests/test-desktop-devices.py
	python3 tests/test-kde-defaults.py
	$(MAKE) shell-shots

# No process a test starts outlives it: a representative set of shell tests, plus one run
# interrupted and one stopped by its own time limit, each checked for survivors. Short socket
# paths as for check-shell.
.PHONY: check-leaks
check-leaks: shell-shaders
	python tests/leak-check.py

# Actual QML pixels; software/offscreen only, synthetic data, no session services.
shell-shaders:
	python scripts/build-shell-shaders

shell-shots: shell-shaders
	python tests/shell-shots.py

# GPU pictures of the liquid glass (production dock.frag through qsb → WebGL2 in headless
# Firefox). Opt-in, not in check-shell: needs Firefox, a GPU and the session's display for
# EGL (no window is opened). Output: .cache/glass-shots/.
.PHONY: glass-shots
glass-shots:
	python tests/glass-shots.py

# Production Qt shaders through test-only surfaceless llvmpipe; no live display.
# Supply a read-only baseline checkout/archive containing shell/.
SHELL_RENDER_BASELINE ?=
SHELL_RENDER_OUTPUT ?= .cache/shell-offscreen
.PHONY: shell-offscreen
shell-offscreen: shell-shaders
	@test -n "$(SHELL_RENDER_BASELINE)" || { echo 'Set SHELL_RENDER_BASELINE=/path/to/baseline'; exit 2; }
	python tests/compare-shell-glass.py --baseline "$(SHELL_RENDER_BASELINE)" --output "$(SHELL_RENDER_OUTPUT)/glass"
	python tests/measure-sound-offscreen.py --before "$(SHELL_RENDER_BASELINE)" --output "$(SHELL_RENDER_OUTPUT)/sound" --seconds 3

.PHONY: shell-config
EMAKI_TERMINAL ?= kitty
QML_TOOLS_DIR ?= /usr/lib/qt6/bin
shell-config:
	python scripts/render-shell-tools --terminal "$(EMAKI_TERMINAL)"
	$(QML_TOOLS_DIR)/qmlformat -i shell/ShellTools.qml

# Canonical wordmark pixels; checked in for installation without build-time artwork tools.
.PHONY: lock-wordmark
lock-wordmark:
	python scripts/build-lock-wordmark

# Checked-in BMP: installation copies it without running Python/artwork tools.
.PHONY: boot-splash
boot-splash:
	python3 scripts/build-boot-splash

# Checked-in startup logo watermark, from the same pixels as the BMP above.
.PHONY: plymouth-theme
plymouth-theme:
	python3 scripts/build-plymouth-theme

.PHONY: cursors
cursors:
	python3 scripts/build-cursors

.PHONY: fetch-assets
fetch-assets:
	python3 scripts/build-fetch

# The recorder uses runtime GStreamer plugins; no recorder SDK is needed to build.
.PHONY: build-capture
build-capture: $(CAPTURE_BIN) $(CAPTURE_BUILD)/dmabuf.py

$(CAPTURE_BIN): capture/recorder.py
	@$(NOT_ROOT)
	install -Dm755 $< "$@"

$(CAPTURE_BUILD)/dmabuf.py: capture/dmabuf.py
	@$(NOT_ROOT)
	install -Dm644 $< "$@"

# PAM, root service and the shell's native transport are one base payload.
.PHONY: build-sudo check-sudo-build
build-sudo:
	@$(NOT_ROOT)
	cmake -S sudo -B "$(SUDO_BUILD)" -DCMAKE_BUILD_TYPE=RelWithDebInfo \
		-DCMAKE_INSTALL_PREFIX="$(PREFIX)" -DEMAKI_DATADIR="$(DATADIR)" -DEMAKI_LIBEXECDIR="$(LIBEXECDIR)" \
		-DEMAKI_PAM_MODULE="$(PREFIX)/lib/security/pam_emaki_sudo.so"
	cmake --build "$(SUDO_BUILD)" --parallel 2
	touch $(SUDO_ARTIFACTS)

$(SUDO_ARTIFACTS) &: $(SUDO_SRC)
	@$(NOT_ROOT)
	$(MAKE) build-sudo

check-sudo-build: $(SUDO_ARTIFACTS)
	@if [ "$$(cat $(SUDO_BUILD)/install-paths 2>/dev/null)" != '$(PREFIX)|$(DATADIR)|$(LIBEXECDIR)' ]; then \
		$(NOT_ROOT); $(MAKE) build-sudo; fi

.PHONY: install-sudo-turn-integration
install-sudo-turn-integration:
	install -Dm755 scripts/emaki-sudo-turn $(BIN)/emaki-sudo-turn
	sed -i 's|@EMAKI_PYTHON@|$(EMAKI_PYTHON)|g; s|@EMAKI_BINDIR@|$(PREFIX)/bin|g' $(BIN)/emaki-sudo-turn
	install -Dm644 sudo/turn-reporting.md $(SHARE)/sudo/turn-reporting.md
	install -Dm644 -t $(SHARE)/sudo/integrations sudo/integrations/claude-hooks.json sudo/integrations/codex-notify.toml
	sed -i 's|@EMAKI_BINDIR@|$(PREFIX)/bin|g' $(SHARE)/sudo/integrations/claude-hooks.json $(SHARE)/sudo/integrations/codex-notify.toml

# Explicit llvmpipe reference comparison; requires the native shell renderer.
.PHONY: check-theme-pixels
check-theme-pixels: paths shell-shaders
	python3 tests/theme-pictures.py --compare-only
	python3 tests/test-shared-canvas.py
	python3 tests/test-theme-slider-rims.py
	python3 tests/test-theme-accent-icons.py
