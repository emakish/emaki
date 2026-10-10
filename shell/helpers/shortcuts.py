#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Read the shipped shortcut reference, never execute configuration contents."""
import argparse
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import emaki_paths

TOKEN = re.compile(r'(?P<space>[ \t\r]+)|(?P<comment>//[^\n]*|/\*.*?\*/)|(?P<raw>r?(?P<hash>\#+)".*?"(?P=hash))|(?P<string>"(?:\\.|[^"\\])*")|(?P<punct>[{};\n=])|(?P<word>[^\s{};="/]+|/(?![/*-])[^\s{};=]*)|(?P<disabled>/-)', re.S)

def tokens(text):
    out = []
    pos = 0
    while pos < len(text):
        match = TOKEN.match(text, pos)
        if not match:
            raise ValueError("Unsupported configuration syntax")
        pos = match.end()
        kind = match.lastgroup
        if kind in ("space", "comment"):
            continue
        value = match.group()
        if kind == "string":
            value = json.loads(value)
        elif kind == "raw":
            value = value[value.index('"') + 1:value.rindex('"')]
        out.append((kind, value))
    return out

def nodes(text):
    stream = tokens(text)
    pos = 0
    def block(nested=False):
        nonlocal pos
        result = []
        while pos < len(stream):
            kind, value = stream[pos]
            if value in ("\n", ";") and kind == "punct":
                pos += 1
                continue
            if value == "}" and kind == "punct":
                if not nested:
                    raise ValueError("Unexpected closing brace")
                pos += 1
                return result
            disabled = kind == "disabled"
            if disabled:
                pos += 1
            head = []
            children = []
            while pos < len(stream):
                kind, value = stream[pos]
                if kind == "punct" and value in ("\n", ";", "}"):
                    break
                pos += 1
                if kind == "punct" and value == "{":
                    children = block(True)
                    break
                head.append(value)
            if not head:
                raise ValueError("Missing node name")
            if not disabled:
                result.append((head, children))
        if nested:
            raise ValueError("Missing closing brace")
        return result
    return block()

def read_shortcuts(path):
    rows = {}
    active = set()
    seen = 0
    modifier = "Super"
    def read(file):
        nonlocal seen, modifier
        file = file.expanduser().resolve()
        seen += 1
        if file in active or seen > 64:
            raise ValueError("Include cycle or too many files")
        active.add(file)
        if not file.is_file() or file.stat().st_size > 1024 * 1024:
            raise ValueError("Configuration is not a bounded regular file")
        data = file.read_bytes()
        if len(data) > 1024 * 1024:
            raise ValueError("Configuration is too large")
        for head, children in nodes(data.decode("utf-8")):
            if head[0] == "include":
                if len(head) != 2:
                    raise ValueError("Unsupported include")
                read(file.parent / Path(head[1]).expanduser())
            elif head[0] == "input":
                for setting, _ in children:
                    if setting[0] == "mod-key" and len(setting) == 2:
                        modifier = setting[1]
            elif head[0] == "binds":
                for binding, actions in children:
                    title = ""
                    if "hotkey-overlay-title" in binding:
                        at = binding.index("hotkey-overlay-title")
                        if binding[at + 1:at + 2] == ["="]:
                            title = binding[at + 2]
                    description = title or "; ".join(" ".join(action) for action, _ in actions)
                    rows[binding[0].casefold()] = {"keys": binding[0], "description": description or "Disabled"}
        active.remove(file)
    read(Path(path))
    for row in rows.values():
        row["keys"] = row["keys"].replace("Mod+", modifier + "+")
    return list(rows.values())

def main():
    parser = argparse.ArgumentParser()
    nearby = Path(__file__).resolve().parents[2] / "niri/default.kdl"
    default = nearby if nearby.is_file() else Path(emaki_paths.DATADIR) / "niri/default.kdl"
    parser.add_argument("--config", type=Path, default=default)
    args = parser.parse_args()
    try:
        result = {"rows": read_shortcuts(args.config), "state": "ready"}
    except (OSError, ValueError, IndexError, RecursionError):
        result = {"rows": [], "state": "unavailable"}
    print(json.dumps(result))

if __name__ == "__main__":
    main()
