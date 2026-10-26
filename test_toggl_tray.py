"""Tests for toggl_tray — covers pure logic, state persistence, and offline queue."""

import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import requests

# Patch heavy imports before importing the module
import sys

_mock_xlib = MagicMock()
sys.modules["Xlib"] = _mock_xlib
sys.modules["Xlib.X"] = MagicMock()
sys.modules["Xlib.XK"] = MagicMock()
sys.modules["Xlib.display"] = MagicMock()
sys.modules["Xlib.ext"] = MagicMock()
sys.modules["Xlib.ext.record"] = MagicMock()
sys.modules["Xlib.protocol"] = MagicMock()
sys.modules["Xlib.protocol.rq"] = MagicMock()
sys.modules["pystray"] = MagicMock()
sys.modules["gi"] = MagicMock()
sys.modules["gi.repository"] = MagicMock()

import toggl_tray


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_state(tmp_path):
    """Reset global state before each test."""
    # Every test gets its own state directory, so none can overwrite the user's real files.
    with patch.object(toggl_tray, "STATE_DIR", tmp_path), \
         patch.object(toggl_tray, "STATE_FILE", tmp_path / "state.json"), \
         patch.object(toggl_tray, "PENDING_FILE", tmp_path / "pending.json"), \
         patch.object(toggl_tray, "CONFIG_FILE", tmp_path / "config.json"), \
         patch.object(toggl_tray, "LEDGER_FILE", tmp_path / "events.jsonl"), \
         patch.object(toggl_tray, "REQUEST_BUDGET_FILE", tmp_path / "request_budget.json", create=True):
        toggl_tray.state.update({
            "tracking": False,
            "entry_id": None,
            "start_time": None,
            "workspace_id": "ws_123",
            "project_id": None,
            "description": "",
        })
        toggl_tray.icon_ref = None
        toggl_tray.timer_window_ref = None
        toggl_tray.native_status_icon_ref = None
        toggl_tray.native_status_menu_ref = None
        toggl_tray.api_token = "tok"
        toggl_tray.rate_limited_until = 0.0
        toggl_tray._last_cloud_poll_at = 0.0
        toggl_tray._consecutive_sync_failures = 0
        toggl_tray._sync_failure_notified = False
        toggl_tray._last_notification_at.clear()
        if hasattr(toggl_tray, "_request_budget_lock"):
            toggl_tray._request_budget_lock = toggl_tray.threading.Lock()
        yield


@pytest.fixture
def tmp_state_dir(tmp_path):
    """Redirect state/pending files to tmp dir."""
    with patch.object(toggl_tray, "STATE_DIR", tmp_path), \
         patch.object(toggl_tray, "CONFIG_FILE", tmp_path / "config.json"), \
         patch.object(toggl_tray, "STATE_FILE", tmp_path / "state.json"), \
         patch.object(toggl_tray, "PENDING_FILE", tmp_path / "pending.json"), \
         patch.object(toggl_tray, "LEDGER_FILE", tmp_path / "events.jsonl"), \
         patch.object(toggl_tray, "REQUEST_BUDGET_FILE", tmp_path / "request_budget.json", create=True):
        yield tmp_path


# ── elapsed_str ──────────────────────────────────────────────────────────────

class TestElapsedStr:
    def test_not_tracking(self):
        assert toggl_tray.elapsed_str() == "Not tracking"

    def test_not_tracking_no_start_time(self):
        toggl_tray.state["tracking"] = True
        toggl_tray.state["start_time"] = None
        assert toggl_tray.elapsed_str() == "Not tracking"

    def test_just_started(self):
        now = datetime.now(timezone.utc)
        toggl_tray.state["tracking"] = True
        toggl_tray.state["start_time"] = now.isoformat()
        result = toggl_tray.elapsed_str()
        assert result == "0:00:00"

    def test_with_description(self):
        now = datetime.now(timezone.utc)
        toggl_tray.state["tracking"] = True
        toggl_tray.state["start_time"] = now.isoformat()
        toggl_tray.state["description"] = "Writing tests"
        result = toggl_tray.elapsed_str()
        assert result.startswith("Writing tests — 0:00:0")

    def test_elapsed_time_formatting(self):
        start = datetime.now(timezone.utc) - timedelta(hours=2, minutes=5, seconds=30)
        toggl_tray.state["tracking"] = True
        toggl_tray.state["start_time"] = start.isoformat()
        result = toggl_tray.elapsed_str()
        assert result == "2:05:30"

    def test_long_duration(self):
        start = datetime.now(timezone.utc) - timedelta(hours=12, minutes=0, seconds=1)
        toggl_tray.state["tracking"] = True
        toggl_tray.state["start_time"] = start.isoformat()
        result = toggl_tray.elapsed_str()
        assert result == "12:00:01"


# ── get_tooltip ──────────────────────────────────────────────────────────────

class TestGetTooltip:
    def test_stopped(self):
        assert toggl_tray.get_tooltip() == "Toggl: Stopped"

    def test_tracking(self):
        toggl_tray.state["tracking"] = True
        toggl_tray.state["start_time"] = datetime.now(timezone.utc).isoformat()
        result = toggl_tray.get_tooltip()
        assert result.startswith("Toggl: 0:00:0")

    def test_tracking_with_description(self):
        toggl_tray.state["tracking"] = True
        toggl_tray.state["start_time"] = datetime.now(timezone.utc).isoformat()
        toggl_tray.state["description"] = "Deep work"
        result = toggl_tray.get_tooltip()
        assert "Deep work" in result
        assert result.startswith("Toggl: Deep work")


# ── timer panel state ────────────────────────────────────────────────────────

class TestTimerPanelState:
    def test_stopped_panel_state(self, tmp_state_dir):
        panel = toggl_tray._timer_panel_state()

        assert panel["status"] == "Stopped"
        assert panel["action"] == "Start timer"
        assert panel["elapsed"] == "0:00:00"
        assert panel["display_description"] == "No description"
        assert panel["sync"] == "Toggl sync ready"

    def test_tracking_panel_state(self, tmp_state_dir):
        start = datetime.now(timezone.utc) - timedelta(minutes=5)
        toggl_tray.state["tracking"] = True
        toggl_tray.state["start_time"] = start.isoformat()
        toggl_tray.state["description"] = "Writing"

        panel = toggl_tray._timer_panel_state()

        assert panel["status"] == "Tracking"
        assert panel["action"] == "Stop timer"
        assert panel["description"] == "Writing"
        assert panel["display_description"] == "Writing"
        assert panel["elapsed"].startswith("0:05:")

    def test_panel_state_surfaces_pending_sync(self, tmp_state_dir):
        toggl_tray.queue_action("start", start_time="2026-04-28T09:00:00+00:00")

        panel = toggl_tray._timer_panel_state()

        assert panel["sync"] == "1 local change waiting to sync"


class TestPanelTimerLabel:
    def test_tracking_label_shows_only_elapsed_time(self):
        panel = {
            "tracking": True,
            "elapsed": "0:05:12",
            "description": "Writing release notes",
        }

        assert toggl_tray._panel_timer_label(panel) == "0:05:12"

    def test_description_can_be_shown_without_timer(self):
        panel = {
            "tracking": True,
            "elapsed": "0:05:12",
            "description": "Writing release notes",
        }

        assert toggl_tray._panel_timer_label(
            panel, show_timer=False, show_description=True
        ) == "Writing release notes"

    def test_timer_and_description_can_be_shown_together(self):
        panel = {
            "tracking": True,
            "elapsed": "0:05:12",
            "description": "Writing release notes",
        }

        assert toggl_tray._panel_timer_label(
            panel, show_timer=True, show_description=True
        ) == "0:05:12 · Writing release notes"

    def test_panel_text_can_be_hidden_completely(self):
        panel = {
            "tracking": True,
            "elapsed": "0:05:12",
            "description": "Writing release notes",
        }

        assert toggl_tray._panel_timer_label(
            panel, show_timer=False, show_description=False
        ) == ""

    def test_stopped_label_is_compact(self):
        panel = {"tracking": False, "elapsed": "0:00:00", "description": ""}

        assert toggl_tray._panel_timer_label(panel) == "Stopped"

    def test_setting_is_off_by_default_and_persists(self, tmp_state_dir):
        assert toggl_tray.get_panel_timer_enabled() is False

        toggl_tray.set_panel_timer_enabled(True)

        assert toggl_tray.get_panel_timer_enabled() is True
        config = json.loads((tmp_state_dir / "config.json").read_text())
        assert config["show_timer_in_panel"] is True

    def test_description_setting_is_off_by_default_and_persists(self, tmp_state_dir):
        assert toggl_tray.get_panel_description_enabled() is False

        toggl_tray.set_panel_description_enabled(True)

        assert toggl_tray.get_panel_description_enabled() is True
        config = json.loads((tmp_state_dir / "config.json").read_text())
        assert config["show_description_in_panel"] is True

    def test_native_indicator_label_is_updated(self):
        indicator = MagicMock()
        toggl_tray.icon_ref = type("Icon", (), {"_appindicator": indicator})()
        panel = {
            "tracking": True,
            "elapsed": "1:02:03",
            "description": "Deep work",
        }

        with patch.object(toggl_tray, "get_panel_timer_enabled", return_value=True), \
             patch.object(toggl_tray, "_panel_timer_snapshot", return_value=panel):
            assert toggl_tray._update_panel_timer_label() is True

        indicator.set_label.assert_called_once_with(
            "1:02:03", toggl_tray.PANEL_TIMER_LABEL_GUIDE
        )

    def test_xapp_label_is_preferred_on_cinnamon(self):
        native_icon = MagicMock()
        appindicator = MagicMock()
        toggl_tray.native_status_icon_ref = native_icon
        toggl_tray.icon_ref = type("Icon", (), {"_appindicator": appindicator})()
        panel = {
            "tracking": True,
            "elapsed": "1:02:03",
            "description": "Deep work",
        }

        with patch.object(toggl_tray, "get_panel_timer_enabled", return_value=True), \
             patch.object(toggl_tray, "_panel_timer_snapshot", return_value=panel):
            assert toggl_tray._update_panel_timer_label() is True

        native_icon.set_label.assert_called_once_with("1:02:03")
        appindicator.set_label.assert_not_called()

    def test_panel_label_idle_callback_runs_only_once(self):
        toggl_tray.icon_ref = MagicMock()
        toggl_tray.native_status_icon_ref = MagicMock()
        toggl_tray.GLib.idle_add.reset_mock()

        with patch.object(
            toggl_tray.time,
            "sleep",
            side_effect=[None, RuntimeError("stop update loop")],
        ), pytest.raises(RuntimeError, match="stop update loop"):
            toggl_tray.update_loop()

        idle_callback = toggl_tray.GLib.idle_add.call_args_list[-1].args[0]
        assert idle_callback() is False

    def test_taskbar_title_tracks_timer_and_task(self):
        panel = {
            "tracking": True,
            "elapsed": "0:05:12",
            "description": "Writing release notes",
        }

        assert toggl_tray._timer_window_title(panel) == "0:05:12 — Toggl"


