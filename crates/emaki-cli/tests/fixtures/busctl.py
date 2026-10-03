#!/usr/bin/env python3
"""Strict fake busctl: accepts read-only methods only, never contacts a bus."""
import json
import os
import sys
import time

args = sys.argv[1:]
for required in ["--json=short", "--no-pager", "--auto-start=no",
                 "--allow-interactive-authorization=no"]:
    assert required in args, required
assert any(a.startswith("--timeout=") for a in args)
assert os.environ["LC_ALL"] == "C"
mode = os.environ.get("FAKE_DBUS", "ok")
if mode == "hang":
    time.sleep(30)
elif mode == "flood":
    sys.stdout.write("x" * (3 * 1024 * 1024))
    sys.exit(0)
elif mode == "truncated":
    sys.stdout.write('{"type":"b","data":[')
    sys.exit(0)
elif mode == "denied":
    sys.stderr.write("org.freedesktop.DBus.Error.AccessDenied: SECRET_DENIED /home/private\n")
    sys.exit(1)
elif mode == "disconnected":
    sys.stderr.write("Connection reset by peer: SECRET_DISCONNECT\n")
    sys.exit(1)
elif mode == "unknown_failure":
    sys.stderr.write("SECRET_OTHER\n")
    sys.exit(1)
elif mode == "timeout":
    sys.stderr.write("Call timed out: SECRET_TIMEOUT\n")
    sys.exit(1)

def reply(signature, value):
    print(json.dumps({"type": signature, "data": [value]}))

if "NameHasOwner" in args:
    reply("b", mode != "absent")
elif "GetUnit" in args:
    assert args[-1] == "wireplumber.service"
    reply("o", "/org/freedesktop/systemd1/unit/wireplumber_2eservice")
elif "Ping" in args:
    assert args[-2:] == ["org.freedesktop.DBus.Peer", "Ping"]
elif "get-property" in args:
    prop = args[-1]
    assert prop in ("Version", "DaemonVersion", "ActiveProfile", "ActiveState")
    # Captured busctl format: property values are NOT wrapped in an argument array.
    print(json.dumps({"type": "s", "data": {
        "ActiveProfile": "performance", "ActiveState": "active"
    }.get(prop, "1.2.3")}))
else:
    raise AssertionError("Unexpected or mutating bus operation")
