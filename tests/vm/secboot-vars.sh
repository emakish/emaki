#!/usr/bin/env bash
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
# Writes the four Secure Boot OVMF variable stores run-iso.sh starts with EMAKI_VM_SECBOOT=<variant>.
# Every store has Secure Boot on, Microsoft's OEM PK and both Microsoft KEKs; they differ in db:
#   2011  Windows Production PCA 2011 + Microsoft Corporation UEFI CA 2011 (an older machine)
#   2023  Windows UEFI CA 2023 + Microsoft UEFI CA 2023 + Option ROM UEFI CA 2023 (a new machine)
#   both  all five (a machine that took Microsoft's 2023 update)
#   none  both Windows CAs, no third-party UEFI CA (a Secured-core PC: Windows boots, shim does not)
# Needs virt-fw-vars (python-virt-firmware, e.g. in a venv); its bundled Microsoft certificates are
# checked against the SHA-256 fingerprints of Microsoft's downloads below before anything is written.
# Usage: secboot-vars.sh --out DIR [--virt-fw-vars PATH] [--template OVMF_VARS.4m.fd] [--certs DIR]
# (--certs: a directory of <name>.pem in virt-firmware's naming; default: virt-firmware's own)
set -Eeuo pipefail
die() { printf 'BAD: %s\n' "$*" >&2; exit 1; }
out='' certs='' tool=${EMAKI_VIRT_FW_VARS:-virt-fw-vars} template=${EMAKI_ISO_OVMF_DIR:-/usr/share/edk2/x64}/OVMF_VARS.4m.fd
while (($#)); do
    case $1 in
        --out|--virt-fw-vars|--template|--certs)
            (($# >= 2)) || die "missing value for $1"
            case $1 in --out) out=$2 ;; --virt-fw-vars) tool=$2 ;; --template) template=$2 ;; --certs) certs=$2 ;; esac
            shift 2 ;;
        --help) sed -n '2,12p' "$0"; exit 0 ;;
        *) die "unknown option: $1" ;;
    esac
done
[[ -n $out ]] || die '--out is required'
[[ -r $template ]] || die "OVMF variable template unavailable: $template"
command -v "$tool" >/dev/null || die "virt-fw-vars unavailable: $tool"
# By default the certificates come with virt-firmware; its Python is the one next to the tool.
if [[ -z $certs ]]; then
    certs=$("$(dirname -- "$(command -v "$tool")")/python3" -c \
        'import os, virt.firmware; print(os.path.join(os.path.dirname(virt.firmware.__file__), "certs", "microsoft.com"))') \
        || die 'cannot locate the Microsoft certificates of virt-firmware'
fi
# SHA-256 of the DER certificates Microsoft publishes (go.microsoft.com links in the Secure Boot docs).
declare -A expect=(
    [windows-2011]=E8E95F0733A55E8BAD7BE0A1413EE23C51FCEA64B3C8FA6A786935FDDCC71961
    [windows-2023]=076F1FEA90AC29155EBF77C17682F75F1FDD1BE196DA302DC8461E350A9AE330
    [ms-uefi-2011]=48E99B991F57FC52F76149599BFF0A58C47154229B9F8D603AC40D3500248507
    [ms-uefi-2023]=F6124E34125BEE3FE6D79A574EAA7B91C0E7BD9D929C1A321178EFD611DAD901
    [ms-uefi-rom-2023]=E5BE3E64C6E66A281457ECDECE0D6D0787577AAD2A3A0144262C10C14BA8D8F1
)
for name in "${!expect[@]}"; do
    file=$certs/$name.pem
    [[ -r $file ]] || die "missing certificate $file"
    got=$(openssl x509 -in "$file" -noout -fingerprint -sha256 | sed 's/.*=//; s/://g')
    [[ $got == "${expect[$name]}" ]] || die "$name.pem does not match Microsoft's certificate ($got)"
done
vendor=77fa9abd-0359-4d32-bd60-28f4e78f784b  # Microsoft's owner GUID in db
declare -A db=(
    [2011]='windows-2011 ms-uefi-2011'
    [2023]='windows-2023 ms-uefi-2023 ms-uefi-rom-2023'
    [both]='windows-2011 windows-2023 ms-uefi-2011 ms-uefi-2023 ms-uefi-rom-2023'
    [none]='windows-2011 windows-2023'
)
mkdir -p -- "$out"
for variant in 2011 2023 both none; do
    args=()
    for name in ${db[$variant]}; do args+=(--add-db "$vendor" "$certs/$name.pem"); done
    target=$out/OVMF_VARS.secboot-$variant.4m.fd
    "$tool" -l warning -i "$template" -o "$target.new" --enroll-microsoft --microsoft-kek all --microsoft-db none \
        "${args[@]}" --secure-boot >/dev/null
    mv -f -- "$target.new" "$target"
    printf 'OK: %s (db: %s)\n' "$target" "${db[$variant]}"
done
