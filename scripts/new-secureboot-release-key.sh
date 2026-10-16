#!/usr/bin/env bash
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
# Make the Emaki Secure Boot release key, once, on the publishing machine (docs/signing.md,
# "The Secure Boot release key"). The install medium's GRUB and kernels are signed with it; a PC
# trusts it after a person enrols emaki.cer from the stick in MokManager.
#
# Writes into DIR (default ${XDG_CONFIG_HOME:-~/.config}/emaki-secureboot, created 0700):
#   emaki-release.key  the private key, RSA-2048, PKCS#8 encrypted with a passphrase
#                      (AES-256-CBC, PBKDF2-HMAC-SHA256, 600000 iterations); never stored unencrypted
#   emaki-release.crt  its self-signed code-signing certificate (PEM, public)
#   emaki-release.cer  the same certificate in DER (public: the stick's emaki.cer)
# The passphrase is typed into OpenSSL's own prompt on this terminal (twice to set it, once more to
# sign the certificate); it never passes through an argument, a variable or a file of this script.
# The unencrypted key exists only inside OpenSSL and one pipe. Refuses: shell tracing, swap that is
# not encrypted, a core dump handler that ignores the size limit, overwriting any of the three
# files, a DIR inside a git work tree. Prints the SHA-256 and SHA-1 fingerprints (MokManager shows
# the SHA-1 one).
#
# Usage: scripts/new-secureboot-release-key.sh [--dir DIR]
set -Eeuo pipefail
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=SCRIPTDIR/../iso/secureboot-host.sh
. "$HERE/../iso/secureboot-host.sh"
secret_no_trace
dir=${XDG_CONFIG_HOME:-$HOME/.config}/emaki-secureboot
while (($#)); do
    case $1 in
        --dir) (($# >= 2)) || fail 'missing value for --dir'; dir=$2; shift 2 ;;
        --help) sed -n '4,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) fail "unknown option: $1" ;;
    esac
done
command -v openssl >/dev/null || fail 'missing dependency: openssl'
dir=$(realpath -m -- "$dir")
key=$dir/emaki-release.key crt=$dir/emaki-release.crt cer=$dir/emaki-release.cer
# Typed passphrases need a terminal; without one OpenSSL would read them from standard input.
{ : </dev/tty; } 2>/dev/null || fail 'run this in a terminal: the passphrase is typed into it'
# No swap that could write the unlocked key to a disk, no core dump of the processes holding it.
host_memory_check
for file in "$key" "$crt" "$cer" "$key.part" "$crt.part"; do
    [[ ! -e $file && ! -L $file ]] || fail "$file already exists; the release key is made once and never overwritten"
done
# Nothing under a git work tree: a stray `git add` would put the key into the source archive.
parent=$dir
while [[ ! -d $parent ]]; do parent=$(dirname -- "$parent"); done
if command -v git >/dev/null && git -C "$parent" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    fail "$dir is inside a git work tree; keep the release key outside every checkout"
fi
if [[ -d $dir ]]; then
    [[ $(stat -c %a -- "$dir") == 700 && -O $dir ]] || fail "$dir must be a private directory (0700, yours)"
else
    mkdir -p -- "$(dirname -- "$dir")"
    mkdir -m 0700 -- "$dir"
fi
umask 077
trap 'rm -f -- "$key.part" "$crt.part"' EXIT
# The same certificate profile as the installed systems' machine key (installer secureboot.py),
# which shim and MokManager accept: code signing, not the module-signing-only usage.
year=$(date -u +%Y)
config="[req]
distinguished_name = dn
prompt = no
x509_extensions = v3
[dn]
CN = Emaki Secure Boot release key $year
[v3]
basicConstraints = critical,CA:FALSE
keyUsage = critical,digitalSignature
extendedKeyUsage = codeSigning
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid
"
# The unencrypted key exists only inside OpenSSL and the pipe between the two commands: never in a
# variable, an argument or a file of this script.
echo 'Choose the passphrase of the Emaki Secure Boot release key (typed twice; keep it in your password manager).'
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 2>/dev/null \
    | openssl pkcs8 -topk8 -v2 aes-256-cbc -v2prf hmacWithSHA256 -iter 600000 -in /dev/stdin -out "$key.part" \
    || fail 'the key was not encrypted (the two passphrases differ or none was typed); nothing was kept'
head -n 1 -- "$key.part" | grep -qx -- '-----BEGIN ENCRYPTED PRIVATE KEY-----' || fail 'the written key is not encrypted; nothing was kept'
# OpenSSL accepts an empty passphrase; the only one this script can test without seeing it.
! openssl pkey -in "$key.part" -passin pass: -noout </dev/null >/dev/null 2>&1 \
    || fail 'the passphrase is empty; nothing was kept'
# The certificate is signed from the encrypted file: typing the passphrase once more proves it opens the key.
echo 'Type the passphrase once more to sign the certificate.'
openssl req -new -x509 -sha256 -days 36500 -config <(printf '%s' "$config") -key "$key.part" -out "$crt.part" \
    || fail 'the certificate was not made (wrong passphrase?); nothing was kept'
mv -- "$crt.part" "$crt"
mv -- "$key.part" "$key"
chmod 0644 -- "$crt"
openssl x509 -in "$crt" -outform DER -out "$cer"
chmod 0644 -- "$cer"
trap - EXIT
fingerprint() { openssl x509 -in "$crt" -noout -fingerprint "-$1" | sed 's/^[^=]*=//'; }
printf 'Made the Emaki Secure Boot release key in %s:\n' "$dir"
printf '  emaki-release.key  private key, encrypted with your passphrase (back up this file and the passphrase)\n'
printf '  emaki-release.crt  certificate, public\n'
printf '  emaki-release.cer  the same certificate in DER, public: the emaki.cer of the install medium\n'
printf 'Subject: %s\n' "$(openssl x509 -in "$crt" -noout -subject -nameopt utf8,sep_comma_plus | sed 's/^subject=//')"
printf 'Valid until: %s\n' "$(openssl x509 -in "$crt" -noout -enddate | sed 's/^notAfter=//')"
printf 'SHA-256 fingerprint: %s\n' "$(fingerprint sha256)"
printf 'SHA-1 fingerprint (MokManager shows this one): %s\n' "$(fingerprint sha1)"
printf 'Next: commit emaki-release.crt as iso/secureboot-release.crt (docs/signing.md).\n'
