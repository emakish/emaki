#!/usr/bin/env bash
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
# Prove a backup of the Emaki Secure Boot release key (docs/signing.md section 5): the copy taken back
# from the backup place is stored encrypted, opens with the passphrase (OpenSSL asks on this terminal)
# and is the private key of the pinned certificate. PASS only when every step succeeded and both public
# keys are present and equal. Same machine rules as making the key (no unencrypted swap, no core dumps).
#
# Usage: scripts/check-secureboot-key-backup.sh BACKUP.key [CERTIFICATE]   (default iso/secureboot-release.crt)
set -Eeuo pipefail
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=SCRIPTDIR/../iso/secureboot-host.sh
. "$HERE/../iso/secureboot-host.sh"
secret_no_trace
(($# >= 1 && $# <= 2)) || fail 'usage: check-secureboot-key-backup.sh BACKUP.key [CERTIFICATE]'
backup=$1 cert=${2:-$HERE/../iso/secureboot-release.crt}
command -v openssl >/dev/null || fail 'missing dependency: openssl'
# Before the first read of the candidate key (also when it is then refused): no unencrypted swap, a
# sleep block held, no core dumps.
host_memory_check
[[ -f $backup && -r $backup ]] || fail "no backup file at $backup"
[[ -f $cert && -r $cert ]] || fail "no certificate at $cert"
head -n 1 -- "$backup" | grep -qx -- '-----BEGIN ENCRYPTED PRIVATE KEY-----' || fail "$backup is not stored encrypted"
cert_pub=$(openssl x509 -in "$cert" -noout -pubkey) || fail "$cert is not a certificate"
[[ -n $cert_pub ]] || fail "$cert has no public key"
{ : </dev/tty; } 2>/dev/null || fail 'run this in a terminal: the passphrase is typed into it'
echo 'Type the passphrase of the Emaki Secure Boot release key.'
key_pub=$(openssl pkey -in "$backup" -pubout) || fail 'the backup did not open (wrong passphrase?)'
[[ -n $key_pub ]] || fail 'the backup gave no public key'
[[ $key_pub == "$cert_pub" ]] || fail "the backup is not the private key of $cert"
echo "PASS: the backup opens with the passphrase and is the key of $cert ($(openssl x509 -in "$cert" -noout -fingerprint -sha256 | sed 's/^[^=]*=//'))"
