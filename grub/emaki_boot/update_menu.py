# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Render an update trial inside the existing, already unlocked GRUB session."""

from datetime import date as calendar_date
import re
import shlex


UUID = r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'
BEGIN = '# BEGIN Emaki update guard\n'
END = '# END Emaki update guard\n'
LEGACY_ATTEMPT = '''  if [ "$emaki_update_ready" = "1" ]; then
    if ! emaki_update_attempt; then
      return
    fi
  fi
'''
ATTEMPT = '''  if [ "$emaki_update_ready" = "1" ]; then
    emaki_update_attempt
  fi
'''


def require(condition, message):
    if not condition:
        raise ValueError(message)


def boot_commands(body, snapshot, root_uuid):
    """Accept only literal paths and arguments for the recorded root snapshot."""
    require(re.fullmatch(r'[1-9][0-9]*', str(snapshot)), 'Invalid snapshot number.')
    require(re.fullmatch(UUID, root_uuid), 'Invalid root filesystem UUID.')
    prefix = '/@snapshots/' + str(snapshot) + '/snapshot'
    logical = body.replace('\\\n', ' ')
    commands = []
    for line in logical.splitlines():
        match = re.match(r'^\s*(linux(?:efi)?|initrd(?:efi)?)\s+(.+)$', line)
        if match:
            require(not re.search(r'[;$`{}\x00-\x1f]', match[2]), 'Nonliteral snapshot boot command.')
            words = shlex.split(match[2])
            require(all(re.fullmatch(r'[A-Za-z0-9_./:=,+@%\-]+', word) for word in words),
                    'Unsupported snapshot boot argument.')
            commands.append((match[1], words))
    require(len(commands) == 2 and commands[0][0] in ('linux', 'linuxefi')
            and commands[1][0] in ('initrd', 'initrdefi'), 'Snapshot needs one kernel and initramfs command.')
    kernel, initrd = commands[0][1], commands[1][1]
    require(re.fullmatch(re.escape(prefix) + r'/boot/vmlinuz-linux(?:-lts)?', kernel[0]),
            'Snapshot kernel path differs from its root.')
    require([word for word in kernel if word.startswith('root=')] == ['root=UUID=' + root_uuid],
            'Snapshot filesystem differs from the installed root.')
    flags = [word[10:] for word in kernel if word.startswith('rootflags=')]
    require(len(flags) == 1 and [flag.lstrip('/') for flag in flags[0].split(',')
            if flag.startswith('subvol=')] in (["subvol=" + prefix], ["subvol=" + prefix.lstrip('/')]),
            'Snapshot subvolume differs from its boot files.')
    require(not any(flag.startswith('subvolid=') for flag in flags[0].split(',')),
            'Snapshot root must not pin a subvolume ID.')
    require(initrd and all(re.fullmatch(re.escape(prefix) + r'/boot/[A-Za-z0-9_.-]+', path)
                           and '..' not in path.split('/') for path in initrd),
            'Snapshot initramfs path differs from its root.')
    kernel_name = kernel[0].rsplit('/vmlinuz-', 1)[1]
    require(initrd[-1] in (prefix + '/boot/initramfs-' + kernel_name + '.img',
                           prefix + '/boot/initramfs-' + kernel_name + '-fallback.img'),
            'Snapshot initramfs does not match its kernel.')
    return commands


def extract_snapshot_body(menu, snapshot, root_uuid):
    """Select the normal initramfs by subvolume, never by menu order or IDs."""
    for match in re.finditer(r'(?m)^([ \t]*)menuentry [^\n]+\{\n', menu):
        close = re.search(r'(?m)^' + re.escape(match[1]) + r'}\s*$', menu[match.end():])
        if not close:
            continue
        body = menu[match.end():match.end() + close.start()]
        try:
            commands = boot_commands(body, snapshot, root_uuid)
        except ValueError:
            continue
        if commands[1][1][-1].endswith('-fallback.img'):
            continue
        return ''.join('  ' + command + ' ' + ' '.join(words) + '\n' for command, words in commands)
    raise ValueError('The pre-update snapshot has no usable boot entry.')


