# Emaki — installation.
#
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

PREFIX  ?= /usr
DESTDIR ?=

BIN     = $(DESTDIR)$(PREFIX)/bin
SHARE   = $(DESTDIR)$(PREFIX)/share/emaki
XDG     = $(DESTDIR)/etc/xdg
ICONS   = $(DESTDIR)$(PREFIX)/share/icons
SYSTEMD = $(DESTDIR)$(PREFIX)/lib/systemd
# The vendor PAM directory is compiled into libpam (/usr/lib/pam.d on Arch),
# so it does not depend on PREFIX.
PAMDIR  = $(DESTDIR)/usr/lib/pam.d
# niri only looks here for the system config (src/main.rs); PREFIX does not affect the path.
NIRI_ETC = $(DESTDIR)/etc/niri/config.kdl
# Header string from niri/system.kdl: install and uninstall use it to recognize our file
# and leave any other /etc/niri/config.kdl alone.
NIRI_ETC_MARK = Emaki — system niri config

SCRIPTS = scripts/emaki-rollback scripts/emaki-drm-hold scripts/emaki-greeter-compositor scripts/emaki-greeter-run scripts/emaki-session-import-environment scripts/emaki-idle scripts/emaki-config-path scripts/emaki-power scripts/emaki-shell scripts/emaki-lock scripts/emaki-greeter-provision scripts/emaki-session-wallpaper scripts/emaki-sleep-guard scripts/emaki-text-session
# Shell: QML, helpers, shader — copied as is to $(SHARE)/shell. Core: release binary
# from `make build` (prefix/datadir paths are compiled in; see CORE_ENV below).
SHELL_SRC = $(shell find shell -type f ! -path '*/__pycache__/*' ! -name '*.pyc' ! -name '*.pyo')
QSB      ?= $(or $(shell command -v qsb),/usr/lib/qt6/bin/qsb)
CORE_BIN  = $(CARGO_TARGET_DIR)/release/emaki

.PHONY: render install uninstall check check-deps build

render:
	./scripts/render-theme
	python scripts/render-shell-palette
	python3 scripts/render-kde-theme

check:
	niri validate -c niri/default.kdl
	niri validate -c greetd/niri.kdl
	@if command -v niri-emaki >/dev/null 2>&1; then \
		niri-emaki validate -c niri/fork-rules.kdl; \
	else \
		echo 'Skipping fork-rules.kdl: niri-emaki is not installed (stock niri lacks fork-only nodes).'; \
	fi
	sh tests/test-niri-entry.sh
	sh tests/test-power.sh
	python3 tests/test-uninstall.py
	python3 tests/test-boot-splash.py
	python3 tests/test-fetch.py
	python3 tests/test-cursors.py
	python3 tests/test-iso-check-env.py
	python3 tests/test-status-words.py
	python3 tests/test-iso-install-harness.py
	EMAKI_TEST_UNIX_SOCKET=1 PYTHONPATH=installer python3 -m unittest discover -s installer/tests
	python3 tests/test-sleep-guard-unit.py
	python3 tests/test-sleep-guard.py
	sh tests/test-idle.sh
	python3 tests/test-keyboard-single-source.py
	python3 tests/test-reaper.py
	python3 tests/test-arch-watch.py

# Every metapackage dependency is in the official repositories (requires the pacman -Sy database).
check-deps:
	bash tests/test-desktop-deps.sh

# The installer window: lint, helpers, protocol, controller over a real unix socket,
# pointer/key interactions and offscreen renders.
.PHONY: check-installer check-iso check-updates check-all
check-installer:
	python3 installer/ui/tests/check.py
	python3 -m unittest discover -s installer/ui/tests -p 'test_*.py'
	node installer/ui/tests/test-protocol.js
	node installer/ui/tests/test-timezones.js
	python3 installer/ui/tests/controller.py --unix
	python3 installer/ui/tests/interactions.py
	python3 installer/ui/tests/render.py

# Static ISO profile checks and the offline tests of the release walk tools (no VM).
check-iso:
	bash iso/check.sh
	python3 tests/vm/eyes/test_eyes.py
	python3 tests/test-release-gate.py
	python3 tests/test-graphics-fallback-check.py

# The package mirror: publisher, R2 client, pointer Worker, key-backup check, upgrade-check
# helpers (no network, no Cloudflare; see docs/mirror.md).
check-updates:
	python3 tests/test-iso-sources.py
	python3 tests/test-source-archives.py
	python3 tests/test-publish.py
	python3 tests/test-r2-client.py
	python3 tests/test-upgrade-check.py
	node tests/test-pointer-worker.mjs
	bash tests/test-key-backup.sh

