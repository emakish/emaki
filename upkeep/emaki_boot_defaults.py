# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Adopt packaged boot defaults only at the installer's exact legacy settings."""
import os
import json
import re
from pathlib import Path
import tempfile


KERNELS = ('linux', 'linux-lts')
SPLASH_THEME = 'usr/share/plymouth/themes/emaki'
# The startup logo's one-time choice of Plymouth theme and the file it replaced.
SPLASH_LEDGER = 'var/lib/emaki/migrations/plymouth-theme.json'
GRUB_SOURCE = ('if [ -r /usr/share/emaki/boot/grub-btrfs.conf ]; then '
               '. /usr/share/emaki/boot/grub-btrfs.conf; fi\n')

# Exact lines from 0.2.0 and 0.1.x; keep independent of current hook policy.
# The 0.1.x ext4 line is identical to the unencrypted 0.2.0 ext4 line.
LEGACY_CHOICES = {
    'HOOKS=(base udev autodetect microcode modconf kms keyboard keymap consolefont block filesystems fsck)\n': (False, False, False),
    'HOOKS=(base udev autodetect microcode modconf kms keyboard keymap consolefont block resume filesystems fsck)\n': (False, False, True),
    'HOOKS=(base udev autodetect microcode modconf kms keyboard keymap consolefont block encrypt filesystems fsck)\n': (False, True, False),
    'HOOKS=(base udev autodetect microcode modconf kms keyboard keymap consolefont block encrypt resume filesystems fsck)\n': (False, True, True),
    'HOOKS=(base udev autodetect microcode modconf kms keyboard keymap consolefont block filesystems grub-btrfs-overlayfs emaki-snapshot-fstab)\n': (True, False, False),
    'HOOKS=(base udev autodetect microcode modconf kms keyboard keymap consolefont block resume filesystems grub-btrfs-overlayfs emaki-snapshot-fstab)\n': (True, False, True),
    'HOOKS=(base udev autodetect microcode modconf kms keyboard keymap consolefont block encrypt filesystems grub-btrfs-overlayfs emaki-snapshot-fstab)\n': (True, True, False),
    'HOOKS=(base udev autodetect microcode modconf kms keyboard keymap consolefont block encrypt resume filesystems grub-btrfs-overlayfs emaki-snapshot-fstab)\n': (True, True, True),
    'HOOKS=(base udev autodetect microcode modconf kms keyboard keymap consolefont block filesystems grub-btrfs-overlayfs fsck)\n': (True, False, False),
}


def safe_file(path):
    return (not any(p.is_symlink() for p in (path, *path.parents))
            and path.is_file() and path.stat().st_nlink == 1)