class TestDescriptionDialog:
    def test_ok_response_applies_edited_description(self):
        dialog = MagicMock()
        entry = MagicMock()
        entry.get_text.return_value = "  New description  "
        toggl_tray.Gtk.Dialog.return_value = dialog
        toggl_tray.Gtk.Entry.return_value = entry

        with patch.object(toggl_tray.threading, "Thread") as thread_cls:
            toggl_tray._show_description_dialog()
            response_handler = next(
                call.args[1]
                for call in dialog.connect.call_args_list
                if call.args[0] == "response"
            )
            response_handler(dialog, toggl_tray.Gtk.ResponseType.OK)

        thread_cls.assert_called_once_with(
            target=toggl_tray._apply_description,
            args=("New description",),
            daemon=True,
        )
        thread_cls.return_value.start.assert_called_once()
        dialog.destroy.assert_called_once()


# ── State persistence ────────────────────────────────────────────────────────

class TestStatePersistence:
    def test_save_and_load(self, tmp_state_dir):
        toggl_tray.state["tracking"] = True
        toggl_tray.state["entry_id"] = "entry_456"
        toggl_tray.state["description"] = "test task"
        toggl_tray.save_state()

        saved = json.loads((tmp_state_dir / "state.json").read_text())
        assert saved["tracking"] is True
        assert saved["entry_id"] == "entry_456"
        assert saved["description"] == "test task"

    def test_load_restores_state(self, tmp_state_dir):
        (tmp_state_dir / "state.json").write_text(json.dumps({
            "tracking": True,
            "entry_id": "e789",
            "start_time": "2026-04-28T10:00:00+00:00",
            "workspace_id": "ws_999",
            "description": "loaded",
        }))
        toggl_tray.load_state()
        assert toggl_tray.state["tracking"] is True
        assert toggl_tray.state["entry_id"] == "e789"
        assert toggl_tray.state["description"] == "loaded"

    def test_load_missing_file(self, tmp_state_dir):
        toggl_tray.load_state()
        assert toggl_tray.state["tracking"] is False

    def test_load_corrupt_json(self, tmp_state_dir):
        (tmp_state_dir / "state.json").write_text("not json{{{")
        toggl_tray.load_state()
        assert toggl_tray.state["tracking"] is False

    def test_save_creates_directory(self, tmp_path):
        nested = tmp_path / "a" / "b"
        with patch.object(toggl_tray, "STATE_DIR", nested), \
             patch.object(toggl_tray, "STATE_FILE", nested / "state.json"):
            toggl_tray.save_state()
            assert (nested / "state.json").exists()

    def test_save_state_writes_backup_and_recovers_from_corrupt_primary(self, tmp_state_dir):
        toggl_tray.state["description"] = "first"
        toggl_tray.save_state()
        toggl_tray.state["description"] = "second"
        toggl_tray.save_state()

        backup = tmp_state_dir / "state.json.bak"
        assert backup.exists()
        assert json.loads(backup.read_text())["description"] == "first"

        (tmp_state_dir / "state.json").write_text("broken")
        toggl_tray.state["description"] = ""
        toggl_tray.load_state()
        assert toggl_tray.state["description"] == "first"

    def test_save_state_does_not_overwrite_good_backup_with_corrupt_primary(self, tmp_state_dir):
        toggl_tray.state["description"] = "good backup"
        toggl_tray.save_state()
        (tmp_state_dir / "state.json.bak").write_text((tmp_state_dir / "state.json").read_text())
        (tmp_state_dir / "state.json").write_text("broken")

        toggl_tray.state["description"] = "new primary"
        toggl_tray.save_state()

        assert json.loads((tmp_state_dir / "state.json.bak").read_text())["description"] == "good backup"
        assert json.loads((tmp_state_dir / "state.json").read_text())["description"] == "new primary"


# ── Offline queue ────────────────────────────────────────────────────────────

class TestOfflineQueue:
    def test_queue_action(self, tmp_state_dir):
        toggl_tray.queue_action("start", description="offline task")
        queue = json.loads((tmp_state_dir / "pending.json").read_text())
        assert len(queue) == 1
        assert queue[0]["action"] == "start"
        assert queue[0]["description"] == "offline task"
        assert "ts" in queue[0]

    def test_queue_action_wakes_sync_worker(self, tmp_state_dir):
        wakeup = MagicMock()

        with patch.object(toggl_tray, "_sync_wakeup", wakeup, create=True):
            toggl_tray.queue_action("start", description="offline task")

        wakeup.set.assert_called_once_with()

    def test_queue_multiple_actions(self, tmp_state_dir):
        toggl_tray.queue_action("start", description="task1")
        toggl_tray.queue_action("stop", entry_id="e1")
        queue = json.loads((tmp_state_dir / "pending.json").read_text())
        assert len(queue) == 2
        assert queue[0]["action"] == "start"
        assert queue[1]["action"] == "stop"

    def test_load_empty_queue(self, tmp_state_dir):
        assert toggl_tray._load_pending() == []

    def test_load_corrupt_pending(self, tmp_state_dir):
        (tmp_state_dir / "pending.json").write_text("broken!")
        assert toggl_tray._load_pending() == []

    def test_save_and_load_pending(self, tmp_state_dir):
        items = [{"action": "start", "ts": "2026-04-28T10:00:00+00:00"}]
        toggl_tray._save_pending(items)
        loaded = toggl_tray._load_pending()
        assert loaded == items

    def test_load_pending_recovers_from_backup(self, tmp_state_dir):
        toggl_tray._save_pending([{"action": "start", "description": "first"}])
        toggl_tray._save_pending([{"action": "start", "description": "second"}])
        (tmp_state_dir / "pending.json").write_text("broken")
        loaded = toggl_tray._load_pending()
        assert loaded == [{"action": "start", "description": "first"}]


class TestEventLedger:
    def test_append_event_writes_jsonl_record(self, tmp_state_dir):
        toggl_tray._append_event("toggle_requested", source="test")
        lines = (tmp_state_dir / "events.jsonl").read_text().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["event"] == "toggle_requested"
        assert record["source"] == "test"
        assert "ts" in record

    def test_queue_action_records_pending_event(self, tmp_state_dir):
        toggl_tray.queue_action("start", description="ledger task")
        events = toggl_tray._read_ledger_events()
        assert events[-1]["event"] == "pending_queued"
        assert events[-1]["action"] == "start"
        assert events[-1]["description"] == "ledger task"

    @patch.object(toggl_tray, "start_entry", return_value={"id": "e1", "start": "2026-04-28T10:00:00+00:00"})
    @patch.object(toggl_tray, "_play_sound")
    def test_toggle_tracking_records_request_before_api(self, mock_sound, mock_start, tmp_state_dir):
        toggl_tray.state["tracking"] = False
        toggl_tray.toggle_tracking()
        events = toggl_tray._read_ledger_events()
        assert events[0]["event"] == "toggle_requested"
        assert events[0]["was_tracking"] is False
        assert events[-1]["event"] == "toggle_started"
        assert events[-1]["entry_id"] == "e1"


class TestRequestBudget:
    def test_api_records_request_attempt(self, tmp_state_dir):
        response = MagicMock()
        response.status_code = 200
        response.content = b"{}"
        response.json.return_value = {}

        with patch.object(toggl_tray.requests, "request", return_value=response), \
             patch.object(toggl_tray.time, "time", return_value=1000.0):
            toggl_tray._api("GET", "/me")

        assert toggl_tray._load_request_timestamps(now=1000.0) == [1000.0]
        assert toggl_tray._request_budget_status(now=1000.0) == {
            "used": 1,
            "remaining": 29,
            "limit": 30,
        }

    def test_request_budget_prunes_old_attempts_when_recording(self, tmp_state_dir):
        (tmp_state_dir / "request_budget.json").write_text(json.dumps([999.0, 4500.0, 4599.0]))

        toggl_tray._record_api_request(now=4600.0)

        assert toggl_tray._load_request_timestamps(now=4600.0) == [4500.0, 4599.0, 4600.0]
        assert toggl_tray._request_budget_status(now=4600.0)["remaining"] == 27


