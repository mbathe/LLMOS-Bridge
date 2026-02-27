"""Tests — Word module security decorator coverage."""
from __future__ import annotations

import pytest

from llmos_bridge.modules.word.module import WordModule
from llmos_bridge.security.decorators import collect_security_metadata


class TestWordSecurity:
    def setup_method(self):
        self.module = WordModule()

    def test_create_document_requires_write_permission(self):
        meta = collect_security_metadata(self.module._action_create_document)
        assert "filesystem.write" in meta.get("permissions", [])

    def test_write_paragraph_requires_write_permission(self):
        meta = collect_security_metadata(self.module._action_write_paragraph)
        assert "filesystem.write" in meta.get("permissions", [])

    def test_save_document_has_audit_trail(self):
        meta = collect_security_metadata(self.module._action_save_document)
        assert meta.get("audit_level") == "standard"
        assert "filesystem.write" in meta.get("permissions", [])

    def test_delete_paragraph_is_sensitive(self):
        meta = collect_security_metadata(self.module._action_delete_paragraph)
        assert meta.get("risk_level") == "medium"
        assert "filesystem.write" in meta.get("permissions", [])

    def test_insert_table_requires_write_permission(self):
        meta = collect_security_metadata(self.module._action_insert_table)
        assert "filesystem.write" in meta.get("permissions", [])

    def test_insert_image_requires_write_permission(self):
        meta = collect_security_metadata(self.module._action_insert_image)
        assert "filesystem.write" in meta.get("permissions", [])

    def test_find_replace_requires_write_permission(self):
        meta = collect_security_metadata(self.module._action_find_replace)
        assert "filesystem.write" in meta.get("permissions", [])

    def test_readonly_actions_have_no_permissions(self):
        readonly_actions = [
            "_action_open_document",
            "_action_read_document",
            "_action_list_paragraphs",
            "_action_list_tables",
            "_action_extract_text",
            "_action_count_words",
            "_action_get_document_meta",
        ]
        for action_name in readonly_actions:
            fn = getattr(self.module, action_name)
            meta = collect_security_metadata(fn)
            assert "permissions" not in meta, f"{action_name} should not have permissions"
