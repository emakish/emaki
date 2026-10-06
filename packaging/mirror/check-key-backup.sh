#!/usr/bin/env bash
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Prove a backup of the signing key: restore it into an empty GNUPGHOME, sign a test file with
# it, and verify that signature against the public keyring in git. A backup counts only after
# this has passed on a machine other than the one that holds the working key.
#
#   packaging/mirror/check-key-backup.sh BACKUP [--keyring emaki.gpg] [--trusted emaki-trusted]
#
# BACKUP is the exported secret key (gpg --export-secret-keys). gpg asks for its passphrase
# through pinentry; this script never takes one. Nothing outside a temporary directory changes.
set -Eeuo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(dirname -- "$(dirname -- "$HERE")")
keyring=$ROOT/packaging/emaki-keyring/emaki.gpg
trusted=$ROOT/packaging/emaki-keyring/emaki-trusted
backup=''
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
while (($#)); do
    case $1 in
        --keyring|--trusted)
            (($# >= 2)) || fail "missing value for $1"
            if [[ $1 == --keyring ]]; then keyring=$2; else trusted=$2; fi
            shift 2 ;;
        -*) fail "unknown option: $1" ;;
        *) [[ -z $backup ]] || fail 'one backup file only'; backup=$1; shift ;;
    esac
done
[[ -r $backup ]] || fail 'usage: check-key-backup.sh BACKUP [--keyring FILE] [--trusted FILE]'
[[ -r $keyring && -r $trusted ]] || fail "public keyring files not readable: $keyring $trusted"
work=$(mktemp -d "${TMPDIR:-/tmp}/emaki-key-check.XXXXXX")
# shellcheck disable=SC2329  # invoked by the EXIT trap
cleanup() {
    gpgconf --homedir "$work/restored" --kill all 2>/dev/null || true
    gpgconf --homedir "$work/public" --kill all 2>/dev/null || true
    rm -rf -- "$work"
}
trap cleanup EXIT
mkdir -m 700 -- "$work/restored" "$work/public"

gpg --homedir "$work/restored" --batch --quiet --import "$backup" 2>"$work/import.log" ||
    fail "the backup does not import: $(tr '\n' ' ' <"$work/import.log")"
mapfile -t secret < <(gpg --homedir "$work/restored" --with-colons --list-secret-keys |
    awk -F: '$1 == "sec" { want = 1; next } want && $1 == "fpr" { print $10; want = 0 }')
((${#secret[@]} > 0)) || fail 'the backup holds no secret key'
mapfile -t trusted_fprs < <(cut -d: -f1 "$trusted" | grep -E '^[0-9A-F]{40}$')

printf 'Emaki key backup check %s\n' "$(date -u +%FT%TZ)" >"$work/test-file"
result=1
for fingerprint in "${secret[@]}"; do
    [[ " ${trusted_fprs[*]} " == *" $fingerprint "* ]] || { printf 'skip %s: not in %s\n' "$fingerprint" "$trusted"; continue; }
    gpg --homedir "$work/restored" --quiet --yes --local-user "$fingerprint" --detach-sign \
        --output "$work/test-file.sig" "$work/test-file" || fail "signing with the restored key $fingerprint failed"
    gpg --homedir "$work/public" --batch --quiet --import "$keyring" 2>/dev/null
    status=$(gpg --homedir "$work/public" --batch --status-fd 1 --verify "$work/test-file.sig" "$work/test-file" 2>/dev/null || true)
    signer=$(awk '$2 == "VALIDSIG" { print $NF }' <<<"$status")
    [[ $signer == "$fingerprint" ]] || fail "the signature by the restored key does not verify against $keyring"
    expires=$(gpg --homedir "$work/public" --with-colons --list-keys "$fingerprint" | awk -F: '$1 == "pub" { print $7 }')
    if [[ -n $expires ]]; then
        printf 'key %s expires %s\n' "$fingerprint" "$(date -u -d "@$expires" +%F)"
        if ((expires < $(date +%s) + 365 * 86400)); then
            printf 'WARNING: it expires within a year; extend it now (the yearly step)\n'
        fi
    else
        printf 'key %s does not expire\n' "$fingerprint"
    fi
    result=0
done
((result == 0)) || fail "the backup holds none of the trusted keys in $trusted"
passphrase=$HOME/.config/emaki-signing/passphrase
if [[ -e $passphrase ]]; then
    printf 'NOTE: %s exists on this machine; it must be deleted\n' "$passphrase"
fi
printf 'PASS: the backup restores, signs, and verifies against %s\n' "$keyring"
