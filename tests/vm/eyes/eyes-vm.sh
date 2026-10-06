#!/usr/bin/env bash
# Eyes launcher: one QEMU pass of a release walk, driven only by eyes.py (QMP + VNC).
# Usage: eyes-vm.sh --walk DIR --run W1 --display gl|fw --res 1920x1080 [--firmware uefi|bios]
#                   (--iso FILE | --no-cd) [--ram 4G] [--smp 2]
# Runs QEMU in the foreground; start it with setsid/& and stop it with `eyes.py quit`.
#   gl  virtio-vga-gl + egl-headless; VNC reads the real scan-out (the desktop needs GL).
#   fw  std VGA at the true firmware resolution; shows no desktop (that frame is stage 04).
# Refuses an image that is not the walk's (sha256), whose .sha256 does not match, or that
# fails iso/verify-image.py in release mode (a test ISO). No TCP port is opened: no ssh,
# no hostfwd; every socket lives in the pass directory, which is never reused.
set -Eeuo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd -- "$HERE/../../.." && pwd)
die() { printf 'eyes-vm: %s\n' "$*" >&2; exit 1; }

walk='' run='' display='' res='' firmware=uefi iso='' no_cd=0 ram=4G smp=2
while (($#)); do
    case $1 in
        --walk|--run|--display|--res|--firmware|--iso|--ram|--smp)
            (($# >= 2)) || die "missing value for $1"
            case $1 in
                --walk) walk=$2 ;; --run) run=$2 ;; --display) display=$2 ;; --res) res=$2 ;;
                --firmware) firmware=$2 ;; --iso) iso=$2 ;; --ram) ram=$2 ;; --smp) smp=$2 ;;
            esac
            shift 2 ;;
        --no-cd) no_cd=1; shift ;;
        --help) sed -n '2,10p' "$0"; exit 0 ;;
        *) die "unknown option: $1" ;;
    esac
done
[[ -n $walk && -f $walk/iso.sha256 ]] || die '--walk must name a walk directory made by eyes-walk.py init'
walk=$(realpath -- "$walk")
[[ $run =~ ^[A-Za-z0-9]+$ ]] || die '--run must be a run name such as W1'
[[ $display == gl || $display == fw ]] || die '--display must be gl or fw'
[[ $res =~ ^(1920x1080|2560x1600|1366x768)$ ]] || die '--res must be 1920x1080, 2560x1600 or 1366x768'
[[ $firmware == uefi || $firmware == bios ]] || die '--firmware must be uefi or bios'
[[ $ram =~ ^[0-9]+[MG]$ && $smp =~ ^[1-9][0-9]?$ ]] || die 'invalid --ram or --smp'
if ((no_cd)); then
    [[ -z $iso ]] || die '--iso and --no-cd exclude each other'
else
    [[ -n $iso ]] || die 'name the image with --iso (or boot the installed disk with --no-cd)'
fi
want=$(<"$walk/iso.sha256")
[[ $want =~ ^[0-9a-f]{64}$ ]] || die "invalid sha256 in $walk/iso.sha256"
[[ $(basename -- "$(dirname -- "$walk")") == "${want:0:12}" ]] || die 'the walk directory is not under the directory named by its image sha256'
for command in qemu-system-x86_64 qemu-img flock sha256sum; do
    command -v "$command" >/dev/null || die "missing $command"
done

# A build that still writes images must not be raced: a half-copied ISO is never tested.
build_lock=${EMAKI_BUILD_LOCK:-$HOME/VMs/iso/.build.lock}
if [[ -e $build_lock ]]; then
    exec 8<"$build_lock"
    flock -n 8 || die "an ISO build holds $build_lock"
    exec 8<&-
fi

if ((no_cd == 0)); then
    [[ -f $iso && -r $iso ]] || die "--iso must name a readable image file: $iso"
    iso=$(realpath -- "$iso")
    [[ -f $iso.sha256 ]] || die "missing $iso.sha256 next to the image"
    (cd -- "$(dirname -- "$iso")" && sha256sum -c --status -- "$(basename -- "$iso").sha256") \
        || die "$iso does not match $iso.sha256"
    got=$(sha256sum -- "$iso" | cut -d' ' -f1)
    [[ $got == "$want" ]] || die "image sha256 $got is not the walk's $want"
    python3 "$ROOT/iso/verify-image.py" "$iso" >"$walk/verify-image-$$.log" 2>&1 \
        || die "iso/verify-image.py (release mode) refused the image; see $walk/verify-image-$$.log"
    rm -f -- "$walk/verify-image-$$.log"
