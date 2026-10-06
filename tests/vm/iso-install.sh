#!/usr/bin/env bash
# Destructive only inside the already running disposable ISO guest.
# Commands in single quotes run in the guest.
# shellcheck disable=SC2016
set -Eeuo pipefail
umask 077
# shellcheck source=tests/vm/iso-common.sh
source "$(dirname -- "$(readlink -f -- "$0")")/iso-common.sh"
iso_parse "$@"
((${#ISO_ARGS[@]} == 1)) || iso_die 'Usage: iso-install.sh FIXTURE [--dir DIR] [--ssh-port PORT]'
fixture=${ISO_ARGS[0]}
[[ $fixture =~ ^[a-z0-9-]+$ ]] || iso_die 'invalid fixture name'
ROOT=$(dirname -- "$(dirname -- "$ISO_HERE")")
plan=$ROOT/installer/fixtures/plan-$fixture.json
[[ -f $plan ]] || plan=$ISO_HERE/fixtures/plan-$fixture.json
[[ -f $plan ]] || iso_die "missing fixture: $fixture"
run=$ISO_VM/runs/$(date +%Y%m%d-%H%M%S)-$fixture-$$
mkdir -p -- "$run"
printf '%s\n' "$run" >"$ISO_VM/last-install-run"
printf '%s\n' "$plan" >"$run/fixture-path"
cp -- "$plan" "$run/plan.json"
"$ISO_HERE/iso-wait-ssh.sh" --dir "$ISO_VM" --ssh-port "$ISO_PORT" --user live
ISO_USER=live
iso_ssh_args
"${ISO_SSH[@]}" 'test -d /run/archiso/bootmnt && grep -qw emaki.test=1 /proc/cmdline && case $(systemd-detect-virt --vm) in qemu|kvm) ;; *) exit 1;; esac' || iso_die 'guest is not a test ISO in QEMU/KVM'
"${ISO_SSH[@]}" 'deadline=$((SECONDS + 300)); until systemctl is-active -q emaki-installerd && test -S /run/emaki-installer/sock; do
    if ((SECONDS >= deadline)); then
        sudo -n systemctl status emaki-installerd pacman-init systemd-time-wait-sync --no-pager
        sudo -n journalctl -b -u pacman-init -u emaki-installerd --no-pager -n 80
        exit 1
    fi
    sleep 1
done' || iso_die 'installer worker was not ready within 300 seconds (see guest service diagnostics)'
"${ISO_SSH[@]}" 'umask 077; cat > "$HOME/emaki-plan.json"' <"$plan"
if [[ $fixture == alongside ]]; then
    # 0.2 ships with alongside Windows switched off in the installer core. Ask the packaged
    # worker for the plan only (no --yes, nothing is written); a refusal of the mode itself
    # means this fixture does not apply to the image: exit 77, neither a pass nor a failure.
    set +e
    "${ISO_SSH[@]}" 'emaki-install-cli --plan "$HOME/emaki-plan.json"' >"$run/offer.ndjson" 2>&1
    set -e
    if python3 -c 'import json, sys
for line in open(sys.argv[1], errors="replace"):
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    if isinstance(msg, dict) and msg.get("type") == "plan_ack" and any(
            error.get("code") == "unsupported_mode" for error in msg.get("errors") or []):
        sys.exit(0)
sys.exit(1)' "$run/offer.ndjson"; then
        "${ISO_SSH[@]}" 'rm -f "$HOME/emaki-plan.json"' || true
        printf 'The installer in this image does not offer install alongside Windows.\n' >"$run/NOT-APPLICABLE"
        printf '77\n' >"$run/exit-code"
        printf 'NOT APPLICABLE: the installer in this ISO refuses install alongside Windows; nothing was installed (%s)\n' "$run/offer.ndjson"
        exit 77
    fi
    [[ -s $ISO_VM/windows-before.json ]] || iso_die 'prepare the Windows fixture and baseline first'
    cp -- "$ISO_VM/windows-before.json" "$run/windows-before.json"
    "${ISO_SSH[@]}" 'umask 077; cat > /tmp/emaki-windows-fixture.py' <"$ISO_HERE/fixtures/windows-disk.py"
    "${ISO_SSH[@]}" 'sudo -n python3 /tmp/emaki-windows-fixture.py check' <"$run/windows-before.json" >"$run/windows-before-check.log" 2>&1
fi
# pipefail is insufficient to preserve the CLI status when tee also fails.
set +e
# --plan --yes already streams the job until done/error.
"${ISO_SSH[@]}" 'emaki-install-cli --plan "$HOME/emaki-plan.json" --yes' \
    2>"$run/install.stderr" | tee "$run/install.ndjson"
statuses=("${PIPESTATUS[@]}")
set -e
printf '%s\n' "${statuses[0]}" >"$run/exit-code"
"${ISO_SSH[@]}" 'rm -f "$HOME/emaki-plan.json"' || true
printf 'Installation evidence: %s\n' "$run"
if ((statuses[0] != 0)); then exit "${statuses[0]}"; fi
((statuses[1] == 0)) || iso_die 'could not save installer NDJSON'