class TestSyncPending:
    def test_empty_queue_returns_zero(self, tmp_state_dir):
        assert toggl_tray.sync_pending() == 0

    @patch.object(toggl_tray, "api_token", None)
    @patch.object(toggl_tray, "api_post")
    @patch.object(toggl_tray, "_notify")
    def test_sync_without_token_preserves_queue_without_api_calls(
        self, mock_notify, mock_post, tmp_state_dir
    ):
        toggl_tray.queue_action("start", description="offline",
                                start_time="2026-04-28T09:00:00+00:00")
        toggl_tray.queue_action("stop", stop_time="2026-04-28T10:00:00+00:00")

        remaining = toggl_tray.sync_pending()

        assert remaining == 2
        mock_post.assert_not_called()
        mock_notify.assert_called_once_with("No Toggl API token — pending entries cannot sync")
        assert len(toggl_tray._load_pending()) == 2

    @patch.object(toggl_tray, "fetch_current_entry", return_value=None)
    @patch.object(toggl_tray, "start_entry", return_value={"id": "new_1", "start": "2026-05-11T08:00:00+00:00"})
    @patch.object(toggl_tray, "save_state")
    def test_open_start_synced_to_cloud(self, mock_save, mock_start, mock_fetch, tmp_state_dir):
        toggl_tray.state["tracking"] = True
        toggl_tray.queue_action("start", description="synced task",
                                start_time="2026-05-11T08:00:00+00:00")
        remaining = toggl_tray.sync_pending()
        assert remaining == 0
        mock_start.assert_called_once_with(
            "ws_123",
            description="synced task",
            project_id=None,
            start_time="2026-05-11T08:00:00+00:00",
        )
        assert toggl_tray.state["entry_id"] == "new_1"
        assert toggl_tray.state["start_time"] == "2026-05-11T08:00:00+00:00"

    @patch.object(toggl_tray, "fetch_current_entry", return_value={
        "id": "cloud_1", "start": "2026-05-11T09:02:00+00:00",
        "description": "local task", "project_id": None,
    })
    @patch.object(toggl_tray, "save_state")
    def test_open_start_adopts_cloud_entry(self, mock_save, mock_fetch, tmp_state_dir):
        toggl_tray.state["tracking"] = True
        toggl_tray.queue_action("start", description="local task",
                                start_time="2026-05-11T09:00:00+00:00")
        remaining = toggl_tray.sync_pending()
        assert remaining == 0
        assert toggl_tray.state["tracking"] is True
        assert toggl_tray.state["entry_id"] == "cloud_1"
        assert toggl_tray.state["start_time"] == "2026-05-11T09:02:00+00:00"

    @patch.object(toggl_tray, "fetch_current_entry", return_value={
        "id": "cloud_1", "start": "2026-05-11T09:00:00+00:00",
        "description": "unrelated cloud task", "project_id": None,
    })
    @patch.object(toggl_tray, "start_entry")
    @patch.object(toggl_tray, "save_state")
    @patch.object(toggl_tray, "_notify")
    def test_open_start_keeps_pending_when_cloud_current_does_not_match(
        self, mock_notify, mock_save, mock_start, mock_fetch, tmp_state_dir
    ):
        toggl_tray.state["tracking"] = True
        toggl_tray.state["entry_id"] = None
        toggl_tray.state["start_time"] = "2026-05-11T08:00:00+00:00"
        toggl_tray.queue_action("start", description="local task",
                                start_time="2026-05-11T08:00:00+00:00")

        remaining = toggl_tray.sync_pending()

        assert remaining == 1
        assert toggl_tray.state["entry_id"] is None
        assert toggl_tray.state["start_time"] == "2026-05-11T08:00:00+00:00"
        assert toggl_tray._load_pending()[0]["description"] == "local task"
        mock_start.assert_not_called()
        mock_save.assert_not_called()
        mock_notify.assert_called_once_with("Toggl conflict — local start kept pending")
        assert toggl_tray._read_ledger_events()[-1]["event"] == "sync_conflict"


    @patch.object(toggl_tray, "fetch_current_entry", side_effect=Exception("network down"))
    def test_open_start_stays_pending_on_api_failure(self, mock_fetch, tmp_state_dir):
        toggl_tray.state["tracking"] = True
        toggl_tray.queue_action("start", description="will retry")
        remaining = toggl_tray.sync_pending()
        assert remaining == 1

    @patch.object(toggl_tray, "api_post", return_value={"id": "new_2"})
    def test_sync_start_stop_pair_creates_one_completed_entry(self, mock_post, tmp_state_dir):
        toggl_tray.queue_action("start", description="offline",
                                start_time="2026-04-28T09:00:00+00:00")
        toggl_tray.queue_action("stop", description="offline",
                                stop_time="2026-04-28T10:00:00+00:00")
        toggl_tray.sync_pending()
        payload = mock_post.call_args[0][1]
        assert payload["start"] == "2026-04-28T09:00:00+00:00"
        assert payload["stop"] == "2026-04-28T10:00:00+00:00"
        assert payload["duration"] == 3600
        assert toggl_tray._load_pending() == []

    @patch.object(toggl_tray, "update_entry")
    def test_sync_stop_with_entry_id_preserves_stop_time(self, mock_update, tmp_state_dir):
        toggl_tray.queue_action("stop", entry_id="e_100",
                                start_time="2026-04-28T09:00:00+00:00",
                                stop_time="2026-04-28T10:15:00+00:00",
                                description="offline stop")
        remaining = toggl_tray.sync_pending()
        assert remaining == 0
        mock_update.assert_called_once_with("ws_123", "e_100", {
            "stop": "2026-04-28T10:15:00+00:00",
            "start": "2026-04-28T09:00:00+00:00",
            "duration": 4500,
            "description": "offline stop",
        })

    @patch.object(toggl_tray, "api_post", return_value={"id": "created"})
    def test_sync_stop_creates_complete_entry(self, mock_post, tmp_state_dir):
        toggl_tray.queue_action("stop",
                                start_time="2026-04-28T09:00:00+00:00",
                                stop_time="2026-04-28T10:30:00+00:00",
                                description="completed offline")
        remaining = toggl_tray.sync_pending()
        assert remaining == 0
        call_data = mock_post.call_args[0][1]
        assert call_data["duration"] == 5400  # 1.5 hours
        assert call_data["description"] == "completed offline"

    @patch.object(toggl_tray, "api_post", side_effect=Exception("network error"))
    def test_failed_sync_keeps_in_queue(self, mock_post, tmp_state_dir):
        toggl_tray.queue_action("start", description="will fail",
                                start_time="2026-04-28T09:00:00+00:00")
        toggl_tray.queue_action("stop", stop_time="2026-04-28T10:00:00+00:00")
        remaining = toggl_tray.sync_pending()
        assert remaining == 2
        queue = toggl_tray._load_pending()
        assert queue[0]["description"] == "will fail"

    @patch.object(toggl_tray, "api_post", side_effect=toggl_tray.RateLimitedError(120))
    def test_rate_limited_sync_stops_and_keeps_queue(self, mock_post, tmp_state_dir):
        toggl_tray.queue_action("start", start_time="2026-04-28T09:00:00+00:00")
        toggl_tray.queue_action("stop", stop_time="2026-04-28T10:00:00+00:00")
        assert toggl_tray.sync_pending() == 2
        assert toggl_tray.rate_limited_until > 0

    @patch.object(toggl_tray, "delete_entry")
    def test_delete_404_treated_as_success(self, mock_delete, tmp_state_dir):
        resp = MagicMock()
        resp.status_code = 404
        mock_delete.side_effect = requests.exceptions.HTTPError(response=resp)
        toggl_tray.queue_action("delete", entry_id="gone_entry",
                                workspace_id="ws_123")
        remaining = toggl_tray.sync_pending()
        assert remaining == 0

    @patch.object(toggl_tray, "api_post")
    def test_failed_item_does_not_block_others(self, mock_post, tmp_state_dir):
        error_resp = MagicMock()
        error_resp.status_code = 500
        mock_post.side_effect = [
            requests.exceptions.HTTPError(response=error_resp),
            {"id": "ok"},
        ]
        toggl_tray.queue_action("start", description="will fail",
                                start_time="2026-04-28T09:00:00+00:00")
        toggl_tray.queue_action("stop", stop_time="2026-04-28T10:00:00+00:00")
        toggl_tray.queue_action("start", description="will succeed",
                                start_time="2026-04-28T11:00:00+00:00")
        toggl_tray.queue_action("stop", stop_time="2026-04-28T12:00:00+00:00")
        remaining = toggl_tray.sync_pending()
        assert remaining == 2
        queue = toggl_tray._load_pending()
        assert queue[0]["description"] == "will fail"

    @patch.object(toggl_tray, "api_post")
    def test_4xx_client_error_keeps_billable_item(self, mock_post, tmp_state_dir):
        error_resp = MagicMock()
        error_resp.status_code = 400
        mock_post.side_effect = requests.exceptions.HTTPError(response=error_resp)
        toggl_tray.queue_action("start", description="bad payload",
                                start_time="2026-04-28T09:00:00+00:00")
        toggl_tray.queue_action("stop", stop_time="2026-04-28T10:00:00+00:00")
        remaining = toggl_tray.sync_pending()
        assert remaining == 2
        assert len(toggl_tray._load_pending()) == 2

    @patch.object(toggl_tray, "delete_entry", side_effect=Exception("offline"))
    def test_old_items_are_not_expired(self, mock_delete, tmp_state_dir):
        old_ts = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        toggl_tray._save_pending([{
            "action": "delete", "ts": old_ts,
            "entry_id": "ancient", "workspace_id": "ws_123",
        }])
        remaining = toggl_tray.sync_pending()
        assert remaining == 1
        assert len(toggl_tray._load_pending()) == 1

    @patch.object(toggl_tray, "api_post", return_value={"id": "recreated"})
    @patch.object(toggl_tray, "update_entry")
    def test_missing_remote_stop_recreates_completed_entry(self, mock_update, mock_post, tmp_state_dir):
        error_resp = MagicMock()
        error_resp.status_code = 404
        mock_update.side_effect = requests.exceptions.HTTPError(response=error_resp)
        toggl_tray.queue_action("stop", entry_id="missing_remote",
                                start_time="2026-04-28T09:00:00+00:00",
                                stop_time="2026-04-28T10:00:00+00:00",
                                description="recreate me")

        remaining = toggl_tray.sync_pending()

        assert remaining == 0
        payload = mock_post.call_args[0][1]
        assert payload["start"] == "2026-04-28T09:00:00+00:00"
        assert payload["stop"] == "2026-04-28T10:00:00+00:00"
        assert payload["duration"] == 3600
        assert payload["description"] == "recreate me"


