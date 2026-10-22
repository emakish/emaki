# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Secure Boot through Fedora's Microsoft-signed shim and a per-machine owner key (MOK).

Shared by the installer, the boot refresh and the signing hooks. A machine signs its boot
files when its key exists under STATE: the installer creates it only when the firmware had
Secure Boot on. The firmware switch alone never turns signing on or off, so a machine
installed without Secure Boot stays exactly as before.

Every command goes through a caller's run(argv, input=None) -> stdout, which raises on a
non-zero exit: the installer runs in the target chroot, the boot refresh on the machine.
"""
from .i18n import _
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import struct
import tarfile

# Root-only machine state. The private key never leaves this directory; swapping the key
# means replacing these three files (and enrolling the new certificate).
STATE = Path('/var/lib/emaki/secureboot')
KEY = STATE / 'machine.key'
CERT = STATE / 'machine.crt'
DER = STATE / 'machine.cer'
# Packaged by emaki-shim-signed: Fedora's shim signed by both Microsoft UEFI CAs, and its
# MokManager signed by Fedora's key that the shim carries.
SHIM_DIR = Path('/usr/share/emaki-shim-signed')
# Installed only with Secure Boot on: the shim, the signing and the MOK tools.
PACKAGES = ('emaki-shim-signed', 'sbsigntools', 'mokutil')
SHIM = 'shimx64.efi'
MOK_MANAGER = 'mmx64.efi'
# shim starts grubx64.efi from its own directory.
GRUB = 'grubx64.efi'
# A copy of the public certificate on the EFI partition, for MokManager's "Enroll key from
# disk" when its first 10-second prompt was missed.
ESP_CERT = 'emaki-machine-key.cer'
SBAT_LINE = 'grub.emaki,1,Emaki,grub,{version},https://github.com/emakish/emaki\n'
# Arch's SBAT data for its GRUB build, shipped by the grub package.
SBAT_SOURCE = Path('/usr/share/grub/sbat.csv')
# Where the loaders live on the EFI partition. shim starts grubx64.efi and mmx64.efi
# from its own directory, so the removable path holds the same loaders with shim as
# BOOTX64.EFI; the firmware entries name shim in the A/B slots below EFI/Emaki.
VENDOR_DIR = 'EFI/Emaki'
REMOVABLE_DIR = 'EFI/BOOT'
REMOVABLE_SHIM = 'BOOTX64.EFI'
# Free space Emaki leaves on an ESP it shares with Windows: the installer's minimum next to
# Windows, and what a Secure Boot update must still leave free there at its peak (slots_need).
# Windows updates fail on an ESP with 10 MB or less free (Microsoft KB5089549, error 0x800f0922).
SHARED_ESP_RESERVE = 32 * 1024 * 1024
# Arch's kernels as /boot holds them, and fwupd's capsule loader, which fwupd starts
# through shim from the copy with the .signed suffix.
KERNELS = ('/boot/vmlinuz-linux', '/boot/vmlinuz-linux-lts')
FWUPD_EFI = Path('/usr/lib/fwupd/efi/fwupdx64.efi')
FWUPD_SIGNED = Path('/usr/lib/fwupd/efi/fwupdx64.efi.signed')
DKMS_CONFIG = Path('/etc/dkms/framework.conf.d/50-emaki-secureboot.conf')

GLOBAL_GUID = '8be4df61-93ca-11d2-aa0d-00e098032b8c'
IMAGE_SECURITY_GUID = 'd719b2cb-3d3a-4596-a3bc-dad00e67656f'
# EFI_CERT_X509_GUID a5c059a1-94e4-4aa7-87b5-ab155c2bf072 in its on-disk byte order.
X509_TYPE = bytes.fromhex('a159c0a5e494a74a87b5ab155c2bf072')
CA_2011 = 'Microsoft Corporation UEFI CA 2011'
CA_2023 = 'Microsoft UEFI CA 2023'
CN_OID = bytes.fromhex('550403')
OPENSSL_CONFIG = '''[req]
distinguished_name = dn
prompt = no
x509_extensions = v3
[dn]
CN = {name}
[v3]
basicConstraints = critical,CA:FALSE
keyUsage = critical,digitalSignature
extendedKeyUsage = codeSigning
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid
'''
PASSWORD_DIGITS = 8
# Built into the signed GRUB image (grub-mkimage arguments). The image's memdisk also
# carries every module of the package, which GRUB trusts as part of the signed image, but
# the boot path never depends on that: plain and LUKS2 roots, the Emaki menu with its
# theme, the snapshot menu, the boot trial, and the Windows entry through firmware
# BootNext. tests/test-secureboot-modules.py audits these names against every command the
# generated menus use.
GRUB_MODULES = (
    'part_gpt', 'part_msdos', 'btrfs', 'ext2', 'fat', 'cryptodisk', 'luks2', 'argon2',
    'gcry_rijndael', 'gcry_sha256', 'gcry_sha512', 'pbkdf2', 'memdisk', 'tar', 'normal',
    'configfile', 'test', 'true', 'echo', 'sleep', 'search', 'search_fs_uuid', 'search_fs_file',
    'search_label', 'loadenv', 'regexp', 'reboot', 'halt', 'efibootnext', 'chain', 'linux',
    'gzio', 'zstd', 'font', 'gfxterm', 'gfxterm_background', 'gfxmenu', 'png', 'jpeg',
    'all_video', 'efi_gop', 'video_bochs', 'video_cirrus', 'terminal', 'keystatus', 'minicmd',
    'cat', 'probe', 'efifwsetup')


class SecureBootError(RuntimeError):
    pass


def efi_variable(root, name, guid):
    """The payload of an efivarfs variable without its 4 attribute bytes; None if absent."""
    path = Path(root) / 'sys/firmware/efi/efivars' / f'{name}-{guid}'
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    if len(data) < 4:
        raise SecureBootError(_('The firmware variable {value1} is unreadable.').format(value1=name))
    return data[4:]


def signature_list_certificates(data):
    """X.509 certificates in a UEFI signature database (EFI_SIGNATURE_LIST entries)."""
    certificates, offset = [], 0
    while offset < len(data):
        if offset + 28 > len(data):
            raise SecureBootError(_('The firmware signature database is truncated.'))
        kind = data[offset:offset + 16]
        list_size, header_size, size = struct.unpack_from('<III', data, offset + 16)
        end = offset + list_size
        if list_size < 28 + header_size or end > len(data) or size < 16 or (
                list_size - 28 - header_size) % size:
            raise SecureBootError(_('The firmware signature database is malformed.'))
        entry = offset + 28 + header_size
        while entry < end:
            if kind == X509_TYPE:
                certificates.append(data[entry + 16:entry + size])
            entry += size
        offset = end
    return certificates


def _der(data, offset):
    """(tag, content start, content end) of the DER element at offset."""
    if offset + 2 > len(data):
        raise ValueError(_('truncated'))
    tag, length = data[offset], data[offset + 1]
    offset += 2
    if length & 0x80:
        count = length & 0x7f
        if not 1 <= count <= 4 or offset + count > len(data):
            raise ValueError(_('bad length'))
        length = int.from_bytes(data[offset:offset + count], 'big')
        offset += count
    if offset + length > len(data):
        raise ValueError(_('truncated'))
    return tag, offset, offset + length


def _children(data, start, end):
    while start < end:
        tag, inner, stop = _der(data, start)
        yield tag, inner, stop
        start = stop


def subject_common_names(certificate):
    """The subject's CN values of a DER certificate; () when it cannot be parsed."""
    try:
        ignored, start, end = _der(certificate, 0)
        ignored, start, end = _der(certificate, start)  # tbsCertificate
        fields = list(_children(certificate, start, end))
        if fields and fields[0][0] == 0xa0:  # explicit version
            fields = fields[1:]
        # serialNumber, signature, issuer, validity, subject
        ignored, start, end = fields[4]
        names = []
        for ignored, set_start, set_end in _children(certificate, start, end):
            for ignored, pair_start, pair_end in _children(certificate, set_start, set_end):
                pair = list(_children(certificate, pair_start, pair_end))
                if len(pair) == 2 and certificate[pair[0][1]:pair[0][2]] == CN_OID:
                    names.append(certificate[pair[1][1]:pair[1][2]].decode('utf-8', 'replace'))
        return tuple(names)
    except (ValueError, IndexError):
        return ()


