# Emaki and NixOS

Emaki is an Arch Linux distribution. Emaki for NixOS is a plan for later: not a separate
distribution, but a port for an ordinary NixOS system. Nothing for NixOS is being built yet. This
page says how far Emaki is from that today, so that new work does not make the distance bigger.

## What already carries over

- niri and Quickshell, the two programs Emaki is built around, are already packaged for NixOS,
  and niri has its own NixOS module.
- Emaki's own command-line tools take their install paths when they are built.
- The shell starts programs by name and finds apps the standard way.

## What is tied to Arch today

- About 25 lines in the shell and its helpers name fixed /usr paths.
- The update window, the recovery prompt and the "sign out after the update" notice talk to
  pacman and snapper directly.
- Updates, snapshots, the boot menu and the installer are Arch by nature. On NixOS their place is
  taken by nixos-rebuild, generations and the system's own boot menu.

## What new code does from now on

1. Install paths come from one file filled in at build time, not from the code.
2. The updates program talks to a replaceable backend: pacman on Arch now, nixos-rebuild on NixOS
   later.
3. "Restore the system to before the update" works with restore points, not with snapper's numbers.
4. The desktop learns that it was updated through one signal, not by reading pacman's database.
5. Settings that NixOS keeps in its configuration (keyboard layouts, time zone, fingerprint or
   face unlock) can show "set by the system" instead of changing files in /etc.

These are not promises, just how we build Emaki so the door stays open.
