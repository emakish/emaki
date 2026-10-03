#!/usr/bin/env python3
"""Finalize a copied profile; never operate on the caller's host configuration."""
import argparse
from pathlib import Path
import re


def prepare(profile, repo, version, key=None, template=False):
    root = profile / 'airootfs'
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('invalid version')
    (profile / 'VERSION').write_text(version + '\n')
    if not template:
        # No network fallback while constructing the live root; all packages have
        # already been resolved into this one signed offline repository.
        (profile / 'pacman.conf').write_text(f'''[options]
Architecture = x86_64
SigLevel = Required DatabaseOptional TrustedOnly
LocalFileSigLevel = Required TrustedOnly
CacheDir = {repo}

[emaki-offline]
SigLevel = Required DatabaseOptional TrustedOnly
Server = file://{repo}
''')
    if (root / 'etc/emaki-test').exists() or (root / 'home/live/.ssh').exists():
        raise ValueError('source profile contains test key material; use a clean release source')
    if key:
        public_key = key.read_bytes()
        for name in ('home/live/.ssh/authorized_keys', 'etc/emaki-test/authorized_keys'):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(public_key)
        # Match every Linux entry, including accessibility and alternate/PXE
        # variants, without touching memtest, chainloader or firmware entries.
        counts = {}
        for pattern in ('efiboot/loader/entries/*.conf', 'grub/*.cfg', 'syslinux/*.cfg'):
            count = 0
            for path in profile.glob(pattern):
                lines = []
                for line in path.read_text().splitlines():
                    if re.match(r'^\s*(?:options|linux(?:efi)?|APPEND)\s+', line, re.I) and ('archiso' in line or '%KERNEL_PARAMS%' in line):
                        if 'emaki.test=1' not in line:
                            line += ' emaki.test=1 console=ttyS0,115200'
                        count += 1
                    lines.append(line)
                path.write_text('\n'.join(lines) + '\n')
            counts[pattern] = count
        modes = (profile / 'profiledef.sh').read_text()
        for mode, pattern in [('bios.syslinux', 'syslinux/*.cfg'), ('uefi.grub', 'grub/*.cfg'),
                              ('uefi.systemd-boot', 'efiboot/loader/entries/*.conf')]:
            if mode in modes and counts[pattern] == 0:
                raise ValueError(f'no test kernel entry found for {mode}')
    elif not template:
        for path in [*profile.glob('efiboot/**/*.conf'), *profile.glob('grub/*.cfg'), *profile.glob('syslinux/*.cfg')]:
            if 'emaki.test=1' in path.read_text():
                raise ValueError(f'test kernel argument in release source: {path}')
    definition = profile / 'profiledef.sh'
    text = definition.read_text().split('# BEGIN EMAKI PERMISSIONS\n')[0]
    # Explicit modes for all overlay paths. Never apply a recursive directory
    # mode: it would turn files executable or remove directory search permission.
    text += '# BEGIN EMAKI PERMISSIONS\nfile_permissions+=(\n'
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            continue
        relative = '/' + str(path.relative_to(root))
        uid = gid = 1000 if relative == '/home/live' or relative.startswith('/home/live/') else 0
        mode = '0755' if path.is_dir() else '0644'
        if relative in ('/root', '/root/.gnupg'):
            mode = '0700'
        elif relative == '/etc/shadow':
            mode = '0400'
        elif relative == '/etc/sudoers.d/10-live':
            mode = '0440'
        elif relative in ('/home/live', '/home/live/.ssh', '/etc/emaki-test'):
            mode = '0700'
        elif relative.endswith('/authorized_keys'):
            mode = '0600'
        elif path.is_file() and path.stat().st_mode & 0o111:
            mode = '0755'
        text += f'  ["{relative}"]="{uid}:{gid}:{mode}"\n'
    text += ')\n# END EMAKI PERMISSIONS\n'
    definition.write_text(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('profile', type=Path)
    parser.add_argument('repo')
    parser.add_argument('version')
    parser.add_argument('--test-key', type=Path)
    parser.add_argument('--template', action='store_true')
    args = parser.parse_args()
    prepare(args.profile, args.repo, args.version, args.test_key, args.template)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as error:
        raise SystemExit(f'ERROR: {error}')
