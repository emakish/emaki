#!/usr/bin/env bash
# Release gate for one release candidate.
# Usage: release-gate.sh --release-iso FILE --test-iso FILE [--walk DIR] [--out DIR] [--jobs a,b,...]
#
# The RELEASE image is the one people download: it is identified by its sha256, checked
# against its .sha256 file and iso/verify-image.py (release mode), and judged only by the walk
# record of that sha256 (tests/vm/eyes/eyes-gate.py). It has no ssh, so the functional VM
# scripts (install, boot check, encryption, alongside) run on the TEST image built from the
# same commit (iso/verify-image.py --test) and are labelled with that image's sha256. The
# two can never be read as one thing: every job line names the image it ran on.
#
# Job results: PASS, FAIL, NOT TESTED (it applies but did not run here), NOT APPLICABLE (the
# image does not offer it, with its reference). The last line is "RESULT: SCRIPTS PASSED",
# "RESULT: FAILED at <job>" (exit 1) or "RESULT: NOT TESTED at <job>" (exit 3); usage errors
# exit 2. Nothing is built here; the images must exist.
set -Eeuo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd -- "$HERE/../.." && pwd)
JOBS=(release-image test-image host-checks accept-erase-btrfs accept-erase-ext4 encrypt-btrfs alongside-btrfs graphics-fallback release-walk)
usage() { printf 'release-gate: %s\n' "$*" >&2; exit 2; }

release_iso='' test_iso='' walk='' out='' selected=''
while (($#)); do
    case $1 in
        --release-iso|--test-iso|--walk|--out|--jobs)
            (($# >= 2)) || usage "missing value for $1"
            case $1 in
                --release-iso) release_iso=$2 ;; --test-iso) test_iso=$2 ;; --walk) walk=$2 ;;
                --out) out=$2 ;; --jobs) selected=$2 ;;
            esac
            shift 2 ;;
        --help) sed -n '2,15p' "$0"; exit 0 ;;
        *) usage "unknown option: $1" ;;
    esac