def microsoft_cas(db):
    """'both', '2011', '2023' or 'none': the Microsoft third-party UEFI CAs a db trusts."""
    names = {name for certificate in signature_list_certificates(db)
             for name in subject_common_names(certificate)}
    found = (CA_2011 in names, CA_2023 in names)
    return {(True, True): 'both', (True, False): '2011', (False, True): '2023'}.get(found, 'none')


def firmware_cas(root=Path('/')):
    """The db's Microsoft third-party CAs, or None when db cannot be read."""
    try:
        db = efi_variable(root, 'db', IMAGE_SECURITY_GUID)
        return microsoft_cas(db or b'')
    except (OSError, SecureBootError):
        return None


def setup_mode(root=Path('/')):
    """True when the firmware has no platform key: it then checks no signatures at all."""
    try:
        data = efi_variable(root, 'SetupMode', GLOBAL_GUID)
    except (OSError, SecureBootError):
        return None
    return None if data is None or len(data) != 1 else data[0] == 1


def openssl_config(label=None):
    label = label or secrets.token_hex(4)
    if not re.fullmatch(r'[0-9a-f]{8}', label):
        raise SecureBootError(_('The machine key label is invalid.'))
    return OPENSSL_CONFIG.format(name=f'Emaki Secure Boot machine key {label}')


