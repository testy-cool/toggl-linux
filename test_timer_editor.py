import pytest
import test_toggl_tray
import toggl_tray as app
import toggl_timer_window as editor


def test_unchanged_fields_make_no_request():
    state=dict(description="Work",project_id=7,tracking=False)
    assert editor.editor_changes(app,state,"Work","7","")=={}


def test_description_and_project_can_be_corrected_together():
    state=dict(description="Before",project_id=7,tracking=False)
    assert editor.editor_changes(app,state,"After","none","")=={"description":"After","project_id":None}


def test_invalid_start_text_is_rejected_before_saving_any_field():
    state=dict(description="Before",project_id=7,tracking=True,start_time="2026-10-25T10:00:00+00:00")
    with pytest.raises(ValueError,match="HH:MM"):
        editor.editor_changes(app,state,"After","7","bad")