done
[[ -n $release_iso && -n $test_iso ]] || usage 'name both images: --release-iso FILE --test-iso FILE'
release_iso=$(realpath -m -- "$release_iso")
test_iso=$(realpath -m -- "$test_iso")
[[ -z $walk ]] || walk=$(realpath -m -- "$walk")
if [[ -n $selected ]]; then
    for job in ${selected//,/ }; do
        [[ " ${JOBS[*]} " == *" $job "* ]] || usage "unknown job $job (jobs: ${JOBS[*]})"
    done
fi
gate_root=$HOME/VMs/release-gate
mkdir -p -- "$gate_root"
exec 7>>"$gate_root/.lock"
flock -n 7 || usage "another release gate holds $gate_root/.lock"
stamp=$(date +%Y%m%d-%H%M%S)
out=${out:-$gate_root/$stamp}
mkdir -p -- "$(dirname -- "$out")"
mkdir -- "$out" || usage "output directory exists, never reused: $out"
out=$(realpath -- "$out")
exec > >(tee -a "$out/gate.log") 2>&1

declare -A state=() detail=() image=()
release_sha='' test_sha=''
step() { printf '== %s %s\n' "$(date +%T)" "$*"; }
record() { state[$1]=$2; detail[$1]=$3; image[$1]=$4; printf '%s: %s - %s\n' "$1" "$2" "$3"; }
selected_job() { [[ -z $selected || ",$selected," == *",$1,"* ]]; }
release_label() { printf 'release image %s sha256 %s' "$release_iso" "${release_sha:-unknown}"; }
test_label() { printf 'test image %s sha256 %s' "$test_iso" "${test_sha:-unknown}"; }

# An image is usable only if its .sha256 matches and verify-image accepts its mode.
check_image() {
    local job=$1 iso=$2 mode=$3 log=$out/$1.log
    if [[ ! -f $iso || ! -f $iso.sha256 ]]; then
        record "$job" FAIL "missing $iso or $iso.sha256" "$iso"
        return 1
    fi
    if ! (cd -- "$(dirname -- "$iso")" && sha256sum -c -- "$(basename -- "$iso").sha256") >"$log" 2>&1; then
        record "$job" FAIL "$iso does not match $iso.sha256 (log $log)" "$iso"
        return 1
    fi
    local args=("$iso")
    [[ $mode == release ]] || args+=(--test)
    if ! python3 "$ROOT/iso/verify-image.py" "${args[@]}" >>"$log" 2>&1; then
        record "$job" FAIL "iso/verify-image.py ($mode mode) refused $iso (log $log)" "$iso"
        return 1
    fi
    return 0
}

stop_vm() { "$HERE/iso-stop.sh" --dir "$1" >>"$1/stop.log" 2>&1 || true; }

# Install a fixture from the live test image, then boot the installed disk and check it.
accept_job() {
    local job=$1 fixture=$2 port=$3 dir=$out/$1
    mkdir -- "$dir"
    step "$job on $(test_label)"
    setsid -f "$HERE/run-iso.sh" --dir "$dir" --ssh-port "$port" --iso "$test_iso" >"$dir/qemu-live.log" 2>&1 </dev/null
    if ! timeout 600 "$HERE/iso-wait-ssh.sh" --dir "$dir" >"$dir/wait-live.log" 2>&1; then
        stop_vm "$dir"
        record "$job" FAIL "live image ssh not reachable (log $dir/wait-live.log)" "$(test_label)"
        return
    fi
    local rc=0
    timeout 3600 "$HERE/iso-install.sh" "$fixture" --dir "$dir" >"$dir/install.log" 2>&1 || rc=$?
    stop_vm "$dir"
    if ((rc != 0)); then
        record "$job" FAIL "iso-install.sh $fixture exit $rc (log $dir/install.log)" "$(test_label)"
        return
    fi
    "$HERE/iso-boot-check.sh" "$fixture" --dir "$dir" >"$dir/boot-check.log" 2>&1 &
    local check=$!
    sleep 1
    setsid -f "$HERE/run-iso.sh" --dir "$dir" --ssh-port "$port" --no-cd >"$dir/qemu-installed.log" 2>&1 </dev/null
    rc=0
    wait "$check" || rc=$?
    stop_vm "$dir"
    if ((rc != 0)); then
        record "$job" FAIL "iso-boot-check.sh $fixture exit $rc (log $dir/boot-check.log)" "$(test_label)"
    else
        record "$job" PASS "script passed: install + boot check; pictures are SHOT lines, not judged (log $dir/boot-check.log)" "$(test_label)"
    fi
}

encrypt_job() {
    local job=encrypt-btrfs dir=$out/encrypt rc=0
    step "$job on $(test_label)"
    (cd -- "$ROOT" && EMAKI_CHECK_ISO=$test_iso VMDIR=$dir timeout 7200 bash "$HERE/jail.sh" "$dir" -- \
        bash tests/vm/iso-encrypt-check.sh btrfs gate) >"$out/$job.log" 2>&1 || rc=$?
    if ((rc == 0)) && [[ -f $dir/btrfs-gate/PASS ]]; then
        record "$job" PASS "script passed (log $out/$job.log, evidence $dir/btrfs-gate)" "$(test_label)"
    elif ((rc == 0)); then
        record "$job" FAIL "exit 0 but no PASS file in $dir/btrfs-gate (log $out/$job.log)" "$(test_label)"
    else
        record "$job" FAIL "iso-encrypt-check exit $rc (log $out/$job.log)" "$(test_label)"
    fi
}

# iso-alongside-check.py only accepts VMDIR=~/VMs/n2-along; exit 77 = the image's installer
# refuses install alongside Windows (off in 0.2 by DECISIONS 2026-10-04).
alongside_job() {
    local job=alongside-btrfs base=$HOME/VMs/n2-along name=gate-$stamp rc=0
    step "$job on $(test_label)"
    mkdir -p -- "$base"
    (cd -- "$ROOT" && EMAKI_CHECK_ISO=$test_iso VMDIR=$base timeout 7200 bash "$HERE/jail.sh" "$base" -- \
        bash tests/vm/iso-alongside-check.sh btrfs "$name") >"$out/$job.log" 2>&1 || rc=$?
    local evidence=$base/btrfs-$name
    if ((rc == 77)) && [[ -f $evidence/NOT-APPLICABLE ]]; then
        record "$job" 'NOT APPLICABLE' "the image's installer refuses install alongside Windows (DECISIONS.md 2026-10-04 \"No experimental options\"; evidence $evidence/offer.ndjson)" "$(test_label)"
    elif ((rc == 0)) && [[ -f $evidence/PASS ]]; then
        record "$job" PASS "script passed (log $out/$job.log, evidence $evidence)" "$(test_label)"
    else
        record "$job" FAIL "iso-alongside-check exit $rc without its PASS or NOT-APPLICABLE file (log $out/$job.log)" "$(test_label)"
    fi
}

# The text screen when graphics cannot start (tests/vm/check-graphics-fallback.py): live and
# installed without GL at two resolutions, plus GL boots. The installed parts boot an overlay of
# accept-erase-btrfs's disk, never the disk itself; without a passed install they are NOT TESTED.
graphics_job() {
    local job=graphics-fallback dir=$out/graphics-fallback rc=0 last
    local args=(--iso "$test_iso" --out "$dir" --ssh-port 2261)
    step "$job on $(test_label)"
    if [[ ${state[accept-erase-btrfs]:-} == PASS ]]; then
        args+=(--installed-disk "$out/accept-erase-btrfs/target.qcow2" --fixture erase-btrfs)
    fi
    timeout 7200 python3 "$HERE/check-graphics-fallback.py" "${args[@]}" >"$out/$job.log" 2>&1 || rc=$?
    last=$(tail -n 1 "$out/$job.log")
    if ((rc == 0)) && [[ -f $dir/PASS ]]; then
        record "$job" PASS "script passed: check-graphics-fallback.py exit 0 with its PASS file; pictures are SHOT lines, not judged (log $out/$job.log)" "$(test_label)"
    elif ((rc == 0)); then
        record "$job" FAIL "exit 0 but no PASS file in $dir (log $out/$job.log)" "$(test_label)"
    elif ((rc == 3)); then
        record "$job" 'NOT TESTED' "check-graphics-fallback.py: $last (log $out/$job.log)" "$(test_label)"
    else
        record "$job" FAIL "check-graphics-fallback.py exit $rc: $last (log $out/$job.log)" "$(test_label)"
    fi
}

walk_job() {
    local job=release-walk rc=0
    step "$job on $(release_label)"
    if [[ -z $walk ]]; then
        record "$job" 'NOT TESTED' "no walk record of the release image (tests/vm/eyes/eyes-walk.py, then --walk DIR)" "$(release_label)"
        return
    fi
    local args=(--record "$walk/walk.toml" --iso "$release_iso" --signoff "$walk/signoff.toml")
    [[ ${state[release-image]:-} != PASS ]] || args+=(--image-verified)
    python3 "$HERE/eyes/eyes-gate.py" "${args[@]}" >"$out/$job.log" 2>&1 || rc=$?
    local last
    last=$(tail -n 1 "$out/$job.log")
    case $rc in
        0) record "$job" PASS "eyes-gate.py exit 0: $last (log $out/$job.log)" "$(release_label)" ;;
        3) record "$job" 'NOT TESTED' "eyes-gate.py: $last (log $out/$job.log)" "$(release_label)" ;;
        *) record "$job" FAIL "eyes-gate.py exit $rc: $last (log $out/$job.log)" "$(release_label)" ;;
    esac
}

