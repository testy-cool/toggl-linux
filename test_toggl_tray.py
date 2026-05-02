"""Tests for toggl_tray — covers pure logic, state persistence, and offline queue."""

import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

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
def reset_state():
    """Reset global state before each test."""
    toggl_tray.state.update({
        "tracking": False,
        "entry_id": None,
        "start_time": None,
        "workspace_id": "ws_123",
        "project_id": None,
        "description": "",
    })
    toggl_tray.icon_ref = None
    toggl_tray.rate_limited_until = 0.0
    yield


@pytest.fixture
def tmp_state_dir(tmp_path):
    """Redirect state/pending files to tmp dir."""
    with patch.object(toggl_tray, "STATE_DIR", tmp_path), \
         patch.object(toggl_tray, "STATE_FILE", tmp_path / "state.json"), \
         patch.object(toggl_tray, "PENDING_FILE", tmp_path / "pending.json"):
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


# ── Offline queue ────────────────────────────────────────────────────────────

class TestOfflineQueue:
    def test_queue_action(self, tmp_state_dir):
        toggl_tray.queue_action("start", description="offline task")
        queue = json.loads((tmp_state_dir / "pending.json").read_text())
        assert len(queue) == 1
        assert queue[0]["action"] == "start"
        assert queue[0]["description"] == "offline task"
        assert "ts" in queue[0]

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


class TestSyncPending:
    def test_empty_queue_returns_zero(self, tmp_state_dir):
        assert toggl_tray.sync_pending() == 0

    @patch.object(toggl_tray, "start_entry", return_value={"id": "new_1"})
    def test_open_start_stays_pending(self, mock_start, tmp_state_dir):
        toggl_tray.queue_action("start", description="synced task")
        remaining = toggl_tray.sync_pending()
        assert remaining == 1
        mock_start.assert_not_called()

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


class TestPendingEntries:
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
    @patch.object(toggl_tray, "api_token", "test_token")
    def test_auth_returns_basic_auth(self):
        auth = toggl_tray._auth()
        assert auth.username == "test_token"
        assert auth.password == "api_token"

    @patch("toggl_tray.requests.request")
    @patch.object(toggl_tray, "api_token", "tok")
    def test_api_success(self, mock_req):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'{"ok": true}'
        mock_resp.json.return_value = {"ok": True}
        mock_req.return_value = mock_resp
        result = toggl_tray._api("GET", "/test")
        assert result == {"ok": True}

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
    @patch.object(toggl_tray, "start_entry", return_value={"id": "e1", "start": "2026-04-28T10:00:00+00:00"})
    @patch.object(toggl_tray, "save_state")
    @patch.object(toggl_tray, "_play_sound")
    def test_start_tracking_online(self, mock_sound, mock_save, mock_start):
        toggl_tray.state["tracking"] = False
        toggl_tray.toggle_tracking()
        assert toggl_tray.state["tracking"] is True
        assert toggl_tray.state["entry_id"] == "e1"
        mock_sound.assert_called_once_with(toggl_tray.SOUND_START)

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


class TestTrayIcon:
    @patch.object(toggl_tray, "_init_icons")
    @patch.object(toggl_tray, "render_icon", return_value="new-icon")
    def test_update_tray_icon_fallback_sets_icon(self, mock_render, mock_init):
        class DummyIcon:
            pass

        icon = DummyIcon()
        toggl_tray.icon_ref = icon
        toggl_tray.update_tray_icon()
        assert icon.icon == "new-icon"
