#!/usr/bin/env bash
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
# packaging/mirror/check-key-backup.sh with throwaway keys made in a temporary GNUPGHOME:
# a real backup passes; another key's backup, and a keyring without the key, fail.
set -Eeuo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
CHECK=$(dirname -- "$HERE")/packaging/mirror/check-key-backup.sh
work=$(mktemp -d "${TMPDIR:-/tmp}/emaki-key-backup-test.XXXXXX")
# shellcheck disable=SC2329  # invoked by the EXIT trap
cleanup() {
    for home in "$work"/*/; do gpgconf --homedir "$home" --kill all 2>/dev/null || true; done
    rm -rf -- "$work"
}
trap cleanup EXIT
# The check makes its temporary homes under TMPDIR: here, so that they are counted at the end.
export TMPDIR=$work/tmp
mkdir -- "$TMPDIR"
key() {  # key NAME -> directory with secret.asc, public.gpg, trusted
    local dir=$work/$1
    mkdir -m 700 -- "$dir"
    GNUPGHOME=$dir gpg --batch --quiet --pinentry-mode loopback --passphrase '' \
        --quick-gen-key "$1 <$1@example.invalid>" ed25519 sign 2d 2>/dev/null
    local fpr
    fpr=$(GNUPGHOME=$dir gpg --with-colons --list-secret-keys 2>/dev/null | awk -F: '$1 == "fpr" { print $10; exit }')
    GNUPGHOME=$dir gpg --batch --quiet --export-secret-keys "$fpr" >"$dir/secret.asc" 2>/dev/null
    GNUPGHOME=$dir gpg --batch --quiet --export "$fpr" >"$dir/public.gpg" 2>/dev/null
    printf '%s:4:\n' "$fpr" >"$dir/trusted"
}
key daily
key stranger
failures=0
expect() {  # expect pass|fail DESCRIPTION -- command...
    local want=$1 what=$2 code=0
    shift 3
    HOME=$work "$@" >"$work/out" 2>&1 || code=$?
    if [[ $want == pass && $code -eq 0 ]] || [[ $want == fail && $code -ne 0 ]]; then
        printf 'ok: %s\n' "$what"
    else
        printf 'NOT OK: %s (exit %s)\n' "$what" "$code"; sed 's/^/    /' "$work/out"; failures=$((failures + 1))
    fi
}
expect pass 'the backup of the trusted key restores, signs and verifies' -- \
    "$CHECK" "$work/daily/secret.asc" --keyring "$work/daily/public.gpg" --trusted "$work/daily/trusted"
if grep -q 'expires within a year' "$work/out"; then printf 'ok: a two-day key is reported as expiring\n'; else
    printf 'NOT OK: no expiry warning\n'; failures=$((failures + 1)); fi
expect fail 'a backup of another key' -- \
    "$CHECK" "$work/stranger/secret.asc" --keyring "$work/daily/public.gpg" --trusted "$work/daily/trusted"
expect fail 'a keyring in git without the backed-up key' -- \
    "$CHECK" "$work/daily/secret.asc" --keyring "$work/stranger/public.gpg" --trusted "$work/daily/trusted"
expect fail 'a public key instead of a secret backup' -- \
    "$CHECK" "$work/daily/public.gpg" --keyring "$work/daily/public.gpg" --trusted "$work/daily/trusted"
mkdir -p "$work/.config/emaki-signing" && : >"$work/.config/emaki-signing/passphrase"
expect pass 'a passphrase file is reported, not hidden' -- \
    "$CHECK" "$work/daily/secret.asc" --keyring "$work/daily/public.gpg" --trusted "$work/daily/trusted"
if grep -q 'passphrase exists on this machine' "$work/out"; then printf 'ok: the passphrase file is named\n'; else
    printf 'NOT OK: passphrase file not reported\n'; failures=$((failures + 1)); fi
# Nothing GnuPG started for the checks may still run once this test's own homes are stopped.
gpgconf --homedir "$work/daily" --kill all
gpgconf --homedir "$work/stranger" --kill all
if python3 "$HERE/gnupg-daemons.py" "$work"; then printf 'ok: no GnuPG daemon left behind\n'; else
    printf 'NOT OK: GnuPG daemons left behind (stopped now)\n'; failures=$((failures + 1)); fi
if ((failures)); then echo "FAIL: $failures"; exit 1; fi
echo PASS
