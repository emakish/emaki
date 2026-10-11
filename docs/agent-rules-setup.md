<!-- Copyright (C) 2026 Artur Yakymenko -->
<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# [PLACEHOLDER] Add Emaki rules

Settings > AI Assistants offers this guide and the [system map](system-map.md).
Opening either changes nothing in your configuration. To turn on the rules, read
the [template](agent-rules.md) and explicitly add it using one of the choices below.
The installed template is `/usr/share/emaki/agent-rules.md`; its companion map is
`/usr/share/emaki/system-map.md`. Both belong to emaki-config and are regenerated
from the same system map for each release. Do not edit the packaged files.

Back up any personal instruction file you will change. Preserve its existing text,
check for conflicting rules and add only the Emaki instructions you want. These
are working instructions, not a sandbox, a permission grant or snapshot protection.
This setup does not install snapguard, enable a sudo key or change authentication.

## Claude Code

For all local projects, create `~/.claude/rules/emaki.md` only if that name is free,
and copy the template's contents into it. If it already exists, inspect it and
merge deliberately; do not overwrite it. An alternative is to append the template
to your existing `~/.claude/CLAUDE.md`. For just one project, use that project's
`CLAUDE.md` or `.claude/CLAUDE.md` instead. Choose one location to avoid duplicates.

The [official memory documentation](https://code.claude.com/docs/en/memory)
documents these instruction locations and user-level rules. If you set
`CLAUDE_CONFIG_DIR`, use that configuration directory instead of `~/.claude`, as
described by the [settings documentation](https://code.claude.com/docs/en/settings).
Start a new session and check the loaded instructions with `/context` before relying
on them. Exclusions or organization settings may prevent personal rules from loading.

## Codex

For all local projects, append the template's contents to `~/.codex/AGENTS.md`.
If `CODEX_HOME` is set, use `AGENTS.md` inside that directory instead. Preserve
existing instructions. A nonempty `AGENTS.override.md` in the same directory takes
precedence; in that case deliberately add the instructions to the active file or
resolve the override yourself. Do not silently remove or replace either file.
For only one project, add the template to the project's active `AGENTS.md` instead.

The [official instruction-file documentation](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
documents these names, precedence and discovery. Paste the actual instructions;
do not assume that a Markdown link or an `@` import loads another file. Project
instructions load after global ones, and the combined default limit is 32 KiB.
Keep room for existing project rules. Start a new session and ask it to identify
the loaded instruction sources and summarize the Emaki ownership zones.

## Updating or turning off the rules

Personal copies are yours. After an Emaki update, compare your copy with the
installed template and update only the Emaki instructions you chose to add. The
packaged template always points to the installed map; use that map for current
component details instead of relying on a stale copy.

To turn the rules off, remove only your Emaki additions, or remove the dedicated
rules file if it contains nothing else. Keep other instructions, hooks and settings.
Restart the session and verify the remaining instruction sources. Turning rules
off does not revoke sudo keys; use the panel or `emaki-sudo revoke-all --json` for that.

File locations checked against the linked official documentation on 2026-10-11.