def generate_key(run, state=STATE):
    """RSA-2048 self-signed code-signing certificate; run executes inside the target.

    The caller has created state as a root-only (0700) directory. Arch's shim guidance
    still recommends 2048-bit keys for MokManager.
    """
    key, cert, der = (state / KEY.name, state / CERT.name, state / DER.name)
    run(['openssl', 'req', '-new', '-x509', '-newkey', 'rsa:2048', '-nodes', '-sha256',
         '-days', '36500', '-config', '/dev/stdin', '-keyout', str(key), '-out', str(cert)],
        input=openssl_config())
    run(['chmod', '0600', str(key)])
    run(['openssl', 'x509', '-in', str(cert), '-outform', 'DER', '-out', str(der)])


def sbat(arch_csv, version, generation=None):
    """Arch GRUB's SBAT entries with Emaki's own line appended, as shim-review asks.

    generation raises the global grub entry to the level the GRUB build declares itself
    (grub-mkimage reports it, see sbat_generation): Arch's sbat.csv for grub 2:2.16-1 still
    says grub,4 while GRUB 2.16 is built at level 6, and a machine whose SbatLevel another
    system raised to grub,5 or later would refuse an image that claims 4.
    """
    lines = [line for line in arch_csv.splitlines() if line.strip()]
    if not lines or not lines[0].startswith('sbat,1,') or not any(line.startswith('grub,') for line in lines):
        raise SecureBootError(_("GRUB's SBAT data is missing or unrecognized."))
    if not re.fullmatch(r'[0-9A-Za-z.:+~_-]{1,64}', version):
        raise SecureBootError(_('The GRUB version for SBAT is invalid.'))
    lines = [line for line in lines if not line.startswith('grub.emaki,')]
    if generation is not None:
        raised = []
        for line in lines:
            fields = line.split(',')
            if fields[0] == 'grub' and len(fields) > 1 and fields[1].isdigit() and int(fields[1]) < generation:
                fields[1] = str(generation)
            raised.append(','.join(fields))
        lines = raised
    return '\n'.join(lines) + '\n' + SBAT_LINE.format(version=version)


def sbat_generation(output):
    """The build-time level grub-mkimage names when the SBAT data declares another one."""
    match = re.search(r'does not match the build-time Global SBAT level ([0-9]{1,3})\b', output)
    return int(match[1]) if match else None


def sign(run, source, output=None, state=STATE):
    """Sign an EFI image with the machine key and prove it with the certificate."""
    output = output or source
    run(['sbsign', '--key', str(state / KEY.name), '--cert', str(state / CERT.name),
         '--output', str(output), str(source)])
    if not signed(run, output, state):
        raise SecureBootError(_('{value1} does not verify with the machine key after signing.').format(value1=output))


def signed(run, path, state=STATE):
    """True when sbverify accepts the image with this machine's certificate."""
    try:
        run(['sbverify', '--cert', str(state / CERT.name), str(path)])
    except Exception:
        return False
    return True


def one_time_password():
    """Digits only: firmware keyboards map keys as US QWERTY, which digits survive."""
    return ''.join(secrets.choice('0123456789') for ignored in range(PASSWORD_DIGITS))


def request_enrollment(run, password, state=STATE):
    """Queue the certificate for MokManager at the next start, protected by password."""
    if not re.fullmatch(r'[0-9]{%d}' % PASSWORD_DIGITS, password):
        raise SecureBootError(_('The one-time password is invalid.'))
    run(['mokutil', '--import', str(state / DER.name)], input=f'{password}\n{password}\n')


def enrollment(run, state=STATE):
    """'enrolled', 'pending' or 'missing' for the machine certificate in shim's lists."""
    try:
        output = run(['mokutil', '--ignore-keyring', '--test-key', str(state / DER.name)])
    except Exception as error:
        output = str(getattr(error, 'output', '') or error)
    if 'is already enrolled' in output:
        return 'enrolled'
    if 'is already in the enrollment request' in output:
        return 'pending'
    if 'is not enrolled' in output:
        return 'missing'
    raise SecureBootError(_('mokutil could not report the machine key: {details}').format(details=output.strip()[-300:]))


def dkms_config(state=STATE):
    """DKMS signs the modules it builds (NVIDIA) with the machine key instead of its own."""
    return ('# Copyright (C) 2026 Artur Yakymenko\n'
            '# SPDX-License-Identifier: GPL-3.0-or-later\n'
            '# Written by the Emaki installer: DKMS signs modules with the Secure Boot machine key.\n'
            f'mok_signing_key={state / KEY.name}\n'
            f'mok_certificate={state / DER.name}\n')


def enabled(root=Path('/')):
    """True when this machine signs its boot files: the installer created its key."""
    return (Path(root) / KEY.relative_to('/')).is_file()


def damaged(root=Path('/')):
    """True when the machine was set up for signing but its private key is gone.

    Such a machine must not be taken for one installed without Secure Boot: its firmware
    boots through shim and refuses unsigned kernels and loaders.
    """
    state = Path(root) / STATE.relative_to('/')
    esp = Path(root) / 'efi' / VENDOR_DIR
    marks = [state / path.name for path in (CERT, DER)] + [esp / ESP_CERT] + [esp / slot / ESP_CERT for slot in SLOTS]
    return not enabled(root) and any(path.exists() for path in marks)


