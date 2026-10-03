#!/usr/bin/env bash
# Local static gates only: no installs, database sync, build, or VM operations.
# Profile variables are supplied by the dynamically sourced profile.
# shellcheck disable=SC2154
set -Eeuo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(dirname -- "$HERE")
incomplete=0
mapfile -t scripts < <(find "$HERE" -type f -name '*.sh' -print)
scripts+=("$ROOT/tests/vm/run-iso.sh" "$ROOT"/tests/vm/iso-*.sh)
for script in "${scripts[@]}"; do bash -n "$script"; done
printf 'OK: bash syntax (%s scripts)\n' "${#scripts[@]}"
if command -v shellcheck >/dev/null; then
    shellcheck -s bash "${scripts[@]}"
    echo 'OK: shellcheck'
else
    echo 'UNVERIFIED: shellcheck unavailable'
fi
python3 - "$HERE" "$ROOT/tests/vm" <<'PY'
import ast
from pathlib import Path
import sys
for directory in map(Path, sys.argv[1:]):
    pattern = 'iso-*.py' if directory.name == 'vm' else '*.py'
    for path in directory.glob(pattern):
        ast.parse(path.read_text(), filename=str(path))
print('OK: Python syntax')
PY
if [[ -f $HERE/profile/profiledef.sh ]]; then
    (
        cd -- "$HERE/profile"
        declare -A file_permissions=()
        # shellcheck disable=SC1091
        source ./profiledef.sh
        [[ $iso_name == emaki && $iso_version == 0.1.0 && $iso_label == EMAKI_0.1.0 ]]
        [[ $install_dir == emaki && ${buildmodes[*]} == iso ]]
        ((${#bootmodes[@]} > 0))
        [[ ${file_permissions[/etc/shadow]} == 0:0:0400 ]]
        [[ ${file_permissions[/root]} == 0:0:0700 ]]
    )
    echo 'OK: profiledef dry parse'
else
    echo 'UNVERIFIED: full releng profile unavailable; build.sh imports installed v91 in the VM'
    incomplete=1
fi
files=("$HERE/packages-extra.txt" "$HERE/target-packages.txt" "$HERE/emaki-packages.txt")
[[ ! -f $HERE/profile/packages.x86_64 ]] || files+=("$HERE/profile/packages.x86_64")
for file in "${files[@]}"; do LC_ALL=C sort -cu "$file"; done
echo 'OK: package seeds sorted and unique'
if command -v pacman >/dev/null; then
    failed=()
    mapfile -t packages < <(cat "${files[@]}" | LC_ALL=C sort -u)
    for package in "${packages[@]}"; do
        grep -Fxq "$package" "$HERE/emaki-packages.txt" && continue
        pacman -Si "$package" >/dev/null 2>&1 || failed+=("$package")
    done
    if ((${#failed[@]})); then printf 'UNVERIFIED package: %s\n' "${failed[@]}"; incomplete=1
    else echo 'OK: every non-Emaki seed exists in the host cached Arch sync databases'; fi
else
    echo 'UNVERIFIED: pacman unavailable'
fi
python3 -B "$HERE/test-static.py"
exit "$incomplete"
