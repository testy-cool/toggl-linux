"""Shared test setup: fake desktop libraries, a private state folder per test, no network."""
import socket
import sys
from unittest.mock import MagicMock, patch

import pytest

# The tray imports X11, tray and GTK bindings at import time; tests never touch a real desktop.
for name in ("Xlib", "Xlib.X", "Xlib.XK", "Xlib.display", "Xlib.ext", "Xlib.ext.record",
             "Xlib.protocol", "Xlib.protocol.rq", "pystray", "gi", "gi.repository"):
    sys.modules[name] = MagicMock()

import toggl_tray  # noqa: E402
import toggl_task_tree  # noqa: E402

_real_connect = socket.socket.connect


def _local_only_connect(sock, address):
    if sock.family != socket.AF_UNIX:
        raise OSError(f"Tests must not reach the network: {address!r}")
    return _real_connect(sock, address)


def _private_paths(tmp_path):
    """Every path the app can write, pointed into this test's own folder."""
    paths = dict(STATE_DIR=tmp_path, CONFIG_FILE=tmp_path / "config.json", STATE_FILE=tmp_path / "state.json",
                 PENDING_FILE=tmp_path / "pending.json", LEDGER_FILE=tmp_path / "events.jsonl",
                 REQUEST_BUDGET_FILE=tmp_path / "request_budget.json", LOCK_FILE=tmp_path / "toggl-tray.lock",
                 APPLICATIONS_DIR=tmp_path / "applications",
                 DESKTOP_FILE=tmp_path / "applications" / "toggl-tray.desktop",
                 AUTOSTART_DIR=tmp_path / "autostart", AUTOSTART_FILE=tmp_path / "autostart" / "toggl-tray.desktop",
                 CLI_FILE=tmp_path / "bin" / "toggl")
    return [patch.object(toggl_tray, name, value, create=True) for name, value in paths.items()]


@pytest.fixture(autouse=True)
def reset_state(tmp_path):
    """Reset global state before each test, with private files and no network."""
    patches = _private_paths(tmp_path)+[patch.object(socket.socket, "connect", _local_only_connect),
                                      patch.object(toggl_tray, "_play_sound")]
    for p in patches:
        p.start()
    try:
        toggl_tray.state.clear()
        toggl_tray.state.update({
            "tracking": False,
            "entry_id": None,
            "start_time": None,
            "workspace_id": "ws_123",
            "project_id": None,
            "task_id": None,
            "description": "",
        })
        toggl_tray.icon_ref = None
        toggl_tray.timer_window_ref = None
        toggl_tray.native_status_icon_ref = None
        toggl_tray.native_status_menu_ref = None
        toggl_tray.api_token = "tok"
        toggl_tray._restored_remote_id = None
        toggl_tray._startup_scope = None
        toggl_tray.rate_limited_until = 0.0
        toggl_tray.status_message = ""
        toggl_tray._control_entries = []
        toggl_tray._control_entries_date = ""
        toggl_tray._control_entries_context = None
        toggl_tray._today_cache = None
        toggl_tray._today_attempt = None
        toggl_tray._control_warning = ""
        toggl_tray._pending_resolutions = []
        toggl_tray._control_refreshed_at = None
        toggl_tray._control_forced_at = None
        toggl_tray._last_cloud_poll_at = 0.0
        toggl_tray._consecutive_sync_failures = 0
        toggl_tray._sync_failure_notified = False
        toggl_tray._project_cache = None
        toggl_tray._task_cache = None
        toggl_tray._task_capability = "unknown"
        toggl_task_tree._attempts.clear()
        if hasattr(toggl_tray, "_request_budget_lock"):
            toggl_tray._request_budget_lock = toggl_tray.threading.Lock()
        yield
    finally:
        for p in reversed(patches):
            p.stop()


@pytest.fixture
def tmp_state_dir(tmp_path):
    """The private state folder reset_state already points the app at."""
    yield tmp_path
