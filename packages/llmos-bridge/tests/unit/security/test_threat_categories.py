"""Unit tests -- ThreatCategory and ThreatCategoryRegistry.

Tests cover:
  - ThreatCategory dataclass creation and to_dict() serialisation
  - Registry register / unregister lifecycle
  - Registry get() for existing and missing categories
  - list_all() vs list_enabled() when some categories are disabled
  - disable() / enable() for existing and missing categories
  - register_builtins() populates the 7 built-in categories
  - BUILTIN_CATEGORIES has correct IDs and threat types
  - to_dict_list() output format
  - Custom (non-builtin) categories
  - Overwrite behaviour (registering same ID twice)
"""

from __future__ import annotations

import pytest

from llmos_bridge.security.intent_verifier import ThreatType
from llmos_bridge.security.threat_categories import (
    BUILTIN_CATEGORIES,
    ThreatCategory,
    ThreatCategoryRegistry,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _custom_category(
    cat_id: str = "custom_cat",
    name: str = "Custom Category",
    description: str = "A custom threat category for testing.",
    threat_type: ThreatType = ThreatType.CUSTOM,
    enabled: bool = True,
    builtin: bool = False,
) -> ThreatCategory:
    return ThreatCategory(
        id=cat_id,
        name=name,
        description=description,
        threat_type=threat_type,
        enabled=enabled,
        builtin=builtin,
    )


# ---------------------------------------------------------------------------
# 1. ThreatCategory creation and to_dict()
# ---------------------------------------------------------------------------

class TestThreatCategory:
    def test_creation_defaults(self) -> None:
        cat = ThreatCategory(
            id="test",
            name="Test",
            description="desc",
            threat_type=ThreatType.CUSTOM,
        )
        assert cat.id == "test"
        assert cat.name == "Test"
        assert cat.description == "desc"
        assert cat.threat_type is ThreatType.CUSTOM
        assert cat.enabled is True
        assert cat.builtin is True

    def test_creation_explicit_flags(self) -> None:
        cat = ThreatCategory(
            id="x",
            name="X",
            description="d",
            threat_type=ThreatType.NONE,
            enabled=False,
            builtin=False,
        )
        assert cat.enabled is False
        assert cat.builtin is False

    def test_to_dict_contains_all_keys(self) -> None:
        cat = ThreatCategory(
            id="abc",
            name="ABC",
            description="some desc",
            threat_type=ThreatType.RESOURCE_ABUSE,
            enabled=False,
            builtin=True,
        )
        d = cat.to_dict()
        assert d == {
            "id": "abc",
            "name": "ABC",
            "description": "some desc",
            "threat_type": "resource_abuse",
            "enabled": False,
            "builtin": True,
        }

    def test_to_dict_threat_type_is_string_value(self) -> None:
        """threat_type must be serialised as the enum .value string."""
        cat = ThreatCategory(
            id="t",
            name="T",
            description="d",
            threat_type=ThreatType.DATA_EXFILTRATION,
        )
        assert cat.to_dict()["threat_type"] == "data_exfiltration"


# ---------------------------------------------------------------------------
# 2-3. Registry register / unregister / get
# ---------------------------------------------------------------------------

class TestRegistryBasicOps:
    @pytest.fixture
    def registry(self) -> ThreatCategoryRegistry:
        return ThreatCategoryRegistry()

    def test_register_and_get(self, registry: ThreatCategoryRegistry) -> None:
        cat = _custom_category()
        registry.register(cat)
        assert registry.get(cat.id) is cat

    def test_get_missing_returns_none(self, registry: ThreatCategoryRegistry) -> None:
        assert registry.get("nonexistent") is None

    def test_unregister_existing_returns_true(self, registry: ThreatCategoryRegistry) -> None:
        cat = _custom_category()
        registry.register(cat)
        assert registry.unregister(cat.id) is True
        assert registry.get(cat.id) is None

    def test_unregister_missing_returns_false(self, registry: ThreatCategoryRegistry) -> None:
        assert registry.unregister("nonexistent") is False

    def test_unregister_is_idempotent(self, registry: ThreatCategoryRegistry) -> None:
        cat = _custom_category()
        registry.register(cat)
        assert registry.unregister(cat.id) is True
        assert registry.unregister(cat.id) is False


# ---------------------------------------------------------------------------
# 4. list_all() vs list_enabled() when some disabled
# ---------------------------------------------------------------------------

class TestListMethods:
    @pytest.fixture
    def registry(self) -> ThreatCategoryRegistry:
        reg = ThreatCategoryRegistry()
        reg.register(_custom_category("a", enabled=True))
        reg.register(_custom_category("b", enabled=True))
        reg.register(_custom_category("c", enabled=False))
        return reg

    def test_list_all_returns_every_category(self, registry: ThreatCategoryRegistry) -> None:
        assert len(registry.list_all()) == 3

    def test_list_enabled_excludes_disabled(self, registry: ThreatCategoryRegistry) -> None:
        enabled = registry.list_enabled()
        assert len(enabled) == 2
        ids = {c.id for c in enabled}
        assert "c" not in ids

    def test_list_enabled_empty_when_all_disabled(self) -> None:
        reg = ThreatCategoryRegistry()
        reg.register(_custom_category("x", enabled=False))
        assert reg.list_enabled() == []

    def test_list_all_empty_on_fresh_registry(self) -> None:
        reg = ThreatCategoryRegistry()
        assert reg.list_all() == []


# ---------------------------------------------------------------------------
# 5. disable() / enable() existing and missing
# ---------------------------------------------------------------------------

class TestDisableEnable:
    @pytest.fixture
    def registry(self) -> ThreatCategoryRegistry:
        reg = ThreatCategoryRegistry()
        reg.register(_custom_category("x", enabled=True))
        return reg

    def test_disable_existing_returns_true(self, registry: ThreatCategoryRegistry) -> None:
        assert registry.disable("x") is True
        assert registry.get("x").enabled is False  # type: ignore[union-attr]

    def test_disable_missing_returns_false(self, registry: ThreatCategoryRegistry) -> None:
        assert registry.disable("missing") is False

    def test_enable_existing_returns_true(self, registry: ThreatCategoryRegistry) -> None:
        registry.disable("x")
        assert registry.enable("x") is True
        assert registry.get("x").enabled is True  # type: ignore[union-attr]

    def test_enable_missing_returns_false(self, registry: ThreatCategoryRegistry) -> None:
        assert registry.enable("missing") is False

    def test_disable_then_list_enabled(self, registry: ThreatCategoryRegistry) -> None:
        """After disabling the only category, list_enabled must be empty."""
        registry.disable("x")
        assert registry.list_enabled() == []

    def test_enable_already_enabled_is_noop(self, registry: ThreatCategoryRegistry) -> None:
        """Enabling an already-enabled category should still return True."""
        assert registry.enable("x") is True
        assert registry.get("x").enabled is True  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# 6. register_builtins() populates 7 categories
# ---------------------------------------------------------------------------

class TestRegisterBuiltins:
    def test_registers_seven_categories(self) -> None:
        reg = ThreatCategoryRegistry()
        reg.register_builtins()
        assert len(reg.list_all()) == 7

    def test_all_builtins_are_enabled(self) -> None:
        reg = ThreatCategoryRegistry()
        reg.register_builtins()
        assert len(reg.list_enabled()) == 7

    def test_all_builtins_marked_builtin(self) -> None:
        reg = ThreatCategoryRegistry()
        reg.register_builtins()
        for cat in reg.list_all():
            assert cat.builtin is True, f"{cat.id} should be builtin"

    def test_builtin_ids_match_expected_set(self) -> None:
        reg = ThreatCategoryRegistry()
        reg.register_builtins()
        expected_ids = {
            "prompt_injection",
            "privilege_escalation",
            "data_exfiltration",
            "suspicious_sequence",
            "intent_misalignment",
            "obfuscated_payload",
            "resource_abuse",
        }
        actual_ids = {c.id for c in reg.list_all()}
        assert actual_ids == expected_ids

    def test_register_builtins_idempotent(self) -> None:
        """Calling register_builtins twice should not duplicate entries."""
        reg = ThreatCategoryRegistry()
        reg.register_builtins()
        reg.register_builtins()
        assert len(reg.list_all()) == 7


# ---------------------------------------------------------------------------
# 7. BUILTIN_CATEGORIES has correct IDs and threat types
# ---------------------------------------------------------------------------

class TestBuiltinCategories:
    def test_count_is_seven(self) -> None:
        assert len(BUILTIN_CATEGORIES) == 7

    def test_ids_and_threat_types_match(self) -> None:
        expected = {
            "prompt_injection": ThreatType.PROMPT_INJECTION,
            "privilege_escalation": ThreatType.PRIVILEGE_ESCALATION,
            "data_exfiltration": ThreatType.DATA_EXFILTRATION,
            "suspicious_sequence": ThreatType.SUSPICIOUS_SEQUENCE,
            "intent_misalignment": ThreatType.INTENT_MISALIGNMENT,
            "obfuscated_payload": ThreatType.OBFUSCATED_PAYLOAD,
            "resource_abuse": ThreatType.RESOURCE_ABUSE,
        }
        for cat in BUILTIN_CATEGORIES:
            assert cat.id in expected, f"Unexpected builtin id: {cat.id}"
            assert cat.threat_type is expected[cat.id], (
                f"{cat.id}: expected threat_type {expected[cat.id]}, got {cat.threat_type}"
            )

    def test_all_have_nonempty_descriptions(self) -> None:
        for cat in BUILTIN_CATEGORIES:
            assert len(cat.description) > 20, f"{cat.id} description is too short"

    def test_all_have_nonempty_names(self) -> None:
        for cat in BUILTIN_CATEGORIES:
            assert cat.name, f"{cat.id} name must not be empty"


# ---------------------------------------------------------------------------
# 8. to_dict_list() format
# ---------------------------------------------------------------------------

class TestToDictList:
    def test_empty_registry(self) -> None:
        reg = ThreatCategoryRegistry()
        assert reg.to_dict_list() == []

    def test_returns_list_of_dicts(self) -> None:
        reg = ThreatCategoryRegistry()
        reg.register(_custom_category("one"))
        reg.register(_custom_category("two"))
        result = reg.to_dict_list()
        assert isinstance(result, list)
        assert len(result) == 2
        for item in result:
            assert isinstance(item, dict)
            assert set(item.keys()) == {
                "id", "name", "description", "threat_type", "enabled", "builtin",
            }

    def test_matches_individual_to_dict(self) -> None:
        reg = ThreatCategoryRegistry()
        cat = _custom_category("only")
        reg.register(cat)
        assert reg.to_dict_list() == [cat.to_dict()]


# ---------------------------------------------------------------------------
# 9. Custom (non-builtin) categories
# ---------------------------------------------------------------------------

class TestCustomCategories:
    def test_custom_category_not_builtin(self) -> None:
        cat = _custom_category(builtin=False)
        assert cat.builtin is False
        assert cat.to_dict()["builtin"] is False

    def test_custom_with_custom_threat_type(self) -> None:
        cat = _custom_category(threat_type=ThreatType.CUSTOM)
        assert cat.threat_type is ThreatType.CUSTOM
        assert cat.to_dict()["threat_type"] == "custom"

    def test_custom_coexists_with_builtins(self) -> None:
        reg = ThreatCategoryRegistry()
        reg.register_builtins()
        custom = _custom_category("my_custom", builtin=False)
        reg.register(custom)
        assert len(reg.list_all()) == 8
        assert reg.get("my_custom") is custom
        # Built-ins still present
        assert reg.get("prompt_injection") is not None


# ---------------------------------------------------------------------------
# 10. Overwrite behaviour (register same ID twice)
# ---------------------------------------------------------------------------

class TestOverwriteBehaviour:
    def test_register_same_id_overwrites(self) -> None:
        reg = ThreatCategoryRegistry()
        first = _custom_category("dup", name="First")
        second = _custom_category("dup", name="Second")
        reg.register(first)
        reg.register(second)
        assert len(reg.list_all()) == 1
        assert reg.get("dup").name == "Second"  # type: ignore[union-attr]

    def test_overwrite_preserves_count(self) -> None:
        reg = ThreatCategoryRegistry()
        reg.register(_custom_category("a"))
        reg.register(_custom_category("b"))
        reg.register(_custom_category("a", name="A-v2"))
        assert len(reg.list_all()) == 2

    def test_overwrite_builtin_with_custom(self) -> None:
        """A custom category can overwrite a built-in by registering the same ID."""
        reg = ThreatCategoryRegistry()
        reg.register_builtins()
        replacement = ThreatCategory(
            id="prompt_injection",
            name="Custom PI",
            description="Replaced.",
            threat_type=ThreatType.PROMPT_INJECTION,
            builtin=False,
        )
        reg.register(replacement)
        cat = reg.get("prompt_injection")
        assert cat is not None
        assert cat.name == "Custom PI"
        assert cat.builtin is False
        # Total count unchanged
        assert len(reg.list_all()) == 7