def esp_files(removable=False, shim_dir=SHIM_DIR, der=DER):
    """Names and bytes of the loaders one EFI directory needs next to the signed GRUB.

    shim and MokManager in both directories, shim named BOOTX64.EFI on the removable
    path; the certificate copy for "Enroll key from disk" only in EFI/Emaki.
    """
    files = {(REMOVABLE_SHIM if removable else SHIM): (Path(shim_dir) / SHIM).read_bytes(),
             MOK_MANAGER: (Path(shim_dir) / MOK_MANAGER).read_bytes()}
    if not removable:
        files[ESP_CERT] = Path(der).read_bytes()
    return files


def fat_bytes(size, cluster):
    """What a file of size bytes occupies on FAT: whole clusters."""
    return -(-size // cluster) * cluster


# A/B slots (Secure Boot installs only). Each slot is a complete loader set in its own
# directory: shim builds the paths of grubx64.efi and mmx64.efi from the directory it was
# started from (shim 16.1 load-options.c generate_path_from_image_path, shim.c init_grub,
# mok.c check_mok_request), so a slot never borrows a file from another. One firmware entry
# per slot; BootOrder is the record of which slot is active. An update writes only the slot
# no BootOrder entry reaches, and no file is ever renamed over a loader a firmware entry starts.
SLOTS = ('a', 'b')
SLOT_MANIFEST = 'slot.json'
ACTIVE_LABEL = 'Emaki'
PREVIOUS_LABEL = 'Emaki (previous)'


def slot_files_names():
    return (SHIM, MOK_MANAGER, GRUB, ESP_CERT)


def slot_dir(esp, slot):
    """EFI/Emaki/<slot> under the mounted ESP."""
    if slot not in SLOTS:
        raise SecureBootError(_('Unknown startup copy {value1!r}.').format(value1=slot))
    return Path(esp) / VENDOR_DIR / slot


def slot_entry(slot):
    r"""The loader a firmware entry names for a slot: \EFI\Emaki\<slot>\shimx64.efi."""
    if slot not in SLOTS:
        raise SecureBootError(_('Unknown startup copy {value1!r}.').format(value1=slot))
    return '\\' + VENDOR_DIR.replace('/', '\\') + '\\' + slot + '\\' + SHIM


def entry_slot(loader):
    """The slot a firmware entry's loader path names (FAT ignores case), or None."""
    for slot in SLOTS:
        if isinstance(loader, str) and loader.casefold() == slot_entry(slot).casefold():
            return slot
    return None


def other_slot(slot):
    return SLOTS[1 - SLOTS.index(slot)]


def slot_payload(grub, shim_dir=SHIM_DIR, der=DER):
    """name -> bytes of one complete slot: shim, MokManager, the signed GRUB, the certificate."""
    return {SHIM: (Path(shim_dir) / SHIM).read_bytes(),
            MOK_MANAGER: (Path(shim_dir) / MOK_MANAGER).read_bytes(),
            GRUB: bytes(grub), ESP_CERT: Path(der).read_bytes()}


def slot_manifest_bytes(slot, files, generation=None):
    """The manifest a complete slot carries: every file's size and hash. Written last."""
    record = {'version': 1, 'slot': slot, 'generation': generation,
              'files': {name: {'size': len(files[name]), 'sha256': hashlib.sha256(files[name]).hexdigest()}
                        for name in slot_files_names()}}
    return (json.dumps(record, sort_keys=True) + '\n').encode()


def read_slot(esp, slot):
    """The manifest of a complete slot, or None.

    Complete: the manifest is there in its exact shape, every file it names is a regular
    file of that size and hash, and the slot holds nothing else. shim reads options.csv from
    its own directory and takes its second stage from it (shim 16.1 shim.c, load options from
    options.csv), so any other name (FAT ignores case) makes a slot incomplete. Anything else
    (no manifest, an empty or cut one, a missing, short or changed file, a stray file) is an
    incomplete slot, never an error: the next update rewrites it.
    """
    try:
        directory = slot_dir(esp, slot)
        path = directory / SLOT_MANIFEST
        if directory.is_symlink() or path.is_symlink() or not path.is_file():
            return None
        allowed = {name.casefold() for name in (*slot_files_names(), SLOT_MANIFEST)}
        if any(entry.name.casefold() not in allowed for entry in directory.iterdir()):
            return None
        record = json.loads(path.read_bytes())
        names = slot_files_names()
        if not (isinstance(record, dict) and set(record) == {'version', 'slot', 'generation', 'files'}
                and type(record['version']) is int and record['version'] == 1 and record['slot'] == slot
                and (record['generation'] is None or isinstance(record['generation'], str)
                     and re.fullmatch(r'[0-9a-f]{32}', record['generation']))
                and isinstance(record['files'], dict) and set(record['files']) == set(names)):
            return None
        for name in names:
            expected = record['files'][name]
            if not (isinstance(expected, dict) and set(expected) == {'size', 'sha256'}
                    and type(expected['size']) is int and isinstance(expected['sha256'], str)
                    and re.fullmatch(r'[0-9a-f]{64}', expected['sha256'])):
                return None
            file = directory / name
            if file.is_symlink() or not file.is_file() or file.stat().st_size != expected['size']:
                return None
            if hashlib.sha256(file.read_bytes()).hexdigest() != expected['sha256']:
                return None
        return record
    except (OSError, ValueError, UnicodeError, RecursionError, SecureBootError):
        return None


def _durable(path, data):
    with open(path, 'wb') as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_slot(esp, slot, files, generation=None, check=None):
    """Write one complete slot that no firmware entry in BootOrder reaches; manifest last.

    The old manifest goes first, so a cut at any later point leaves a slot without a valid
    manifest, which read_slot calls incomplete and the next update rewrites. Each file is
    written under a temporary name, made durable and renamed: the slot is unreferenced, so a
    torn rename there reaches nothing. Every file is read back against the payload and
    check(path, data) may refuse it (signature checks). Returns the manifest.
    """
    names = slot_files_names()
    if set(files) != set(names):
        raise SecureBootError(_('A startup copy needs exactly {names}.').format(names=', '.join(names)))
    directory = slot_dir(esp, slot)
    if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
        raise SecureBootError(_('{value1} is not a directory.').format(value1=directory))
    manifest = directory / SLOT_MANIFEST
    if manifest.exists() or manifest.is_symlink():
        manifest.unlink()
        _sync_directory(directory)
    directory.mkdir(parents=True, exist_ok=True)
    _sync_directory(directory.parent)
    for path in sorted(directory.iterdir()):
        # Leftovers of a cut update: a slot holds only its own names.
        if path.name not in names:
            if path.is_dir() and not path.is_symlink():
                raise SecureBootError(_('{value1} is a directory inside a startup copy.').format(value1=path))
            path.unlink()
    _sync_directory(directory)
    for name in names:
        temporary = directory / (name + '.tmp')
        _durable(temporary, files[name])
        os.replace(temporary, directory / name)
        _sync_directory(directory)
    for name in names:
        path = directory / name
        if path.read_bytes() != files[name]:
            raise SecureBootError(_('{value1} could not be read back after it was written.').format(value1=path))
        if check is not None:
            check(path, files[name])
    _durable(manifest, slot_manifest_bytes(slot, files, generation))
    _sync_directory(directory)
    record = read_slot(esp, slot)
    if record is None:
        raise SecureBootError(_('The startup copy {value1} could not be read back after it was written.').format(value1=slot))
    return record


def slot_bytes(sizes, cluster):
    """ESP bytes one slot takes at most: each file in whole clusters, one temporary copy of
    the largest (it exists while that file is written), the manifest, and the directory."""
    sizes = list(sizes)
    return (sum(fat_bytes(size, cluster) for size in sizes) + fat_bytes(max(sizes, default=0), cluster)
            + 2 * cluster + 2 * cluster)


def slots_need(sizes, cluster, removable=False, stage=0):
    """ESP bytes Emaki's Secure Boot files need at an update's peak.

    sizes: name -> bytes of one slot (SHIM, MOK_MANAGER, GRUB, ESP_CERT). Two complete slots
    (the active one stays while the other is written), the removable copies of shim, GRUB and
    MokManager when Emaki owns the ESP, the boot refresh's grub-install stage (stage bytes),
    and Emaki's small state files in EFI/Emaki. The installer admits by this rule and the
    boot refresh refuses an update by the same rule: one rule, every destination.
    """
    sizes = dict(sizes)
    need = 2 * slot_bytes(sizes.values(), cluster) + 2 * cluster
    if removable:
        need += sum(fat_bytes(sizes[name], cluster) for name in (SHIM, MOK_MANAGER, GRUB)) + 2 * cluster
    if stage:
        need += fat_bytes(stage, cluster) + 2 * cluster
    return need + ESP_SMALL_FILES * 2 * cluster


def firmware_entries(listing):
    """efibootmgr -v as {'order', 'next', 'current', 'entries': {number: (active, label,
    partition GUID or None, loader or None)}, 'data': {number: True when the entry carries
    optional data}}, or None when its shape cannot be trusted (then nothing in NVRAM is
    changed)."""
    guid = r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}'
    result = {'order': [], 'next': None, 'current': None, 'entries': {}, 'data': {}}
    if not isinstance(listing, str):
        return None
    for name, key in (('BootOrder', 'order'), ('BootNext', 'next'), ('BootCurrent', 'current')):
        found = re.findall(rf'^{name}: *(\S*)\s*$', listing, re.M)
        if len(found) > 1:
            return None
        if not found:
            continue
        if key == 'order':
            items = [item.upper() for item in found[0].split(',') if item]
            if not all(re.fullmatch(r'[0-9A-F]{4}', item) for item in items) or len(set(items)) != len(items):
                return None
            result['order'] = items
        elif re.fullmatch(r'[0-9A-Fa-f]{4}', found[0]):
            result[key] = found[0].upper()
        else:
            return None
    for record in re.split(r'(?m)(?=^Boot[0-9A-Fa-f]{4}(?:\*| ))', listing):
        match = re.match(r'Boot([0-9A-Fa-f]{4})(\*| )([^\t\n]*)(?:\t([^\n]*))?', record)
        if not match:
            continue
        number, path = match[1].upper(), match[4] or ''
        if number in result['entries']:
            return None
        # efibootmgr 18 appends optional data to a bare filename; its data line says which.
        # Older versions print it right after File(...).
        data = re.findall(r'^    data: ([0-9a-fA-F]{2}(?: [0-9a-fA-F]{2})*)$', record, re.M)
        optional = bool(data)
        if len(data) == 1 and path.lower().endswith(data[0].replace(' ', '').lower()):
            path = path[:-len(data[0].replace(' ', ''))]
        partition = re.findall(rf'HD\([1-9][0-9]*,GPT,({guid}),0x[0-9a-fA-F]+,0x[0-9a-fA-F]+\)/', path)
        loader = re.search(r'\)/(?:File\(([^()\t\n]+)\)([^()\t\n]*)|(\\[^()\t\n]+))$', path)
        optional = optional or bool(loader and loader[2])
        loader = (loader[1] or loader[3]) if loader else None
        result['entries'][number] = (match[2] == '*', match[3].strip(),
                                     partition[0].lower() if len(partition) == 1 else None, loader)
        result['data'][number] = optional
    return result