class TestPendingEntries:
    def test_duration_seconds_matches_whole_second_api_validation(self):
        assert toggl_tray._duration_seconds(
            "2026-06-02T06:45:54.804899+00:00",
            "2026-06-02T09:12:33.060614+00:00",
        ) == 8799

    def test_pending_as_entries_pairs_start_stop(self, tmp_state_dir):
        toggl_tray.queue_action("start", description="draft",
                                start_time="2026-04-28T09:00:00+00:00")
        toggl_tray.queue_action("stop", stop_time="2026-04-28T10:30:00+00:00")
        entries = toggl_tray._pending_as_entries(datetime(2026, 4, 28).date())
        assert len(entries) == 1
        assert entries[0]["_offline"] is True
        assert entries[0]["duration"] == 5400
        assert entries[0]["_pending_indexes"] == [0, 1]

    def test_update_pending_entry_edits_queue(self, tmp_state_dir):
        toggl_tray.queue_action("start", description="draft",
                                start_time="2026-04-28T09:00:00+00:00")
        toggl_tray.queue_action("stop", stop_time="2026-04-28T10:00:00+00:00")
        entry = toggl_tray._pending_as_entries(datetime(2026, 4, 28).date())[0]
        assert toggl_tray._update_pending_entry(entry, {
            "description": "edited",
            "start": "2026-04-28T09:15:00+00:00",
            "stop": "2026-04-28T10:45:00+00:00",
        })
        queue = toggl_tray._load_pending()
        assert queue[0]["description"] == "edited"
        assert queue[0]["start_time"] == "2026-04-28T09:15:00+00:00"
        assert queue[1]["description"] == "edited"
        assert queue[1]["stop_time"] == "2026-04-28T10:45:00+00:00"

    def test_delete_pending_local_entry_removes_queue_items(self, tmp_state_dir):
        toggl_tray.queue_action("start", start_time="2026-04-28T09:00:00+00:00")
        toggl_tray.queue_action("stop", stop_time="2026-04-28T10:00:00+00:00")
        entry = toggl_tray._pending_as_entries(datetime(2026, 4, 28).date())[0]
        assert toggl_tray._delete_pending_entry(entry)
        assert toggl_tray._load_pending() == []

    def test_delete_pending_remote_stop_queues_delete(self, tmp_state_dir):
        toggl_tray.queue_action("stop", entry_id="e_100",
                                start_time="2026-04-28T09:00:00+00:00",
                                stop_time="2026-04-28T10:00:00+00:00")
        entry = toggl_tray._pending_as_entries(datetime(2026, 4, 28).date())[0]
        assert toggl_tray._delete_pending_entry(entry)
        queue = toggl_tray._load_pending()
        assert queue == [{
            "action": "delete",
            "ts": queue[0]["ts"],
            "entry_id": "e_100",
            "workspace_id": "ws_123",
        }]


# ── API wrapper ──────────────────────────────────────────────────────────────

class TestApiWrapper:
    @patch.dict("toggl_tray.os.environ", {}, clear=True)
    def test_get_api_base_defaults_to_toggl(self):
        assert toggl_tray.get_api_base() == toggl_tray.DEFAULT_API_BASE

    @patch.dict("toggl_tray.os.environ", {}, clear=True)
    def test_set_api_base_persists_normalized_url(self):
        result = toggl_tray.set_api_base("https://opentickly.example.com/api/v9/")
        assert result == "https://opentickly.example.com/api/v9"
        assert toggl_tray.get_api_base() == "https://opentickly.example.com/api/v9"

    @patch.dict("toggl_tray.os.environ", {"TOGGL_API_BASE": "https://env.example/api/v9/"}, clear=True)
    def test_get_api_base_prefers_environment_override(self):
        toggl_tray.set_api_base("https://config.example/api/v9")
        assert toggl_tray.get_api_base() == "https://env.example/api/v9"

    @patch.dict("toggl_tray.os.environ", {}, clear=True)
    @patch("toggl_tray.subprocess.run")
    def test_get_api_token_falls_back_to_service_key_without_application(self, mock_run):
        missing = MagicMock(returncode=1, stdout="")
        legacy = MagicMock(returncode=0, stdout="legacy_token\n")
        mock_run.side_effect = [missing, legacy]

        assert toggl_tray.get_api_token() == "legacy_token"
        assert mock_run.call_args_list[0][0][0] == [
            "secret-tool", "lookup", "service", "toggl", "username", "api_token", "application", "toggl-tray",
        ]
        assert mock_run.call_args_list[1][0][0] == [
            "secret-tool", "lookup", "service", "toggl", "username", "api_token",
        ]

    @patch.dict("toggl_tray.os.environ", {}, clear=True)
    @patch("toggl_tray.subprocess.run")
    def test_get_api_token_keeps_opentickly_separate_from_toggl(self, mock_run):
        toggl_tray.set_api_base("https://opentickly.example.com/api/v9")
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        assert toggl_tray.get_api_token() is None
        assert mock_run.call_args_list[0][0][0] == [
            "secret-tool", "lookup", "service", "opentickly", "username", "api_token", "application", "toggl-tray",
        ]
        assert mock_run.call_args_list[1][0][0] == [
            "secret-tool", "lookup", "service", "opentickly", "username", "api_token",
        ]
        assert mock_run.call_count == 2

    @patch.dict("toggl_tray.os.environ", {}, clear=True)
    @patch("toggl_tray.subprocess.run")
    def test_store_api_token_uses_opentickly_service_when_configured(self, mock_run):
        toggl_tray.set_api_base("https://opentickly.example.com/api/v9")

        assert toggl_tray.store_api_token("tok")
        assert mock_run.call_args[0][0] == [
            "secret-tool", "store", "--label=OpenTickly API Token",
            "service", "opentickly", "username", "api_token",
            "application", "toggl-tray",
        ]

    @patch.object(toggl_tray, "api_token", "test_token")
    def test_auth_returns_basic_auth(self):
        auth = toggl_tray._auth()
        assert auth.username == "test_token"
        assert auth.password == "api_token"

    @patch("toggl_tray.requests.request")
    @patch.object(toggl_tray, "api_token", "tok")
    def test_api_success(self, mock_req):
        toggl_tray.set_api_base("https://opentickly.example.com/api/v9")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'{"ok": true}'
        mock_resp.json.return_value = {"ok": True}
        mock_req.return_value = mock_resp
        result = toggl_tray._api("GET", "/test")
        assert result == {"ok": True}
        assert mock_req.call_args.args[:2] == ("GET", "https://opentickly.example.com/api/v9/test")

    @patch("toggl_tray.requests.request")
    @patch.object(toggl_tray, "api_token", "tok")
    @patch.object(toggl_tray, "_play_sound")
    def test_api_429_defers_without_retrying(self, mock_sound, mock_req):
        rate_resp = MagicMock()
        rate_resp.status_code = 429
        rate_resp.headers = {"X-Toggl-Quota-Resets-In": "120"}

        mock_req.return_value = rate_resp
        with pytest.raises(toggl_tray.RateLimitedError) as exc:
            toggl_tray._api("GET", "/test")
        assert exc.value.retry_after == 120
        assert mock_req.call_count == 1
        mock_sound.assert_called_once()

    @patch("toggl_tray.requests.request")
    @patch.object(toggl_tray, "api_token", "tok")
    @patch.object(toggl_tray, "_play_sound")
    def test_api_429_bad_header_uses_default_retry_after(self, mock_sound, mock_req):
        rate_resp = MagicMock()
        rate_resp.status_code = 429
        rate_resp.headers = {"X-Toggl-Quota-Resets-In": "bad"}
        mock_req.return_value = rate_resp

        with pytest.raises(toggl_tray.RateLimitedError) as exc:
            toggl_tray._api("GET", "/test")
        assert exc.value.retry_after == toggl_tray.SYNC_INTERVAL_SECONDS
        assert mock_req.call_count == 1

    @patch("toggl_tray.requests.request")
    @patch.object(toggl_tray, "api_token", "tok")
    def test_api_empty_response(self, mock_req):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b""
        mock_req.return_value = mock_resp
        assert toggl_tray._api("DELETE", "/test") is None


