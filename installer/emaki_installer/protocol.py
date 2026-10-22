# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Frozen v1 NDJSON framing and the single-writer job controller."""
from .i18n import _, LOCALES, available_locales, using_language
from dataclasses import dataclass, field
import json
from pathlib import Path
import secrets
import tempfile
import threading
import time
import weakref

from . import __label__, __version__
from .constants import LOG, MAX_FRAME
from .errors import Code, InstallError, require
from .inventory import public_inventory
from .latin_layouts import CONSOLE_CHARS
from .planner import RESERVED_LOGINS, make_plan, validate_config
from .runtime import Redactor


def decode_frame(frame):
    require(len(frame) <= MAX_FRAME and frame.endswith(b'\n'), Code.BAD_REQUEST, _('Invalid frame length.'))
    try:
        value = json.loads(frame.decode('utf-8'), parse_constant=lambda ignored: (ignored for ignored in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise InstallError(Code.BAD_REQUEST, _('Invalid UTF-8 JSON.')) from exc
    require(isinstance(value, dict) and isinstance(value.get('type'), str)
            and isinstance(value.get('id'), str) and 0 < len(value['id']) <= 128,
            Code.BAD_REQUEST, _('Requests need a string type and a nonempty ID (at most 128 characters).'))
    return value


def encode_frame(value):
    result = (json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n').encode()
    require(len(result) <= MAX_FRAME, Code.BAD_REQUEST, _('Outgoing message exceeds 64 KiB.'))
    return result


@dataclass
class Job:
    id: str
    cancelled: threading.Event = field(default_factory=threading.Event)
    skip_update: threading.Event = field(default_factory=threading.Event)
    state: dict | None = None
    terminal: dict | None = None
    history_full: bool = False
    history: object = field(default_factory=lambda: tempfile.SpooledTemporaryFile(
        max_size=4 * 1024 * 1024, mode='w+t', encoding='utf-8'), repr=False)

    def __post_init__(self):
        self._history_cleanup = weakref.finalize(self, self.history.close)


class Controller:
    def __init__(self, inventory, worker_factory, *, test_mode=False, version='4.5',
                 clock=time.monotonic, log_stream=None, save_log=None, reboot=None,
                 validate_plan=None, set_timezone=None, prepare_reboot=None, restart_deadline=None,
                 check_installation=None):
        self.inventory, self.worker_factory = inventory, worker_factory
        self.test_mode, self.version, self.clock = test_mode, version, clock
        self.log_stream, self.save_log_fn, self.reboot_fn = log_stream, save_log, reboot
        self.validate_plan = validate_plan
        self.set_timezone_fn = set_timezone
        self.restart_deadline_fn = restart_deadline
        self.prepare_reboot_fn = prepare_reboot
        self.check_installation_fn = check_installation
        self.redactor = Redactor()
        self.lock = threading.RLock()
        self.operation_lock = threading.Lock()
        self.seq = 0
        self.pending = None
        self.job = None
        self.stopping = False
        self.restart_requested = False
        self.restart_state = None
        self.restart_deadline = 0
        self.listeners = set()

    @property
    def busy(self):
        return self.job is not None and self.job.terminal is None

    def message(self, kind, request=None, **fields):
        with self.lock:
            self.seq += 1
            return {'type': kind, 'id': request or '', 'seq': self.seq, **fields}

    def reply(self, request, ok=True, **fields):
        return self.message('reply', request, for_id=request, ok=ok, **fields)

    def emit(self, kind, **fields):
        with self.lock:
            if kind in ('state', 'progress') and 'notice' not in fields and (self.job.state or {}).get('notice'):
                fields['notice'] = self.job.state['notice']
            msg = self.message(kind, job_id=self.job.id, **fields)
            if kind in ('state', 'progress'):
                self.job.state = msg
            if kind == 'log':
                msg['line'] = self.redactor.text(msg['line'])
                if not self.job.history_full:
                    end = self.job.history.seek(0, 2)
                    try:
                        self.job.history.write(json.dumps(msg) + '\n')
                        self.job.history.flush()
                    except OSError:
                        # The history rolls over to a file, which can fill up.
                        # Losing replay must not lose the job: drop any partial
                        # line and keep delivering to listeners.
                        self.job.history_full = True
                        try:
                            self.job.history.truncate(end)
                        except OSError:
                            pass
            if kind in ('done', 'error'):
                self.job.terminal = msg
            for listener in list(self.listeners):
                listener(msg)
            return msg

    def log(self, line):
        # Call this after subprocess output and before ANY sink, including disk.
        with self.lock:
            line = self.redactor.text(line)
            # Keep every wire frame small even for a hostile subprocess line.
            for offset in range(0, max(1, len(line)), 4096):
                chunk = line[offset:offset + 4096]
                if self.log_stream:
                    try:
                        self.log_stream.write(chunk + '\n')
                        self.log_stream.flush()
                    except OSError:
                        # A full log file must not kill the job thread: stop
                        # writing to it for good, keep emitting to listeners.
                        self.log_stream = None
                if self.busy:
                    self.emit('log', line=chunk)

    def invalidate(self):
        if self.pending:
            self.pending['plan'].config['user']['password'] = ''
            self.pending['plan'].config['disk_password'] = ''
        self.pending = None
        # plan and probe requests invalidate before their busy check: a running
        # job still logs and needs its secrets redacted until it ends.
        if not self.busy:
            self.redactor.secrets.clear()

    def invalidate_own(self, client):
        # The outstanding plan belongs to the connection that reviewed it. Another client's
        # probe, time zone or cancel says nothing about that plan: the live session's window
        # probes as it connects, which once wiped a CLI plan before its confirm (N-D2).
        if self.pending is None or self.pending['client'] is client:
            self.invalidate()

    def release(self, client):
        # A closed connection can never confirm its plan; drop it and its secrets now.
        with self.lock:
            if self.pending and self.pending['client'] is client:
                self.invalidate()

    def expire(self):
        with self.lock:
            if self.pending and self.clock() >= self.pending['deadline']:
                self.invalidate()

    def handle(self, msg, client=None):
        # Probe/plan are serialized, including their read-only temporary mounts.
        language = msg.get('language')
        if language is not None and language not in LOCALES:
            return [self.reply(msg['id'], False, code=Code.BAD_REQUEST.value,
                               msg=_('Invalid config values.'))]
        with self.operation_lock, using_language(language):
            try:
                return self._handle(msg, client)
            except InstallError as exc:
                return [self.reply(msg['id'], False, code=exc.code.value,
                                   msg=self.redactor.text(exc.message), log_path=str(LOG),
                                   **dict(self.restart_reply_fields(msg),
                                          **({'recovery_included': exc.recovery_included}
                                             if msg['type'] == 'reboot' else {})))]
            except Exception as exc:
                self.log(_('Request failed: ') + type(exc).__name__)
                return [self.reply(msg['id'], False, code=Code.INTERNAL.value,
                                   msg=_('The request could not be completed. Check the installation log for details.'), log_path=str(LOG),
                                   **self.restart_reply_fields(msg))]

    def restart_reply_fields(self, msg):
        if msg['type'] != 'reboot':
            return {}
        return {'restart_state': self.restart_state,
                'remaining_s': max(0, self.restart_deadline - self.clock())}

    def _handle(self, msg, client):
        kind, ident = msg['type'], msg['id']
        if kind == 'hello':
            require(type(msg.get('proto')) is int and msg['proto'] == 1,
                    Code.BAD_REQUEST, _('Only protocol 1 is supported.'))
            # reserved_logins: the window refuses these before the password is typed.
            # console_chars: the window names the password characters the text console types
            # differently (planner.console_unsafe_chars), without sending the password.
            return [self.message('hello', ident, proto=1, archinstall_version=self.version,
                                 emaki_version=__version__, emaki_label=__label__, test_mode=self.test_mode,
                                 busy_job=self.job.id if self.busy else None,
                                 restart=({'state': self.restart_state, 'job_id': self.job.id,
                                           'remaining_s': max(0, self.restart_deadline - self.clock())}
                                          if self.restart_state else None),
                                 timezone_guess=hasattr(self.inventory, "timezone_guess"),
                                 available_locales=available_locales(),
                                 reserved_logins=list(RESERVED_LOGINS),
                                 console_chars={name: list(record) for name, record in CONSOLE_CHARS.items()})]
        if kind == 'get_timezone_guess':
            return [self.reply(ident, **self.inventory.timezone_guess())]
        if kind == 'set_timezone':
            with self.lock:
                require(not self.busy and not self.stopping, Code.BUSY, _('The installer is busy or stopping. Wait before trying again.'))
                self.invalidate_own(client)
            require(self.set_timezone_fn is not None, Code.BAD_REQUEST,
                    _('Live clock changes are unavailable.'))
            return [self.reply(ident, **self.set_timezone_fn(msg.get('timezone')))]
        if kind in ('plan', 'probe'):
            with self.lock:
                # A new plan from any client replaces the one outstanding plan.
                if kind == 'plan':
                    self.invalidate()
                else:
                    self.invalidate_own(client)
                require(not self.busy and not self.stopping, Code.BUSY, _('The installer is busy or stopping. Wait before trying again.'))
                if kind == 'plan' and self.check_installation_fn:
                    self.check_installation_fn()
            if kind == 'probe':
                return [self.message('inventory', ident, **public_inventory(self.inventory.probe()))]
            config = msg.get('config')
            if isinstance(config, dict) and isinstance(config.get('user'), dict):
                self.redactor.add(config['user'].get('password'))
            if isinstance(config, dict):
                self.redactor.add(config.get('disk_password'))
            plan_id = secrets.token_hex(16)
            try:
                validate_config(config, self.test_mode)  # An unoffered mode is refused before any probe.
                plan = make_plan(config, self.inventory.probe(), self.test_mode,
                                 esp_need=getattr(self.inventory, 'esp_need', None))
                if self.validate_plan:
                    self.validate_plan(plan)
            except InstallError as exc:
                return [self.message('plan_ack', ident, plan_id=plan_id, expires_s=600,
                                     summary=[], errors=[{'code': exc.code.value, 'msg': self.redactor.text(exc.message)}],
                                     warnings=[])]
            except (TypeError, ValueError, UnicodeError):
                return [self.message('plan_ack', ident, plan_id=plan_id, expires_s=600,
                                     summary=[], errors=[{'code': Code.BAD_CONFIG.value, 'msg': _('Invalid config values.')}],
                                     warnings=[])]
            token = secrets.token_urlsafe(32)
            with self.lock:
                self.pending = {'plan': plan, 'plan_id': plan_id, 'token': token,
                                'deadline': self.clock() + 600, 'client': client}
            return [self.message('plan_ack', ident, plan_id=plan_id, token=token, expires_s=600,
                                 summary=plan.summary, summary_keys=plan.summary_keys, errors=[], warnings=plan.warnings)]
        if kind == 'renew':
            with self.lock:
                require(not self.busy and not self.stopping, Code.BUSY, _('The installer is busy or stopping. Wait before trying again.'))
                pending = self.pending
                if pending and self.clock() >= pending['deadline']:
                    self.invalidate()
                    raise InstallError(Code.TOKEN_EXPIRED, _('Plan token expired; review a new plan.'))
                require(pending is not None and pending['client'] is client
                        and msg.get('plan_id') == pending['plan_id']
                        and isinstance(msg.get('token'), str)
                        and secrets.compare_digest(msg['token'], pending['token']),
                        Code.TOKEN_INVALID, _('Invalid plan confirmation.'))
                pending['token'] = secrets.token_urlsafe(32)
                pending['deadline'] = self.clock() + 600
                return [self.message('plan_ack', ident, plan_id=pending['plan_id'],
                                     token=pending['token'], expires_s=600,
                                     summary=pending['plan'].summary, summary_keys=pending['plan'].summary_keys, errors=[], warnings=pending['plan'].warnings)]
        if kind == 'skip_update':
            with self.lock:
                require(self.busy, Code.BAD_REQUEST, _('No installation is running.'))
                self.job.skip_update.set()
                return [self.reply(ident)]
        if kind == 'confirm':
            with self.lock:
                require(not self.busy and not self.stopping, Code.BUSY, _('The installer is busy or stopping. Wait before trying again.'))
                if self.check_installation_fn:
                    self.check_installation_fn()
                pending = self.pending
                require(pending is not None, Code.TOKEN_INVALID, _('No outstanding installation plan.'))
                if self.clock() >= pending['deadline']:
                    self.invalidate()
                    raise InstallError(Code.TOKEN_EXPIRED, _('Plan token expired; review a new plan.'))
                # Never a plan another connection reviewed, even with its token.
                require(pending['client'] is client and msg.get('plan_id') == pending['plan_id']
                        and isinstance(msg.get('token'), str)
                        and secrets.compare_digest(msg['token'], pending['token']),
                        Code.TOKEN_INVALID, _('Invalid plan confirmation.'))
                self.pending = None  # Single use, including failed jobs.
                if self.job:
                    self.job.history.close()
                self.job = Job(secrets.token_hex(16))
                response = self.reply(ident, job_id=self.job.id)
                # Return the launch closure; transport enqueues reply before work.
                return [response, lambda: self.start(pending['plan'])]
        if kind == 'cancel':
            with self.lock:
                if self.busy:
                    self.job.cancelled.set()
                    return []  # cancel_ack is emitted after cleanup at the boundary.
                self.invalidate_own(client)
                return [self.reply(ident)]
        if kind == 'resume':
            with self.lock:
                job = self.job
                require(job is not None and msg.get('job_id') == job.id,
                        Code.JOB_NOT_FOUND, _('Job is not retained by this daemon.'))
                since = msg.get('since_seq', 0)
                require(type(since) is int and since >= 0, Code.BAD_REQUEST, _('Invalid resume sequence.'))
                job.history.seek(0)
                result = [x for line in job.history if (x := json.loads(line))['seq'] > since]
                if job.state:
                    result.append(self.message('state', job_id=job.id,
                                               **{k: v for k, v in job.state.items()
                                                  if k not in ('type', 'id', 'seq', 'job_id')}))
                if job.terminal:
                    # Replay terminal status too, so --follow can terminate.
                    result.append(self.message(job.terminal['type'], job_id=job.id,
                                               **{k: v for k, v in job.terminal.items()
                                                  if k not in ('type', 'id', 'seq', 'job_id')}))
                return result
        if kind == 'save_log':
            if self.restart_state == 'preparing':
                unavailable = _('Log export is unavailable during restart preparation.')
            elif self.restart_state in ('ready', 'forced'):
                unavailable = _('Log export is unavailable after restart preparation.')
            else:
                unavailable = _('The installation log cannot be saved while the installer is stopping.')
            require(not self.stopping or self.restart_state == 'failed', Code.BUSY,
                    unavailable)
            require(self.save_log_fn is not None, Code.BAD_DEST, _('Log export unavailable.'))
            self.save_log_fn(msg.get('dest'))
            return [self.reply(ident)]
        if kind in ('prepare_reboot', 'reboot'):
            require(self.job is not None and self.job.terminal is not None
                    and self.job.terminal['type'] == 'done', Code.BAD_REQUEST,
                    _('Reboot is only available after a completed installation.'))
            if kind == 'prepare_reboot':
                require(self.prepare_reboot_fn is not None, Code.BAD_REQUEST, _('Reboot is unavailable.'))
                # Once sent, the guardian can restart without another request.
                # Do not let any client mount or change the target again, even
                # if the reply is lost. Preparation and reboot retries remain OK.
                self.restart_requested = self.stopping = True
                self.invalidate()
                self.restart_state = 'preparing'
                try:
                    forced = self.prepare_reboot_fn() is True
                except Exception:
                    self.restart_state = 'failed'
                    raise
                self.restart_state = 'forced' if forced else 'ready'
                if forced and not self.restart_deadline:
                    self.restart_deadline = ((self.restart_deadline_fn() or self.clock()) if self.restart_deadline_fn
                                             else self.clock() + 15)
                return [self.reply(ident, forced_reboot=forced,
                                   remaining_s=max(0, self.restart_deadline - self.clock()))]
            require(self.reboot_fn is not None, Code.BAD_REQUEST, _('Reboot is unavailable.'))
            self.stopping = True
            self.invalidate()
            try:
                self.reboot_fn()
            except Exception:
                self.stopping = self.restart_requested
                if self.restart_requested:
                    if self.restart_deadline_fn:
                        self.restart_deadline = self.restart_deadline_fn() or 0
                    self.restart_state = 'forced' if self.restart_deadline else 'ready'
                raise
            return [self.reply(ident)]
        raise InstallError(Code.BAD_REQUEST, _('Unknown request type.'))

    def start(self, plan):
        def run():
            try:
                self.worker_factory(self).run(plan, self.job.cancelled)
            except Exception as exc:
                try:
                    self.log(_('Worker failed: ') + self.redactor.text(exc))
                finally:
                    # Without a terminal event the daemon stays busy forever.
                    self.emit('error', code=Code.INTERNAL.value, phase='prepare_disk',
                              message=_('Worker failed; inspect the log.'), retryable=False, log_path=str(LOG))
            finally:
                plan.config['user']['password'] = ''
                plan.config['disk_password'] = ''
                self.redactor.secrets.clear()
        def localized_run():
            with using_language(plan.config.get('language')):
                run()
        thread = threading.Thread(target=localized_run, name='emaki-install-job', daemon=False)
        thread.start()
        return thread


def read_test_mode(path=Path('/proc/cmdline')):
    return 'emaki.test=1' in path.read_text().split()
