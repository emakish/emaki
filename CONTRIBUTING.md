# Contributing to Emaki

How to report a bug, suggest an idea and sign off your commits, who decides, the licence and the
Code of Conduct are in the [organization's contributing guide](https://github.com/emakish/.github/blob/main/CONTRIBUTING.md).
This page adds what is specific to this repository.

New here? Pick an unassigned issue labelled `good first issue` and comment on it before you start.

## Build and test

Work in a disposable Arch Linux VM, not in your everyday session. The prerequisites are listed in
`packaging/emaki-desktop/PKGBUILD` and the targets at the top of `Makefile`. As an ordinary user:

    make prepare-core
    make build
    make check

For shell changes also run `make check-shell`; for installer changes, `make check-installer`.
Put the commands you ran and their results in the pull request. If a check cannot run, say which one
and why.

## Translations

English is the source language. Start with [i18n/README.md](i18n/README.md) and the glossary for your
language. Keep placeholders and markup, translate every plural form and run `make check-i18n`.
Do not edit the generated `en` and `en_XA` catalogues.

## After you open a pull request

We answer by the next day. Every change gets an independent review and must pass the `public-guard`,
`arch-check` and `dco` checks. Accepted changes are merged with a merge commit, so your commits keep
your name, and ship with a later release.
