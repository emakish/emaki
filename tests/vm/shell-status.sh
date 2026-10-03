#!/bin/bash
# Shell status in the VM desktop: tests/vm/shell-status.sh [jq-filter]
HERE=$(dirname "$(readlink -f "$0")")
"$HERE/ssh.sh" "export XDG_RUNTIME_DIR=/run/user/1000
    W=\$(ls /run/user/1000 | grep -E '^wayland-[0-9]+\$' | head -1)
    WAYLAND_DISPLAY=\$W emaki-shell call dock status" | jq -c "${1:-.}"
