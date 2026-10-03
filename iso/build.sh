#!/usr/bin/env bash
# Run only in the disposable Arch build VM with archiso 91 and trusted signing keys.
set -Eeuo pipefail
trap 'printf "ERROR: ISO build failed at line %s (rc=%s)\n" "$LINENO" "$?" >&2' ERR
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(dirname -- "$HERE")
repo=$ROOT/.cache/repo/testing
out='' work='' test_key='' test_mode=0
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
while (($#)); do
    case $1 in
        --repo|--out|--work|--test-key)
            (($# >= 2)) || fail "missing value for $1"
            case $1 in --repo) repo=$2 ;; --out) out=$2 ;; --work) work=$2 ;; --test-key) test_key=$2; test_mode=1 ;; esac
            shift 2 ;;
        --test) test_mode=1; shift ;;
        --help)
            echo 'Usage: sudo iso/build.sh [--repo DIR] [--out DIR] [--work DIR] [--test --test-key PUBLIC_KEY]'
            exit 0 ;;
        *) fail "unknown option: $1" ;;
    esac
done
((EUID == 0)) || fail 'run as root inside the disposable build VM'
[[ $(systemd-detect-virt --vm) =~ ^(qemu|kvm)$ ]] || fail 'build is restricted to the QEMU/KVM build VM'
[[ $(pacman -Q archiso) == 'archiso 91-'* ]] || fail 'archiso 91 is required; re-audit staging before upgrading'
for command in mkarchiso pacman pacman-key repo-add python3 ssh-keygen flock xorriso unsquashfs mcopy; do
    command -v "$command" >/dev/null || fail "missing dependency: $command"
done
mode=release
if ((test_mode)); then
    mode='test'
    [[ -n $test_key && -r $test_key ]] || fail '--test requires --test-key PUBLIC_KEY'
    test_key=$(realpath -- "$test_key")
    ssh-keygen -l -f "$test_key" >/dev/null || fail 'invalid test public key'
    [[ $(wc -l < "$test_key") -le 1 ]] || fail 'use one public key'
    [[ $(head -c 8 -- "$test_key") != '-----BEG' ]] || fail 'private keys are forbidden'
