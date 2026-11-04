# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Own logind protection for the complete Emaki update transaction."""
from contextlib import contextmanager
import os
import sys
from pathlib import Path

from emaki_update_space import _

try:
    import emaki_session_state as state
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
    import emaki_session_state as state


class AcquisitionError(RuntimeError):
    """The transaction has not started because its lease could not be acquired."""


def acquire():
    """Take ownership of the returned FD, including every copy in Gio's list."""
    try:
        import gi
        gi.require_version('Gio', '2.0')
        from gi.repository import Gio, GLib

        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        reply, fd_list = bus.call_with_unix_fd_list_sync(
            'org.freedesktop.login1', '/org/freedesktop/login1',
            'org.freedesktop.login1.Manager', 'Inhibit',
            GLib.Variant('(ssss)', ('shutdown:sleep:handle-lid-switch', 'Emaki packages',
                                   _('Updating Emaki'), 'block')),
            GLib.VariantType('(h)'), Gio.DBusCallFlags.NONE, 5000, None, None)
        descriptors = fd_list.steal_fds()
        try:
            index, = reply.unpack()
            if not 0 <= index < len(descriptors):
                raise ValueError
            descriptor = descriptors[index]
            os.set_inheritable(descriptor, False)
            descriptors.pop(index)
            return descriptor
        finally:
            for descriptor in descriptors:
                os.close(descriptor)
    except Exception as error:
        raise AcquisitionError(_('Shutdown and sleep protection could not start. The update was not started.')) from error


@contextmanager
def inhibit(*, required=True):
    # Recovery roots share the host bus; never inhibit the host from a chroot.
    if state.foreign_root():
        yield None
        return
    try:
        descriptor = acquire()
    except RuntimeError:
        if required:
            raise
        print(_('Warning: shutdown and sleep protection is unavailable; the package transaction will continue.'),
              file=sys.stderr)
        yield None
        return
    try:
        yield descriptor
    finally:
        os.close(descriptor)