not_run() {
    local job reason=$1
    shift
    for job in "$@"; do
        [[ -n ${state[$job]:-} ]] || record "$job" 'NOT TESTED' "$reason" ''
    done
}

step "release gate $stamp, checkout $(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo unknown), output $out"
for job in "${JOBS[@]}"; do
    selected_job "$job" || record "$job" 'NOT TESTED' 'not selected in this run (--jobs)' ''
done

if selected_job release-image; then
    step "release-image $release_iso"
    if check_image release-image "$release_iso" release; then
        release_sha=$(sha256sum -- "$release_iso" | cut -d' ' -f1)
        record release-image PASS "sha256 matches its .sha256 file; verify-image.py (release mode) exit 0" "$(release_label)"
    fi
elif [[ -f $release_iso ]]; then
    release_sha=$(sha256sum -- "$release_iso" | cut -d' ' -f1)
fi
if [[ ${state[release-image]} == FAIL ]]; then
    not_run 'not run: the release image failed its checks' "${JOBS[@]}"
fi

if selected_job test-image && [[ -z ${state[test-image]:-} ]]; then
    step "test-image $test_iso"
    if check_image test-image "$test_iso" test; then
        test_sha=$(sha256sum -- "$test_iso" | cut -d' ' -f1)
        if [[ $test_sha == "$release_sha" ]]; then
            record test-image FAIL 'the test image is the release image (same sha256)' "$(test_label)"
        else
            record test-image PASS "sha256 matches its .sha256 file; verify-image.py --test exit 0" "$(test_label)"
        fi
    fi
