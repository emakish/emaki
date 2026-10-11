<!-- Copyright (C) 2026 Artur Yakymenko -->
<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Optional turn reporting

Turn reporting is offered in Settings → AI Assistants. Opening this guide does not
change an agent's configuration. Enable it yourself only if you want the Sudo key
choices to include a number of turns. A turn is one message from you and the work
until the reply. Type a whole number from 1 to 1000; there are no preset counts.

The packaged examples are beside this guide in `integrations/`. Preserve existing
hooks and notification commands. Back up your configuration before editing it;
merge the examples instead of replacing the file. To turn reporting off, remove
only the entries you added. Existing keys can be revoked from the panel.

## Claude Code

Merge only the `hooks` object from `integrations/claude-hooks.json` into your
user settings, appending its entries to any existing arrays. Do not copy the
example's `$comment` metadata. The command must run synchronously as shown.
`UserPromptSubmit` reports a start and `Stop` reports an end using `session_id`.
The next sudo request after a start can offer turn counts. Restart the agent
if its settings require it, and submit a new message before requesting a key.

The [official hooks reference](https://code.claude.com/docs/en/hooks) documents
both events and the JSON input. Stop hooks can cause more work before a reply;
repeated Stop events are counted once until the next UserPromptSubmit. This
cannot detect every continuation of the same human message. An interrupted turn
may have no Stop report. UserPromptSubmit also includes scheduled work and
background reports, with no documented origin field to separate them from human
messages. These callbacks therefore cannot prove exact human-only turn counts.

## Codex

Add `integrations/codex-notify.toml` to your user configuration only when you do
not already have `notify`. If you already use it, keep that command and explicitly
arrange for your existing notification handler to invoke
`emaki-sudo-turn codex` with the unchanged JSON argument, synchronously as a child
of the reporting agent. Do not replace an existing notification without deciding
how to preserve it. Project configuration cannot enable `notify`.

The [official notifications reference](https://learn.chatgpt.com/docs/config-file/config-advanced#notifications)
and [configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
document a JSON argument with `type: agent-turn-complete`, `thread-id` and `turn-id`.
There is no documented start notification in this integration. The first completed
notification registers reporting support, so only subsequent sudo requests offer
turn counts. A duplicate completion ID is ignored by the service.

## Scope and limits

Agents without a valid report have only time and session choices. Reports alone
never grant permission. The root service checks the reporter's process ancestry
in the kernel and accepts it only for that agent. The adapter passes only hashed
session and turn identifiers; prompts, transcripts and replies are not forwarded
or logged. It does not open transcript paths or write agent configuration.

A turn key ends when its count reaches zero, when revoked, when the agent exits,
or four hours after approval, whichever comes first. A missing end report can
therefore keep it alive only until that four-hour cap. Turn counts describe reports,
not command counts. The key only extends sudo authentication; it does not alter an
agent's own approval or sandbox settings. Independent sudo timestamps retain their
normal behavior.
