"""Private rotating diagnostics for the tray process; the event ledger is separate."""
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import sys
import threading
import traceback

LOGGER = logging.getLogger('toggl_tray')
LOGGER.addHandler(logging.NullHandler())
LOGGER.propagate = False
MAX_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 3


class RedactingFormatter(logging.Formatter):
    def __init__(self, secrets):
        super().__init__('%(asctime)s %(levelname)s [%(threadName)s] %(message)s')
        self.secrets = secrets

    def format(self, record):
        text = super().format(record)
        for value in sorted((str(value) for value in self.secrets() if value), key=len, reverse=True):
            text = text.replace(value, '[REDACTED]')
        # Also cover labelled credentials in third-party exception messages.
        text = re.sub(r'(?i)(authorization[\"\']?\s*[:=]\s*[\"\']?(?:Bearer\s+|Basic\s+)?)[^\s,;\"\'}]+',
                      r'\1[REDACTED]', text)
        text = re.sub(r'(?i)((?:api[_-]?token|api[_-]?key|password|access_token)\s*[\"\']?\s*[:=]\s*[\"\']?)[^\s\"\'&,;]+',
                      r'\1[REDACTED]', text)
        text = re.sub(r'(https?://)[^\s/@]+:[^\s/@]+@', r'\1[REDACTED]@', text)
        return text


class PrivateRotatingHandler(RotatingFileHandler):
    def __init__(self, path, fallback, **kwargs):
        self.fallback = fallback
        super().__init__(path, encoding='utf-8', **kwargs)

    def _open(self):
        fd = os.open(self.baseFilename, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        return os.fdopen(fd, 'a', encoding='utf-8', errors='backslashreplace')

    def handleError(self, record):
        # Logging failures must not recurse through the captured stderr stream.
        try:
            self.fallback.write('Toggl Tray: could not write debug.log\n')
            self.fallback.flush()
        except Exception:
            pass


class DiagnosticStream:
    def __init__(self, original):
        self.original = original
        self.local = threading.local()

    def write(self, text):
        self.original.write(text)
        pending = getattr(self.local, 'pending', '') + text
        while '\n' in pending:
            line, pending = pending.split('\n', 1)
            if line.strip():
                LOGGER.warning('stderr: %s', line)
        if len(pending) > 8192:
            LOGGER.warning('stderr: %s', pending)
            pending = ''
        self.local.pending = pending
        return len(text)

    def flush(self):
        pending = getattr(self.local, 'pending', '')
        if pending.strip():
            LOGGER.warning('stderr: %s', pending)
        self.local.pending = ''
        self.original.flush()

    def __getattr__(self, name):
        return getattr(self.original, name)


class DebugLog:
    def __init__(self, state_dir, secrets, max_bytes=MAX_BYTES):
        directory = Path(state_dir)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
        self.path = directory / 'debug.log'
        for path in [self.path] + [directory / f'debug.log.{index}' for index in range(1, BACKUP_COUNT + 1)]:
            if path.exists() and not path.is_symlink():
                path.chmod(0o600)
        self.stderr = sys.stderr
        self.handler = PrivateRotatingHandler(self.path, self.stderr, maxBytes=max_bytes,
                                               backupCount=BACKUP_COUNT)
        self.handler.setFormatter(RedactingFormatter(secrets))
        self.previous_level = LOGGER.level
        LOGGER.setLevel(logging.DEBUG)
        LOGGER.addHandler(self.handler)
        self.stream = DiagnosticStream(self.stderr)
        sys.stderr = self.stream
        self.exception_hook, self.thread_hook = sys.excepthook, threading.excepthook
        sys.excepthook = self._exception
        threading.excepthook = self._thread_exception
        self.glib_handlers = []
        LOGGER.info('Debug logging enabled pid=%s path=%s', os.getpid(), self.path)

    def capture_glib(self, glib):
        """Route native GTK/GLib warnings into the same persistent log."""
        self.glib = glib
        for domain in ('Gtk', 'Gdk', 'GLib', 'GLib-GObject'):
            handler_id = glib.log_set_handler(domain, glib.LogLevelFlags.LEVEL_MASK
                                              | glib.LogLevelFlags.FLAG_FATAL
                                              | glib.LogLevelFlags.FLAG_RECURSION,
                                              self._glib_message, None)
            self.glib_handlers.append((domain, handler_id))

    def _glib_message(self, domain, level, message, _data):
        LOGGER.warning('%s: %s', domain, message)

    def _exception(self, kind, value, tb):
        LOGGER.critical('Uncaught exception', exc_info=(kind, value, tb))
        traceback.print_exception(kind, value, tb, file=self.stderr)

    def _thread_exception(self, args):
        LOGGER.critical('Uncaught worker exception thread=%s', args.thread.name if args.thread else 'unknown',
                        exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
        traceback.print_exception(args.exc_type, args.exc_value, args.exc_traceback, file=self.stderr)

    def close(self):
        self.stream.flush()
        if sys.stderr is self.stream:
            sys.stderr = self.stderr
        if sys.excepthook == self._exception:
            sys.excepthook = self.exception_hook
        if threading.excepthook == self._thread_exception:
            threading.excepthook = self.thread_hook
        for domain, handler_id in self.glib_handlers:
            self.glib.log_remove_handler(domain, handler_id)
        self.glib_handlers.clear()
        LOGGER.removeHandler(self.handler)
        self.handler.close()
        LOGGER.setLevel(self.previous_level)
