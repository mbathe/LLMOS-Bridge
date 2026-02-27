"""Unit tests -- PromptComposer (dynamic security system prompt assembly).

Tests cover:
  - compose() with all builtins enabled contains all 7 category names
  - compose() with a category disabled excludes it
  - compose() with custom_suffix appends it at the end
  - compose() with no categories shows fallback message
  - compose() always includes the 4 static sections
  - custom_suffix setter updates the suffix
  - category_registry property returns the registry
  - Prompt changes dynamically when registry is modified after creation
"""

from __future__ import annotations

import pytest

from llmos_bridge.security.prompt_composer import (
    PromptComposer,
    _BASE_INTRO,
    _CRITICAL_RULES,
    _OUTPUT_FORMAT,
    _VERDICT_GUIDELINES,
)
from llmos_bridge.security.threat_categories import (
    BUILTIN_CATEGORIES,
    ThreatCategory,
    ThreatCategoryRegistry,
    ThreatType,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def registry() -> ThreatCategoryRegistry:
    """Return a registry with all 7 built-in categories registered."""
    reg = ThreatCategoryRegistry()
    reg.register_builtins()
    return reg


@pytest.fixture
def empty_registry() -> ThreatCategoryRegistry:
    """Return an empty registry (no categories)."""
    return ThreatCategoryRegistry()


@pytest.fixture
def composer(registry: ThreatCategoryRegistry) -> PromptComposer:
    """Return a PromptComposer backed by a full builtin registry."""
    return PromptComposer(category_registry=registry)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

ALL_BUILTIN_NAMES = [cat.name for cat in BUILTIN_CATEGORIES]


@pytest.mark.unit
class TestComposeAllBuiltins:
    """compose() with all builtins enabled contains all 7 category names."""

    def test_all_seven_category_names_present(
        self, composer: PromptComposer
    ) -> None:
        prompt = composer.compose()
        for name in ALL_BUILTIN_NAMES:
            assert name in prompt, f"Expected category name {name!r} in prompt"

    def test_all_categories_are_numbered(
        self, composer: PromptComposer
    ) -> None:
        prompt = composer.compose()
        for i in range(1, 8):
            assert f"### {i}." in prompt


@pytest.mark.unit
class TestComposeDisabledCategory:
    """compose() with a category disabled excludes it from the prompt."""

    def test_disabled_category_excluded(
        self, registry: ThreatCategoryRegistry
    ) -> None:
        registry.disable("resource_abuse")
        composer = PromptComposer(category_registry=registry)
        prompt = composer.compose()

        assert "Resource Abuse" not in prompt

    def test_remaining_categories_still_present(
        self, registry: ThreatCategoryRegistry
    ) -> None:
        registry.disable("resource_abuse")
        composer = PromptComposer(category_registry=registry)
        prompt = composer.compose()

        for cat in BUILTIN_CATEGORIES:
            if cat.id != "resource_abuse":
                assert cat.name in prompt

    def test_numbering_is_contiguous_after_disable(
        self, registry: ThreatCategoryRegistry
    ) -> None:
        registry.disable("resource_abuse")
        composer = PromptComposer(category_registry=registry)
        prompt = composer.compose()

        # With 6 enabled categories, numbering should be 1-6, not 1-7
        for i in range(1, 7):
            assert f"### {i}." in prompt
        assert "### 7." not in prompt


@pytest.mark.unit
class TestComposeCustomSuffix:
    """compose() with custom_suffix appends it at the end."""

    def test_suffix_appears_at_end(
        self, registry: ThreatCategoryRegistry
    ) -> None:
        suffix = "## Domain-Specific: Never allow access to /data/secret"
        composer = PromptComposer(
            category_registry=registry, custom_suffix=suffix
        )
        prompt = composer.compose()

        assert prompt.endswith(suffix)

    def test_suffix_absent_when_empty(
        self, composer: PromptComposer
    ) -> None:
        prompt = composer.compose()
        # The prompt should end with _CRITICAL_RULES, not an extra section
        assert prompt.rstrip().endswith(_CRITICAL_RULES.rstrip())


@pytest.mark.unit
class TestComposeNoCategories:
    """compose() with no categories shows the fallback message."""

    def test_no_categories_message(
        self, empty_registry: ThreatCategoryRegistry
    ) -> None:
        composer = PromptComposer(category_registry=empty_registry)
        prompt = composer.compose()

        assert "No threat categories configured." in prompt

    def test_no_categories_still_has_static_sections(
        self, empty_registry: ThreatCategoryRegistry
    ) -> None:
        composer = PromptComposer(category_registry=empty_registry)
        prompt = composer.compose()

        assert _BASE_INTRO in prompt
        assert _OUTPUT_FORMAT in prompt
        assert _VERDICT_GUIDELINES in prompt
        assert _CRITICAL_RULES in prompt


@pytest.mark.unit
class TestComposeStaticSections:
    """compose() always includes BASE_INTRO, OUTPUT_FORMAT, VERDICT_GUIDELINES, CRITICAL_RULES."""

    def test_base_intro_present(self, composer: PromptComposer) -> None:
        assert _BASE_INTRO in composer.compose()

    def test_output_format_present(self, composer: PromptComposer) -> None:
        assert _OUTPUT_FORMAT in composer.compose()

    def test_verdict_guidelines_present(
        self, composer: PromptComposer
    ) -> None:
        assert _VERDICT_GUIDELINES in composer.compose()

    def test_critical_rules_present(self, composer: PromptComposer) -> None:
        assert _CRITICAL_RULES in composer.compose()

    def test_section_ordering(self, composer: PromptComposer) -> None:
        """Static sections appear in the canonical order."""
        prompt = composer.compose()
        idx_intro = prompt.index(_BASE_INTRO)
        idx_format = prompt.index(_OUTPUT_FORMAT)
        idx_verdict = prompt.index(_VERDICT_GUIDELINES)
        idx_rules = prompt.index(_CRITICAL_RULES)

        assert idx_intro < idx_format < idx_verdict < idx_rules


@pytest.mark.unit
class TestCustomSuffixSetter:
    """custom_suffix setter updates the suffix for subsequent compose() calls."""

    def test_setter_updates_suffix(
        self, composer: PromptComposer
    ) -> None:
        assert composer.custom_suffix == ""

        new_suffix = "## Extra: block all network calls"
        composer.custom_suffix = new_suffix

        assert composer.custom_suffix == new_suffix

    def test_setter_reflected_in_compose(
        self, composer: PromptComposer
    ) -> None:
        composer.custom_suffix = "## Custom Addendum"
        prompt = composer.compose()

        assert "## Custom Addendum" in prompt
        assert prompt.endswith("## Custom Addendum")

    def test_setter_to_empty_removes_suffix(
        self, composer: PromptComposer
    ) -> None:
        composer.custom_suffix = "temporary"
        composer.custom_suffix = ""

        prompt = composer.compose()
        assert "temporary" not in prompt


@pytest.mark.unit
class TestCategoryRegistryProperty:
    """category_registry property returns the registry instance."""

    def test_returns_same_registry(
        self, registry: ThreatCategoryRegistry
    ) -> None:
        composer = PromptComposer(category_registry=registry)
        assert composer.category_registry is registry

    def test_registry_identity_preserved(
        self, empty_registry: ThreatCategoryRegistry
    ) -> None:
        composer = PromptComposer(category_registry=empty_registry)
        assert composer.category_registry is empty_registry


@pytest.mark.unit
class TestDynamicRegistryChanges:
    """Prompt changes dynamically when registry is modified after composer creation."""

    def test_adding_category_after_creation(
        self, empty_registry: ThreatCategoryRegistry
    ) -> None:
        composer = PromptComposer(category_registry=empty_registry)

        # Initially no categories
        prompt_before = composer.compose()
        assert "No threat categories configured." in prompt_before

        # Register a custom category after composer creation
        empty_registry.register(
            ThreatCategory(
                id="custom_threat",
                name="Custom Threat Detection",
                description="Detect custom domain-specific threats.",
                threat_type=ThreatType.CUSTOM,
                builtin=False,
            )
        )

        prompt_after = composer.compose()
        assert "Custom Threat Detection" in prompt_after
        assert "No threat categories configured." not in prompt_after

    def test_disabling_category_after_creation(
        self, registry: ThreatCategoryRegistry
    ) -> None:
        composer = PromptComposer(category_registry=registry)

        prompt_before = composer.compose()
        assert "Privilege Escalation" in prompt_before

        registry.disable("privilege_escalation")

        prompt_after = composer.compose()
        assert "Privilege Escalation" not in prompt_after

    def test_enabling_category_after_creation(
        self, registry: ThreatCategoryRegistry
    ) -> None:
        registry.disable("data_exfiltration")
        composer = PromptComposer(category_registry=registry)

        prompt_disabled = composer.compose()
        assert "Data Exfiltration Patterns" not in prompt_disabled

        registry.enable("data_exfiltration")

        prompt_enabled = composer.compose()
        assert "Data Exfiltration Patterns" in prompt_enabled

    def test_unregistering_category_after_creation(
        self, registry: ThreatCategoryRegistry
    ) -> None:
        composer = PromptComposer(category_registry=registry)

        prompt_before = composer.compose()
        assert "Obfuscated Payloads" in prompt_before

        registry.unregister("obfuscated_payload")

        prompt_after = composer.compose()
        assert "Obfuscated Payloads" not in prompt_after