fi

ovmf=${EYES_OVMF_DIR:-/usr/share/edk2/x64}
code=$ovmf/OVMF_CODE.4m.fd
vars=$ovmf/OVMF_VARS.4m.fd
if [[ $firmware == uefi ]]; then [[ -r $code && -r $vars ]] || die 'OVMF unavailable'; fi
render=${EYES_RENDER_NODE:-/dev/dri/renderD128}
if [[ $display == gl ]]; then [[ -r $render && -w $render ]] || die "$render unavailable"; fi

# One GL guest at a time: a virgl guest has reset the host GPU ring before.
# The lock descriptor is inherited by QEMU, so the lock lives exactly as long as the guest.
eyes_root=${EYES_ROOT:-$HOME/VMs/eyes}
if [[ $display == gl ]]; then
    mkdir -p -- "$eyes_root"
    exec 9>>"$eyes_root/.gl.lock"
    flock -n 9 || die "another GL guest holds $eyes_root/.gl.lock"
fi

disk_dir=$walk/$run
mkdir -p -- "$disk_dir"
n=1
while [[ -e $disk_dir/$display-$res-$firmware-$(printf '%02d' "$n") ]]; do n=$((n + 1)); done
pass=$disk_dir/$display-$res-$firmware-$(printf '%02d' "$n")
((${#pass} <= 96)) || die "pass directory path too long for unix sockets: $pass"
mkdir -- "$pass"
[[ $pass != *,* && ${iso:-x} != *,* ]] || die 'QEMU paths must not contain commas'
disk=$disk_dir/disk.qcow2
[[ -f $disk ]] || qemu-img create -f qcow2 "$disk" 40G >/dev/null
printf '%s\n' "$res" >"$pass/res"

x=${res%x*} y=${res#*x}
args=(-machine q35 -enable-kvm -cpu host -smp "$smp" -m "$ram")
if [[ $firmware == uefi ]]; then
    [[ -f $disk_dir/OVMF_VARS.4m.fd ]] || cp -- "$vars" "$disk_dir/OVMF_VARS.4m.fd"
    args+=(-drive "if=pflash,format=raw,readonly=on,file=$code" -drive "if=pflash,format=raw,file=$disk_dir/OVMF_VARS.4m.fd")
fi
args+=(-drive "file=$disk,if=none,id=target,format=qcow2,discard=unmap" -device "nvme,drive=target,serial=emaki-eyes-0001")
if ((no_cd == 0)); then
    args+=(-drive "if=none,id=cd,media=cdrom,readonly=on,format=raw,file=$iso" -device "ide-cd,drive=cd,bootindex=2")
fi
case $display in
    gl) args+=(-device "virtio-vga-gl,xres=$x,yres=$y" -display "egl-headless,rendernode=$render") ;;
    fw) args+=(-device "VGA,xres=$x,yres=$y,vgamem_mb=64" -display none) ;;
esac
args+=(-vnc "unix:$pass/vnc.sock" -device qemu-xhci -device usb-tablet -nic "user,model=virtio-net-pci"
    -qmp "unix:$pass/qmp.sock,server=on,wait=off" -monitor "unix:$pass/mon.sock,server=on,wait=off"
    -serial "file:$pass/serial.log" -pidfile "$pass/qemu.pid")
printf '%q ' qemu-system-x86_64 "${args[@]}" >"$pass/qemu-cmdline.txt"
printf '\n' >>"$pass/qemu-cmdline.txt"
printf '%s\n' "$pass" >"$walk/current-pass"
printf '%s start %s %s %s %s iso=%s\n' "$(date +%FT%T)" "$run" "$display" "$res" "$firmware" "${iso:-none}" >>"$walk/timeline.log"
echo "EYES_PASS=$pass"
# QEMU must not hold the caller's terminal or pipe: its own output goes to the pass directory.
exec >>"$pass/qemu.log" 2>&1
exec qemu-system-x86_64 "${args[@]}"