def slot_entries(parsed, partuuid, usable=False):
    """{slot: [entry numbers]} of the firmware entries that name a slot on this ESP.

    usable: only the entries firmware starts as that slot: active (firmware skips an inactive
    entry) and without optional data (with data, shim may load another second stage; shim 16.1
    load-options.c). Every entry, usable or not, is withdrawn before its slot is rewritten.
    """
    found = {slot: [] for slot in SLOTS}
    for number, (active, ignored, partition, loader) in sorted(parsed['entries'].items()):
        slot = entry_slot(loader)
        if slot and partition == str(partuuid).lower():
            if usable and (not active or parsed.get('data', {}).get(number)):
                continue
            found[slot].append(number)
    return found


def active_slot(parsed, partuuid):
    """The slot of the first BootOrder entry firmware starts as a slot on this ESP (active, no
    optional data), or None. An inactive entry is skipped by firmware, so it never names the
    active slot: the slot firmware really starts must never be the one an update rewrites."""
    owners = {number: slot for slot, numbers in slot_entries(parsed, partuuid, usable=True).items()
              for number in numbers}
    return next((owners[number] for number in parsed['order'] if number in owners), None)


def pe_certificates_present(data):
    """True when a PE image's certificate table holds at least one well-formed Authenticode
    entry (WIN_CERTIFICATE: dwLength >= 8, revision 0x0200, type PKCS_SIGNED_DATA 2), every
    entry inside the table on 8-byte alignment. Bounds alone never count as a signature."""
    try:
        if len(data) < 64 or data[:2] != b'MZ':
            return False
        pe = int.from_bytes(data[0x3c:0x40], 'little')
        optional = pe + 24
        magic, = struct.unpack_from('<H', data, optional)
        if data[pe:pe + 4] != b'PE\0\0' or magic != 0x20b:
            return False
        directories, = struct.unpack_from('<I', data, optional + 108)
        if directories < 5:
            return False
        start, size = struct.unpack_from('<II', data, optional + 112 + 4 * 8)
        if size < 8 or start + size > len(data):
            return False
        position, found = start, 0
        while position < start + size:
            length, revision, kind = struct.unpack_from('<IHH', data, position)
            if length <= 8 or position + length > start + size or revision != 0x0200 or kind != 2:
                return False
            found += 1
            position += (length + 7) & ~7
        return found > 0
    except struct.error:
        return False


