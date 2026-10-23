<!-- Copyright (C) 2026 Artur Yakymenko -->
<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Translating Emaki

English is the source language. Latin American Spanish is supplied as `es`, including
fallback for `es_MX.UTF-8`, `es_419`, `es_AR` and other Spanish locales. Brazilian Portuguese (`pt_BR`) is fully
translated; plain `pt` uses the same compiled catalogues. German (`de`), French (`fr`) and
Russian (`ru`) are also shipped, with regional fallback to these catalogues.
The shell, Programs & Updates, installer and Notes each have
Qt TS catalogues for QML and gettext PO catalogues for Python. Human CLI output has the `cli`
gettext domain. Shared terminology: [es-glossary.md](es-glossary.md),
[pt_BR-glossary.md](pt_BR-glossary.md), [de-glossary.md](de-glossary.md),
[fr-glossary.md](fr-glossary.md) and [ru-glossary.md](ru-glossary.md).

Run `make translations-update` after changing source strings. Commit the regenerated English
and pseudo files along with the source. This command only replaces `en` and `en_XA`; it never
overwrites a human translation. Do not edit these generated files directly.

To update a language, merge each component's English catalogue into its TS and PO files.
Use Qt Linguist to translate each message, including every plural form. Set Language and
Plural-Forms for that language and translate gettext entries with a gettext editor.
Russian requires three forms in both formats; the other shipped languages require two.
Preserve named/numbered
placeholders and markup, and translate whole messages rather than fragments. Latin American
Spanish uses `es_MX` for the installer's supported POSIX locale; the translation can also be
supplied as `es` for the broader Spanish fallback. Keep the Monday-first calendar and
24-hour time. Date patterns are catalogue messages, so each language controls day/month
order and punctuation. Display numbers follow the session or installer request language;
all five translated languages use decimal commas. Preserve each surface's precision and
grouping choice, including the existing English output. Translate displayed size units with
their enclosing message (French Mo/Go/Gio/Ko); keep units in literal commands, filenames and
machine-readable values unchanged.

Use `lupdate`/`lconvert` to merge updated Qt sources into an existing language and `msgmerge`
with the component POT for gettext. Resolve obsolete/missing messages before release.
Run `make check-i18n` to compile every TS/PO and check the generated source extraction,
completeness in every shipped language, plural forms, placeholders and compiled fallback catalogues.
`check_catalogues.py` checks exact source inventories, rejects obsolete, unfinished and fuzzy
entries, and preserves markup and line breaks. Each language's `*-unchanged.json` explains
invariant terms. Mutation tests remove a message from every format, domain and language and
require rejection; compiled Qt/gettext checks cover regional fallback and plural selection.
French punctuation requires non-breaking spaces (U+00A0 before colons and inside
guillemets; U+202F before semicolons, exclamation/question marks and between grouped
thousands). Literal commands, format fields and date patterns retain their syntax.
The gate also checks translated desktop-entry fields and the shipped fonts' glyph coverage.
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
(English, Spanish, Brazilian Portuguese, German, French and Russian), retains the selection
when its page is rebuilt, and immediately retranslates the window and backend responses.
It carries that choice into the target's locale configuration. All six supported POSIX
locales are generated during installation so they remain available in Settings. The package's
existing language migration generates the added locales on upgrades too. Step 6 enables
Spanish/Portuguese and step 7 enables German/French/Russian; each step checks only its own
set, preserving languages disabled after an earlier step, the current language and unrelated
locale-generation entries. Existing language and regional
format settings remain backed by systemd-localed. `EMAKI_LOCALE_DIR` overrides the catalogue
location for fixtures; ordinary packages use the installed data directory.

Language and keyboard choices remain separate. German and French follow the existing
Spanish/Portuguese US keyboard default; a person chooses their layouts on the Keyboard page.
An untouched Russian selection defaults to `us,ru`, with Latin first and the existing
Mod+Space switch. An explicitly chosen keyboard survives language changes; Russian cannot
be the sole or first layout. The installation planner enforces that boundary too.

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
Both render scripts accept `--language de` and `--language fr` (also `pt_BR` and `ru`),
with evidence under the corresponding language directory. `tests/test-i18n-fonts.py`
checks the shipped Noto Sans and Noto Sans Mono faces against the characters used by the
German/French/Russian catalogues and the complete Russian alphabet, including Ё/ё,
without relying on personal fonts. It also checks the four packaged Iosevka Nerd Font Mono
faces used by the terminal and the Unicode glyph tables in the selected Terminus console
fonts. These checks run in `make check-i18n`; the config package already depends on all
three font packages. Lock and greeter
text explicitly use the shared UI face. The welcome capture includes its scrollable lower
content so longer instructions can be inspected in full.

The scanner checks presentation properties, copy dictionaries, Python message/exception
boundaries and stable identifier contracts.
Its short exceptions describe internal failures converted to protocol codes and invariant
technical names. It cannot infer arbitrary external text or prove every dynamic rendering
path. The pseudo render tests cover real Welcome pages and Programs/installer surfaces;
VM checks are still required for compositor-specific surfaces, PAM and hardware-dependent
settings. External application metadata, user notes, PAM/provider messages and static
preboot artwork are owned outside these source catalogues and can remain in their supplied
language. Clock tools and the update reminder are included in the shell catalogue.
