#!/usr/bin/env python3
"""Private shell pipe, not a public CLI: paths never go to status or diagnostics."""
import datetime
import json
import os
from pathlib import Path
import re
import signal
import stat
import sys
import app_scope
from urllib.parse import unquote, urlsplit
from xml.parsers import expat

MAX_BYTES = 2 * 1024 * 1024
MAX_BOOKMARKS = 2048
MAX_RESULTS = 256


class Refused(Exception):
    pass


def local_path(uri):
    if not uri.startswith('file://') or re.search(r'%(?![0-9a-fA-F]{2})', uri):
        return None
    try:
        value = urlsplit(uri)
        if value.netloc not in ('', 'localhost') or value.query or value.fragment:
            return None
        path = unquote(value.path, encoding='utf-8', errors='strict')
        if not path.startswith('/') or path.startswith('//') or len(path) > 4096:
            return None
        if any(ord(char) < 32 or ord(char) == 127 for char in path):
            return None
        return os.path.normpath(path)
    except (ValueError, UnicodeError):
        return None


def timestamp(value):
    try:
        parsed = datetime.datetime.fromisoformat(value)
        return parsed.replace(tzinfo=parsed.tzinfo or datetime.timezone.utc).timestamp()
    except (ValueError, OverflowError, OSError):
        return 0


def read_recent():
    data = os.environ.get('XDG_DATA_HOME') or str(Path.home() / '.local/share')
    if not os.path.isabs(data):
        raise Refused('invalid_data_home')
    try:
        fd = os.open(Path(data) / 'recently-used.xbel', os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    except FileNotFoundError:
        return dict(state='absent', entries=[], limited=False)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise Refused('invalid_source')
        if info.st_size > MAX_BYTES:
            raise Refused('source_too_large')
        content = source.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise Refused('source_too_large')
    bookmarks = []
    depth = 0
    nodes = 0
    parser = expat.ParserCreate()

    def start(name, attrs):
        nonlocal depth, nodes
        depth += 1
        nodes += 1
        if depth > 24 or nodes > 32768:
            raise Refused('xml_limit')
        if depth == 1 and name != 'xbel':
            raise Refused('invalid_xbel')
        if name == 'bookmark' and depth == 2:
            if len(bookmarks) >= MAX_BOOKMARKS:
                raise Refused('too_many_entries')
            bookmarks.append((timestamp(attrs.get('visited', attrs.get('modified', attrs.get('added', '')))), attrs.get('href', '')))

    def end(_name):
        nonlocal depth
        depth -= 1

    def refuse_dtd(*_args):
        raise Refused('dtd_not_allowed')

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.StartDoctypeDeclHandler = refuse_dtd
    parser.EntityDeclHandler = refuse_dtd
    parser.ExternalEntityRefHandler = refuse_dtd
    try:
        parser.Parse(content, True)
    except expat.ExpatError:
        raise Refused('invalid_xbel') from None
    entries = []
    seen = set()
    for _when, uri in sorted(bookmarks, key=lambda row: row[0], reverse=True):
        path = local_path(uri)
        if path is None or path in seen:
            continue
        seen.add(path)
        try:
            if not stat.S_ISREG(os.stat(path).st_mode):
                continue
        except OSError:
            continue
        entries.append(dict(path=path, name=os.path.basename(path)))
        if len(entries) > MAX_RESULTS:
            return dict(state='ready', entries=entries[:MAX_RESULTS], limited=True)
    return dict(state='ready', entries=entries, limited=False)


def open_file(path):
    if not isinstance(path, str) or local_path(Path(path).as_uri() if os.path.isabs(path) else '') != path:
        raise Refused('invalid_path')
    try:
        if not stat.S_ISREG(os.stat(path).st_mode):
            raise Refused('not_a_file')
    except FileNotFoundError:
        raise Refused('file_missing') from None
    import gi
    gi.require_version('Gio', '2.0')
    from gi.repository import Gio, GLib
    file = Gio.File.new_for_path(path)
    try:
        handler = file.query_default_handler(None)
    except GLib.Error as error:
        if error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.NOT_SUPPORTED):
            raise Refused('no_handler') from None
        if error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.NOT_FOUND):
            raise Refused('file_missing') from None
        raise Refused('handler_failed') from None
    if handler is None:
        raise Refused('no_handler')
    # From here the shared helper owns the launch budget. An alarm must not kill
    # its caller while an independent worker is completing activation.
    signal.alarm(0)
    try:
        if not app_scope.gio_launch(handler, files=[path]):
            raise Refused('open_failed')
    except GLib.Error:
        raise Refused('open_failed') from None
    return dict(state='requested')  # Acceptance by handler, not proof of an opened window.


def main():
    # GIO-launched children must not inherit the private JSON pipe or print paths
    # to the shell's logs. dup is non-inheritable; only our response uses it.
    protocol = os.fdopen(os.dup(sys.stdout.fileno()), 'w')
    with open(os.devnull, 'w') as sink:
        os.dup2(sink.fileno(), 1)
        os.dup2(sink.fileno(), 2)
    def timeout(_signum, _frame):
        raise Refused('timeout')
    signal.signal(signal.SIGALRM, timeout)
    signal.alarm(3)
    try:
        if sys.argv[1:] == ['list']:
            value = read_recent()
        elif sys.argv[1:] == ['open']:
            line = sys.stdin.buffer.readline(16385)
            if len(line) > 16384:
                raise Refused('invalid_request')
            value = open_file(json.loads(line)['path'])
        else:
            raise Refused('invalid_request')
    except Refused as error:
        value = dict(state=str(error))
    except PermissionError:
        value = dict(state='access_denied')
    except ImportError:
        value = dict(state='helper_dependency_missing')
    except Exception:
        value = dict(state='read_or_open_failed')
    signal.alarm(0)
    protocol.write(json.dumps(dict(schema_version=1, **value), ensure_ascii=True) + '\n')
    protocol.flush()
    return 0 if value['state'] in ('ready', 'absent', 'requested') else 1


if __name__ == '__main__':
    sys.exit(main())
