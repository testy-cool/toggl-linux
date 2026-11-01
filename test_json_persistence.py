"""Corrupt text files cannot take down local timer recovery."""
import json

import toggl_tray as app


def test_invalid_utf8_primary_uses_backup_and_can_be_repaired(tmp_state_dir):
    path = tmp_state_dir / "sample.json"
    backup = app._backup_path(path)
    path.write_bytes(b"\xff")
    backup.write_text(json.dumps({"source": "backup"}))

    assert app._load_json_with_backup(path, {}) == {"source": "backup"}

    app._atomic_write_text(path, json.dumps({"source": "repaired"}))
    assert app._load_json_with_backup(path, {}) == {"source": "repaired"}
    # A corrupt primary must not replace the last valid recovery copy.
    assert json.loads(backup.read_text()) == {"source": "backup"}


def test_invalid_utf8_timer_and_pending_files_fall_back_then_rewrite():
    state_backup = app._backup_path(app.STATE_FILE)
    app.STATE_FILE.write_bytes(b"\xff")
    state_backup.write_bytes(b"\xfe")
    before = dict(app.state)

    app.load_state()
    assert app.state == before
    app.state["description"] = "Recovered timer"
    app.save_state()
    assert app._load_json_with_backup(app.STATE_FILE, {})["description"] == "Recovered timer"

    pending_backup = app._backup_path(app.PENDING_FILE)
    app.PENDING_FILE.write_bytes(b"\xff")
    pending_backup.write_bytes(b"\xfe")
    assert app._load_pending() == []
    queue = [{"action": "start", "description": "Recovered pending action"}]
    app._save_pending(queue)
    assert app._load_pending() == queue
