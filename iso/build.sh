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
preflight_args=(--repo "$repo")
((test_mode == 0)) || preflight_args+=(--test)
"$HERE/preflight.sh" "${preflight_args[@]}"
((EUID == 0)) || fail 'run as root inside the disposable build VM'
[[ $(systemd-detect-virt --vm) =~ ^(qemu|kvm)$ ]] || fail 'build is restricted to the QEMU/KVM build VM'
archiso_version=$(pacman -Q archiso)
[[ $archiso_version == 'archiso 91-'* ]] || fail 'archiso 91 is required; re-audit staging before upgrading'
for command in mkarchiso pacman pacman-key repo-add python3 ssh-keygen flock xorriso unsquashfs mcopy mdir; do
    command -v "$command" >/dev/null || fail "missing dependency: $command"
done
# A checked-in date freezes only the build resolver, never the live or installed
# mirrorlist. Reject aliases such as "last" and impossible calendar dates.
snapshot_values=$(python3 "$HERE/snapshot.py" show)
read -r arch_snapshot pinned_archiso pinned_archinstall <<<"$snapshot_values"
[[ $archiso_version == "archiso $pinned_archiso-"* ]] || fail 'install the recorded build tool version [archiso]'
arch_server="https://archive.archlinux.org/repos/${arch_snapshot//-/\/}/\$repo/os/\$arch"
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
if [[ -f $work/.emaki-mode ]]; then
    [[ $(<"$work/.emaki-mode") == "$mode" ]] || fail 'test/release work directories must be separate'
else
    [[ -z $(find "$work" -mindepth 1 -maxdepth 1 ! -name .lock -print -quit) ]] || fail 'refusing an unowned nonempty work directory'
    printf '%s\n' "$mode" >"$work/.emaki-mode"
