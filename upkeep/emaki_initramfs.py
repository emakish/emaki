# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Coordinate pending migrations with the stock initramfs transaction."""
from pathlib import Path
import subprocess

from emaki_boot_defaults import safe_file, replace

HOOK = 'etc/pacman.d/hooks/90-mkinitcpio-install.hook'
LEDGER = 'var/lib/emaki/migrations/mkinitcpio-hook'
RECEIPT = 'run/emaki/initramfs-stock-ran'
PENDING = 'var/lib/emaki/migrations/initramfs-pending'
STOCK_EXEC = 'Exec = /usr/share/libalpm/scripts/mkinitcpio install\n'
WRAPPER_EXEC = 'Exec = /usr/share/libalpm/scripts/emaki-initramfs-refresh --stock\n'


def write(path, text):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise OSError('Unsafe initramfs transaction state path.')
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch(mode=0o600)
    if not safe_file(path):
        raise OSError('Unsafe initramfs transaction state file.')
    replace(path, text)


def release(root):
    """Remove only our exact override before replacing or removing the package."""
    hook, ledger = root / HOOK, root / LEDGER
    if safe_file(hook) and safe_file(ledger) and hook.read_text() == ledger.read_text():
        hook.unlink()
        ledger.unlink()


def install(root):
    """Wrap stock triggers without replacing an administrator's hook."""
    hook, ledger = root / HOOK, root / LEDGER
    stock_hook = root / 'usr/share/libalpm/hooks/90-mkinitcpio-install.hook'
    if not safe_file(stock_hook):
        release(root)
        return
    text = stock_hook.read_text()
    if text.count(STOCK_EXEC) != 1 or 'NeedsTargets\n' not in text:
        release(root)
        return
    if hook.exists() or hook.is_symlink():
        if not (safe_file(hook) and safe_file(ledger)
                and hook.read_text() == ledger.read_text()):
            return
    text = ('# Copyright (C) 2026 Artur Yakymenko\n'
            '# SPDX-License-Identifier: GPL-3.0-or-later\n'
            '# Emaki pending initramfs transaction coordination.\n'
            + text.replace(STOCK_EXEC, WRAPPER_EXEC))
    write(ledger, text)
    write(hook, text)


def begin(root):
    # Refresh the saved triggers after mkinitcpio installs its new stock hook.
    install(root)
    receipt = root / RECEIPT
    if safe_file(receipt):
        receipt.unlink()


def stock(root=Path('/'), targets='', run=subprocess.run):
    """Retain stock kernel installation and account for its actual exit status."""
    pending = root / PENDING
    requested = safe_file(pending)
    if requested:
        print('Retrying pending initramfs migration in the kernel transaction [Emaki].', flush=True)
        # The stock helper treats an initcpio change as an all-preset rebuild.
        # It still copies updated kernels and creates missing presets first.
        targets = targets.rstrip('\n') + '\nusr/lib/initcpio/emaki-pending\n'
    write(root / RECEIPT, 'started\n')
    try:
        run(['/usr/share/libalpm/scripts/mkinitcpio', 'install'],
            input=targets, text=True, check=True)
    except FileNotFoundError:
        # mkinitcpio is being removed in this transaction; its stock hook leaves with it.
        (root / RECEIPT).unlink(missing_ok=True)
        print('Initramfs refresh skipped: mkinitcpio is not installed [Emaki].', flush=True)
        return
    except subprocess.CalledProcessError:
        if requested:
            print('Initramfs migration remains pending; the next kernel or initramfs '
                  'transaction will retry. Boot refresh is blocked until it succeeds '
                  '[Emaki].', flush=True)
        raise
    if requested:
        pending.unlink()


if __name__ == '__main__':
    install(Path('/'))
