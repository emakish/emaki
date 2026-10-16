#!/usr/bin/env bash
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
# Secure Boot for the install medium (iso/build.sh --secure-boot-key PREFIX, between archiso's passes).
#
# The stick boots through Fedora's Microsoft-signed shim. shim starts our own GRUB, built with every
# module its menus need and signed with the Emaki release key, and GRUB starts the kernels, signed
# with the same key. The release key's certificate lies on the stick as /emaki.cer: on the first
# boot shim cannot verify GRUB and opens MokManager by itself, the person picks "Enroll key from
# disk" → emaki.cer, and from the next boot the stick starts with Secure Boot on.
#
# The key is one setting: PREFIX.key (PEM private key) and PREFIX.crt (PEM certificate); a test key
# and the real one differ only in the path. Nothing else of the image changes.
#
# Which key may sign which image (docs/signing.md, "The Secure Boot release key"):
# - a release image must be signed, and only by the Emaki Secure Boot release key, whose certificate
#   is pinned in iso/secureboot-release.crt; its private key lies unencrypted on a RAM file system
#   of the build VM for that one build (iso/secureboot-key-to-vm.sh puts it there and removes it),
#   readable by its owner only, with swap off, so it never reaches a disk;
# - a test image may be unsigned or signed with any other key, never with the release key;
# - a passphrase-protected key is refused: sbsign can only ask for the passphrase on a terminal.
# `--check-only` runs just these rules (iso/build.sh, before any build work).
#
# Usage: secureboot.sh --mode release|test|test-alongside --check-only [--key PREFIX]
#        secureboot.sh --mode MODE --key PREFIX --mk WORK/mk --cache DIR [--grub-lib DIR] [--sbat FILE]
#                      [--shim-list FILE] [--release-cert FILE]
set -Eeuo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
fail() { printf 'ERROR: secure boot: %s\n' "$*" >&2; exit 1; }
key='' mk='' cache='' grub_lib=/usr/lib/grub/x86_64-efi sbat=/usr/share/grub/sbat.csv shim_list=$HERE/secureboot-shim.txt
mode='' check_only=0 release_cert=$HERE/secureboot-release.crt proc=${EMAKI_PROC_ROOT:-/proc}
while (($#)); do
    case $1 in
        --key|--mk|--cache|--grub-lib|--sbat|--shim-list|--mode|--release-cert)
            (($# >= 2)) || fail "missing value for $1"
            case $1 in --key) key=$2 ;; --mk) mk=$2 ;; --cache) cache=$2 ;; --grub-lib) grub_lib=$2 ;; --sbat) sbat=$2 ;;
                --shim-list) shim_list=$2 ;; --mode) mode=$2 ;; --release-cert) release_cert=$2 ;; esac
            shift 2 ;;
        --check-only) check_only=1; shift ;;
        *) fail "unknown option: $1" ;;
    esac