def replace(path, text):
    metadata = path.stat()
    fd, name = tempfile.mkstemp(prefix='.emaki-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            os.fchmod(output.fileno(), metadata.st_mode & 0o7777)
            os.fchown(output.fileno(), metadata.st_uid, metadata.st_gid)
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def legacy_preset(kernel):
    return ('ALL_config="/etc/mkinitcpio.conf"\n'
            f'ALL_kver="/boot/vmlinuz-{kernel}"\n'
            "PRESETS=('default' 'fallback')\n"
            f'default_image="/boot/initramfs-{kernel}.img"\n'
            f'fallback_image="/boot/initramfs-{kernel}-fallback.img"\n'
            'fallback_options="-S autodetect"\n')


def hook_line(btrfs, encrypted, hibernation):
    hooks = 'base udev autodetect microcode modconf kms keyboard keymap consolefont block'
    if encrypted:
        hooks += ' encrypt'
    if hibernation:
        hooks += ' emaki-resume'
    hooks += ' filesystems'
    hooks += ' grub-btrfs-overlayfs emaki-snapshot-fstab' if btrfs else ' fsck'
    return f'HOOKS=({hooks})\n'


def machine_hooks(text):
    """Update unchanged managed hooks using recorded machine choices."""
    lines = text.splitlines(keepends=True)
    assignments = [line for line in lines if line.lstrip().startswith('HOOKS=')]
    markers = [line for line in lines
               if line.startswith(('# Emaki HOOKS=', '# Emaki btrfs='))]
    if len(assignments) != 1 or len(markers) > 1:
        return text
    old = assignments[0]
    if markers and markers[0].startswith('# Emaki btrfs='):
        match = re.fullmatch(r'# Emaki btrfs=([01]) encrypted=([01]) hibernation=([01]) (HOOKS=\([^\n]*\)\n)',
                             markers[0])
        if not match or match[4] != old:
            return text
        choices = tuple(value == '1' for value in match.group(1, 2, 3))
    else:
        # Earlier managed markers can only be adopted through the same exact table.
        if markers and markers[0] != '# Emaki ' + old:
            return text
        choices = LEGACY_CHOICES.get(old)
        if choices is None:
            return text
    new = hook_line(*choices)
    btrfs, encrypted, hibernation = map(int, choices)
    marker = (f'# Emaki btrfs={btrfs} encrypted={encrypted} '
              f'hibernation={hibernation} ' + new)
    return ''.join((new if markers else new + marker) if line == old
                   else marker if line in markers else line for line in lines)


def queue_presets(root, kernels):
    """Persist pending work before changing inputs, so failures can be retried."""
    pending = root / 'var/lib/emaki/migrations/initramfs-pending'
    if any(p.is_symlink() for p in (pending, *pending.parents)):
        raise OSError('Unsafe initramfs refresh state path.')
    pending.parent.mkdir(parents=True, exist_ok=True)
    previous = pending.read_text().splitlines() if pending.exists() else []
    if not pending.exists():
        pending.touch(mode=0o600)
    if not safe_file(pending):
        raise OSError('Unsafe initramfs refresh state file.')
    replace(pending, ''.join(kernel + '\n' for kernel in KERNELS
                             if kernel in set(previous) | set(kernels)))


def record_release(path, text):
    """Record fallback bytes before changing an unowned configuration file."""
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise OSError('Unsafe boot transition state path.')
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch(mode=0o600)
    if not safe_file(path):
        raise OSError('Unsafe boot transition state file.')
    replace(path, text)


def release(root, removing=False):
    """Use native fallbacks before the package providing managed hooks leaves."""
    root = Path(root)
    main = root / 'etc/mkinitcpio.conf'
    if safe_file(main):
        text = main.read_text()
        lines = text.splitlines(keepends=True)
        hooks = [line for line in lines if line.lstrip().startswith('HOOKS=')]
        active = (re.fullmatch(r'(\s*HOOKS=\()([^()\n]*)(\)[^\n]*\n?)', hooks[0])
                  if len(hooks) == 1 else None)
        if active:
            # Replace only package tokens in the active array, including a local
            # reorder. Keep its marker so recorded machine choices survive removal.
            values = re.sub(r'''(?<!\S)(['"]?)emaki-resume\1(?!\S)''', r'\1resume\1', active[2])
            if removing:
                values = re.sub(r'''(^|\s+)(['"]?)emaki-snapshot-fstab\2(?!\S)''', '', values)
            fallback = active[1] + values + active[3]
            if fallback != hooks[0]:
                ledger = root / 'var/lib/emaki/migrations/released-hooks.json'
                previous = json.loads(ledger.read_text()) if safe_file(ledger) else {}
                original = (previous['original'] if previous.get('fallback') == hooks[0]
                            else hooks[0])
                record_release(ledger, json.dumps({'original': original, 'fallback': fallback}) + '\n')
                replace(main, ''.join(fallback if line == hooks[0] else line for line in lines))
    for kernel in KERNELS:
        path = root / f'etc/mkinitcpio.d/{kernel}.preset'
        if safe_file(path) and path.read_text() == f'. /usr/share/emaki/boot/{kernel}.preset\n':
            record_release(root / f'var/lib/emaki/migrations/released-preset-{kernel}',
                           legacy_preset(kernel))
            replace(path, legacy_preset(kernel))
    config, ledger = root / 'etc/plymouth/plymouthd.conf', root / SPLASH_LEDGER
    if removing and safe_file(ledger):
        # The theme leaves with the package: give back the file as it was before, unless
        # it was edited since; a later reinstallation chooses the theme again.
        record = json.loads(ledger.read_text())
        if record.get('adopted') and safe_file(config) and config.read_text() == record['adopted']:
            replace(config, record['original'])
        ledger.unlink()


def plymouth_theme(text):
    """Plymouth's configuration with Theme=emaki, or None when it already names a theme."""
    lines = text.splitlines(keepends=True)
    section, daemon = None, None
    for number, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('[') and stripped.endswith(']'):
            section = stripped[1:-1].strip()
            daemon = number if section == 'Daemon' and daemon is None else daemon
        elif section == 'Daemon' and re.match(r'Theme\s*=', stripped):
            return None
    if lines and not lines[-1].endswith('\n'):
        lines[-1] += '\n'
    if daemon is None:
        return ''.join(lines) + '[Daemon]\nTheme=emaki\n'
    lines.insert(daemon + 1, 'Theme=emaki\n')
    return ''.join(lines)


def splash(root):
    """Choose the packaged startup logo once, unless the administrator named a theme.

    Without a theme Plymouth falls back to Arch's bgrt, which shows the Arch logo. The
    images carry plymouthd.conf, so they are queued before the choice is written. In an
    emaki-config transaction the stock mkinitcpio hook builds them (the package ships files
    under usr/lib/initcpio). A retry after a failed write can come in an emaki-desktop or
    emaki-apps transaction; there the 93 hook builds them, because it fires for the same
    packages as the 89 hook. The installer builds the images itself after pacstrap."""
    if not (safe_file(root / SPLASH_THEME / 'emaki.plymouth')
            and (root / 'usr/lib/initcpio/install/plymouth').is_file()):
        return False
    config, ledger = root / 'etc/plymouth/plymouthd.conf', root / SPLASH_LEDGER
    if not safe_file(config) or ledger.exists() or ledger.is_symlink():
        return False
    original = config.read_text()
    adopted = plymouth_theme(original)
    try:
        # Recorded first and once: a theme the administrator removes later stays removed.
        record_release(ledger, json.dumps({'original': original, 'adopted': adopted}) + '\n')
        if adopted is None:
            return False
        queue_presets(root, KERNELS)
        replace(config, adopted)
    except BaseException:
        # Not chosen after all (a failed write): the next transaction tries again.
        if safe_file(ledger):
            ledger.unlink()
        raise
    return True


def apply(root):
    """Return whether boot inputs changed; the caller owns versioning and refresh."""
    root = Path(root)
    changed = False
    main = root / 'etc/mkinitcpio.conf'
    ledger = root / 'var/lib/emaki/migrations/released-hooks.json'
    if safe_file(ledger) and safe_file(main):
        released = json.loads(ledger.read_text())
        lines = main.read_text().splitlines(keepends=True)
        hooks = [line for line in lines if line.lstrip().startswith('HOOKS=')]
        # Restoring our own fallback is independent of preset adoption: a local
        # preset edit must not strand the configuration in its released state.
        if hooks == [released['fallback']]:
            queue_presets(root, KERNELS)
            replace(main, ''.join(released['original'] if line == hooks[0] else line for line in lines))
            changed = True
        ledger.unlink()
    presets = [root / f'etc/mkinitcpio.d/{kernel}.preset' for kernel in KERNELS]
    defaults = [root / f'usr/share/emaki/boot/{kernel}.preset' for kernel in KERNELS]
    for kernel, path, default in zip(KERNELS, presets, defaults):
        ledger = root / f'var/lib/emaki/migrations/released-preset-{kernel}'
        if safe_file(ledger) and safe_file(default):
            if safe_file(path) and path.read_text() == ledger.read_text():
                queue_presets(root, [kernel])
                replace(path, f'. /usr/share/emaki/boot/{kernel}.preset\n')
                changed = True
            ledger.unlink()
    if all(safe_file(path) for path in (main, *presets)):
        original = main.read_text()
        updated = machine_hooks(original)
        # Preserve custom presets and their boot configuration as a whole.
        # Released installer presets remain self-contained; fresh package sources
        # are also recognized, but only those sources need the packaged file.
        expected = [legacy_preset(kernel) for kernel in KERNELS]
        sources = [f'. /usr/share/emaki/boot/{kernel}.preset\n' for kernel in KERNELS]
        if updated != original and all(
                path.read_text() == old or (path.read_text() == source and safe_file(default))
                for path, old, source, default in zip(presets, expected, sources, defaults)):
            old_hooks = [line for line in original.splitlines() if line.startswith('HOOKS=')]
            new_hooks = [line for line in updated.splitlines() if line.startswith('HOOKS=')]
            if old_hooks != new_hooks:
                queue_presets(root, KERNELS)
            replace(main, updated)
            changed = True
    grub = root / 'etc/default/grub-btrfs/config'
    defaults = root / 'usr/share/emaki/boot/grub-btrfs.conf'
    if safe_file(grub) and safe_file(defaults):
        original = grub.read_text()
        lines = original.splitlines(keepends=True)
        old = 'GRUB_BTRFS_SUBMENUNAME="Emaki snapshots"\n'
        assignments = [line for line in lines
                       if line.lstrip().removeprefix('export ').startswith('GRUB_BTRFS_SUBMENUNAME=')]
        bare = '. /usr/share/emaki/boot/grub-btrfs.conf\n'
        if ((assignments == [old] and GRUB_SOURCE not in lines)
                or (not assignments and bare in lines)):
            replace(grub, ''.join(GRUB_SOURCE if line in (old, bare) else line for line in lines))
            changed = True
    changed = splash(root) or changed
    # /etc/default/grub contains the measured swap offset and LUKS/root UUIDs.
    # They remain machine-owned; adopting package defaults never recomputes them.
    return changed