# The packages whose files make up what a Secure Boot install writes to EFI/Emaki.
GRUB_PACKAGE, SHIM_PACKAGE, CONFIG_PACKAGE = 'grub', 'emaki-shim-signed', 'emaki-config'
GRUB_PLATFORM = 'usr/lib/grub/x86_64-efi/'
# grub-mkimage's PE headers, the early config, Emaki's SBAT section and the signature around
# the modules and the memdisk; measured images carry far less than this.
SIGNED_GRUB_MARGIN = 1024 * 1024
# Small memdisk files: the conventional and snapshot menu sources and start.cfg.
MEMDISK_SMALL = {'boot/grub/grub.cfg': 1024, 'boot/grub/grub-btrfs.cfg': 1024, 'start.cfg': 1024}
# The DER certificate copy (about 1 KiB) and Emaki's small EFI/Emaki state files that the boot
# refresh writes later (boot-state.json, trial.env, update.json, update.env, update.cfg,
# boot-intent.json and a spare): bounded by one cluster and one directory cluster each.
ESP_CERT_BOUND = 4096
ESP_SMALL_FILES = 7


def package_name(archive):
    """pacman's package name of an archive named <name>-<version>-<release>-<arch>.pkg.tar.*."""
    return Path(archive).name.rsplit('-', 3)[0]