done
[[ $mode == release || $mode == test || $mode == test-alongside ]] || fail 'usage: --mode release|test|test-alongside'
der_sha256() { openssl x509 -in "$1" -outform DER | sha256sum | cut -d' ' -f1; }
cert_pub_sha256() { openssl x509 -in "$1" -noout -pubkey | sha256sum | cut -d' ' -f1; }
check_key() {
    if [[ -z $key ]]; then
        [[ $mode != release ]] || fail 'a release image starts with Secure Boot on: pass --secure-boot-key PREFIX, the Emaki Secure Boot release key unlocked into the build VM (iso/secureboot-key-to-vm.sh, docs/signing.md)'
        return 0
    fi
    [[ -r $key.key && -r $key.crt ]] || fail "the release key needs $key.key and $key.crt"
    # The files themselves, not the names: a PREFIX.key may be a link to somewhere else.
    local key_file crt_file
    key_file=$(realpath -e -- "$key.key") || fail "cannot resolve $key.key"
    crt_file=$(realpath -e -- "$key.crt") || fail "cannot resolve $key.crt"
    # sbsign has no passphrase option: OpenSSL would ask on a terminal the build does not have.
    if grep -q -e '^-----BEGIN ENCRYPTED PRIVATE KEY-----' -e '^Proc-Type: 4,ENCRYPTED' -- "$key_file"; then
        fail "$key.key is protected by a passphrase, which the build cannot ask for; unlock it into the build VM's memory with iso/secureboot-key-to-vm.sh (docs/signing.md)"
    fi
    # A key the module-signing-only EKU marks is refused by shim for boot images; a key that does not
    # match its certificate would sign images nobody can verify.
    openssl x509 -in "$crt_file" -noout >/dev/null 2>&1 || fail "$key.crt is not a PEM certificate"
    openssl x509 -in "$crt_file" -noout -ext extendedKeyUsage 2>/dev/null | grep -q '1\.3\.6\.1\.4\.1\.2312\.16\.1\.2' \
        && fail "$key.crt carries the module-signing-only usage; shim refuses it for boot images"
    local key_pub=''
    key_pub=$(openssl pkey -in "$key_file" -pubout -passin pass: </dev/null 2>/dev/null) || key_pub=''
    [[ -n $key_pub ]] || fail "$key.key is not a private key OpenSSL can read"
    key_pub=$(sha256sum <<<"$key_pub" | cut -d' ' -f1)
    [[ $(openssl x509 -in "$crt_file" -noout -pubkey | sha256sum | cut -d' ' -f1) == "$key_pub" ]] \
        || fail "$key.key does not belong to $key.crt"
    if [[ $mode == release ]]; then
        [[ -f $release_cert ]] || fail "no release certificate is pinned in $release_cert; it is committed when the Emaki Secure Boot release key is made (docs/signing.md)"
        [[ $(der_sha256 "$crt_file") == "$(der_sha256 "$release_cert")" ]] || fail "$key.crt is not the release certificate pinned in $release_cert"
        local fs swaps
        fs=$(stat -f -c %T -- "$key_file")
        [[ $fs == tmpfs || $fs == ramfs ]] || fail "the release key must lie on a RAM file system (/run in the build VM), never on a disk; $key.key is on $fs"
        [[ $(stat -c %a -- "$key_file") == [0-7]00 ]] || fail "$key.key must be readable by its owner only"
        # The inspection itself must succeed; then the list must be empty.
        swaps=$(<"$proc/swaps") || fail "cannot inspect the swap of this machine ($proc/swaps), so the release key could reach a disk"
        [[ $swaps == Filename* ]] || fail "cannot inspect the swap of this machine ($proc/swaps), so the release key could reach a disk"
        [[ $(wc -l <<<"$swaps") == 1 ]] \
            || fail 'swap is on: the release key in memory could be written to a disk; turn swap off for the release build (swapoff -a)'
    elif [[ -f $release_cert && $key_pub == "$(cert_pub_sha256 "$release_cert")" ]]; then
        # By the key, not the certificate: a new certificate for the release key is still the release key.
        fail 'a test image is never signed with the release key; use a test key (docs/iso.md)'
    fi
}
command -v openssl >/dev/null || fail 'missing dependency: openssl'
if [[ -n $key ]]; then
    # Every process that reads the key from here on (openssl in the checks, sbsign, sbverify) runs
    # with a zero core size, and this machine's dump handler must honour it.
    # shellcheck source=SCRIPTDIR/secureboot-host.sh
    . "$HERE/secureboot-host.sh"
    core_dumps_off
    core_pattern=$(<"$proc/sys/kernel/core_pattern") || fail 'cannot read the core dump setting'
    core_handler_check "$core_pattern" 'this machine'
fi
check_key
((check_only == 0)) || exit 0
[[ -n $key && -n $mk && -n $cache ]] || fail 'usage: secureboot.sh --mode MODE --key PREFIX --mk WORK/mk --cache DIR'
for command in sbsign sbverify grub-mkstandalone mkfs.fat mcopy openssl bsdtar sha256sum python3; do
    command -v "$command" >/dev/null || fail "missing dependency: $command"
