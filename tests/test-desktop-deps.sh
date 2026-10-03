#!/bin/bash
# Dependency arrays come from PKGBUILD via source.
# shellcheck disable=SC2154
# Every dependency of the packaging/emaki-desktop metapackage is in the official repositories
# (pacman -Si against a synchronized database). Installs and builds nothing.
set -u
R="$(cd "$(dirname "$0")/.." && pwd)"
# PKGBUILD is bash: read its arrays the same way makepkg does.
# shellcheck source=/dev/null
source "$R/packaging/emaki-desktop/PKGBUILD"
fail=0
names=()
for d in "${depends[@]}" "${checkdepends[@]}"; do names+=("${d%%[<>=]*}"); done
for d in "${optdepends[@]}"; do names+=("${d%%:*}"); done
# Evaluate each local recipe in isolation so its metadata cannot replace ours.
declare -A local_packages=()
for recipe in "$R"/packaging/*/PKGBUILD; do
    while IFS= read -r name; do local_packages["$name"]=1; done < <(
        export startdir
        startdir=$(dirname "$recipe")
        # shellcheck source=/dev/null
        source "$recipe"
        printf '%s\n' "${pkgname[@]}"
    )
done
for n in "${names[@]}"; do
    if [[ ${local_packages[$n]:-} ]]; then
        echo "ok   $n (local packaging recipe)"
        continue
    fi
    if pacman -Si "$n" >/dev/null 2>&1; then
        echo "ok   $n"
    else
        echo "FAIL $n: not in the sync repositories (pacman -Si)"
        fail=1
    fi
done
# Single source: the name is not repeated.
dups=$(printf '%s\n' "${names[@]}" | sort | uniq -d)
if [ -n "$dups" ]; then echo "FAIL duplicates: $dups"; fail=1; fi
[ "$fail" -eq 0 ] && echo "ALL GREEN (${#names[@]} names)"
exit "$fail"