fi
repo=$(realpath -e -- "$repo")
work=$(realpath -m -- "${work:-$ROOT/.cache/iso-work-$mode}")
out=$(realpath -m -- "${out:-$ROOT/.cache/iso-out-$mode}")
# These paths enter pacman Server and HMP-independent tools; keep URI syntax unambiguous.
for path in "$repo" "$work" "$out"; do
    [[ $path != *[[:space:]\#%\?]* ]] || fail 'build paths must not contain whitespace, #, %, or ?'
done
[[ $work != / && $work != "$ROOT" && $work != "$HERE" ]] || fail 'unsafe work directory'
[[ $ROOT != "$work/"* && $repo != "$work/"* && $out != "$work/"* && $repo != "$work" && $out != "$work" ]] || fail 'work must not contain the source, repository, or output'
[[ -z $test_key || $test_key != "$work/"* ]] || fail 'test key must be outside work'
mkdir -p -- "$work" "$out"
exec 9>"$work/.lock"
flock -n 9 || fail 'work directory is already in use'
exec 8>"$out/.lock"
flock -n 8 || fail 'output directory is already in use'
if [[ -f $out/.emaki-mode ]]; then
    [[ $(<"$out/.emaki-mode") == "$mode" ]] || fail 'test/release output directories must be separate'
fi
printf '%s\n' "$mode" >"$out/.emaki-mode"
# A failed rebuild must never leave an older image/checksum looking current.
rm -f -- "$out"/emaki-*.iso*
if [[ -f $work/.emaki-mode ]]; then
    [[ $(<"$work/.emaki-mode") == "$mode" ]] || fail 'test/release work directories must be separate'
else
    [[ -z $(find "$work" -mindepth 1 -maxdepth 1 ! -name .lock -print -quit) ]] || fail 'refusing an unowned nonempty work directory'
    printf '%s\n' "$mode" >"$work/.emaki-mode"
fi
version=$(<"$HERE/VERSION")
[[ $version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail 'invalid VERSION'
python3 "$HERE/repo-files.py" check-input "$repo" "$HERE/emaki-packages.txt"
# Keep downloads across failed builds, but never keep resolver DBs, generated profile,
# or mkarchiso _run_once stamps. This also prevents stale input/package overlays.
for directory in db resolved-db profile mk input-repo; do
    [[ ! -L $work/$directory ]] || fail "refusing symlink: $work/$directory"
    rm -rf -- "${work:?}/$directory"
done
if [[ -f $HERE/profile/RELENG-SHA256SUMS ]]; then
    cp -a -- "$HERE/profile" "$work/profile"
else
    # Source retrieval may be unavailable on the authoring host. The build VM's
    # installed v91 profile is the authoritative, complete fallback, never a stub.
    python3 "$HERE/import-releng.py" /usr/share/archiso/configs/releng "$work/profile"
fi
mkdir -p -- "$work/db" "$work/offline"
[[ ! -L $work/offline ]] || fail 'offline cache must not be a symlink'
# The supplied repository is named emaki.db. Expose its signed packages under
# the emaki-offline database name without changing the supplied repository.
mkdir -- "$work/input-repo"
cp -a --reflink=auto -- "$repo/." "$work/input-repo/"
ln -sf -- emaki.db "$work/input-repo/emaki-offline.db"
if [[ -f $repo/emaki.db.sig ]]; then ln -sf -- emaki.db.sig "$work/input-repo/emaki-offline.db.sig"; fi
cat >"$work/download.conf" <<CONF
[options]
Architecture = x86_64
SigLevel = Required DatabaseOptional TrustedOnly
LocalFileSigLevel = Required TrustedOnly
GPGDir = /etc/pacman.d/gnupg
LogFile = $work/pacman.log
[emaki-offline]
SigLevel = Required DatabaseOptional TrustedOnly
Server = file://$work/input-repo
[core]
Include = /etc/pacman.d/mirrorlist
[extra]
Include = /etc/pacman.d/mirrorlist
CONF
mapfile -t packages < <(cat "$work/profile/packages.x86_64" "$HERE/target-packages.txt" | sed '/^\s*#/d;/^\s*$/d' | LC_ALL=C sort -u)
pacman -Syw --noconfirm --cachedir "$work/offline" --dbpath "$work/db" \
    --config "$work/download.conf" "${packages[@]}"
# Record the exact transaction and prune older cached versions before repo-add.
pacman -Sp --print-format '%f' --dbpath "$work/db" --config "$work/download.conf" \
    "${packages[@]}" >"$work/closure.txt"
python3 - "$work/offline" "$work/closure.txt" <<'PY'
from pathlib import Path
import sys
cache, manifest = map(Path, sys.argv[1:])
keep = set(manifest.read_text().splitlines())
if not keep or any(Path(name).name != name or not name.endswith('.pkg.tar.zst') for name in keep):
    raise SystemExit('ERROR: unexpected package transaction manifest')
for name in keep:
    if not (cache / name).is_file():
        raise SystemExit(f'ERROR: missing downloaded package: {name}')
for package in cache.glob('*.pkg.tar.zst'):
    if package.name not in keep:
        package.unlink()
        Path(str(package) + '.sig').unlink(missing_ok=True)
for old in cache.glob('emaki-offline.*'):
    old.unlink()
PY
python3 "$HERE/repo-files.py" signatures "$work/offline" "$work/db"
repo-add "$work/offline/emaki-offline.db.tar.gz" "$work/offline/"*.pkg.tar.zst
printf '%s\n' "$version" >"$work/profile/VERSION"
profile_args=()
[[ -z $test_key ]] || profile_args+=(--test-key "$test_key")
python3 "$HERE/prepare-profile.py" "$work/profile" "$work/offline" "$version" "${profile_args[@]}"
# Re-resolve with ONLY the offline repo and a second empty DB. Missing transitive
# dependencies or signatures fail here, before the expensive squashfs stage.
mkdir -- "$work/resolved-db"
pacman -Syw --noconfirm --cachedir "$work/offline" --dbpath "$work/resolved-db" \
    --config "$work/profile/pacman.conf" "${packages[@]}"
mkdir -p -- "$work/mk"
# v91 has no external-tree hook. Suppress only mastering on pass one, retaining
# normal base construction; _run_once uses iso._build_iso_image for that stage.
# -r means DELETE work, not resume, and must not be used in either pass.
touch "$work/mk/iso._build_iso_image"
mkarchiso -v -w "$work/mk" -o "$out" "$work/profile"
[[ -d $work/mk/iso/emaki && -f $work/mk/build._build_buildmode_iso ]] || fail 'archiso v91 stage layout changed'
for installed in usr/bin/emaki-installerd usr/bin/emaki-install-cli usr/bin/niri-emaki-session usr/share/emaki/wallpaper/ring.png; do
    [[ -f $work/mk/x86_64/airootfs/$installed ]] || fail "required live file missing from packages: /$installed"
done
if [[ ! -f $work/mk/x86_64/airootfs/usr/bin/emaki-install ]]; then
    ((test_mode)) || fail 'required live file missing from packages: /usr/bin/emaki-install'
    echo 'WARNING: /usr/bin/emaki-install is missing; test ISO supports CLI installation only' >&2
fi
mkdir -p -- "$work/mk/iso/emaki/repo"
cp -a -- "$work/offline/." "$work/mk/iso/emaki/repo/"
cp -- "$work/closure.txt" "$work/mk/iso/emaki/repo/closure.txt"
rm -- "$work/mk/iso._build_iso_image" "$work/mk/build._build_buildmode_iso"
mkarchiso -v -w "$work/mk" -o "$out" "$work/profile"
image=$out/emaki-$version-x86_64.iso
[[ -s $image ]] || fail "missing ISO: $image"
# Inspect the mastered ISO and its EFI partition, not just build staging.
verify_args=()
((test_mode == 0)) || verify_args+=(--test)
if ! python3 "$HERE/verify-image.py" "$image" "${verify_args[@]}" >"$work/iso-image-check.log" 2>&1; then
    rm -f -- "$image" "$image.sha256"
    fail "mastered image verification failed; see $work/iso-image-check.log"
fi
printf 'ISO: %s\n' "$image"
stat -c 'Size: %s bytes' -- "$image"
(cd -- "$out" && sha256sum -- "${image##*/}") | tee "$image.sha256"