# ── toggle_tracking ──────────────────────────────────────────────────────────

class TestToggleTracking:
    @patch.object(toggl_tray, "api_token", None)
    @patch.object(toggl_tray, "start_entry")
    @patch.object(toggl_tray, "save_state")
    @patch.object(toggl_tray, "_notify")
    @patch.object(toggl_tray, "_play_sound")
    @patch.object(toggl_tray, "queue_action")
    def test_start_without_token_queues_without_api_call(
        self, mock_queue, mock_sound, mock_notify, mock_save, mock_start
    ):
        toggl_tray.state["tracking"] = False
        toggl_tray.toggle_tracking()
        assert toggl_tray.state["tracking"] is True
        assert toggl_tray.state["entry_id"] is None
        mock_start.assert_not_called()
        mock_queue.assert_called_once()
        mock_notify.assert_any_call("No Toggl API token — start saved locally")

    @patch.object(toggl_tray, "start_entry", return_value={"id": "e1", "start": "2026-04-28T10:00:00+00:00"})
    @patch.object(toggl_tray, "save_state")
    @patch.object(toggl_tray, "_play_sound")
    def test_start_tracking_online(self, mock_sound, mock_save, mock_start):
        toggl_tray.state["tracking"] = False
        toggl_tray.toggle_tracking()
        assert toggl_tray.state["tracking"] is True
        assert toggl_tray.state["entry_id"] == "e1"
        mock_sound.assert_called_once_with(toggl_tray.SOUND_START)

    @patch.object(toggl_tray, "start_entry", return_value={"id": "e1", "start": "2026-04-28T10:00:00+00:00"})
    @patch.object(toggl_tray, "save_state")
    @patch.object(toggl_tray, "_play_sound")
    @patch.object(toggl_tray, "_prompt_for_description_async")
    @patch.object(toggl_tray, "update_tray_icon")
    def test_start_tracking_without_description_opens_description_prompt(
        self, mock_icon, mock_prompt, mock_sound, mock_save, mock_start
    ):
        toggl_tray.icon_ref = type("Icon", (), {})()
        toggl_tray.state["tracking"] = False
        toggl_tray.state["description"] = ""

        toggl_tray.toggle_tracking()

        mock_prompt.assert_called_once()

    @patch.object(toggl_tray, "start_entry", return_value={"id": "e1", "start": "2026-04-28T10:00:00+00:00"})
    @patch.object(toggl_tray, "save_state")
    @patch.object(toggl_tray, "_play_sound")
    @patch.object(toggl_tray, "_prompt_for_description_async")
    @patch.object(toggl_tray, "update_tray_icon")
    def test_start_from_timer_panel_does_not_open_extra_description_prompt(
        self, mock_icon, mock_prompt, mock_sound, mock_save, mock_start
    ):
        toggl_tray.icon_ref = type("Icon", (), {})()
        toggl_tray.state["tracking"] = False
        toggl_tray.state["description"] = ""

        toggl_tray.toggle_tracking(prompt_for_description=False)

        mock_prompt.assert_not_called()

    @patch.object(toggl_tray, "api_token", None)
    @patch.object(toggl_tray, "stop_entry")
    @patch.object(toggl_tray, "save_state")
    @patch.object(toggl_tray, "_notify")
    @patch.object(toggl_tray, "_play_sound")
    @patch.object(toggl_tray, "queue_action")
    def test_stop_without_token_queues_without_api_call(
        self, mock_queue, mock_sound, mock_notify, mock_save, mock_stop
    ):
        toggl_tray.state["tracking"] = True
        toggl_tray.state["entry_id"] = "e1"
        toggl_tray.state["start_time"] = "2026-04-28T10:00:00+00:00"
        toggl_tray.toggle_tracking()
        assert toggl_tray.state["tracking"] is False
        mock_stop.assert_not_called()
        mock_queue.assert_called_once()
        mock_notify.assert_any_call("No Toggl API token — stop saved locally")

    @patch.object(toggl_tray, "start_entry", side_effect=Exception("offline"))
    @patch.object(toggl_tray, "save_state")
    @patch.object(toggl_tray, "_play_sound")
    @patch.object(toggl_tray, "queue_action")
    def test_start_tracking_offline(self, mock_queue, mock_sound, mock_save, mock_start):
        toggl_tray.state["tracking"] = False
        toggl_tray.toggle_tracking()
        assert toggl_tray.state["tracking"] is True
        assert toggl_tray.state["entry_id"] is None
        mock_queue.assert_called_once()
        assert mock_queue.call_args[0][0] == "start"

    @patch.object(toggl_tray, "stop_entry")
    @patch.object(toggl_tray, "save_state")
    @patch.object(toggl_tray, "_play_sound")
    def test_stop_tracking_online(self, mock_sound, mock_save, mock_stop):
        toggl_tray.state["tracking"] = True
        toggl_tray.state["entry_id"] = "e1"
        toggl_tray.state["start_time"] = "2026-04-28T10:00:00+00:00"
        toggl_tray.toggle_tracking()
        assert toggl_tray.state["tracking"] is False
        assert toggl_tray.state["entry_id"] is None
        mock_sound.assert_called_once_with(toggl_tray.SOUND_STOP)

    @patch.object(toggl_tray, "stop_entry", side_effect=Exception("offline"))
    @patch.object(toggl_tray, "save_state")
    @patch.object(toggl_tray, "_play_sound")
    @patch.object(toggl_tray, "queue_action")
    def test_stop_tracking_offline(self, mock_queue, mock_sound, mock_save, mock_stop):
        toggl_tray.state["tracking"] = True
        toggl_tray.state["entry_id"] = "e1"
        toggl_tray.state["start_time"] = "2026-04-28T10:00:00+00:00"
        toggl_tray.toggle_tracking()
        assert toggl_tray.state["tracking"] is False
        mock_queue.assert_called_once()
        assert mock_queue.call_args[0][0] == "stop"

    @patch.object(toggl_tray, "update_entry")
    @patch.object(toggl_tray, "save_state")
    def test_apply_description_updates_running_cloud_entry(self, mock_save, mock_update):
        toggl_tray.state["tracking"] = True
        toggl_tray.state["entry_id"] = "e1"
        toggl_tray.state["workspace_id"] = "ws_1"

        toggl_tray._apply_description("Focused work")

        assert toggl_tray.state["description"] == "Focused work"
        mock_update.assert_called_once_with("ws_1", "e1", {"description": "Focused work"})

    @patch.object(toggl_tray, "save_state")
    def test_apply_description_updates_open_pending_entry(self, mock_save, tmp_state_dir):
        toggl_tray.state["tracking"] = True
        toggl_tray.state["entry_id"] = None
        toggl_tray.queue_action("start", start_time="2026-04-28T09:00:00+00:00")

        toggl_tray._apply_description("Local work")

        assert toggl_tray.state["description"] == "Local work"
        assert toggl_tray._load_pending()[0]["description"] == "Local work"


# ── start_entry payload ─────────────────────────────────────────────────────

class TestStartEntryPayload:
    @patch.object(toggl_tray, "api_post", return_value={"id": "x"})
    def test_payload_structure(self, mock_post):
        toggl_tray.start_entry("ws_1", description="test", project_id="p_1")
        payload = mock_post.call_args[0][1]
        assert payload["workspace_id"] == "ws_1"
        assert payload["description"] == "test"
        assert payload["project_id"] == "p_1"
        assert payload["created_with"] == "toggl-tray-linux"
        assert payload["duration"] < 0  # negative = running

    @patch.object(toggl_tray, "api_post", return_value={"id": "x"})
    def test_payload_no_project(self, mock_post):
        toggl_tray.start_entry("ws_1")
        payload = mock_post.call_args[0][1]
        assert "project_id" not in payload

    @patch.object(toggl_tray, "api_post", return_value={"id": "x"})
    def test_start_iso(self, mock_post):
        toggl_tray.start_entry("ws_1")
        payload = mock_post.call_args[0][1]
        start = datetime.fromisoformat(payload["start"])
        assert start.tzinfo is not None  # must be timezone-aware

    @patch.object(toggl_tray, "api_post", return_value={"id": "x"})
    def test_start_preserves_supplied_start_time(self, mock_post):
        start_time = "2026-04-28T09:00:00+00:00"
        toggl_tray.start_entry("ws_1", start_time=start_time)
        payload = mock_post.call_args[0][1]
        assert payload["start"] == start_time
        assert payload["duration"] == -int(datetime.fromisoformat(start_time).timestamp())


