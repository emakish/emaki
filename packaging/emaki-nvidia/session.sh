# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
# Sourced by the session wrapper before both manager import and compositor exec.
# Only refresh values previously set by this policy. Preserve profile overrides.
for emaki_nv_name in NVD_BACKEND LIBVA_DRIVER_NAME __GLX_VENDOR_LIBRARY_NAME; do
    emaki_nv_marker=EMAKI_NVIDIA_AUTO_$emaki_nv_name
    if [[ -v $emaki_nv_marker && ${!emaki_nv_name-} == "${!emaki_nv_marker}" ]]; then
        unset "$emaki_nv_name"
    fi
    unset "$emaki_nv_marker"
done
while IFS='=' read -r emaki_nv_name emaki_nv_value; do
    case "$emaki_nv_name" in
        NVD_BACKEND|LIBVA_DRIVER_NAME|__GLX_VENDOR_LIBRARY_NAME)
            if [[ ! -v $emaki_nv_name ]]; then
                export "$emaki_nv_name=$emaki_nv_value"
                export "EMAKI_NVIDIA_AUTO_$emaki_nv_name=$emaki_nv_value"
            fi ;;
    esac
done < <(/usr/bin/python3 -I /usr/lib/emaki/nvidia/runtime.py environment)
unset emaki_nv_name emaki_nv_value emaki_nv_marker

emaki_nvidia_clear_activation() {
    systemctl --user unset-environment NVD_BACKEND LIBVA_DRIVER_NAME __GLX_VENDOR_LIBRARY_NAME \
        EMAKI_NVIDIA_AUTO_NVD_BACKEND EMAKI_NVIDIA_AUTO_LIBVA_DRIVER_NAME \
        EMAKI_NVIDIA_AUTO___GLX_VENDOR_LIBRARY_NAME
}