def repo_archives(repo, names=(GRUB_PACKAGE, SHIM_PACKAGE, CONFIG_PACKAGE)):
    """The one archive of each named package in a pacman repository directory (signatures and
    the database aside), in the order of names. None or two versions of one package cannot be
    matched to what pacman will install: SecureBootError."""
    found = {name: [] for name in names}
    try:
        for path in sorted(Path(repo).iterdir()):
            if '.pkg.tar' in path.name and not path.name.endswith('.sig') and path.is_file():
                if package_name(path) in found:
                    found[package_name(path)].append(path)
    except OSError as error:
        raise SecureBootError(_('The package repository could not be read: {value1}').format(value1=error)) from error
    for name, paths in found.items():
        if len(paths) != 1:
            raise SecureBootError(_('The package repository holds {value1} archives of {value2}.').format(value1=len(paths), value2=name))
    return [found[name][0] for name in names]


def archive_files(archive, read=()):
    """({member: size} of the regular files, {member: bytes} of those named in read)."""
    sizes, contents = {}, {}
    try:
        with tarfile.open(archive, 'r:*') as package:
            for member in package:
                if member.isfile():
                    sizes[member.name] = member.size
                    if member.name in read:
                        contents[member.name] = package.extractfile(member).read()
    except (OSError, tarfile.TarError, EOFError) as error:
        raise SecureBootError(_('{value1} could not be read: {value2}').format(value1=Path(archive).name, value2=error)) from error
    return sizes, contents


