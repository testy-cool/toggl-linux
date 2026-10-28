"""Debug diagnostics persist without credentials or changes to CLI stdout."""
import io
import json
import logging
import sys
import threading
from unittest.mock import patch

import pytest
import requests

import toggl_control
import toggl_debug as debug
import toggl_tray as app


def test_stderr_and_uncaught_main_and_worker_exceptions_are_persisted(tmp_path):
    console = io.StringIO()
    with patch.object(sys, 'stderr', console):
        recorder = debug.DebugLog(tmp_path, lambda: [])
        try:
            print('Background sync failed', file=sys.stderr)
            try:
                raise RuntimeError('main diagnostic')
            except RuntimeError:
                sys.excepthook(*sys.exc_info())
            def fail():
                raise ValueError('worker diagnostic')
            worker = threading.Thread(target=fail, name='diagnostic-worker')
            worker.start()
            worker.join()
        finally:
            recorder.close()
    text = (tmp_path / 'debug.log').read_text()
    assert 'Background sync failed' in text
    assert 'Uncaught exception' in text and 'RuntimeError: main diagnostic' in text
    assert 'Uncaught worker exception thread=diagnostic-worker' in text
    assert 'ValueError: worker diagnostic' in text
    assert 'Traceback (most recent call last)' in text
    assert 'Background sync failed' in console.getvalue()


def test_debug_log_is_private_rotates_and_appends_after_restart(tmp_path):
    with patch.object(sys, 'stderr', io.StringIO()):
        recorder = debug.DebugLog(tmp_path, lambda: [], max_bytes=300)
        try:
            for index in range(30):
                debug.LOGGER.info('diagnostic %s %s', index, 'x' * 70)
        finally:
            recorder.close()
        assert (tmp_path / 'debug.log.3').exists()
        assert not (tmp_path / 'debug.log.4').exists()
        assert all(path.stat().st_mode & 0o777 == 0o600 for path in tmp_path.glob('debug.log*'))
        assert tmp_path.stat().st_mode & 0o777 == 0o700
        recorder = debug.DebugLog(tmp_path, lambda: [])
        debug.LOGGER.info('after restart')
        recorder.close()
    text = (tmp_path / 'debug.log').read_text()
    assert 'diagnostic 29' in text and 'after restart' in text


def test_redaction_covers_rotated_logs_stderr_and_exception_tracebacks(tmp_path):
    secrets = ['real-secret-value']
    with patch.object(sys, 'stderr', io.StringIO()):
        recorder = debug.DebugLog(tmp_path, lambda: secrets, max_bytes=400)
        try:
            print('problem real-secret-value', file=sys.stderr)
            secrets.append('new-account-secret')
            try:
                raise RuntimeError('new-account-secret api_token=another-secret Authorization: Bearer bearer-secret')
            except RuntimeError:
                debug.LOGGER.exception('Request failed')
            debug.LOGGER.warning('url=https://user:pass@example.test/test?access_token=query-secret')
        finally:
            recorder.close()
    text = '\n'.join(path.read_text() for path in tmp_path.glob('debug.log*'))
    for secret in secrets + ['another-secret', 'bearer-secret', 'user:pass', 'query-secret']:
        assert secret not in text
    assert '[REDACTED]' in text


def test_api_diagnostics_omit_payload_query_and_authorization(tmp_path):
    response = requests.Response()
    response.status_code = 200
    response._content = b'{"ok":true}'
    with patch.object(sys, 'stderr', io.StringIO()):
        recorder = debug.DebugLog(tmp_path, lambda: [app.api_token])
        try:
            with patch.object(app.requests, 'request', return_value=response):
                assert app._api('POST', '/test', json={'description': 'private entry detail'},
                                params={'sensitive': 'private query detail'}) == {'ok': True}
            with patch.object(app.requests, 'request', side_effect=requests.ConnectionError('private error text')):
                with pytest.raises(requests.ConnectionError):
                    app._api('GET', '/me?private=query')
        finally:
            recorder.close()
    text = (tmp_path / 'debug.log').read_text()
    assert 'API POST /test status=200' in text
    assert 'API GET /me failed=ConnectionError' in text
    for value in ('private entry detail', 'private query detail', 'private error text', '?private='):
        assert value not in text


def test_control_errors_are_logged_without_changing_json_response(tmp_path):
    with patch.object(sys, 'stderr', io.StringIO()):
        recorder = debug.DebugLog(tmp_path, lambda: [app.api_token])
        server = toggl_control.start_server(app)
        try:
            with pytest.raises(toggl_control.ControlError, match='Unknown action'):
                toggl_control.request(app.STATE_DIR, 'unknown', {'description': 'private request body'})
            assert toggl_control.request(app.STATE_DIR, 'snapshot', {})['state'] == app.state
        finally:
            server.shutdown()
            server.server_close()
            recorder.close()
    text = (tmp_path / 'debug.log').read_text()
    assert 'Control action=unknown failed' in text
    assert 'ControlError: Unknown action' in text
    assert 'private request body' not in text
    assert 'action=snapshot' not in text


def test_disk_error_does_not_recurse_through_redirected_stderr(tmp_path):
    console = io.StringIO()
    with patch.object(sys, 'stderr', console):
        recorder = debug.DebugLog(tmp_path, lambda: [])
        try:
            with patch.object(recorder.handler, 'shouldRollover', side_effect=OSError('disk full')):
                print('should not crash', file=sys.stderr)
        finally:
            recorder.close()
    assert console.getvalue().count('could not write debug.log') == 1


def test_logging_leaves_cli_stdout_and_hooks_intact_after_close(tmp_path, capsys):
    previous = (sys.excepthook, threading.excepthook, sys.stderr)
    recorder = debug.DebugLog(tmp_path, lambda: [])
    print(json.dumps({'ok': True}))
    recorder.close()
    assert json.loads(capsys.readouterr().out) == {'ok': True}
    assert (sys.excepthook, threading.excepthook, sys.stderr) == previous


def test_refuses_debug_log_symlink(tmp_path):
    target = tmp_path / 'unrelated-file'
    target.write_text('keep')
    (tmp_path / 'debug.log').symlink_to(target)
    with pytest.raises(OSError):
        debug.DebugLog(tmp_path, lambda: [])
    assert target.read_text() == 'keep'