# Everything a release candidate passes on the host before any VM job (tests/vm/release-gate.sh).
check-all: check check-deps check-shell check-installer check-iso check-updates check-leaks

install:
	@if [ -e "$(XDG)/fastfetch/config.jsonc" ] && ! grep -qF 'Emaki fetch default.' "$(XDG)/fastfetch/config.jsonc"; then \
		echo 'make install: existing non-Emaki fastfetch system config; not overwriting.' >&2; exit 1; fi
	@if [ -e "$(NIRI_ETC)" ] && ! grep -qF "$(NIRI_ETC_MARK)" "$(NIRI_ETC)"; then \
		echo "make install: $(NIRI_ETC) already exists and is not from Emaki — refusing to overwrite." >&2; \
		echo "Move your settings to ~/.config/niri/config.kdl and remove this file, then retry." >&2; \
		exit 1; fi
	install -Dm644 polkit/org.emaki.rollback.policy $(DESTDIR)$(PREFIX)/share/polkit-1/actions/org.emaki.rollback.policy
	install -Dm755 -t $(BIN) $(SCRIPTS)
	rm -f $(BIN)/emaki-session-cover
	install -Dm755 $(CORE_BIN) $(BIN)/emaki
	for f in $(SHELL_SRC); do install -Dm644 "$$f" "$(SHARE)/$$f"; done
	install -Dm644 shell/emaki-welcome.desktop $(DESTDIR)$(PREFIX)/share/applications/emaki-welcome.desktop
	install -Dm644 art/logo/mark.svg $(ICONS)/hicolor/scalable/apps/emaki-welcome.svg
	install -Dm644 docs/ZONES.md $(DESTDIR)$(PREFIX)/share/doc/emaki/ZONES.md
	# Cancellation now belongs to the attempt's existing greetd connection.
	rm -f "$(SHARE)/shell/helpers/greeter-cancel.py"
	# Dock glass shaders: built by `make build` in .cache/shell-shaders; only copied here.
	for f in $(SHADERS); do install -Dm644 "$$f" "$(SHARE)/shell/shaders/$$(basename "$$f")"; done
	install -Dm644 -t $(SHARE)/niri niri/default.kdl niri/theme.kdl niri/shell.kdl
	install -Dm644 niri/system.kdl $(NIRI_ETC)
	install -Dm644 -t $(SYSTEMD)/user systemd/emaki-shell.service systemd/emaki-sleep-guard.service systemd/emaki-idle.service
	install -Dm644 -t $(SHARE)/kitty kitty/theme.conf
	install -Dm644 -t $(SHARE)/qt6ct qt6ct/emaki.conf
	install -Dm644 packaging/emaki-config/kdeglobals $(XDG)/kdeglobals
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
	install -Dm644 art/grub/background.png $(SHARE)/grub/background.png
	install -Dm644 grub/90-emaki-grub-title.hook $(DESTDIR)$(PREFIX)/share/libalpm/hooks/90-emaki-grub-title.hook
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
	@commit='$(if $(EMAKI_COMMIT),$(EMAKI_COMMIT),$(shell git rev-parse --verify HEAD))'; \
		printf '%s\n' "$$commit" | grep -Eq '^[0-9a-f]{40}$$' || exit 1; \
		printf 'EMAKI_COMMIT=%s\n' "$$commit" >> $(DESTDIR)$(PREFIX)/lib/emaki-release
	install -Dm644 systemd/50-emaki.preset $(SYSTEMD)/system-preset/50-emaki.preset
	install -Dm644 systemd/logind.conf.d/50-emaki.conf $(SYSTEMD)/logind.conf.d/50-emaki.conf
	for f in wpaperd/config.toml kitty/kitty.conf qt6ct/qt6ct.conf; do \
		install -Dm644 "etc-skel/.config/$$f" "$(DESTDIR)/etc/skel/.config/$$f"; done
	# Cursor theme: files plus alias symlinks, replaced whole so a renamed cursor doesn't linger.
	rm -rf $(ICONS)/Emaki
	install -Dm644 cursors/Emaki/index.theme $(ICONS)/Emaki/index.theme
	install -dm755 $(ICONS)/Emaki/cursors
	cp -P --no-preserve=ownership cursors/Emaki/cursors/* $(ICONS)/Emaki/cursors/
	# Living wallpaper for niri-emaki (emaki-wallpaper in fork-rules.kdl): ring, front layer, train.
	install -Dm644 -t $(SHARE)/wallpaper art/wallpaper/ring.png art/wallpaper/ring-front.png art/wallpaper/train-frames.png art/wallpaper/train.json
	install -Dm644 -t $(SHARE)/fetch fetch/config.jsonc fetch/details.jsonc fetch/render.py fetch/wordmark.png fetch/wordmark.txt
	install -Dm644 fetch/config.jsonc $(XDG)/fastfetch/config.jsonc
	sed -i 's|/usr/share/emaki/fetch|$(PREFIX)/share/emaki/fetch|g' $(SHARE)/fetch/config.jsonc $(XDG)/fastfetch/config.jsonc
	install -Dm644 greetd/pam $(PAMDIR)/emaki-greetd
	install -Dm644 systemd/emaki-drm-hold.service $(SYSTEMD)/system/emaki-drm-hold.service
	install -Dm644 systemd/greetd.service.d/emaki.conf $(SYSTEMD)/system/greetd.service.d/emaki.conf
	install -Dm644 -t $(SYSTEMD)/user systemd/emaki-greeter-wallpaper.path systemd/emaki-greeter-wallpaper.service systemd/emaki-greeter-wallpaper-watch.service
	install -dm755 $(SYSTEMD)/user/graphical-session.target.wants
	ln -sfn ../emaki-greeter-wallpaper.path $(SYSTEMD)/user/graphical-session.target.wants/emaki-greeter-wallpaper.path
	ln -sfn ../emaki-greeter-wallpaper-watch.service $(SYSTEMD)/user/graphical-session.target.wants/emaki-greeter-wallpaper-watch.service
	install -Dm644 greetd/emaki-greeter.conf $(DESTDIR)$(PREFIX)/lib/tmpfiles.d/emaki-greeter.conf
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
	/usr/lib/pam.d/emaki-greetd $(PREFIX)/lib/systemd/system/greetd.service.d/emaki.conf
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
	rm -f $(DESTDIR)$(PREFIX)/share/libalpm/hooks/90-emaki-grub-title.hook $(DESTDIR)$(PREFIX)/share/libalpm/scripts/emaki-grub-title
	rm -f $(DESTDIR)/usr/lib/initcpio/hooks/emaki-snapshot-fstab $(DESTDIR)/usr/lib/initcpio/install/emaki-snapshot-fstab
	# Point /etc/os-release back at Arch's file before removing ours (source helper, as below).
	bash os-release/emaki-os-release --restore "$(if $(DESTDIR),$(DESTDIR),/)"
	rm -f $(DESTDIR)$(PREFIX)/share/libalpm/hooks/50-emaki-os-release.hook $(DESTDIR)$(PREFIX)/share/libalpm/hooks/50-emaki-os-release-remove.hook
	rm -f $(DESTDIR)$(PREFIX)/share/libalpm/scripts/emaki-os-release $(DESTDIR)$(PREFIX)/lib/emaki/os-release
	[ ! -d $(DESTDIR)$(PREFIX)/lib/emaki ] || rmdir --ignore-fail-on-non-empty $(DESTDIR)$(PREFIX)/lib/emaki
	# Use this source helper: older installed provisioners may not support purging.
	@if ! python3 -I scripts/emaki-greeter-provision --root "$(if $(DESTDIR),$(DESTDIR),/)" --purge-published; then \
		echo "uninstall: WARNING: published wallpaper cleanup incomplete at $(if $(DESTDIR),$(DESTDIR),)/var/lib/emaki-greeter; retained copies need administrator cleanup. Continuing removal." >&2; \
	fi
	rm -f $(BIN)/emaki-rollback $(DESTDIR)$(PREFIX)/share/polkit-1/actions/org.emaki.rollback.policy
	rm -f $(BIN)/emaki-drm-hold $(BIN)/emaki-greeter-compositor $(BIN)/emaki-greeter-run $(BIN)/emaki-text-session $(BIN)/emaki-session-import-environment $(SYSTEMD)/system/emaki-drm-hold.service
	rm -f $(BIN)/emaki-idle $(BIN)/emaki-config-path $(BIN)/emaki-power $(BIN)/emaki-shell $(BIN)/emaki-lock $(BIN)/emaki-session-cover $(BIN)/emaki-session-wallpaper $(BIN)/emaki
	# The fork session (install-niri-emaki) goes too: its wrapper reads configs from $(SHARE).
	rm -f $(BIN)/niri-emaki-session
	rm -f $(SHARE)/niri/fork.kdl $(SHARE)/niri/fork-system.kdl $(SHARE)/niri/fork-rules.kdl
	rm -f $(DESTDIR)$(PREFIX)/lib/systemd/user/niri-emaki.service
	rm -f $(DESTDIR)$(PREFIX)/share/wayland-sessions/niri-emaki.desktop
	rm -rf $(SHARE)
	rm -f $(DESTDIR)$(PREFIX)/share/applications/emaki-welcome.desktop $(ICONS)/hicolor/scalable/apps/emaki-welcome.svg
	rm -rf $(ICONS)/Emaki
	rm -f $(SYSTEMD)/user/emaki-shell.service $(SYSTEMD)/user/emaki-sleep-guard.service $(SYSTEMD)/user/emaki-idle.service
	rm -f $(BIN)/emaki-sleep-guard $(DESTDIR)$(PREFIX)/lib/emaki-release
	rm -f $(SYSTEMD)/system-preset/50-emaki.preset $(SYSTEMD)/logind.conf.d/50-emaki.conf
	for f in wpaperd/config.toml kitty/kitty.conf qt6ct/qt6ct.conf; do \
		if cmp -s "etc-skel/.config/$$f" "$(DESTDIR)/etc/skel/.config/$$f"; then \
			rm -f "$(DESTDIR)/etc/skel/.config/$$f"; fi; done
	# System defaults in /etc: remove only the file as shipped. An edited copy is the
	# administrator's (the package keeps it too, backup=), so it stays and is named.
	for pair in packaging/emaki-config/niri-portals.conf:xdg-desktop-portal/niri-portals.conf \
		hypr/hyprlock.conf:hypr/hyprlock.conf packaging/emaki-config/kdeglobals:kdeglobals \
		packaging/emaki-config/dolphinrc:dolphinrc packaging/emaki-config/mimeapps.list:mimeapps.list \
		packaging/emaki-config/emaki-applications.menu:menus/emaki-applications.menu \
		etc-skel/.config/qt6ct/qt6ct.conf:qt6ct/qt6ct.conf; do \
		src="$${pair%%:*}"; dst="$(XDG)/$${pair#*:}"; \
		if cmp -s "$$src" "$$dst"; then rm -f "$$dst"; \
		elif [ -e "$$dst" ]; then echo "uninstall: kept $$dst (it differs from the shipped file)"; fi; done
	if [ -f $(XDG)/fastfetch/config.jsonc ] && grep -qF 'Emaki fetch default.' $(XDG)/fastfetch/config.jsonc; then rm -f $(XDG)/fastfetch/config.jsonc; fi
	rm -f $(PAMDIR)/emaki-greetd $(SYSTEMD)/system/greetd.service.d/emaki.conf
	rm -f $(BIN)/emaki-greeter-provision $(DESTDIR)$(PREFIX)/lib/tmpfiles.d/emaki-greeter.conf
	rm -f $(SYSTEMD)/user/emaki-greeter-wallpaper.path $(SYSTEMD)/user/emaki-greeter-wallpaper.service $(SYSTEMD)/user/emaki-greeter-wallpaper-watch.service
	rm -f $(SYSTEMD)/user/graphical-session.target.wants/emaki-greeter-wallpaper.path $(SYSTEMD)/user/graphical-session.target.wants/emaki-greeter-wallpaper-watch.service
	[ ! -d $(SYSTEMD)/user/graphical-session.target.wants ] || rmdir --ignore-fail-on-non-empty $(SYSTEMD)/user/graphical-session.target.wants
	[ ! -d $(SYSTEMD)/system/greetd.service.d ] || rmdir --ignore-fail-on-non-empty $(SYSTEMD)/system/greetd.service.d
	if cmp -s niri/system.kdl "$(NIRI_ETC)"; then rm -f "$(NIRI_ETC)"; \
	elif [ -e "$(NIRI_ETC)" ] && grep -qF "$(NIRI_ETC_MARK)" "$(NIRI_ETC)"; then \
		echo "uninstall: kept $(NIRI_ETC) (it differs from the shipped file)"; fi
	[ ! -d $(DESTDIR)/etc/niri ] || rmdir --ignore-fail-on-non-empty $(DESTDIR)/etc/niri
	rm -f $(DESTDIR)$(PREFIX)/share/doc/emaki/ZONES.md
	[ ! -d $(DESTDIR)$(PREFIX)/share/doc/emaki ] || rmdir --ignore-fail-on-non-empty $(DESTDIR)$(PREFIX)/share/doc/emaki

# The emaki CLI binary (crates/), built reproducibly by scripts/core-package.py.
CARGO_HOME      ?= $(CURDIR)/.cache/cargo-home
CARGO_TARGET_DIR ?= $(CURDIR)/.cache/target
EMAKI_PREFIX     ?= $(PREFIX)
EMAKI_DATADIR    ?= $(EMAKI_PREFIX)/share/emaki
EMAKI_LIBEXECDIR ?= $(EMAKI_PREFIX)/libexec/emaki
EMAKI_SYSCONFDIR ?= /etc
EMAKI_BUSCTL     ?= busctl
EMAKI_PW_CLI     ?= pw-cli
EMAKI_NIRI       ?= niri

CORE_ENV = CARGO_HOME="$(CARGO_HOME)" CARGO_TARGET_DIR="$(CARGO_TARGET_DIR)" \
	EMAKI_PREFIX="$(EMAKI_PREFIX)" EMAKI_DATADIR="$(EMAKI_DATADIR)" \
	EMAKI_LIBEXECDIR="$(EMAKI_LIBEXECDIR)" EMAKI_SYSCONFDIR="$(EMAKI_SYSCONFDIR)" \
	EMAKI_BUSCTL="$(EMAKI_BUSCTL)" EMAKI_PW_CLI="$(EMAKI_PW_CLI)" EMAKI_NIRI="$(EMAKI_NIRI)"

.PHONY: build-core

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
	tokens.toml niri/default.kdl niri/theme.kdl niri/shell.kdl
SHADER_SRC = $(wildcard shell/shaders/*.frag)
SHADERS    = $(patsubst shell/shaders/%,.cache/shell-shaders/%.qsb,$(SHADER_SRC))
NOT_ROOT   = if [ "$$(id -u)" = 0 ]; then \
	echo "make: $@ is missing or older than its sources. Build as your regular user:" >&2; \
	echo "    make build     — then rerun sudo make install" >&2; exit 1; fi

build: $(CORE_BIN) $(SHADERS)
install: $(CORE_BIN) $(SHADERS)

$(CORE_BIN): $(CORE_SRC)
	@$(NOT_ROOT)
	$(MAKE) build-core
	touch $@

$(SHADERS) &: $(SHADER_SRC)
	@$(NOT_ROOT)
	@[ -x "$(QSB)" ] || { echo "make: qsb is missing ($(QSB)); install qt6-shadertools (packaging/emaki-config)." >&2; exit 1; }
	QSB="$(QSB)" python scripts/build-shell-shaders

# No live Wayland; fixtures use .cache/, with short temporary Unix-socket paths.
.PHONY: check-shell shell-shots shell-shaders
check-shell: shell-shaders
	mkdir -p "$(CURDIR)/.cache/tmp"
	env $(CORE_ENV) TMPDIR="$(CURDIR)/.cache/tmp" cargo build -p emaki-cli --locked
	python tests/test-shell.py
	python tests/test-app-scope.py
	python tests/test-allocator-env.py
	python tests/test-app-scope-dbus.py
	python tests/test-recent-files.py
	python tests/test-wallpaper.py
	python tests/test-backdrop.py
	python tests/test-dock-region.py
	python tests/test-greeter-wallpaper.py
	python tests/test-greeter-state.py
	python tests/test-greeter-compositor.py
	python tests/test-greeter-visual.py
	python tests/test-greeter-auth.py
	python tests/test-greeter-grab.py
	python tests/test-greeter-entry.py
	python tests/test-greeter-harness.py
	python tests/test-drm-hold.py
	python tests/test-session-environment.py
	python tests/test-session-start.py
	python tests/test-session-start-cover.py
	python tests/test-session-start-shell.py
	python tests/test-session-start-harness.py
	python tests/test-notifications.py
	python tests/test-launcher-tools.py
	python tests/test-system.py
	python tests/test-sound-meter.py
	python tests/test-measure-shell.py
	python tests/test-lock-supervisor-unit.py
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
	python tests/test-lock-wordmark.py
	python tests/test-bar.py
	python tests/test-strip.py
	python tests/test-shell-niri-actions.py "$(CARGO_TARGET_DIR)/debug/emaki"
	python tests/test-shell-close.py "$(CARGO_TARGET_DIR)/debug/emaki"
	python tests/test-dock.py "$(CARGO_TARGET_DIR)/debug/emaki"
	# Package payloads and metadata; builds the core offline from the cargo cache filled by the
	# cargo build above.
	python3 tests/test-packaging.py
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

.PHONY: cursors
cursors:
	python3 scripts/build-cursors

.PHONY: fetch-assets
fetch-assets:
	python3 scripts/build-fetch
