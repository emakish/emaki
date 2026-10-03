#!/usr/bin/env python3
"""No PipeWire connection; only the read-only core query is accepted."""
import sys
assert sys.argv[1:] == ["info", "0"]
print('''id: 0
permissions: rwxm
type: PipeWire:Interface:Core/4
name: "SECRET_CORE_NAME"
user-name: "SECRET_USERNAME"
version: "1.4.9"
properties:
    core.name = "SECRET_SOCKET"
''')
