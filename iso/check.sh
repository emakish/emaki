#!/usr/bin/env bash
# Local static gates only: no installs, database sync, build, or VM operations.
set -Eeuo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(dirname -- "$HERE")
"$HERE/preflight.sh" "$@"
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
    incomplete=1
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
files=("$HERE/packages-extra.txt" "$HERE/target-packages.txt" "$HERE/emaki-packages.txt" "$HERE/profile/packages.x86_64")
if command -v pacman >/dev/null; then
    failed=()
    mapfile -t packages < <(cat "${files[@]}" | sed '/^#/d; /^$/d' | LC_ALL=C sort -u)
    for package in "${packages[@]}"; do
        grep -Fxq "$package" "$HERE/emaki-packages.txt" && continue
        pacman -Si "$package" >/dev/null 2>&1 || failed+=("$package")
    done
    if ((${#failed[@]})); then printf 'UNVERIFIED package: %s\n' "${failed[@]}"; incomplete=1
    else echo 'OK: every non-Emaki seed exists in the host cached Arch sync databases'; fi
else
    echo 'UNVERIFIED: pacman unavailable'
fi
python3 -B "$ROOT/tests/test-iso-preflight.py"
modules=${GRUB_MODULE_DIR-/usr/lib/grub/x86_64-efi}
if [[ -d $modules ]]; then
    python3 -B "$HERE/check-grub-modules.py" "$HERE/profile/grub" "$modules"
elif [[ ${GRUB_MODULE_DIR+x} ]]; then
    printf 'ERROR: GRUB_MODULE_DIR is not a directory: %s\n' "$modules" >&2
    exit 1
else
    printf 'SKIP: build GRUB module check; default directory absent: %s (set GRUB_MODULE_DIR to require it)\n' "$modules"
fi
python3 -B "$HERE/test-static.py"
python3 -B "$HERE/test-live-hygiene.py"
python3 -B "$HERE/test-live-payload.py"
python3 -B "$HERE/test-loopback-assets.py"
python3 -B "$HERE/test-offline-repo.py"
python3 -B "$HERE/test-efi-image.py"
exit "$incomplete"