fi
version=$(<"$HERE/VERSION")
[[ $version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail 'invalid VERSION'
python3 "$HERE/repo-files.py" check-input "$repo" "$HERE/emaki-packages.txt"
# A failed rebuild must never leave an older image/checksum looking current.
rm -f -- "$out"/emaki-*.iso*
# Keep downloads across failed builds, but never keep resolver DBs, generated profile,
# or mkarchiso _run_once stamps. This also prevents stale input/package overlays.
for directory in db resolved-db verify-cache build-repo target-repo profile mk input-repo \
    nvidia-{open-prebuilt,open-dkms}-{db,cache}; do
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
# archiso v91 runs the host grub-mkstandalone, including optional mixed-mode EFI.
python3 "$HERE/check-grub-modules.py" "$work/profile/grub" /usr/lib/grub/x86_64-efi
if [[ -d /usr/lib/grub/i386-efi ]]; then
    python3 "$HERE/check-grub-modules.py" "$work/profile/grub" /usr/lib/grub/i386-efi
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
Server = $arch_server
[extra]
Server = $arch_server
CONF
mapfile -t packages < <(cat "$work/profile/packages.x86_64" "$HERE/target-packages.txt" | python3 "$HERE/nvidia-seeds.py" base | LC_ALL=C sort -u)
mapfile -t target_packages < <(python3 "$HERE/nvidia-seeds.py" base <"$HERE/target-packages.txt")
pacman -Syw --noconfirm --cachedir "$work/offline" --dbpath "$work/db" \
    --config "$work/download.conf" "${packages[@]}"
# The date pins the full archiso package release too. The VM must use the same
# tools as the frozen repositories, rather than any later archiso 91 rebuild.
archive_archiso=$(pacman -Sp --print-format '%n %v' --dbpath "$work/db" \
    --config "$work/download.conf" archiso | awk '$1 == "archiso" { print }')
[[ $archiso_version == "$archive_archiso" ]] || fail "install $archive_archiso from the recorded Arch snapshot in the build VM"
# Retain the union manifest and cache for exact source collection of the live image.
pacman -Sp --print-format '%f' --dbpath "$work/db" --config "$work/download.conf" \
    "${packages[@]}" >"$work/closure.txt"
python3 "$HERE/repo-files.py" check-closure "$work/closure.txt"
grep -Fxq "archinstall-$pinned_archinstall-any.pkg.tar.zst" "$work/closure.txt" || fail 'use the recorded installer package version [archinstall]'
pacman -Sp --print-format '%f' --dbpath "$work/db" --config "$work/download.conf" \
    "${target_packages[@]}" >"$work/target-closure.txt"
python3 "$HERE/repo-files.py" check-closure "$work/target-closure.txt"
# Prebuilt and DKMS module packages conflict. Resolve complete target transactions
# separately and retain their union; the live package list keeps nouveau/Mesa.
python3 "$HERE/nvidia-seeds.py" download --config "$work/download.conf" --db "$work/db" \
    --cache "$work/offline" --closure "$work/closure.txt" --target-closure "$work/target-closure.txt"
python3 "$HERE/repo-files.py" check-closure "$work/closure.txt"
python3 "$HERE/repo-files.py" check-closure "$work/target-closure.txt"
# Staging copies are recreated above; bound the persistent cache to this full transaction.
python3 "$HERE/repo-files.py" prune "$work/offline" "$work/closure.txt"
for kind in build target; do
    manifest=$work/closure.txt
    [[ $kind != target ]] || manifest=$work/target-closure.txt
    python3 "$HERE/repo-files.py" stage "$work/offline" "$manifest" "$work/db" "$work/$kind-repo"
    repo-add "$work/$kind-repo/emaki-offline.db.tar.gz" "$work/$kind-repo/"*.pkg.tar.zst
done
printf '%s\n' "$version" >"$work/profile/VERSION"
profile_args=()
[[ -z $test_key ]] || profile_args+=(--test-key "$test_key")
python3 "$HERE/prepare-profile.py" "$work/profile" "$work/build-repo" "$version" "${profile_args[@]}"
# Prove installation from only the target repository using an empty DB and cache.
# Package signatures remain required; there is no network repository or cache fallback.
cat >"$work/target.conf" <<CONF
[options]
Architecture = x86_64
SigLevel = Required DatabaseOptional TrustedOnly
LocalFileSigLevel = Required TrustedOnly
GPGDir = /etc/pacman.d/gnupg
LogFile = $work/pacman.log
[emaki-offline]
SigLevel = Required DatabaseOptional TrustedOnly
Server = file://$work/target-repo
CONF
mkdir -- "$work/resolved-db" "$work/verify-cache"
pacman -Syw --noconfirm --cachedir "$work/verify-cache" --dbpath "$work/resolved-db" \
    --config "$work/target.conf" "${target_packages[@]}"
python3 "$HERE/nvidia-seeds.py" verify --config "$work/target.conf" --db "$work/resolved-db" \
    --cache "$work/verify-cache"
rm -rf -- "$work/verify-cache"
mkdir -p -- "$work/mk"
# archiso v91 copies profile/grub to ISO9660; the ESP contains only EFI binaries.
install -Dm644 "$ROOT/art/grub/background.png" "$work/profile/grub/background.png"
# v91 has no external-tree hook. Suppress mastering (iso._build_iso_image) and packing
# the live root (base._prepare_airootfs_image) on pass one, retaining the rest of base
# construction; the live root is packed on pass two, after its pacman hooks are released.
# -r means DELETE work, not resume, and must not be used in either pass.
touch "$work/mk/iso._build_iso_image" "$work/mk/base._prepare_airootfs_image"
mkarchiso -v -w "$work/mk" -o "$out" "$work/profile"
# Pin v91's rewrite: pacstrap uses live-root admin hooks, not build-host hooks.
hookdirs=$(grep -E '^[[:space:]]*HookDir[[:space:]]*=' "$work/mk/iso.pacman.conf") || fail 'archiso v91 HookDir missing'
[[ $hookdirs == "HookDir = $work/mk/x86_64/airootfs/etc/pacman.d/hooks/" ]] || fail 'archiso v91 HookDir changed'
[[ -d $work/mk/iso/emaki && -f $work/mk/build._build_buildmode_iso ]] || fail 'archiso v91 stage layout changed'
[[ ! -e $work/mk/base._mkairootfs_squashfs ]] || fail 'archiso v91 packed the live root before its hooks were released'
# pacstrap runs the live system's /etc/pacman.d/hooks for the target. emaki-config's
# mkinitcpio wrapper there shadows the target's stock hook while the target has no
# wrapper yet: no kernel is copied and no preset is made (0.3.0 candidate, Mac install).
live=$work/mk/x86_64/airootfs
hook=$live/etc/pacman.d/hooks/90-mkinitcpio-install.hook
ledger=$live/var/lib/emaki/migrations/mkinitcpio-hook
if [[ -e $hook || -L $hook ]]; then
    if [[ ! -f $hook || -L $hook || ! -f $ledger ]] || ! cmp -s -- "$hook" "$ledger"; then
        fail 'the live mkinitcpio hook is not the one emaki-config wrote'
    fi
    rm -- "$hook" "$ledger"
fi
[[ ! -L $live/etc/pacman.d/hooks ]] || fail 'the live /etc/pacman.d/hooks is a symlink'
leftover=$(find "$live/etc/pacman.d/hooks" -mindepth 1 -print -quit 2>/dev/null || true)
[[ -z $leftover ]] || fail "the live /etc/pacman.d/hooks is not empty (pacstrap reads it for the target): ${leftover#"$live"}"
# Keep the final ISO9660 artwork explicit across either loader's staging flow.
install -Dm644 "$ROOT/art/grub/background.png" "$work/mk/iso/boot/grub/background.png"
for installed in usr/bin/emaki-installerd usr/bin/emaki-install-cli usr/bin/niri-emaki-session usr/share/emaki/wallpaper/ring.png; do
    [[ -f $work/mk/x86_64/airootfs/$installed ]] || fail "required live file missing from packages: /$installed"
done
if [[ ! -f $work/mk/x86_64/airootfs/usr/bin/emaki-install ]]; then
    ((test_mode)) || fail 'required live file missing from packages: /usr/bin/emaki-install'
    echo 'WARNING: /usr/bin/emaki-install is missing; test ISO supports CLI installation only' >&2
fi
mkdir -p -- "$work/mk/iso/emaki/repo"
cp -a -- "$work/target-repo/." "$work/mk/iso/emaki/repo/"
cp -- "$work/target-closure.txt" "$work/mk/iso/emaki/repo/closure.txt"
cp -- "$work/closure.txt" "$work/mk/iso/emaki/live-closure.txt"
python3 "$ROOT/packaging/mirror/iso_sources.py" --inventory-only \
    --closure "$work/closure.txt" --packages "$work/build-repo" \
    --pkglist "$work/mk/iso/emaki/pkglist.x86_64.txt" \
    --target-closure "$work/target-closure.txt" \
    --output "$work/mk/iso/emaki/live-packages.json"
# Keep the date, exact build tools and downloaded bytes with the image. This
# records package-input reproducibility, not a byte-identical ISO guarantee.
{
    printf 'arch_snapshot %s\narch_server %s\n%s\narchinstall %s\n' "$arch_snapshot" "$arch_server" "$archiso_version" "$pinned_archinstall"
    (
        cd "$work/offline"
        while IFS= read -r package; do sha256sum -- "$package"; done <"$work/closure.txt"
    )
} >"$work/mk/iso/emaki/BUILDINFO"
rm -- "$work/mk/iso._build_iso_image" "$work/mk/build._build_buildmode_iso" "$work/mk/base._prepare_airootfs_image"
mkarchiso -v -w "$work/mk" -o "$out" "$work/profile"
image=$out/emaki-$version-x86_64.iso
[[ -s $image ]] || fail "missing ISO: $image"
# Inspect the mastered ISO and its EFI partition, not just build staging.
verify_args=()
((test_mode == 0)) || verify_args+=(--test)
if ! python3 "$HERE/verify-image.py" "$image" --efi-report "$image.efi.json" "${verify_args[@]}" >"$work/iso-image-check.log" 2>&1; then
    rm -f -- "$image" "$image.sha256" "$image.sig" "$image.efi.json"
    fail "mastered image verification failed; see $work/iso-image-check.log"
fi
cp -- "$work/mk/iso/emaki/BUILDINFO" "$image.buildinfo"
printf 'ISO: %s\n' "$image"
stat -c 'Size: %s bytes' -- "$image"
# Checksum always; a detached signature only when EMAKI_ISO_SIGN_KEY is set (the build VM holds
# no private key by default: packaging/publish.sh iso signs on the publishing machine).
"$ROOT/packaging/mirror/iso-sums.sh" "$image"
