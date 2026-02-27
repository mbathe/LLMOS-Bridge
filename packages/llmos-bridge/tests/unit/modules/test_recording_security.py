"""Tests — Recording module security decorator coverage."""
from __future__ import annotations
import pytest
from llmos_bridge.modules.recording.module import RecordingModule
from llmos_bridge.security.decorators import collect_security_metadata


class TestRecordingSecurity:
    def setup_method(self):
        self.module = RecordingModule()

    def test_start_recording_has_audit_trail(self):
        meta = collect_security_metadata(self.module._action_start_recording)
        assert meta.get("audit_level") == "standard"

    def test_stop_recording_has_audit_trail(self):
        meta = collect_security_metadata(self.module._action_stop_recording)
        assert meta.get("audit_level") == "standard"

    def test_replay_has_detailed_audit(self):
        meta = collect_security_metadata(self.module._action_generate_replay_plan)
        assert meta.get("audit_level") == "detailed"

    def test_readonly_actions_have_no_metadata(self):
        for action_name in ["_action_list_recordings", "_action_get_recording"]:
            fn = getattr(self.module, action_name)
            meta = collect_security_metadata(fn)
            assert meta == {}, f"{action_name} should have no security metadata"
