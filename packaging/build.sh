#!/usr/bin/env bash
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Build the Emaki packages in the disposable Arch build VM, in dependency order, from a clean
# committed checkout, into one output directory. Packages come out unsigned: sign them on the
# publishing machine with `packaging/publish.sh sign DIR` (the private key never enters the VM).
#
# makepkg --cleanbuild, not a chroot: emaki-config and emaki-installer archive the git checkout
# around their recipe directory ($startdir/../..), which a makechrootpkg chroot does not contain.
set -Eeuo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(dirname -- "$HERE")
ORDER=(niri-emaki quickshell-emaki xdg-desktop-portal-gnome-emaki emaki-config emaki-nvidia emaki-desktop emaki-apps
       emaki-installer emaki-keyring emaki-mirrorlist emaki)
# Packages whose build needs makedepends from Arch (--syncdeps). The others depend at run time on
# Emaki packages that are not in any repository yet, so their dependencies are not checked here
# (--nodeps); publish.sh resolves the whole set against Arch before anything is published.
# pacman cannot install a package of this build either: a SYNCDEPS package whose only missing
# dependencies were built earlier in the same run gets --nodeps (dependency_flag below).
SYNCDEPS=(niri-emaki quickshell-emaki xdg-desktop-portal-gnome-emaki emaki-installer)
out=$ROOT/.cache/build-out
only=()
published_sources=''
dry_run=0
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
usage() {
    echo 'Usage: packaging/build.sh [--out DIR] [--only NAME]... [--published-sources SOURCES.json] [--dry-run]'
    echo "Order: ${ORDER[*]}"
}
while (($#)); do
    case $1 in
        --out) (($# >= 2)) || fail 'missing value for --out'; out=$2; shift 2 ;;
        --only) (($# >= 2)) || fail 'missing value for --only'; only+=("$2"); shift 2 ;;
        --published-sources) (($# >= 2)) || fail 'missing value for --published-sources'; published_sources=$2; shift 2 ;;
        --dry-run) dry_run=1; shift ;;
        --help) usage; exit 0 ;;
        *) fail "unknown option: $1" ;;
    esac
done
selected=()
for name in "${ORDER[@]}"; do
    if ((${#only[@]} == 0)) || [[ " ${only[*]} " == *" $name "* ]]; then selected+=("$name"); fi
done
for name in "${only[@]}"; do
    [[ " ${ORDER[*]} " == *" $name "* ]] || fail "unknown package: $name"
done
needs_syncdeps() { [[ " ${SYNCDEPS[*]} " == *" $1 "* ]]; }
# Name -> pkgver-pkgrel of every package this run has built so far.
declare -A built=()
# satisfied DEPENDENCY: a package built earlier in this run meets it (pacman's operators).
satisfied() {
    local name=${1%%[<>=]*} rest op cmp
    [[ -n ${built[$name]:-} ]] || return 1
    rest=${1#"$name"}
    op=${rest%%[!<>=]*}
    [[ -n $op ]] || return 0
    cmp=$(vercmp "${built[$name]}" "${rest#"$op"}")
    case $op in
        '=') ((cmp == 0)) ;;
        '>=') ((cmp >= 0)) ;;
        '<=') ((cmp <= 0)) ;;
        '>') ((cmp > 0)) ;;
        '<') ((cmp < 0)) ;;
        *) return 1 ;;
    esac
}
# Sets flag for a SYNCDEPS package: --syncdeps when no missing dependency is a package of this
# build; --nodeps when every missing one is and was built earlier in this run; otherwise stops
# before makepkg, which would fail with "target not found".
dependency_flag() {
    local name=$1 srcinfo dep deps=() missing=() ours=() others=() unbuilt=() described=()
    srcinfo=$(cd "$HERE/$name" && makepkg --printsrcinfo) || fail "makepkg --printsrcinfo failed for $name"
    mapfile -t deps < <(sed -n 's/^\t\(make\|check\)\{0,1\}depends\(_[a-z0-9_]*\)\{0,1\} = //p' <<<"$srcinfo")
    ((${#deps[@]} == 0)) || mapfile -t missing < <(pacman -T "${deps[@]}" || true)
    for dep in "${missing[@]}"; do
        if [[ " ${ORDER[*]} " == *" ${dep%%[<>=]*} "* ]]; then
            ours+=("$dep")
            satisfied "$dep" || unbuilt+=("$dep")
        else
            others+=("$dep")
        fi
    done
    flag=--syncdeps
    ((${#ours[@]})) || return 0
    ((${#unbuilt[@]} == 0)) || fail "$name needs ${unbuilt[*]}, which this run did not build; build it in the same run, before $name"
    ((${#others[@]} == 0)) || fail "$name needs ${others[*]} from the Arch repositories and ${ours[*]} from this build; makepkg --syncdeps cannot install only the former: install them in the build VM first"
    for dep in "${ours[@]}"; do described+=("$dep ${built[${dep%%[<>=]*}]}"); done
    flag=--nodeps
    printf '%s: --nodeps: %s built in this run\n' "$name" "${described[*]}"
}
# One output directory is one build: packages left by an earlier build would be signed and
# published with this one (two versions of a package, or an old version beside the new set).
if [[ -d $out && -n $(find "$out" -mindepth 1 -maxdepth 1 -print -quit) ]]; then
    fail "--out $out is not empty; remove it or choose an empty directory"
fi

commit=$(git -C "$ROOT" rev-parse --verify HEAD)
if [[ -n $published_sources ]]; then
    ((${#only[@]})) || fail '--published-sources requires explicit --only selections'
    release_args=()
    for name in "${only[@]}"; do release_args+=(--only "$name"); done
    python3 "$HERE/release_inputs.py" --repo "$ROOT" --published-sources "$published_sources" \
        "${release_args[@]}"
fi
if ((dry_run)); then
    printf 'Would build from %s into %s:\n' "$commit" "$out"
    for name in "${selected[@]}"; do
        flags='--cleanbuild --clean --noconfirm'
        if needs_syncdeps "$name"; then flags+=' --syncdeps'; else flags+=' --nodeps'; fi
        printf '  %s: makepkg --nobuild %s; archive prepared sources; makepkg %s\n' \
            "$name" "${flags/--clean /}" "${flags/--cleanbuild/--noextract}"
        [[ $name != niri-emaki && $name != emaki-config ]] || echo '    Vendor locked Cargo dependencies before archiving and compiling.'
    done
    echo '--syncdeps becomes --nodeps when every missing dependency is a package built earlier in the run.'
    exit 0
fi

((EUID != 0)) || fail 'run as the build user; makepkg refuses root'
[[ $(systemd-detect-virt --vm 2>/dev/null || true) =~ ^(qemu|kvm)$ ]] || fail 'builds run only in the disposable QEMU/KVM build VM'
for command in makepkg pacman vercmp git gpg sha256sum python3; do command -v "$command" >/dev/null || fail "missing $command"; done
[[ -z $(git -C "$ROOT" status --porcelain) ]] || fail 'the checkout has uncommitted changes; a package must equal a commit'
if [[ " ${selected[*]} " == *' emaki-config '* ]] && pacman -Q niri-emaki >/dev/null 2>&1; then
    # emaki-config's check() validates fork-only config with an installed niri-emaki and treats
    # an outdated one as an error: install the fork built from this checkout first.
    wanted=$(cd "$HERE/niri-emaki" && makepkg --printsrcinfo | awk '$1=="pkgver"{v=$3} $1=="pkgrel"{r=$3} END{print v"-"r}')
    installed=$(pacman -Q niri-emaki | awk '{print $2}')
    [[ $installed == "$wanted" ]] || fail "installed niri-emaki $installed differs from the recipe ($wanted); install the new build before emaki-config"
fi

out=$(realpath -m -- "$out")
mkdir -p -- "$out/logs"
work=$(mktemp -d "${TMPDIR:-/var/tmp}/emaki-build.XXXXXX")
trap 'rm -rf -- "$work"' EXIT
gpg --batch --quiet --import "$HERE"/xdg-desktop-portal-gnome-emaki/keys/pgp/*.asc

for name in "${selected[@]}"; do
    printf '== %s\n' "$name"
    flags=(--cleanbuild --clean --noconfirm)
    if needs_syncdeps "$name"; then dependency_flag "$name"; flags+=("$flag"); else flags+=(--nodeps); fi
    mapfile -t expected < <(cd "$HERE/$name" && PKGDEST=$out makepkg --packagelist | grep -v -- '-debug-')
    # Archive after prepare(), then compile that exact tree without extracting or
    # preparing again. In particular niri must compile against the bundled vendor tree.
    (
        cd "$HERE/$name" || exit 1
        export BUILDDIR=$work/build SRCDEST=$work/src PKGDEST=$out
        makepkg --nobuild --cleanbuild --noconfirm "${flags[-1]}" || exit 1
        src=$work/build/$name/src
        if [[ $name == niri-emaki || $name == emaki-config ]]; then
            python3 "$HERE/source_archive.py" vendor --src "$src" || exit 1
        fi
        version=$(makepkg --printsrcinfo | awk '$1=="pkgver"{v=$3} $1=="pkgrel"{r=$3} END{print v"-"r}') || exit 1
        python3 "$HERE/source_archive.py" archive --recipe "$HERE/$name" --src "$src" \
            --output "$out" --commit "$commit" --version "$version" || exit 1
        CARGO_NET_OFFLINE=true makepkg --noextract --clean --noconfirm "${flags[-1]}"
    ) >"$out/logs/$name.log" 2>&1 || fail "$name failed; see $out/logs/$name.log"
    for file in "${expected[@]}"; do
        [[ -s $file ]] || fail "$name did not produce $file"
        # NAME-PKGVER-PKGREL-ARCH.pkg.tar.*: neither pkgver nor pkgrel contains a hyphen.
        base=${file##*/}
        base=${base%-*.pkg.tar.*}
        built[${base%-*-*}]=${base#"${base%-*-*}"-}
    done
    recorded=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["packages"][sys.argv[2]]["version"])' "$out/SOURCES.json" "$name")
    [[ ${built[$name]:-} == "$recorded" ]] || fail "$name binary and source versions differ"
done
rm -f -- "$out"/*-debug-*.pkg.tar.zst
{
    printf 'commit %s\n' "$commit"
    (
        cd "$out"
        shopt -s nullglob
        artifacts=(*.pkg.tar.zst *.sources.tar.gz)
        [[ ! -f SOURCES.json ]] || artifacts+=(SOURCES.json)
        sha256sum -- "${artifacts[@]}"
    )
} >"$out/BUILDINFO"
printf 'Built %s package(s) from %s into %s\n' "${#selected[@]}" "$commit" "$out"
printf 'Next, on the publishing machine: packaging/publish.sh sign DIR, then publish testing DIR\n'