def tar_bytes(sizes):
    """The size of a ustar archive of files with these sizes, as Python's tarfile writes it."""
    blocks = sum(512 + -(-size // 512) * 512 for size in sizes) + 1024
    return -(-blocks // 10240) * 10240


def signed_grub_bytes(archives, modules, extra_memdisk=None):
    """An upper bound of the signed GRUB image the installer builds, from its real inputs.

    The memdisk holds what grub-install puts into /boot/grub (every module list and module,
    modinfo.sh, every GRUB locale, unicode.pf2), the menu faces of emaki-config and, when
    encrypted, the unlock files (extra_memdisk: name -> size); the image also builds in the
    audited modules and their dependencies (moddep.lst) next to kernel.img.
    """
    by_name = {package_name(archive): archive for archive in archives}
    missing = [name for name in (GRUB_PACKAGE, CONFIG_PACKAGE) if name not in by_name]
    if missing:
        raise SecureBootError(_('The packages {value1} are not in this installation.').format(value1=', '.join(missing)))
    grub, contents = archive_files(by_name[GRUB_PACKAGE], (GRUB_PLATFORM + 'moddep.lst',))
    config, ignored = archive_files(by_name[CONFIG_PACKAGE])
    memdisk = dict(MEMDISK_SMALL)
    for name, size in grub.items():
        base = name[len(GRUB_PLATFORM):] if name.startswith(GRUB_PLATFORM) else None
        if base and '/' not in base and (base.endswith(('.mod', '.lst')) or base == 'modinfo.sh'):
            memdisk['boot/grub/x86_64-efi/' + base] = size
        match = re.fullmatch(r'usr/share/locale/([^/]+)/LC_MESSAGES/grub\.mo', name)
        if match:
            memdisk[f'boot/grub/locale/{match[1]}.mo'] = size
    if 'usr/share/grub/unicode.pf2' in grub:
        memdisk['boot/grub/fonts/unicode.pf2'] = grub['usr/share/grub/unicode.pf2']
    for name, size in config.items():
        if re.fullmatch(r'usr/share/emaki/grub/(?:fonts|theme)/[^/]+\.pf2', name):
            memdisk[name] = size
    memdisk.update(extra_memdisk or {})
    kernel = grub.get(GRUB_PLATFORM + 'kernel.img')
    moddep = contents.get(GRUB_PLATFORM + 'moddep.lst')
    if kernel is None or moddep is None:
        raise SecureBootError(_('The grub package has no kernel.img or moddep.lst for x86_64-efi.'))
    depends = {}
    for line in moddep.decode('ascii', 'replace').splitlines():
        name, ignored, rest = line.partition(':')
        depends[name.strip()] = rest.split()
    core, pending = set(), list(modules)
    while pending:
        name = pending.pop()
        if name not in core:
            core.add(name)
            pending += depends.get(name, [])
    absent = sorted(name for name in core if GRUB_PLATFORM + name + '.mod' not in grub)
    if absent:
        raise SecureBootError(_('The grub package lacks the modules ') + ', '.join(absent) + '.')
    built_in = sum(grub[GRUB_PLATFORM + name + '.mod'] for name in core)
    return tar_bytes(memdisk.values()) + kernel + built_in + SIGNED_GRUB_MARGIN


def secure_boot_esp_need(archives, cluster, modules, extra_memdisk=None, removable=False):
    """Free ESP bytes a Secure Boot install on a kept ESP needs before it starts.

    The boot refresh's rule (slots_need): two complete slots, as an update holds them at its
    peak, with the signed GRUB this install builds; EFI/BOOT's copies when this install
    writes them (removable); the refresh's grub-install stage, an image of the same size;
    and SHARED_ESP_RESERVE stays free for Windows even then. All of it comes from the
    verified package archives, never a guess.
    """
    shim_archive = next((archive for archive in archives if package_name(archive) == SHIM_PACKAGE), None)
    if shim_archive is None:
        raise SecureBootError(_('The package emaki-shim-signed is not in this installation.'))
    sizes, ignored = archive_files(shim_archive)
    shim_dir = str(SHIM_DIR).lstrip('/') + '/'
    shim, mok = sizes.get(shim_dir + SHIM), sizes.get(shim_dir + MOK_MANAGER)
    if shim is None or mok is None:
        raise SecureBootError(_('emaki-shim-signed does not hold shimx64.efi and mmx64.efi.'))
    grub = signed_grub_bytes(archives, modules, extra_memdisk)
    return slots_need({SHIM: shim, MOK_MANAGER: mok, GRUB: grub, ESP_CERT: ESP_CERT_BOUND}, cluster,
                      removable=removable, stage=grub) + SHARED_ESP_RESERVE


def signed_image_modules(base):
    """The modules built into a signed GRUB image: base, then GRUB_MODULES, each once."""
    return tuple(dict.fromkeys((*base, *GRUB_MODULES)))


def sbat_version(arch_csv):
    """The grub package version that Arch's sbat.csv names in its grub line."""
    for line in arch_csv.splitlines():
        fields = line.split(',')
        if fields[0] == 'grub' and len(fields) == 6:
            return fields[4]
    raise SecureBootError(_("GRUB's SBAT data names no GRUB version."))


def build_signed_grub(run, write, mkimage, sbat_path, arch_csv, state=STATE):
    """Build a GRUB image with Emaki's SBAT data and sign it with the machine key.

    mkimage is a grub-mkimage command with its --output; --sbat goes after the program
    name. write(path, text) stores the SBAT data where run's grub-mkimage reads it. When
    grub-mkimage reports that its build-time level differs from Arch's line (on stderr,
    hence sh and 2>&1), the image is built again at that level.
    """
    outputs = [argument.split('=', 1)[1] for argument in mkimage if argument.startswith('--output=')]
    if len(outputs) != 1:
        raise SecureBootError(_('The GRUB image command names no single output.'))
    version = sbat_version(arch_csv)
    command = ['sh', '-c', '"$@" 2>&1', 'sh', mkimage[0], '--sbat=' + str(sbat_path), *mkimage[1:]]
    write(sbat_path, sbat(arch_csv, version))
    level = sbat_generation(run(command) or '')
    if level is not None:
        write(sbat_path, sbat(arch_csv, version, level))
        if sbat_generation(run(command) or '') is not None:
            raise SecureBootError(_('grub-mkimage still rejects the SBAT level of the signed image.'))
    sign(run, outputs[0], state=state)
    return outputs[0]


# fwupd 2.x: DisabledPlugins in the [fwupd] group, names separated by ';' (fwupd.conf(5));
# uefi_db and uefi_kek are the plugins that update the firmware's db and KEK.
FWUPD_CONFIG = Path('/etc/fwupd/fwupd.conf')
FWUPD_KEY_PLUGINS = ('uefi_db', 'uefi_kek')


def fwupd_config(text):
    """fwupd.conf with the db and KEK update plugins disabled, everything else kept.

    Next to Windows a changed db or KEK makes BitLocker ask for its recovery key.
    """
    lines = text.splitlines()
    header = next((index for index, line in enumerate(lines) if line.strip() == '[fwupd]'), None)
    if header is None:
        return text.rstrip('\n') + ('\n\n' if text.strip() else '') + \
            '[fwupd]\nDisabledPlugins=' + ';'.join(FWUPD_KEY_PLUGINS) + '\n'
    end = next((index for index in range(header + 1, len(lines)) if lines[index].strip().startswith('[')),
               len(lines))
    for index in range(header + 1, end):
        key, separator, value = lines[index].partition('=')
        if separator and key.strip() == 'DisabledPlugins':
            names = [name.strip() for name in value.split(';') if name.strip()]
            names += [name for name in FWUPD_KEY_PLUGINS if name not in names]
            lines[index] = 'DisabledPlugins=' + ';'.join(names)
            break
    else:
        lines.insert(header + 1, 'DisabledPlugins=' + ';'.join(FWUPD_KEY_PLUGINS))
    return '\n'.join(lines) + '\n'
