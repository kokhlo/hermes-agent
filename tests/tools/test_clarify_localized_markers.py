"""Tests for localized recommendation markers in tools/clarify_tool.py."""

from tools.clarify_tool import (
    clarify_tool,
    strip_recommended,
)
import json


class TestLocalizedRecommendationMarkers:
    """A model writing its own localized marker (「（推荐）」 in a Chinese
    conversation, localized equivalents elsewhere) must be treated exactly
    like the hand-written English one: no double badge for the user, no
    marker leaking back into the answer the agent reads."""

    def test_strip_removes_fullwidth_recommended_marker(self):
        assert strip_recommended("Option A（推荐）") == "Option A"

    def test_strip_removes_halfwidth_recommended_marker(self):
        assert strip_recommended("Option A (推荐)") == "Option A"

    def test_strip_still_removes_the_canonical_english_label(self):
        assert strip_recommended("Option A (Recommended)") == "Option A"
        assert strip_recommended("Option A (recommended)") == "Option A"

    def test_strip_leaves_bare_options_untouched(self):
        assert strip_recommended("Rebase (merge first)") == "Rebase (merge first)"
        assert strip_recommended("推荐系统 setup") == "推荐系统 setup"

    def test_no_double_marker_when_first_choice_carries_a_localized_one(self):
        seen = []

        def cb(question, choices):
            seen.extend(choices or [])
            return choices[0]

        clarify_tool("Pick", choices=["Option A（推荐）", "Option B"], callback=cb)
        assert seen == ["Option A（推荐）", "Option B"]

    def test_localized_marker_never_leaks_into_the_answer(self):
        def cb(question, choices):
            return choices[0]

        result = json.loads(
            clarify_tool("Pick", choices=["Option A（推荐）", "Option B"], callback=cb)
        )
        assert result["user_response"] == "Option A"
        assert result["choices_offered"] == ["Option A（推荐）", "Option B"]

    def test_localized_marker_never_leaks_into_multi_select_answers(self):
        def cb(question, choices, multi_select=False):
            return ", ".join(choices[:2])

        result = json.loads(
            clarify_tool(
                "Pick some",
                choices=["甲（推荐）", "乙", "丙"],
                multi_select=True,
                callback=cb,
            )
        )
        assert result["user_response"] == ["甲", "乙"]

    def test_cyrillic_and_german_markers_are_stripped_too(self):
        assert strip_recommended("Вариант А (рекомендуется)") == "Вариант А"
        assert strip_recommended("Option A (empfohlen)") == "Option A"
