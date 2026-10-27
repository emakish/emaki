<!-- Copyright (C) 2026 Artur Yakymenko -->
<!-- SPDX-License-Identifier: GPL-3.0-or-later -->

# Authentication origin belongs to sudo's PAM transaction

The integration installs no askpass helper. A sudo child and its output pipe cannot prove
that it is an authentication helper: ordinary command execution with output
logging can reproduce that topology. Password delivery therefore terminates in
PAM inside sudo, never in an executable requested by the caller.

The root service accepts authentication messages only from a root peer with the
configured sudo executable and the configured root-owned PAM module mapped by
matching device/inode. It pins the peer, checks its credentials and terminal state,
and reads bounded ancestry itself. The packaged module initiates the transaction
only from its authentication callback. These checks trust the installed sudo and
PAM code; they are not a defense against an already compromised root process.
The dedicated socket is `/run/emaki-sudo/broker.sock` in a root-owned runtime
directory. The shell registers only as a prompt provider after executable,
credentials and session checks. Passwords pass through bounded, locked and wiped
native buffers into PAM_AUTHTOK for the stock stack. They are not returned to the
requesting process, retained as keys, placed in JSON or exposed by general IPC.

## Version-specific routing and confirmation

Read-only local metadata identified sudo **1.9.17.p2-6**, PAM 1.7.2-2 and pambase
20260616-1. Installed `sudoers(5)` documents `pam_service` (supported since 1.8.8),
independent `pam_login_service`, and `timestamp_timeout=0` requiring authentication
each time. The matching [sudoers manual source](https://github.com/sudo-project/sudo/blob/v1.9.17p2/docs/sudoers.man.in)
is the versioned reference. No host sudo command was executed for this evidence.

The [sudo 1.9.17p2 PAM implementation](https://github.com/sudo-project/sudo/blob/v1.9.17p2/plugins/sudoers/auth/pam.c)
selects ordinary and login service names independently (lines 207–213), sets
PAM_RUSER and PAM_TTY (226–252), authenticates (302–340), and runs account validation
(350–408). The package sets both defaults to `emaki-sudo`. The backed-up service runs
the Emaki module first, then includes stock `sudo`; account, password and session
stacks retain the stock includes. Explicit askpass mode is not this integration.

The [sudo authentication dispatcher](https://github.com/sudo-project/sudo/blob/v1.9.17p2/plugins/sudoers/check.c)
bypasses password authentication for root/configured exemptions (131–146) and valid
timestamps (152–175). Approval/account validation follows successful authentication
or an intentional bypass (190–206). The module's account callback commits a grant
only with pending state from this same handle's fresh password transaction and the
same trusted service connection; a bypass has no such state. This proves successful
authentication, not successful command execution or final authorization. Stock
account checks remain in place for both fresh passwords and existing grants.

An auth callback appended after stock modules would be unsafe: a required failure
can survive while later modules run. The [Linux-PAM 1.7.2 dispatcher](https://github.com/linux-pam/linux-pam/blob/v1.7.2/libpam/pam_dispatch.c)
retains those earlier failures (194–234). Confirmation therefore depends explicitly
on sudo's account-callback ordering. A future sudo/PAM upgrade must recheck it.

## Failure and grant boundaries

Terminal requests immediately return PAM_IGNORE. Explicit `-S`/`--stdin` and
`-A`/`--askpass` also return PAM_IGNORE before broker contact, preserving sudo's
stdin and askpass conversations without a graphical prompt. The module parses
bounded, NUL-delimited process arguments with sudo option boundaries; a command's
own `-S` or `-A` is not a sudo mode. Unknown syntax uses stock PAM. A non-empty
`SUDO_ASKPASS` from the original process environment also selects stock PAM, even if
sudo has cleared its live environment. This untrusted hint only selects a standard
conversation and never authorizes access. A helper configured solely through
`Path askpass` in sudo.conf is not detected here; use `-A` or `SUDO_ASKPASS` to keep
that implicit helper on the standard path. The ingress control's
`default=ignore` preserves stock authentication if the module cannot load, the
service or shell is unavailable, or infrastructure times out. Explicit cancellation
returns failure and ends that attempt. Root login policy is not modified. Removing
the entire selected PAM service while retaining its sudoers selector is a separate
packaging failure: module-level fallback cannot repair a missing PAM configuration.
The backed-up service at `/etc/pam.d/emaki-sudo` precedes its selector in the archive.
libalpm removes the selector before the service in reverse file order. Tests build
the full staged payload archive and exercise real isolated libalpm removal with
both stock and locally edited service files; `.pacsave` does not change the order.

Only `emaki-sudo-key` installs the marker. Neither package changes sudo's timestamp
policy. Ordinary terminal and prompt approval keep stock five-minute renewing memory,
including same-parent timestamps without a tty, unless an administrator configures
otherwise. Keys extend access; revocation does not invalidate sudo's independent
memory. The guest procedure probes cached repeats and cached access after revocation
without invalidation, then explicitly clears timestamps to test the key path alone.

The dispatcher rejects `sudo -n` before PAM when authentication is needed, unless
`noninteractive_auth` is set. That setting remains unchanged: a key does not make
`sudo -n` work. Existing sudo timestamps or password exemptions still follow stock
policy. Use ordinary sudo for the graphical prompt and key path.

Keys contain only a process-tree grant, scoped to pinned identities, session and
expiry: a typed whole number of minutes (5–1440), reported turns (1–1000, with a
four-hour hard cap), or process-session lifetime. Password approval without
a key is separate. Owner exit or identity change, revoke, expiry, desktop session exit,
missing package or root service restart invalidates keys. Lock, suspend and shell
restart retain keys, and the reconnected shell receives their status. Expiry uses
boottime and includes suspension. Existing keys can answer PAM while locked or while
the shell is absent. New prompts and grants still need a fresh UI heartbeat, active
session and unlocked supervisor. A UID-wide lock-marker scan fails conservatively;
a stale marker can block new graphical authentication until its supervisor clears it.
Lock or UI loss cancels pending prompts and invalidates uncommitted grants.
The first stable non-wrapper executable remains the owner. Known executable/script
identities in the requester chain supply display names and the agent label only;
unknown requesters use generic copy. Display hints never grant authorization or
attest application integrity. The command field is the requested program basename;
arguments are omitted and indirect shell/edit modes remain unavailable. The target
account is unavailable because PAM_USER is the authentication account.

Same-user code can modify the owner's hooks, configuration or shell snapshot and
gain root when run below an owner with a live key. A user unit override can also
replace the shell's loaded prompt configuration. The placeholder warning states
these boundaries; neither executable names nor process checks make them safe.

Native buffer wiping does not prove erasure of QML string allocations. Executable
and session checks also do not by themselves prove trusted compositor input. The
production module runs under the rendered service through real libpam, alongside
failure-injection fixtures and shell tests. These establish implementation contracts;
run
`tests/sudo-key-vm.py` against the packaged stack for terminal fallback, password
success/failure, cancellation, grants and revocation acceptance.

The registered prompt provider must be the trusted shell executable in the
`emaki-shell.service` user unit, directly beneath its pinned, trusted user manager,
and in the matching active login. This supports the shell's retained generation
paths and its `-n -p` invocation. The process checks do not attest loaded QML or
protect a compromised desktop process; trusted surface and retained configuration
acceptance remain integration requirements.

After verifying a fresh password, the account callback waits at most one second
for the service to acknowledge its decision. Otherwise a short command could exit
before the service validates the sudo peer. Explicit cancellation during stock
verification denies account approval; unavailable infrastructure still falls
through. The root account is identified by PAM_USER, never getuid(): sudo's
[permission transitions](https://raw.githubusercontent.com/sudo-project/sudo/v1.9.17p2/plugins/sudoers/set_perms.c)
can set all process user IDs to root (156–168).