done
iso=$mk/iso efiboot=$mk/efiboot.img embed=$mk/grub-embed.cfg
[[ -d $iso/EFI/BOOT && -f $efiboot && -f $embed && -f $iso/boot/grub/grub.cfg ]] \
    || fail "$mk is not archiso's staged UEFI GRUB boot (EFI/BOOT, efiboot.img, grub-embed.cfg)"

# shim and MokManager: Fedora's shim-x64, pinned by hash, signed by both Microsoft UEFI CAs.
read -r shim_url shim_sha256 < <(grep -v "^#" "$shim_list")
mkdir -p -- "$cache"
rpm=$cache/${shim_url##*/}
if [[ ! -f $rpm ]] || [[ $(sha256sum <"$rpm") != "$shim_sha256  -" ]]; then
    curl -fsSL --retry 3 -o "$rpm.part" -- "$shim_url" || fail "cannot download $shim_url"
    mv -f -- "$rpm.part" "$rpm"
fi
[[ $(sha256sum <"$rpm") == "$shim_sha256  -" ]] || fail "$rpm does not match the pinned SHA-256"
stage=$(mktemp -d "$cache/stage.XXXXXX")
trap 'rm -rf -- "$stage"' EXIT
mkdir -- "$stage/rpm" "$stage/esp"
bsdtar -xf "$rpm" -C "$stage/rpm"
shim=$(find "$stage/rpm" -path '*/EFI/BOOT/BOOTX64.EFI' -print -quit)
mm=$(find "$stage/rpm" -path '*/EFI/fedora/mmx64.efi' -print -quit)
[[ -n $shim && -n $mm ]] || fail 'the shim package holds no BOOTX64.EFI and mmx64.efi'
signers=$(sbverify --list "$shim")
for ca in 'Microsoft Corporation UEFI CA 2011' 'Microsoft UEFI CA 2023'; do
    grep -q "issuer:.*CN=$ca" <<<"$signers" || fail "shim is not signed by $ca"
done

# GRUB: one image with every module its menus need (under Secure Boot it loads none from disk),
# shim lock on, SBAT of Arch's GRUB plus Emaki's line.
version=$(<"$HERE/VERSION")
modules=$(python3 "$HERE/secureboot-grub-modules.py" --grub-lib "$grub_lib" --base "$(grep -v "^#" "$HERE/secureboot-grub-base.txt")" \
    "$embed" "$iso/boot/grub/grub.cfg") || fail 'the GRUB module audit failed'
{ cat -- "$sbat"; printf 'grub.emaki,1,Emaki,grub,%s,https://github.com/emakish/emaki\n' "$version"; } >"$stage/sbat.csv"
# GRUB 2.16 under Secure Boot refuses fonts from the stick ("prohibited by secure boot policy";
# the PNG background still loads), and trusts its own memdisk: the menu font goes there and the
# staged menu loads it from (memdisk). Any other font line would silently drop the picture menu.
font_line='    if loadfont /boot/grub/menu.pf2; then'
[[ $(grep -cxF -- "$font_line" "$iso/boot/grub/grub.cfg") == 1 && -f $iso/boot/grub/menu.pf2 ]] \
    || fail 'boot/grub/grub.cfg no longer loads its font with the one line this step moves to the memdisk'
mkimage() {
    grub-mkstandalone -O x86_64-efi -d "$grub_lib" --modules="$modules" --install-modules="$modules" \
        --locales='' --themes='' --fonts='' --sbat="$stage/sbat.csv" -o "$stage/grubx64.efi" \
        "boot/grub/grub.cfg=$embed" "boot/grub/menu.pf2=$iso/boot/grub/menu.pf2" \
        2>"$stage/mkimage.log" || { cat "$stage/mkimage.log" >&2; exit 1; }
}
mkimage
# Arch's sbat.csv can lag the global level the GRUB build carries the fixes of (2:2.16-1 says 4,
# grub-mkimage reports 6); declare that level, as the installed systems' image does, so a machine
# whose SbatLevel was raised past 4 still starts the stick. Never lower it.
level=$(sed -n 's/.*does not match the build-time Global SBAT level \([0-9]\{1,3\}\)\b.*/\1/p' "$stage/mkimage.log")
declared=$(sed -n 's/^grub,\([0-9]\{1,3\}\),.*/\1/p' "$stage/sbat.csv")
if [[ -n $level && -n $declared ]] && ((level > declared)); then
    sed -i "s/^grub,$declared,/grub,$level,/" "$stage/sbat.csv"
    mkimage
fi
sign() {  # sign FILE in place and prove it verifies against the release certificate
    sbsign --key "$key.key" --cert "$key.crt" --output "$1.signed" "$1" </dev/null >/dev/null
    sbverify --cert "$key.crt" "$1.signed" >/dev/null || fail "$1 does not verify after signing"
    mv -f -- "$1.signed" "$1"
}
sign "$stage/grubx64.efi"
sed -i "s|^    if loadfont /boot/grub/menu\.pf2; then\$|    if loadfont (memdisk)/boot/grub/menu.pf2; then|" "$iso/boot/grub/grub.cfg"
kernels=("$iso"/*/boot/x86_64/vmlinuz-*)
[[ -f ${kernels[0]} ]] || fail "no kernel under $iso/*/boot/x86_64"
for kernel in "${kernels[@]}"; do sign "$kernel"; done

# The ESP (El Torito image) and the ISO 9660 copy get the same files: shim as the removable-media
# loader, GRUB and MokManager next to it, the certificate at the root.
openssl x509 -in "$key.crt" -outform DER -out "$stage/emaki.cer"
install -m 0644 -- "$shim" "$iso/EFI/BOOT/BOOTx64.EFI"
install -m 0644 -- "$stage/grubx64.efi" "$iso/EFI/BOOT/grubx64.efi"
install -m 0644 -- "$mm" "$iso/EFI/BOOT/mmx64.efi"
install -m 0644 -- "$stage/emaki.cer" "$iso/emaki.cer"
mcopy -s -n -i "$efiboot" '::/' "$stage/esp/"
install -m 0644 -- "$shim" "$stage/esp/EFI/BOOT/BOOTx64.EFI"
install -m 0644 -- "$stage/grubx64.efi" "$stage/esp/EFI/BOOT/grubx64.efi"
install -m 0644 -- "$mm" "$stage/esp/EFI/BOOT/mmx64.efi"
install -m 0644 -- "$stage/emaki.cer" "$stage/esp/emaki.cer"
# Same sizing as archiso's _make_efibootimg: contents rounded up to a MiB, plus 8 MiB.
size_kib=$(du -bcs -- "$stage/esp/". | awk 'END { print (int(($1 / 1024 + 8192 + 1023) / 1024)) * 1024 }')
fat=()
((size_kib < 36864)) || fat=(-F 32)
rm -f -- "$efiboot"
# The label is the volume name MokManager lists under "Enroll key from disk".
mkfs.fat -C -n EMAKI "${fat[@]}" "$efiboot" "$size_kib" >/dev/null
mcopy -s -i "$efiboot" "$stage/esp/"* '::/'
printf 'secure_boot_cert_sha256 %s\nshim_rpm %s %s\n' \
    "$(openssl x509 -in "$key.crt" -noout -fingerprint -sha256 | sed 's/.*=//; s/://g')" "${shim_url##*/}" "$shim_sha256" \
    >"$iso/emaki/SECUREBOOT"
echo "OK: secure boot medium staged (shim ${shim_url##*/}, release certificate $(openssl x509 -in "$key.crt" -noout -subject))"
