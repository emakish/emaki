#!/bin/bash
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
# Build Quickshell with the patches of packaging/quickshell-emaki on this host's Qt, for the
# shell tests only (never install it). Use it when the host's qs is older than the packaged
# fork or the host has another Qt minor than the package's build:
#   tests/build-quickshell-fork.sh <directory> [<quickshell-<pkgver>.tar.gz>]
#   PATH=<directory>/bin:$PATH make check-shell
# Without a tarball the PKGBUILD's source is downloaded into <directory>; a tarball given
# must exist. Either way it must match the PKGBUILD's sha256. tests/test-quickshell-fork.py checks the result.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
RECIPE=$ROOT/packaging/quickshell-emaki
[ $# -ge 1 ] || { echo "usage: $0 <directory> [<tarball>]" >&2; exit 2; }
mkdir -p "$1"
OUT=$(cd "$1" && pwd)
pkgver=$(sed -n 's/^pkgver=//p' "$RECIPE/PKGBUILD")
url=$(sed -n 's/^url="\(.*\)"$/\1/p' "$RECIPE/PKGBUILD" | sed 's/\$_pkgname/quickshell/g')
sum=$(sed -n "s/^sha256sums=('\([0-9a-f]*\)'$/\1/p" "$RECIPE/PKGBUILD")
mapfile -t patches < <(sed -n 's/^\s*patch -Np1 -i "\$srcdir\/\(.*\)"$/\1/p' "$RECIPE/PKGBUILD")
[ -n "$pkgver" ] && [ -n "$url" ] && [ -n "$sum" ] && [ ${#patches[@]} -gt 0 ] ||
	{ echo "cannot read pkgver, url, sha256 or patches from $RECIPE/PKGBUILD" >&2; exit 1; }
if [ $# -ge 2 ]; then
	tarball=$2
	[ -f "$tarball" ] || { echo "no such tarball: $tarball" >&2; exit 1; }
else
	tarball=$OUT/quickshell-$pkgver.tar.gz
	[ -e "$tarball" ] || curl -fL --proto '=https' -o "$tarball" "$url/archive/v$pkgver.tar.gz"
fi
echo "$sum  $tarball" | sha256sum -c --quiet
rm -rf "$OUT/src" "$OUT/build" "$OUT/bin"
mkdir -p "$OUT/src"
tar -xzf "$tarball" -C "$OUT/src"
cd "$OUT/src/quickshell"
for patch in "${patches[@]}"; do patch -Np1 -i "$RECIPE/$patch"; done
# The PKGBUILD's options, installed into the directory instead of the system prefix.
cmake -G Ninja -B "$OUT/build" -W no-dev -D CMAKE_BUILD_TYPE=RelWithDebInfo \
	-D DISTRIBUTOR="Emaki test build (packaging/quickshell-emaki patches, host Qt)" \
	-D DISTRIBUTOR_DEBUGINFO_AVAILABLE=Yes -D CRASH_HANDLER=OFF \
	-D CMAKE_INSTALL_PREFIX="$OUT" -D INSTALL_QML_PREFIX=lib/qt6/qml
nice -n 15 cmake --build "$OUT/build" -j "$(( $(nproc) / 2 > 0 ? $(nproc) / 2 : 1 ))"
cmake --install "$OUT/build" > /dev/null
"$OUT/bin/qs" --version
echo "Built. Run the shell tests with PATH=$OUT/bin:\$PATH"
