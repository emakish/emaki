# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Explain update refusals without changing the package manager's decisions."""
import re


DETAIL_LIMIT = 2048


def error_lines(output):
    """Select pacman errors and their dependency/file-conflict diagnostics."""
    text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', output)
    text = ''.join(char for char in text if char in '\n\t' or ord(char) >= 32)
    context = ''
    selected = []
    for raw in text.splitlines():
        line = raw.strip()
        lower = line.lower()
        if lower.startswith('error:'):
            selected.append(line)
            context = lower
        elif (re.match(r'^(?:The holds file is unreadable|Package versions could not be checked) \(pacman\)\.', line)
              or re.match(r'^[a-zA-Z0-9@_+][a-zA-Z0-9@_.+:-]* version \S+ was rolled back \(pacman\)\.', line)):
            selected.append(line)
        elif ('dependencies' in context and line.startswith(':: ')
              and any(part in lower for part in ('breaks dependency', 'unable to satisfy dependency', 'are in conflict'))):
            selected.append(line)
        elif 'conflicting files' in context and ' exists in filesystem' in lower:
            selected.append(line)
        else:
            context = ''
    return selected


def details(output):
    """Return bounded technical diagnostics without a second explanation."""
    detail = "\n".join(error_lines(output)) or "pacman returned no details"
    if len(detail) > DETAIL_LIMIT:
        detail = detail[:DETAIL_LIMIT - 1] + "…"
    return "[" + detail + "]"


def explain(output):
    lines = error_lines(output)
    text = '\n'.join(lines)
    lower = text.lower()
    if ('breaks dependency' in lower or 'could not satisfy dependencies' in lower) and any(
            name in lower for name in ('quickshell-emaki', 'niri-emaki', 'emaki-desktop', 'required by emaki')):
        message = ('The available packages do not yet match this Emaki release. '
                   'Wait for matching packages, then try the full update again. Do not force this update.')
    elif 'breaks dependency' in lower or 'could not satisfy dependencies' in lower or 'conflicting dependencies' in lower:
        message = 'Some packages need different versions of each other. Wait for matching packages, then try the full update again.'
    elif 'was rolled back (pacman)' in lower:
        message = 'The update includes a package held after a rollback. Review the held packages before trying again.'
    elif 'package versions could not be checked' in lower or 'holds file is unreadable' in lower:
        message = 'The update could not check packages held after a rollback. Resolve the reported problem before trying again.'
    elif 'failed to commit transaction (failed to run transaction hooks)' in lower:
        message = 'A safety check stopped the update. Resolve the reported problem before trying again.'
    elif 'command failed to execute correctly' in lower:
        message = 'An update step failed. Some packages may already have changed. Resolve the reported problem before updating again.'
    elif 'unable to lock database' in lower or 'could not lock database' in lower:
        message = 'Another package operation may be running. Wait for it to finish before trying again.'
    elif 'invalid or corrupted package' in lower or 'signature' in lower or 'unknown trust' in lower:
        message = 'The update could not verify a download. Check the date and connection, then try again. Do not turn off signature checks.'
    elif 'exists in filesystem' in lower or 'conflicting files' in lower:
        message = 'An installed file is in the way of this update. Resolve the file conflict before trying again.'
    elif 'not enough free disk space' in lower or 'no space left on device' in lower:
        message = 'There is not enough free space for the update. Free some space, then try again.'
    elif any(part in lower for part in ('failed retrieving file', 'failed to synchronize', 'could not resolve host', 'timed out')):
        message = 'The update could not reach a package source. Check your connection, then try again.'
    else:
        message = 'The update could not finish. Resolve the reported problem before trying again.'
    return message + ' ' + details(output)
