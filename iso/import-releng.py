#!/usr/bin/env python3
"""Copy an archiso 91 releng profile and apply the checked-in Emaki overlay.

This is also the recovery path when the authoring host cannot fetch releng.
The build VM must have archiso 91; build.sh checks that package version first.
"""
import argparse
import hashlib
from pathlib import Path
import re
import shutil
import subprocess

HERE = Path(__file__).resolve().parent


def copy_overlay(source, destination):
    for path in sorted(source.rglob('*')):
        relative = path.relative_to(source)
        target = destination / relative
        if path.is_symlink():
            if target.is_symlink() or target.is_file():
                target.unlink()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(path.readlink())
        elif path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            if target.is_symlink():
                target.unlink()
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def import_profile(source, destination):
    if destination.exists():
        raise ValueError('destination must not exist')
    required = ['profiledef.sh', 'packages.x86_64', 'pacman.conf', 'airootfs', 'grub', 'syslinux']
    if any(not (source / name).exists() for name in required):
        raise ValueError('incomplete releng source')
    original = (source / 'profiledef.sh').read_text()
    if 'bios.syslinux' not in original or 'uefi.' not in original:
        raise ValueError('unexpected releng boot mode syntax; expected archiso 91')
    shutil.copytree(source, destination, symlinks=True)
    # The Emaki overlay supplies GRUB for UEFI and retains Syslinux for BIOS.
    shutil.rmtree(destination / 'efiboot', ignore_errors=True)
    # Remove releng live-root, SSH and network defaults that conflict with the live session.
    root = destination / 'airootfs'
    removals = [
        'etc/systemd/system/getty@tty1.service.d/autologin.conf',
        'etc/systemd/system/serial-getty@.service.d/autologin.conf',
        'etc/ssh/sshd_config.d/10-archiso.conf',
        'root/.automated_script.sh', 'root/.zlogin', 'root/.zshrc',
        'etc/systemd/system/sshd.service.d',
        'etc/systemd/system/sysinit.target.wants/systemd-time-wait-sync.service',
        # Mirror chooser (its service is masked), install guide and masked networkd units.
        'usr/local/bin/choose-mirror', 'usr/local/bin/Installation_guide',
        'etc/systemd/network',
        'etc/systemd/system/cloud-init.target.wants',
        'etc/systemd/system/sockets.target.wants/pcscd.socket',
        'etc/systemd/system/multi-user.target.wants/hv_fcopy_daemon.service',
        'etc/systemd/system/livecd-talk.service',
        'etc/systemd/system/multi-user.target.wants/livecd-talk.service',
        'usr/local/share/livecd-sound',
        'usr/local/bin/livecd-sound',
        'etc/systemd/system/livecd-alsa-unmuter.service',
        'etc/systemd/system/sound.target.wants/livecd-alsa-unmuter.service',
    ]
    for relative in removals:
        path = root / relative
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    # Follow releng's shipped account database approach, never its root shell or
    # password deletion logic. Reject unknown custom scripts instead of running
    # them as root silently; v91 releng normally uses services and pacman hooks.
    customize = root / 'root/customize_airootfs.sh'
    if customize.exists():
        raise ValueError('unexpected releng customize_airootfs.sh; review it before importing')
    for path in (root / 'etc/systemd/system').rglob('*'):
        if path.is_symlink() and (path.name.startswith(('sshd', 'systemd-networkd', 'iwd', 'choose-mirror', 'reflector'))
                                  or path.name == 'getty@tty1.service'):
            path.unlink()
    copy_overlay(HERE / 'profile', destination)
    version = (HERE / 'VERSION').read_text().strip()
    (destination / 'VERSION').write_text(version + '\n')
    package_names = {line.split('#', 1)[0].strip() for line in (source / 'packages.x86_64').read_text().splitlines()}
    package_names.update((HERE / 'packages-extra.txt').read_text().split())
    package_names.difference_update(('', 'iwd', 'cloud-init', 'grim', 'espeakup', 'livecd-sounds'))
    if any(name.startswith('nvidia') or name == 'broadcom-wl' for name in package_names):
        raise ValueError('unexpected proprietary driver in the releng seed')
    (destination / 'packages.x86_64').write_text('\n'.join(sorted(package_names)) + '\n')
    # v91's single GRUB mode includes ESP and El Torito booting.
    original = re.sub(r'bootmodes=\([^)]*\)', "bootmodes=('bios.syslinux' 'uefi.grub')", original)
    # Preserve image options and replace Emaki identity/build fields and permissions.
    original = re.sub(r'^\s*\[["\']?/root/[^\n]+\n', '', original, flags=re.M)
    original = re.sub(r'^\s*\[["\']?/usr/local/bin/(?:choose-mirror|Installation_guide|livecd-sound)["\']?\][^\n]*\n',
                      '', original, flags=re.M)
    original = re.sub(r'^\s*\[[\"\']?/etc/systemd/system/cloud-init\.target\.wants[\"\']?\][^\n]*\n',
                      '', original, flags=re.M)
    additions = '''
# Emaki ISO overrides; GRUB handles both UEFI removable-media boot paths.
# shellcheck disable=SC2034
iso_name="emaki"
_emaki_profile_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "${_emaki_profile_dir}/VERSION" ]]; then
    iso_version="$(<"${_emaki_profile_dir}/VERSION")"
else
    iso_version="$(<"${_emaki_profile_dir}/../VERSION")"
fi
iso_label="EMAKI_${iso_version}"
iso_publisher="Emaki <https://emaki.sh>"
iso_application="Emaki Live/Installer"
install_dir="emaki"
buildmodes=('iso')
pacman_conf="pacman.conf"
unset _emaki_profile_dir
'''
    (destination / 'profiledef.sh').write_text(original + additions)
    # Brand all existing boot paths while retaining archiso's substitution tokens.
    for pattern in ('grub/*.cfg', 'syslinux/*.cfg'):
        for path in destination.glob(pattern):
            text = path.read_text()
            text = text.replace('Arch Linux', f'Emaki {version}')
            if path.name == 'archiso_sys-linux.cfg':
                # The installer needs UEFI: the BIOS entries only start the live session.
                uefi = ("Emaki installs only on computers with UEFI. If the computer's boot menu\n"
                        'offers a UEFI entry for the USB stick, choose it; otherwise this computer\n'
                        'is not supported.\n')
                medium = f'Emaki {version} install medium'
                for old, new in (
                        (f'Boot the {medium} on BIOS.\n'
                         f'It allows you to install Emaki {version} or perform system maintenance.\n',
                         f'Try Emaki {version} from the USB stick on this BIOS computer.\n' + uefi),
                        (f'MENU LABEL {medium} (%ARCH%, BIOS)', f'MENU LABEL Try Emaki {version} (BIOS: installing needs UEFI)')):
                    text = text.replace(old, new)
            # v91 supports kernel_params_x86_64; keep this requirement explicit
            # in every loader too, including retained inactive/alternate loaders.
            text = '\n'.join(
                line + ' copytoram=n' if re.match(r'^\s*(?:options|linux(?:efi)?|APPEND)\s+', line, re.I)
                and ('archiso' in line or '%KERNEL_PARAMS%' in line) and 'copytoram=n' not in line
                else line for line in text.splitlines()) + '\n'
            path.write_text(text)
    manifest = []
    for path in sorted(source.rglob('*')):
        if path.is_file() and not path.is_symlink():
            manifest.append(f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(source)}')
    (destination / 'RELENG-SHA256SUMS').write_text('\n'.join(manifest) + '\n')
    subprocess.run(['python3', str(HERE / 'prepare-profile.py'), str(destination),
                    '@EMAKI_OFFLINE_REPO@', version, '--template'], check=True)
    print(f'Copied releng into {destination}; UEFI GRUB and BIOS Syslinux selected')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    import_profile(args.source.resolve(), args.destination.resolve())


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(f'ERROR: {error}')
