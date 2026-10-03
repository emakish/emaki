#!/usr/bin/env python3
"""Try a palette: replace colors in tokens.toml without touching the rest of the file.

  scripts/try-palette.py key=value ...     e.g.  accent=e2733f background=14100d
  scripts/try-palette.py --ansi key=value  same for the [ansi] section

Only changes quoted values for the named keys; preserves comments and order.
Afterwards: make render && sudo make install.
"""
import re
import sys
from pathlib import Path

p = Path(__file__).resolve().parent.parent / "tokens.toml"
t = p.read_text()
section = "color"
changed = []
for arg in sys.argv[1:]:
    if arg == "--ansi":
        section = "ansi"
        continue
    if arg == "--color":
        section = "color"
        continue
    key, val = arg.split("=", 1)
    val = val.lstrip("#").lower()
    assert re.fullmatch(r"[0-9a-f]{6}", val), f"not a color: {val}"
    # Find section boundaries.
    m = re.search(rf"^\[{section}\]\n(.*?)(?=^\[|\Z)", t, re.S | re.M)
    assert m, f"missing section [{section}]"
    body = m.group(1)
    new_body, n = re.subn(rf'^({re.escape(key)}\s*=\s*")[0-9a-fA-F]{{6}}(")', rf"\g<1>{val}\2", body, flags=re.M)
    assert n == 1, f"key {key} not found in [{section}]"
    t = t[: m.start(1)] + new_body + t[m.end(1):]
    changed.append(f"{section}.{key}={val}")
p.write_text(t)
print("changed:", " ".join(changed))