def wrap_menu(text, esp_uuid):
    """Source the shared guard after defaults, with arming after loader trials."""
    require(re.fullmatch(r'[0-9A-Fa-f-]+', esp_uuid), 'Invalid EFI filesystem UUID.')
    text = re.sub(re.escape(BEGIN) + r'.*?' + re.escape(END), '', text, flags=re.S)
    text = text.replace(LEGACY_ATTEMPT, '').replace(ATTEMPT, '')
    # Keep the installed default's exact arguments for a failed counter write.
    normal = re.search(r'(?m)^\s*(linux(?:efi)?\s+/\@/boot/vmlinuz-linux(?:-lts)?\s[^\n]*)'
                       r'\n(?:[^\n{}]*\n)*?[ \t]*(initrd(?:efi)?\s+[^\n]*)', text)
    require(normal is not None, 'The installed default has no kernel and initramfs pair.')
    # Explicit boot transfers control before GRUB's menu error-prompt path.
    # A failed save must never pass through `!`: GRUB treats non-test errors
    # differently from shell negation. The kernel marker disarms in userspace.
    unprotected = ('function emaki_update_unprotected_boot {\n'
                   '  set emaki_update_ready=\n'
                   '  ' + normal[1] + ' emaki.update_unprotected=1\n'
                   '  ' + normal[2] + '\n'
                   '  boot\n'
                   '}\n')
    text = re.sub(r'(?m)^(\s*linux(?:efi)?\s+/\@/boot/vmlinuz-linux(?:-lts)?\s[^\n]*)$',
                  lambda match: ATTEMPT + match[0], text)
    return text.rstrip('\n') + '\n' + BEGIN + unprotected + f'''set emaki_update_ready=
set emaki_update_esp=
search --no-floppy --fs-uuid --set=emaki_update_esp {esp_uuid}
if [ -n "$emaki_update_esp" ]; then
  if [ -f ($emaki_update_esp)/EFI/Emaki/update.cfg ]; then
    source ($emaki_update_esp)/EFI/Emaki/update.cfg
  fi
fi
''' + END


def render(snapshot_body, transaction, snapshot, date, root_uuid):
    """One update trial and one recovery attempt, both recorded before Linux."""
    require(re.fullmatch(r'[0-9a-f]{32}', transaction), 'Invalid update transaction.')
    require(re.fullmatch(r'\d{4}-\d{2}-\d{2}', date), 'Invalid snapshot date.')
    calendar_date.fromisoformat(date)
    commands = boot_commands(snapshot_body, snapshot, root_uuid)
    kernel = [word for word in commands[0][1] if word != 'noresume' and not word.startswith(
        ('resume=', 'resume_offset=', 'emaki.auto_return=', 'emaki.snapshot_date=', 'emaki.generation=',
         'emaki.update_unprotected='))]
    # An EFI hibernation location can survive removal of explicit resume arguments.
    kernel += ['noresume', 'emaki.snapshot_date=' + date]
    paths = [kernel[0], *commands[1][1]]
    checks = '[ ' + ' -a '.join('-f ($root)' + path for path in paths) + ' ]'
    linux = commands[0][0] + ' ' + ' '.join(kernel) + ' $emaki_return_argument'
    initrd = commands[1][0] + ' ' + ' '.join(commands[1][1])
    return f'''# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
set emaki_update=
set emaki_attempt=
set emaki_suppress=
set next_entry=
if load_env -f ($emaki_update_esp)/EFI/Emaki/update.env emaki_update emaki_attempt emaki_suppress next_entry; then
  if [ "$emaki_update" = "{transaction}" ]; then
    if [ "$emaki_attempt" = "0" -o "$emaki_attempt" = "1" -o "$emaki_attempt" = "2" ]; then
      set emaki_update_ready=1
    fi
  fi
fi
function emaki_update_attempt {{
  set emaki_suppress=
  if [ "$emaki_attempt" = "0" ]; then
    set emaki_attempt=1
    set next_entry=emaki-auto-recovery
  fi
  if save_env -f ($emaki_update_esp)/EFI/Emaki/update.env emaki_suppress emaki_attempt next_entry; then
    true
  else
    emaki_update_unprotected_boot
  fi
}}
if [ "$emaki_update_ready" = "1" ]; then
  menuentry 'Emaki recovery' --id emaki-auto-recovery {{
    # GRUB clears fallback on menu keyboard input. Use a visible timed menu so
    # only its unattended selection can claim an automatic return.
    set emaki_return_argument=
    if [ "$emaki_attempt" = "1" -a "$next_entry" = "emaki-auto-recovery" -a "$emaki_suppress" != "1" -a "$fallback" = "0" ]; then
      set emaki_return_argument=emaki.auto_return={transaction}
    fi
    set emaki_attempt=2
    set next_entry=
    if save_env -f ($emaki_update_esp)/EFI/Emaki/update.env emaki_attempt next_entry; then
      if search --no-floppy --fs-uuid --set=root {root_uuid}; then
        if {checks}; then
          set gfxpayload=keep
          {linux}
          {initrd}
        fi
      fi
    else
      emaki_update_unprotected_boot
    fi
  }}
  if [ "$emaki_attempt" = "1" -a "$next_entry" = "emaki-auto-recovery" ]; then
    if [ "$emaki_suppress" != "1" ]; then
      set default=emaki-auto-recovery
      set timeout_style=menu
      set fallback=
      if [ -n "$timeout" ]; then
        if [ "$timeout" -ge "0" ]; then
          set fallback=0
        fi
      fi
    fi
  fi
fi
'''
