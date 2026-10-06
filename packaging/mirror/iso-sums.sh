#!/usr/bin/env bash
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Write IMAGE.sha256 ("<64 hex>  <file name>", what `sha256sum -c` reads) and, when a key is
# given in the environment, a detached binary signature IMAGE.sig, verified before success.
#
#   EMAKI_ISO_SIGN_KEY=<fingerprint> [EMAKI_SIGNING_GNUPGHOME=<dir>] packaging/mirror/iso-sums.sh IMAGE
#
# Without EMAKI_ISO_SIGN_KEY only the checksum is written: the build VM holds no private key,
# and `packaging/publish.sh iso` signs on the publishing machine. gpg asks for the passphrase
# through pinentry; this script never takes one.
set -Eeuo pipefail
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
(($# == 1)) || fail 'usage: iso-sums.sh IMAGE'
image=$(realpath -e -- "$1")
directory=$(dirname -- "$image")
name=$(basename -- "$image")
[[ $name != *[[:space:]]* ]] || fail 'the image name must not contain spaces'
rm -f -- "$image.sha256" "$image.sig"
(cd -- "$directory" && sha256sum -- "$name") >"$image.sha256.tmp"
mv -- "$image.sha256.tmp" "$image.sha256"
cat -- "$image.sha256"
key=${EMAKI_ISO_SIGN_KEY:-}
if [[ -z $key ]]; then
    echo 'No EMAKI_ISO_SIGN_KEY: checksum only; packaging/publish.sh iso signs it before upload.'
    exit 0
fi
home=${EMAKI_SIGNING_GNUPGHOME:-${GNUPGHOME:-$HOME/.gnupg}}
GNUPGHOME=$home gpg --yes --quiet --local-user "$key" --detach-sign --no-armor \
    --output "$image.sig.tmp" -- "$image"
status=$(GNUPGHOME=$home gpg --batch --status-fd 1 --verify "$image.sig.tmp" "$image" 2>/dev/null || true)
grep -q "^\[GNUPG:\] VALIDSIG .* ${key}\$\|^\[GNUPG:\] VALIDSIG ${key} " <<<"$status" ||
    { rm -f -- "$image.sig.tmp"; fail "the new signature does not verify with key $key"; }
mv -- "$image.sig.tmp" "$image.sig"
printf 'Signed: %s.sig (key %s)\n' "$name" "$key"
