#!/bin/bash
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Decide from saved `sbverify --list` output whether a shim build may be packaged. The
# package's check() runs sbverify and passes its output here; tests pass fixture text, so this
# script never runs sbverify itself.
#
#   check-signatures.sh shim NAME LIST    LIST must name both Microsoft UEFI CAs among the
#                                         image signature issuers: CA 2011, which most
#                                         firmware trusts, and CA 2023, which replaces it
#   check-signatures.sh signed NAME LIST  LIST must show at least one image signature
#
# NAME is the file name used in messages. Exit status: 0 accepted, 1 refused, 2 usage.
set -euo pipefail

if (($# != 3)) || [[ $1 != shim && $1 != signed ]]; then
    echo 'usage: check-signatures.sh shim|signed NAME LIST' >&2
    exit 2
fi
kind=$1 name=$2 list=$3
[[ -f $list ]] || { printf 'check-signatures.sh: %s does not exist\n' "$list" >&2; exit 2; }

# The common names of the issuers listed under each "image signature issuers:" heading.
# Certificate lines below "image signature certificates:" are not issuers of the image.
issuers=$(awk '
    /^image signature issuers:$/ { inside = 1; next }
    /^[^ ]/ { inside = 0 }
    inside && /^ - / { sub(/^ - /, ""); print }' "$list" | sed -n 's|.*/CN=\([^/]*\).*|\1|p')

if [[ -z $issuers ]]; then
    printf '%s carries no image signature, so firmware with Secure Boot on would refuse it. Not packaging it.\n' "$name" >&2
    exit 1
fi

if [[ $kind == signed ]]; then
    printf '%s: signed by %s\n' "$name" "$(paste -sd ';' <<<"$issuers" | sed 's/;/; /g')"
    exit 0
fi

refused=0
for required in 'Microsoft Corporation UEFI CA 2011' 'Microsoft UEFI CA 2023'; do
    if ! grep -Fxq -- "$required" <<<"$issuers"; then
        printf '%s is not signed by %s. Firmware that trusts only that certificate would refuse it. Not packaging it.\n' \
            "$name" "$required" >&2
        refused=1
    fi
done
((refused == 0)) || exit 1
printf '%s: signed by Microsoft Corporation UEFI CA 2011 and Microsoft UEFI CA 2023\n' "$name"