elif [[ -f $test_iso ]]; then
    test_sha=$(sha256sum -- "$test_iso" | cut -d' ' -f1)
fi

if selected_job host-checks && [[ -z ${state[host-checks]:-} ]]; then
    step 'host-checks: make check-all'
    if (cd -- "$ROOT" && make check-all) >"$out/host-checks.log" 2>&1; then
        record host-checks PASS "script passed: make check-all (log $out/host-checks.log)" 'checkout'
    else
        record host-checks FAIL "make check-all failed (log $out/host-checks.log)" 'checkout'
    fi
fi

vm_jobs=(accept-erase-btrfs accept-erase-ext4 encrypt-btrfs alongside-btrfs graphics-fallback)
if [[ ${state[test-image]:-} != PASS ]]; then
    not_run "not run: the test image is not usable (test-image ${state[test-image]:-not checked})" "${vm_jobs[@]}"
elif [[ ${state[host-checks]:-} == FAIL ]]; then
    not_run 'not run: host checks failed' "${vm_jobs[@]}"
fi
[[ -n ${state[accept-erase-btrfs]:-} ]] || accept_job accept-erase-btrfs erase-btrfs 2251
[[ -n ${state[accept-erase-ext4]:-} ]] || accept_job accept-erase-ext4 erase-ext4 2252
[[ -n ${state[encrypt-btrfs]:-} ]] || encrypt_job
[[ -n ${state[alongside-btrfs]:-} ]] || alongside_job
[[ -n ${state[graphics-fallback]:-} ]] || graphics_job
[[ -n ${state[release-walk]:-} ]] || walk_job

echo
echo "Results. Functional scripts ran on the TEST image $test_iso (sha256 ${test_sha:-unknown})."
echo "Only release-walk judges the RELEASE image $release_iso (sha256 ${release_sha:-unknown})."
first_fail='' first_untested=''
for job in "${JOBS[@]}"; do
    printf '%-20s %-15s %s\n' "$job" "${state[$job]}" "${detail[$job]}"
    [[ -z ${image[$job]} ]] || printf '%-20s %-15s on %s\n' '' '' "${image[$job]}"
    case ${state[$job]} in
        FAIL) first_fail=${first_fail:-$job} ;;
        'NOT TESTED') first_untested=${first_untested:-$job} ;;
    esac
done
if [[ -n $first_fail ]]; then
    echo "RESULT: FAILED at $first_fail"
    exit 1
fi
if [[ -n $first_untested ]]; then
    echo "RESULT: NOT TESTED at $first_untested"
    exit 3
fi
echo 'RESULT: SCRIPTS PASSED'
exit 0
