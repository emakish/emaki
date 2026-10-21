<!-- Copyright (C) 2026 Artur Yakymenko -->
<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Translating Emaki

English is the source language. Latin American Spanish is supplied as `es`, including
fallback for `es_MX.UTF-8`, `es_419`, `es_AR` and other Spanish locales. Brazilian Portuguese (`pt_BR`) is fully
translated; plain `pt` uses the same compiled catalogues. The shell, Programs & Updates, installer and Notes each have
Qt TS catalogues for QML and gettext PO catalogues for Python. Human CLI output has the `cli`
gettext domain. Shared terminology: [es-glossary.md](es-glossary.md) and `pt_BR-glossary.md`.

Run `make translations-update` after changing source strings. Commit the regenerated English
and pseudo files along with the source. This command only replaces `en` and `en_XA`; it never
overwrites a human translation. Do not edit these generated files directly.

To add Brazilian Portuguese, copy each component's `en.ts` to `pt_BR.ts` and set the TS
`language` attribute to `pt_BR`. Use Qt Linguist to translate each message, including every
plural form. Initialize `pt_BR.po` from the component's `.pot`, set Language and Plural-Forms
for Brazilian Portuguese, and translate it with a gettext editor. Preserve named/numbered
placeholders and markup, and translate whole messages rather than fragments. Latin American
Spanish uses `es_MX` for the installer's supported POSIX locale; the translation can also be
supplied as `es` for the broader Spanish fallback. Regional numbering/date rules remain
separate from translated text. Preserve the fixed date/time patterns, Monday-first calendar,
number grouping and units from 0.10. A locale may supply day and month names within those
patterns; adding a catalogue does not authorize changing their layout.

Use `lupdate`/`lconvert` to merge updated Qt sources into an existing language and `msgmerge`
with the component POT for gettext. Resolve obsolete/missing messages before release.
Run `make check-i18n` to compile every TS/PO and check the generated source extraction,
Portuguese completeness, plural forms, placeholders and compiled fallback catalogues.
Add the new compiled files to the config and installer package inventories. Build the
packages; do not copy files into a running system by hand. For a new installer locale, add
its validated POSIX value and UI choice together with locale generation tests.

For a source-tree pseudo test, run `make check-i18n`. Screenshots are saved under
`.cache/evidence/i18n/`. In a disposable VM with the built packages, sign in normally, then
launch an application with `EMAKI_LANGUAGE=en_XA` to select the bracketed/expanded test copy.
Test the shell only in that disposable VM or through the isolated offscreen test runner;
do not launch a second shell in an existing personal session. Open Settings, Welcome, the
clock, notifications, lock, Programs & Updates, installer and Notes; inspect narrow windows
and larger text settings for clipped labels. Native and Python translation tests both use
the compiled catalogues, not a JavaScript approximation.

For Brazilian Portuguese evidence, run `python3 tests/test-i18n-pt-br-shell.py`
and `python3 tests/test-i18n-pt-br-apps.py` after `make check-i18n`. They use private
homes, XDG directories, inert backends and offscreen rendering, and save PNGs plus
text/geometry diagnostics in `.cache/evidence/i18n/pt_BR/`. The shell renderer
requires the shipped Noto fonts and local Qt development tools. These fixtures
cover all Settings pages, Welcome, the bar, Clocks, notifications, Notes, Programs
and installer pages; hardware-dependent pages include unavailable states.

Settings follows the machine language by default and requires sign-out to apply a new
language to all applications. The installer offers only languages with a packaged catalogue
(English, Spanish and Brazilian Portuguese), retains the selection when its page is rebuilt, and carries that choice into
the target's locale configuration. All three supported POSIX
locales are generated during installation so they remain available in Settings. Existing language and regional
format settings remain backed by systemd-localed. `EMAKI_LOCALE_DIR` overrides the catalogue
location for fixtures; ordinary packages use the installed data directory.

Messages use an explicit `EMAKI_LANGUAGE` override, otherwise LC_ALL/LC_MESSAGES/LANG.
C/POSIX forces English; other locales use LANGUAGE preferences before the effective locale.
The lock and greeter preserve the display language separately from the C locale used by PAM.
The Qt loader fails softly so a missing optional module does not prevent a window opening;
package dependencies and staged-layout tests enforce the normal translated path.

Never translate identifiers: notification application keys, IPC/JSON values, NetworkManager
groups, GRUB titles, confirmation tokens and window identities remain stable. Translate their
display labels separately; use metadata rather than parsing displayed sentences.

`tests/test-i18n-es.py` requires every source message, both Spanish plural forms, matching
placeholders, markup and line breaks, and no fuzzy or unfinished entries. Identical technical
terms and valid Spanish cognates are explicitly documented in `es-unchanged.json`.
It also exercises the compiled Spanish catalogues through regional Qt and gettext fallback.
Run `python3 tests/render-i18n-es-shell.py` and `python3 tests/render-i18n-es-apps.py`
for Spanish offscreen evidence in `.cache/evidence/i18n/es/`. These runners use private
home/runtime directories and fixture backends; they do not connect to a live desktop.

The scanner checks presentation properties, copy dictionaries, Python message/exception
boundaries and stable identifier contracts.
Its short exceptions describe internal failures converted to protocol codes and invariant
technical names. It cannot infer arbitrary external text or prove every dynamic rendering
path. The pseudo render tests cover real Welcome pages and Programs/installer surfaces;
VM checks are still required for compositor-specific surfaces, PAM and hardware-dependent
settings. External application metadata, user notes, PAM/provider messages and static
preboot artwork are owned outside these source catalogues and can remain in their supplied
language. Clock tools and the update reminder are included in the shell catalogue.