class TestTrayIcon:
    def test_refresh_uses_the_credential_that_owns_its_cache(self):
        def request(method, path, **kwargs):
            assert kwargs.get("token") == "tok"
            toggl_tray.api_token = "other_account"
            return [{"id": 7, "workspace_id": "ws_123", "name": "Original", "color": "#123456"}]
        with patch.object(toggl_tray, "_api", side_effect=request):
            toggl_tray._refresh_projects()
        assert toggl_tray._cached_projects() == []
        toggl_tray.api_token = "tok"
        assert toggl_tray._cached_projects()[0]["name"] == "Original"

    def test_cloud_workspace_change_uses_the_running_projects_color(self):
        toggl_tray.state.update(workspace_id=100)
        with patch.object(toggl_tray, "_api", return_value=[
            {"id": 7, "workspace_id": 200, "name": "Other workspace", "color": "#123456"},
        ]):
            toggl_tray._refresh_projects()
        with patch.object(toggl_tray, "fetch_current_entry", return_value={
            "id": 9, "workspace_id": 200, "project_id": 7, "description": "Work", "start": "2026-10-25T10:00:00+00:00",
        }):
            toggl_tray._sync_cloud_state()
        assert toggl_tray.state["workspace_id"] == 200
        assert toggl_tray._current_icon_path().endswith("project-123456.png")

    @pytest.fixture(autouse=True)
    def isolate_icons(self, tmp_state_dir):
        with patch.object(toggl_tray, "_icon_path_active", None), \
             patch.object(toggl_tray, "_icon_path_inactive", None), \
             patch.object(toggl_tray, "_project_cache", None, create=True), \
             patch.object(toggl_tray, "_last_project_refresh_attempt", None, create=True), \
             patch.object(toggl_tray, "_last_tray_icon_path", None, create=True):
            yield

    @patch.object(toggl_tray, "_api", return_value=[
        {"id": 7, "workspace_id": "ws_123", "name": "Example", "color": "#e28c1c", "active": True},
    ])
    def test_saved_project_color_tints_running_icon_and_survives_offline_restart(self, mock_get):
        from PIL import Image
        toggl_tray.state.update(tracking=True, project_id=7)
        toggl_tray._refresh_projects()
        path = toggl_tray._current_icon_path()
        with Image.open(path) as im:
            assert im.getpixel((toggl_tray.ICON_SIZE // 2, toggl_tray.ICON_PADDING + 2)) == (226, 140, 28, 255)
            assert im.getpixel((0, 0))[3] == 0
        with patch.object(toggl_tray, "_project_cache", None), \
             patch.object(toggl_tray, "_api", side_effect=requests.ConnectionError):
            assert toggl_tray._current_icon_path() == path
        mock_get.assert_called_once_with("GET", "/me/projects", token="tok")

    @pytest.mark.parametrize("project_id,color,tracking", [(None, "#ff0000", True), (7, "bad", True), (7, "#ff0000", False)])
    def test_missing_invalid_or_stopped_project_uses_default_icon(self, project_id, color, tracking):
        toggl_tray.state.update(tracking=tracking, project_id=project_id)
        with patch.object(toggl_tray, "_api", return_value=[
            {"id": 7, "workspace_id": "ws_123", "name": "Example", "color": color},
        ]):
            toggl_tray._refresh_projects()
        toggl_tray._init_icons()
        expected = toggl_tray._icon_path_active if tracking else toggl_tray._icon_path_inactive
        assert toggl_tray._current_icon_path() == expected

    @patch.object(toggl_tray, "_api", return_value=[])
    def test_project_refresh_respects_cache_account_and_request_budget(self, mock_get):
        toggl_tray._refresh_projects()
        toggl_tray._refresh_projects()
        mock_get.assert_called_once_with("GET", "/me/projects", token="tok")
        with patch.object(toggl_tray, "api_token", "another_account"):
            assert toggl_tray._cached_projects() == []
        with patch.object(toggl_tray, "_request_budget_remaining", return_value=6):
            toggl_tray._refresh_projects(force=True)
        assert mock_get.call_count == 1

    def test_native_and_appindicator_paths_use_project_icon(self):
        with patch.object(toggl_tray, "_current_icon_path", return_value="project.png"):
            native = MagicMock()
            toggl_tray.native_status_icon_ref = native
            toggl_tray.update_tray_icon()
            toggl_tray.GLib.idle_add.assert_called_with(toggl_tray._set_native_status_icon_path, "project.png")
            toggl_tray.native_status_icon_ref = None
            indicator = MagicMock()
            toggl_tray.icon_ref = type("Icon", (), {"_appindicator": indicator})()
            toggl_tray.update_tray_icon()
            indicator.set_icon_full.assert_called_once_with("project.png", "Toggl")

    def test_icon_refresh_runs_once_per_color_change(self):
        with patch.object(toggl_tray, "_current_icon_path", side_effect=["first.png", "first.png", "second.png"]), \
             patch.object(toggl_tray, "update_tray_icon") as update:
            for _ in range(3):
                assert toggl_tray._update_tray_icon_once() is False
            assert update.call_count == 2

    @patch.object(toggl_tray, "_init_icons")
    @patch.object(toggl_tray, "render_icon", return_value="new-icon")
    def test_update_tray_icon_fallback_sets_icon(self, mock_render, mock_init):
        class DummyIcon:
            pass

        icon = DummyIcon()
        toggl_tray.icon_ref = icon
        toggl_tray.update_tray_icon()
        assert icon.icon == "new-icon"


class TestHotkeyListener:
    @patch.object(toggl_tray, "_notify")
    @patch.object(toggl_tray, "_play_sound")
    def test_grab_failure_reports_visible_error(self, mock_sound, mock_notify):
        class FakeRoot:
            def grab_key(self, *_args):
                return None

        class FakeScreen:
            root = FakeRoot()

        class FakeDisplay:
            def set_error_handler(self, handler):
                self.handler = handler

            def screen(self):
                return FakeScreen()

            def keysym_to_keycode(self, _keysym):
                return 42

            def sync(self):
                self.handler(Exception("BadAccess"), None)

        assert toggl_tray._run_hotkey_listener(display_factory=FakeDisplay) is False
        mock_notify.assert_called_once_with("Toggl hotkey unavailable — use tray menu or terminal commands")
        mock_sound.assert_called_once_with(toggl_tray.SOUND_ERROR)


# ── Notification throttling ─────────────────────────────────────────────────

class TestNotificationThrottling:
    def test_same_notification_is_throttled(self):
        assert toggl_tray._notification_allowed("same", now=100.0) is True
        assert toggl_tray._notification_allowed("same", now=101.0) is False
        assert toggl_tray._notification_allowed("same", now=3701.0) is True

    @patch.object(toggl_tray, "_reap")
    @patch.object(toggl_tray.subprocess, "Popen")
    def test_no_notification_reaches_desktop(self, mock_popen, _mock_reap, tmp_state_dir):
        toggl_tray._last_notification_at.clear()
        toggl_tray._notify("Tracking with no description — right-click tray icon to set one")
        toggl_tray._notify(toggl_tray.AUTH_FAILED_SYNC_MESSAGE)
        mock_popen.assert_not_called()

    @patch.object(toggl_tray, "_notify")
    def test_sync_failure_notifies_once_until_success(self, mock_notify):
        toggl_tray._record_sync_failure(Exception("down 1"))
        toggl_tray._record_sync_failure(Exception("down 2"))
        toggl_tray._record_sync_failure(Exception("down 3"))
        toggl_tray._record_sync_failure(Exception("down 4"))
        assert mock_notify.call_count == 1

        toggl_tray._record_sync_success()
        toggl_tray._record_sync_failure(Exception("down 5"))
        toggl_tray._record_sync_failure(Exception("down 6"))
        toggl_tray._record_sync_failure(Exception("down 7"))
        assert mock_notify.call_count == 2


# ── CLI recovery helpers ────────────────────────────────────────────────────

class TestCliRecovery:
    @patch.dict("toggl_tray.os.environ", {}, clear=True)
    def test_set_api_base_clears_provider_specific_ids(self, tmp_state_dir, capsys):
        toggl_tray.state["workspace_id"] = "old_ws"
        toggl_tray.state["project_id"] = "old_project"
        toggl_tray.state["entry_id"] = "old_entry"
        toggl_tray.save_state()
        toggl_tray._save_pending([{
            "action": "start",
            "workspace_id": "old_ws",
            "project_id": "old_project",
            "entry_id": "old_entry",
            "start_time": "2026-04-28T09:00:00+00:00",
        }])

        args = type("Args", (), {"url": "https://opentickly.example.com/api/v9/"})()
        assert toggl_tray._cli_set_api_base(args) == 0

        assert toggl_tray.get_api_base() == "https://opentickly.example.com/api/v9"
        assert toggl_tray.state["workspace_id"] is None
        assert toggl_tray.state["project_id"] is None
        assert toggl_tray.state["entry_id"] is None
        assert toggl_tray._load_pending() == [{
            "action": "start",
            "start_time": "2026-04-28T09:00:00+00:00",
        }]
        assert "Cleared 3 id field" in capsys.readouterr().out

    @patch.object(toggl_tray, "get_api_token", return_value="tok")
    @patch.object(toggl_tray, "api_post", return_value={"id": 101})
    @patch.object(toggl_tray, "_sync_cloud_state")
    def test_cli_sync_clears_pending_primary_and_backup(
        self, mock_cloud, mock_post, mock_token, tmp_state_dir
    ):
        toggl_tray.state["workspace_id"] = "ws_1"
        toggl_tray.save_state()
        toggl_tray.queue_action("start", start_time="2026-04-28T09:00:00+00:00")
        toggl_tray.queue_action("stop", stop_time="2026-04-28T10:00:00+00:00")

        assert toggl_tray._cli_sync(type("Args", (), {})()) == 0

        mock_post.assert_called_once()
        assert not (tmp_state_dir / "pending.json").exists()
        assert not (tmp_state_dir / "pending.json.bak").exists()

    @patch.object(toggl_tray, "get_api_token", return_value=None)
    def test_set_start_updates_running_pending_entry(self, mock_token, tmp_state_dir):
        toggl_tray.state["tracking"] = True
        toggl_tray.state["start_time"] = "2026-04-28T09:00:00+00:00"
        toggl_tray.save_state()
        toggl_tray.queue_action("start", start_time="2026-04-28T09:00:00+00:00")

        args = type("Args", (), {"time": "08:30"})()
        assert toggl_tray._cli_set_start(args) == 0
        assert "08:30:00" in toggl_tray.state["start_time"]
        queue = toggl_tray._load_pending()
        assert "08:30:00" in queue[0]["start_time"]

    @patch.object(toggl_tray, "get_api_token", return_value="tok")
    @patch.object(toggl_tray, "fetch_current_entry", return_value={
        "id": "cloud_1",
        "start": "2026-04-28T10:00:00+00:00",
        "description": "cloud task",
    })
    def test_doctor_reports_local_and_cloud_health(self, mock_current, mock_token, capsys, tmp_state_dir):
        toggl_tray.state["tracking"] = True
        toggl_tray.state["entry_id"] = "local_1"
        toggl_tray.state["start_time"] = "2026-04-28T09:00:00+00:00"
        toggl_tray.save_state()
        toggl_tray.queue_action("start", description="pending task")
        toggl_tray._append_event("toggle_requested")
        toggl_tray._record_api_request(now=1000.0)

        args = type("Args", (), {"cloud": True})()
        with patch.object(toggl_tray.time, "time", return_value=1000.0):
            assert toggl_tray._cli_doctor(args) == 0
        out = capsys.readouterr().out
        assert "API token: yes" in out
        assert "Tracking: yes" in out
        assert "Pending actions: 1" in out
        assert "Ledger events: 2" in out
        assert "Request budget: 1/30 used, 29 remaining" in out
        assert "Cloud current: yes (cloud_1)" in out

    @patch.object(toggl_tray, "get_api_token", return_value=None)
    def test_doctor_without_cloud_does_not_fetch_current(self, mock_token, capsys, tmp_state_dir):
        args = type("Args", (), {"cloud": False})()
        assert toggl_tray._cli_doctor(args) == 0
        out = capsys.readouterr().out
        assert "Cloud current: skipped (use --cloud)" in out

    @patch.object(toggl_tray, "get_api_token", return_value="tok")
    @patch.object(toggl_tray, "fetch_entries_for_date")
    def test_audit_reports_daily_totals_blank_descriptions_and_gaps(
        self, mock_fetch, mock_token, capsys, tmp_state_dir
    ):
        mock_fetch.return_value = [
            {
                "id": "one",
                "start": "2026-04-28T09:00:00+00:00",
                "stop": "2026-04-28T10:00:00+00:00",
                "duration": 3600,
                "description": "",
            },
            {
                "id": "two",
                "start": "2026-04-28T13:30:00+00:00",
                "stop": "2026-04-28T14:00:00+00:00",
                "duration": 1800,
                "description": "work",
            },
        ]
        toggl_tray.queue_action("start", description="local",
                                start_time="2026-04-28T15:00:00+00:00")
        toggl_tray.queue_action("stop", description="local",
                                stop_time="2026-04-28T16:00:00+00:00")

        args = type("Args", (), {
            "date_from": "2026-04-28",
            "date_to": "2026-04-28",
            "local_only": False,
            "gap_minutes": 120,
        })()
        assert toggl_tray._cli_audit(args) == 0
        out = capsys.readouterr().out
        assert "2026-04-28: 2:30:00 total, 3 entries, 1 blank, 1 local pending, 1 large gaps" in out
        assert "Total: 2:30:00" in out

    @patch.object(toggl_tray, "get_api_token", return_value=None)
    def test_audit_rejects_reversed_range(self, mock_token, capsys, tmp_state_dir):
        args = type("Args", (), {
            "date_from": "2026-04-29",
            "date_to": "2026-04-28",
            "local_only": True,
            "gap_minutes": 120,
        })()
        assert toggl_tray._cli_audit(args) == 2
        assert "Start date must be before or equal to end date." in capsys.readouterr().err

    def test_parser_uses_installed_cli_name(self):
        assert toggl_tray._build_cli_parser().prog == "toggl"


class TestDesktopInstall:
    def test_desktop_entry_uses_repo_script(self, tmp_state_dir):
        text = toggl_tray._desktop_entry_text()
        assert "Name=Toggl Tray" in text
        assert "toggl_tray.py" in text
        assert " run" in text
        assert "Terminal=false" in text

    def test_write_desktop_file(self, tmp_path, tmp_state_dir):
        path = tmp_path / "toggl-tray.desktop"
        toggl_tray._write_desktop_file(path)
        text = path.read_text()
        assert text.startswith("[Desktop Entry]")
        assert path.stat().st_mode & 0o111

    def test_write_cli_launcher_uses_app_interpreter_and_forwards_arguments(
        self, tmp_path, tmp_state_dir
    ):
        path = tmp_path / "toggl"

        toggl_tray._write_cli_launcher(path)

        text = path.read_text()
        assert text.startswith("#!/bin/sh\n")
        assert str(toggl_tray._launcher_python()) in text
        assert str(Path(toggl_tray.__file__).resolve()) in text
        assert '"$@"' in text
        assert path.stat().st_mode & 0o111

    @patch.object(toggl_tray, "_refresh_desktop_database")
    def test_install_app_installs_cli_launcher(
        self, mock_refresh, tmp_path, tmp_state_dir
    ):
        desktop_file = tmp_path / "applications" / "toggl-tray.desktop"
        cli_file = tmp_path / "bin" / "toggl"
        args = type("Args", (), {"autostart": False})()

        with patch.object(toggl_tray, "DESKTOP_FILE", desktop_file), \
             patch.object(toggl_tray, "CLI_FILE", cli_file):
            assert toggl_tray._cli_install_app(args) == 0

        assert cli_file.exists()
        assert cli_file.stat().st_mode & 0o111

    @patch.object(toggl_tray, "_refresh_desktop_database")
    def test_uninstall_app_removes_cli_launcher(
        self, mock_refresh, tmp_path, tmp_state_dir
    ):
        desktop_file = tmp_path / "applications" / "toggl-tray.desktop"
        autostart_file = tmp_path / "autostart" / "toggl-tray.desktop"
        cli_file = tmp_path / "bin" / "toggl"
        for path in (desktop_file, autostart_file, cli_file):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("installed")

        with patch.object(toggl_tray, "DESKTOP_FILE", desktop_file), \
             patch.object(toggl_tray, "AUTOSTART_FILE", autostart_file), \
             patch.object(toggl_tray, "CLI_FILE", cli_file):
            assert toggl_tray._cli_uninstall_app(type("Args", (), {})()) == 0

        assert not cli_file.exists()


class TestTrayMenu:
    def test_menu_exposes_diagnostics_actions(self):
        toggl_tray.pystray.MenuItem.reset_mock()

        toggl_tray.build_menu()

        first_item = toggl_tray.pystray.MenuItem.call_args_list[0]
        assert first_item.args[0] == "Open timer"
        assert first_item.kwargs["default"] is True

        labels = [call.args[0] for call in toggl_tray.pystray.MenuItem.call_args_list]
        assert "Show timer in panel" in labels
        assert "Show description in panel" in labels
        assert "Start tracking" in labels
        assert "Edit description..." in labels
        assert "Doctor" in labels
        assert "Audit today" in labels


# ── Health check ────────────────────────────────────────────────────────────

class TestHealthCheck:
    @patch.object(toggl_tray, "_load_pending", return_value=[])
    @patch.object(toggl_tray, "fetch_current_entry", return_value={
        "id": "cloud_1", "start": "2026-05-11T09:00:00+00:00",
        "description": "", "project_id": None,
    })
    @patch.object(toggl_tray, "save_state")
    def test_recovers_entry_id_from_cloud(self, mock_save, mock_fetch, mock_pending):
        toggl_tray.state["tracking"] = True
        toggl_tray.state["entry_id"] = None
        toggl_tray._health_check()
        assert toggl_tray.state["entry_id"] == "cloud_1"
        assert toggl_tray.state["start_time"] == "2026-05-11T09:00:00+00:00"

    @patch.object(toggl_tray, "_load_pending", return_value=[])
    @patch.object(toggl_tray, "fetch_current_entry", return_value=None)
    @patch.object(toggl_tray, "save_state")
    @patch.object(toggl_tray, "_notify")
    @patch.object(toggl_tray, "_play_sound")
    def test_stops_phantom_local_timer(self, mock_sound, mock_notify, mock_save, mock_fetch, mock_pending):
        toggl_tray.state["tracking"] = True
        toggl_tray.state["entry_id"] = None
        toggl_tray._health_check()
        assert toggl_tray.state["tracking"] is False
        mock_notify.assert_called_once()
        mock_sound.assert_called_once_with(toggl_tray.SOUND_ERROR)

    @patch.object(toggl_tray, "fetch_current_entry")
    def test_skips_when_entry_id_present(self, mock_fetch):
        toggl_tray.state["tracking"] = True
        toggl_tray.state["entry_id"] = "e1"
        toggl_tray._health_check()
        mock_fetch.assert_not_called()

    @patch.object(toggl_tray, "fetch_current_entry")
    def test_skips_when_not_tracking(self, mock_fetch):
        toggl_tray.state["tracking"] = False
        toggl_tray._health_check()
        mock_fetch.assert_not_called()

    @patch.object(toggl_tray, "fetch_current_entry")
    @patch.object(toggl_tray, "_load_pending", return_value=[{"action": "start"}])
    def test_skips_when_pending_items_exist(self, mock_pending, mock_fetch):
        toggl_tray.state["tracking"] = True
        toggl_tray.state["entry_id"] = None
        toggl_tray._health_check()
        mock_fetch.assert_not_called()


class TestSyncCycle:
    @pytest.mark.parametrize("lookup_token", [None, "stale_token"])
    def test_keyring_retry_preserves_token_set_while_lookup_runs(self, lookup_token, tmp_state_dir):
        toggl_tray.api_token = None

        def lookup():
            toggl_tray.api_token = "manually_entered_token"
            return lookup_token

        with patch.object(toggl_tray, "get_api_token", side_effect=lookup):
            toggl_tray._run_sync_cycle(now=1000.0)

        assert toggl_tray.api_token == "manually_entered_token"

    @patch.object(toggl_tray, "get_api_token", return_value="recovered_token")
    @patch.object(toggl_tray, "_init_workspace")
    @patch.object(toggl_tray, "_notify")
    def test_workspace_recovery_waits_for_request_budget(
        self, mock_notify, mock_init, mock_token, tmp_state_dir
    ):
        toggl_tray.api_token = None
        toggl_tray.state["workspace_id"] = None
        toggl_tray._save_request_timestamps([time.time()] * toggl_tray.REQUEST_BUDGET_LIMIT)

        toggl_tray._run_sync_cycle(now=1000.0)

        mock_init.assert_not_called()
        mock_notify.assert_called_once_with(toggl_tray.REQUEST_BUDGET_SYNC_MESSAGE)
        assert toggl_tray._request_budget_status()["used"] == toggl_tray.REQUEST_BUDGET_LIMIT

    @patch.object(toggl_tray, "get_api_token", side_effect=[None, "recovered_token"])
    @patch.object(toggl_tray, "fetch_current_entry", return_value=None)
    @patch.object(toggl_tray, "start_entry", return_value={
        "id": "recovered_entry", "start": "2026-10-24T11:00:00+00:00",
    })
    @patch.object(toggl_tray, "_notify")
    def test_missing_token_retries_and_syncs_saved_start(
        self, mock_notify, mock_start, mock_current, mock_token, tmp_state_dir
    ):
        toggl_tray.api_token = None
        toggl_tray.state["tracking"] = True
        toggl_tray.state["start_time"] = "2026-10-24T11:00:00+00:00"
        toggl_tray.queue_action("start", description="saved task",
                                start_time=toggl_tray.state["start_time"])
        saved_queue = toggl_tray._load_pending()

        toggl_tray._run_sync_cycle(now=1000.0)

        assert toggl_tray._load_pending() == saved_queue
        mock_start.assert_not_called()
        mock_current.assert_not_called()
        mock_notify.assert_called_once_with(toggl_tray.NO_TOKEN_SYNC_MESSAGE)

        toggl_tray._run_sync_cycle(now=1300.0)

        assert toggl_tray.api_token == "recovered_token"
        assert not toggl_tray.PENDING_FILE.exists()
        assert toggl_tray.state["entry_id"] == "recovered_entry"
        assert toggl_tray.state["start_time"] == "2026-10-24T11:00:00+00:00"
        mock_start.assert_called_once_with(
            "ws_123", description="saved task", project_id=None,
            start_time="2026-10-24T11:00:00+00:00",
        )
        assert mock_token.call_count == 2

    @patch.object(toggl_tray, "get_api_token", return_value="recovered_token")
    @patch.object(toggl_tray, "fetch_me", side_effect=[
        requests.exceptions.ConnectionError("offline"), {"default_workspace_id": "new_ws"},
    ])
    @patch.object(toggl_tray, "fetch_current_entry", return_value=None)
    @patch.object(toggl_tray, "start_entry", return_value={
        "id": "recovered_entry", "start": "2026-10-24T11:00:00+00:00",
    })
    def test_recovered_token_retries_workspace_before_syncing(
        self, mock_start, mock_current, mock_me, mock_token, tmp_state_dir
    ):
        toggl_tray.api_token = None
        toggl_tray.state["workspace_id"] = None
        toggl_tray.queue_action("start", description="saved task",
                                start_time="2026-10-24T11:00:00+00:00")
        saved_queue = toggl_tray._load_pending()

        toggl_tray._run_sync_cycle(now=1000.0)

        assert toggl_tray._load_pending() == saved_queue
        mock_start.assert_not_called()

        toggl_tray._run_sync_cycle(now=1300.0)

        assert toggl_tray.state["workspace_id"] == "new_ws"
        assert toggl_tray.state["entry_id"] == "recovered_entry"
        assert not toggl_tray.PENDING_FILE.exists()
        assert mock_start.call_args.args[0] == "new_ws"
        assert mock_me.call_count == 2
        mock_token.assert_called_once_with()

    @patch.object(toggl_tray, "get_api_token", return_value="recovered_token")
    @patch.object(toggl_tray, "fetch_current_entry", return_value=None)
    def test_idle_cycle_recovers_token_without_pending_entries(
        self, mock_current, mock_token, tmp_state_dir
    ):
        toggl_tray.api_token = None

        toggl_tray._run_sync_cycle(now=999999.0)

        assert toggl_tray.api_token == "recovered_token"
        mock_current.assert_called_once_with()

    @patch.object(toggl_tray, "get_api_token")
    def test_loaded_token_does_not_repeat_keyring_lookup(self, mock_token, tmp_state_dir):
        toggl_tray._run_sync_cycle(now=1000.0)

        mock_token.assert_not_called()

    @patch.object(toggl_tray, "_run_sync_cycle")
    def test_sync_loop_runs_cycle_before_waiting(self, mock_cycle):
        wakeup = MagicMock()
        wakeup.wait.side_effect = StopIteration

        with patch.object(toggl_tray, "_sync_wakeup", wakeup, create=True), \
             patch.object(toggl_tray.time, "sleep", side_effect=StopIteration), \
             pytest.raises(StopIteration):
            toggl_tray.sync_loop()

        mock_cycle.assert_called_once_with()

    @patch.object(toggl_tray, "_load_pending", return_value=[])
    @patch.object(toggl_tray, "_sync_cloud_state")
    @patch.object(toggl_tray, "_health_check")
    @patch.object(toggl_tray, "_record_sync_success")
    def test_idle_cycle_does_not_poll_cloud_before_interval(
        self, mock_success, mock_health, mock_cloud, mock_pending
    ):
        toggl_tray._last_cloud_poll_at = 1000.0
        toggl_tray._run_sync_cycle(now=1100.0)
        mock_cloud.assert_not_called()
        mock_health.assert_called_once()
        mock_success.assert_called_once()

    @patch.object(toggl_tray, "_load_pending", return_value=[])
    @patch.object(toggl_tray, "_sync_cloud_state")
    @patch.object(toggl_tray, "_health_check")
    @patch.object(toggl_tray, "_record_sync_success")
    def test_idle_cycle_polls_cloud_when_interval_elapsed(
        self, mock_success, mock_health, mock_cloud, mock_pending
    ):
        toggl_tray._last_cloud_poll_at = 1000.0
        toggl_tray._run_sync_cycle(now=1000.0 + toggl_tray.CLOUD_POLL_INTERVAL_SECONDS + 1)
        mock_cloud.assert_called_once()
        mock_health.assert_called_once()
        mock_success.assert_called_once()

    @patch.object(toggl_tray, "_load_pending", return_value=[{"action": "start"}])
    @patch.object(toggl_tray, "sync_pending", return_value=1)
    @patch.object(toggl_tray, "_sync_cloud_state")
    @patch.object(toggl_tray, "_health_check")
    @patch.object(toggl_tray, "_record_sync_success")
    def test_pending_cycle_prioritizes_pending_without_cloud_poll(
        self, mock_success, mock_health, mock_cloud, mock_sync, mock_pending
    ):
        toggl_tray._last_cloud_poll_at = 0.0
        toggl_tray._run_sync_cycle(now=999999.0)
        mock_sync.assert_called_once()
        mock_cloud.assert_not_called()
        mock_health.assert_called_once()
        mock_success.assert_called_once()

    @patch.object(toggl_tray, "_load_pending", return_value=[])
    @patch.object(toggl_tray, "_sync_cloud_state")
    @patch.object(toggl_tray, "_health_check")
    @patch.object(toggl_tray, "_record_sync_success")
    def test_idle_cycle_keeps_reserved_budget_for_user_actions(
        self, mock_success, mock_health, mock_cloud, mock_pending
    ):
        toggl_tray._last_cloud_poll_at = 0.0
        with patch.object(toggl_tray, "_request_budget_remaining", return_value=6, create=True):
            toggl_tray._run_sync_cycle(now=999999.0)
        mock_cloud.assert_not_called()
        mock_health.assert_called_once()
        mock_success.assert_called_once()
