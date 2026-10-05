"""Showcase colouring for the CLI's thinking surfaces.

The buffered ``[thinking]`` preview, the live ``show_reasoning`` box and that box's
closing tail all render through :mod:`hermes_cli.thinking_colors`, so a token is
painted the same way no matter which of the three the reader is looking at.
"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from hermes_cli.thinking_colors import (  # noqa: E402
    SHOWCASE_GREEN,
    SHOWCASE_ORANGE,
    SHOWCASE_WHITE,
    render_thinking_text,
    split_safe_stream,
    thinking_spans,
)

GREEN = "\x1b[38;2;40;254;20m"
ORANGE = "\x1b[38;2;255;159;10m"
WHITE = "\x1b[38;2;255;255;255m"
RST = "\x1b[0m"


def _colors(text: str) -> list[tuple[str, str]]:
    """(chunk, SGR in force) pairs from rendered output.

    Every span is emitted as SGR + chunk + reset, so the SGR that applies to a
    chunk is the last one seen before it — a reset belongs to the span that ended,
    not to the one that follows.
    """
    spans: list[tuple[str, str]] = []
    sgr = ""
    for match in re.finditer(r"\x1b\[([0-9;]*)m|([^\x1b]+)", text):
        if match.group(1) is not None:
            sgr = match.group(1)
        else:
            spans.append((match.group(2), f"\x1b[{sgr}m"))
    return spans


def test_palette_matches_the_requested_truecolor():
    assert (SHOWCASE_GREEN, SHOWCASE_ORANGE, SHOWCASE_WHITE) == ("#28FE14", "#FF9F0A", "#FFFFFF")


def test_plain_prose_is_green():
    assert _colors(render_thinking_text("just some prose")) == [("just some prose", GREEN)]


def test_order_mark_is_orange_and_the_lookalikes_stay_green():
    for text, expected in (
        ("1. read the file", ("1.", ORANGE)),
        ("2. ship it", ("2.", ORANGE)),
        ("3.14 is pi", ("3.14 is pi", GREEN)),
        ("v1.0.1 ships", ("v1.0.1 ships", GREEN)),
        ("it took 2.5 hours", ("it took 2.5 hours", GREEN)),
        ("step 1. then 2. done", None),
    ):
        spans = _colors(render_thinking_text(text))
        if expected is None:
            assert spans == [("step ", GREEN), ("1.", ORANGE), (" then ", GREEN), ("2.", ORANGE), (" done", GREEN)]
        else:
            assert expected in spans, (text, spans)


def test_quantity_lookalikes_stay_green():
    # "~570." is an approximate count, not an order mark; three-digit
    # "100." items are not order marks either.
    assert _colors(render_thinking_text("old ~1000; new ~570. File shrinks.")) == [
        ("old ~1000; new ~570. File shrinks.", GREEN)]
    assert _colors(render_thinking_text("≈42. done")) == [("≈42. done", GREEN)]
    assert _colors(render_thinking_text("100. item")) == [("100. item", GREEN)]


def test_two_digit_order_marks_still_claim():
    assert _colors(render_thinking_text("12. twelve 99. ninety-nine")) == [
        ("12.", ORANGE), (" twelve ", GREEN), ("99.", ORANGE), (" ninety-nine", GREEN)]


def test_order_marks_after_parens_brackets_and_indent():
    assert _colors(render_thinking_text("(1. step")) == [
        ("(", GREEN), ("1.", ORANGE), (" step", GREEN)]
    assert _colors(render_thinking_text("[1. step")) == [
        ("[", GREEN), ("1.", ORANGE), (" step", GREEN)]
    assert _colors(render_thinking_text("  1. step")) == [
        ("  ", GREEN), ("1.", ORANGE), (" step", GREEN)]


def test_order_mark_with_tab_or_double_space_after():
    assert _colors(render_thinking_text("1.\tstep")) == [
        ("1.", ORANGE), ("\tstep", GREEN)]
    assert _colors(render_thinking_text("1.  double")) == [
        ("1.", ORANGE), ("  double", GREEN)]


def test_pr_number_needs_a_word_boundary():
    assert _colors(render_thinking_text("see #123456x")) == [("see #123456x", GREEN)]
    assert _colors(render_thinking_text("#123456 at start")) == [
        ("#123456", WHITE), (" at start", GREEN)]


def test_url_peels_multiple_trailing_punctuation():
    assert _colors(render_thinking_text("See https://x.com/path).")) == [
        ("See ", GREEN), ("https://x.com/path", WHITE), (").", GREEN)]
    assert _colors(render_thinking_text("https://x.com...")) == [
        ("https://x.com", WHITE), ("...", GREEN)]


def test_url_keeps_balanced_parens_in_its_path():
    assert _colors(render_thinking_text("(https://en.wikipedia.org/wiki/C_(programming_language))")) == [
        ("(", GREEN), ("https://en.wikipedia.org/wiki/C_(programming_language)", WHITE), (")", GREEN)]


def test_multiline_two_command_bullets_claim_each_dash():
    assert _colors(render_thinking_text("- git fetch\n- hermes update")) == [
        ("-", ORANGE), (" git fetch\n", GREEN), ("-", ORANGE), (" hermes update", GREEN)]


def test_log_mark_with_embedded_newline_stays_prose():
    assert _colors(render_thinking_text("§[text\nmore]")) == [("§[text\nmore]", GREEN)]


def test_pr_number_needs_six_digits():
    assert _colors(render_thinking_text("see #12345")) == [("see #12345", GREEN)]
    assert _colors(render_thinking_text("see #123456")) == [
        ("see ", GREEN), ("#123456", WHITE)]


def test_log_mark_is_one_white_span_including_the_pr_inside():
    assert _colors(render_thinking_text("§[2026-10-02] done")) == [
        ("§[2026-10-02]", WHITE), (" done", GREEN)]
    assert _colors(render_thinking_text("§ [#123456] done")) == [
        ("§ [#123456]", WHITE), (" done", GREEN)]


def test_url_is_one_white_span_through_its_fragment():
    assert _colors(render_thinking_text("at https://x.com/a#frag ok")) == [
        ("at ", GREEN), ("https://x.com/a#frag", WHITE), (" ok", GREEN)]


def test_sentence_punctuation_hugging_a_url_stays_green():
    assert _colors(render_thinking_text("(https://x.com/a).")) == [
        ("(", GREEN), ("https://x.com/a", WHITE), (").", GREEN)]


def test_adjacent_same_colour_runs_merge_into_one_span():
    assert _colors(render_thinking_text("§[2026-10-02]§[2026-10-03]")) == [
        ("§[2026-10-02]§[2026-10-03]", WHITE)]
    # …but a space between them is prose, so the white run really does break.
    assert _colors(render_thinking_text("§[2026-10-02] §[2026-10-03]")) == [
        ("§[2026-10-02]", WHITE), (" ", GREEN), ("§[2026-10-03]", WHITE)]


@pytest.mark.parametrize("partial", ["§[2026-10-0", "#12345", "1.", "3.14", "#12345x"])
def test_a_fragment_no_rule_claims_renders_as_prose(partial):
    """An unfinished stamp / short PR / bare order mark matches no rule, so the
    renderer leaves it green instead of guessing a class it may have to take back."""
    assert _colors(render_thinking_text(partial)) == [(partial, GREEN)]


@pytest.mark.parametrize("buffered,ready", [
    ("§[2026-10-0", ""),
    ("#12345", ""),
    ("trailing 1.", "trailing "),
    ("trailing 12.", "trailing "),
    ("took ~57", "took ~57"),  # quantity prefix: never a mark, safe to paint
    ("prefix 1. ", "prefix 1. "),  # order mark complete (space arrived)
    ("Issue #", "Issue "),  # a bare # can still grow into a 6-digit PR
    ("see #123456", "see "),  # 6+ digits could still gain a word char
    ("Fixed #847 and more", "Fixed #847 and more"),  # space-terminated: complete
    ("see https://x.co", "see "),
    ("plain tail", "plain tail"),
])
def test_stream_cut_holds_back_the_oldest_unfinished_token(buffered, ready):
    assert split_safe_stream(buffered) == (ready, buffered[len(ready):])


def test_stream_cut_is_idempotent_across_a_token_completing():
    """The held fragment plus what follows it is cut exactly once, so the token is
    painted as one span instead of a prose-coloured head and a coloured tail."""
    first, held = split_safe_stream("open §[2026-10-0")
    assert (first, held) == ("open ", "§[2026-10-0")
    painted = _colors(render_thinking_text(first))
    assert painted == [("open ", GREEN)]
    # …the rest of the token arrives; now it is claimed whole.
    assert _colors(render_thinking_text(held + "2]")) == [("§[2026-10-02]", WHITE)]


def test_spans_preserve_the_original_text_exactly():
    text = "1. open §[2026-10-02], see #123456 at https://x.com/a#f (3.14)"
    assert "".join(chunk for chunk, _ in thinking_spans(text)) == text


# ---- Command bullets: "-" before a known shell command is orange ----

def test_command_bullet_is_orange_before_a_known_command():
    for text in (
        "- git fetch",
        "- pgrep -l Hermes",
        "- hermes update --plan (read-only)",
        "  - git status",  # the indentation stays prose, only the dash claims
    ):
        spans = _colors(render_thinking_text(text))
        assert ("-", ORANGE) in spans, (text, spans)


def test_command_bullet_only_colours_the_dash():
    assert _colors(render_thinking_text("- git fetch")) == [
        ("-", ORANGE), (" git fetch", GREEN)]
    assert _colors(render_thinking_text("  - git status")) == [
        ("  ", GREEN), ("-", ORANGE), (" git status", GREEN)]


def test_non_command_bullets_stay_prose():
    for text in (
        "- checkout the notes",     # not a known command word
        "- the file was changed",   # prose bullet
        "- Check the repo",         # capitalized first word
        "- git.",                   # trailing punctuation = sentence, not a command
        "- 2.5 hours left",         # number first
    ):
        assert _colors(render_thinking_text(text)) == [(text, GREEN)], text


def test_mid_line_hyphens_never_claim():
    assert _colors(render_thinking_text("re-test with - git flow")) == [
        ("re-test with - git flow", GREEN)]
    assert _colors(render_thinking_text("fail-soft")) == [("fail-soft", GREEN)]


def test_command_bullet_claims_per_line_in_multiline_text():
    assert _colors(render_thinking_text("- git fetch\n- notes here")) == [
        ("-", ORANGE), (" git fetch\n- notes here", GREEN)]


def test_cmd_is_an_independent_knob(monkeypatch):
    monkeypatch.setattr(tc, "_thinking_config_overrides", lambda: {"cmd": "#654321"})
    assert _colors(render_thinking_text("- git fetch")) == [
        ("-", "\x1b[38;2;101;67;33m"), (" git fetch", GREEN)]


@pytest.mark.parametrize("buffered,ready", [
    ("open\n- ", "open\n"),
    ("open\n- gi", "open\n"),
    ("open\n- git", "open\n"),
])
def test_stream_holds_back_a_command_bullet_until_its_word_is_terminated(buffered, ready):
    assert split_safe_stream(buffered) == (ready, buffered[len(ready):])


def test_command_bullet_paints_once_the_word_completes():
    first, held = split_safe_stream("run\n- git")
    assert (first, held) == ("run\n", "- git")
    assert _colors(render_thinking_text(first)) == [("run\n", GREEN)]
    assert _colors(render_thinking_text(held + " fetch")) == [
        ("-", ORANGE), (" git fetch", GREEN)]


@pytest.fixture
def reasoning_cli(monkeypatch):
    """CLI stub with the reasoning-box state the three surfaces read."""
    from cli import HermesCLI
    import cli as climod

    cli = HermesCLI.__new__(HermesCLI)
    cli.show_reasoning = True
    cli.verbose = False
    cli.show_timestamps = False
    cli._stream_box_opened = False
    cli._reset_stream_state()
    emitted: list[str] = []
    monkeypatch.setattr(climod, "_cprint", lambda s: emitted.append(s))
    monkeypatch.setattr(HermesCLI, "_scrollback_box_width", lambda self: 74)
    return cli, emitted


def _body(emitted: list[str]) -> str:
    return "".join(re.sub(r"\x1b\[[0-9;]*m", "", chunk) for chunk in emitted)


def test_buffered_preview_colours_its_tokens(reasoning_cli):
    cli, emitted = reasoning_cli
    cli._emit_reasoning_preview("1. read §[2026-10-02] then open #123456")
    assert _body(emitted) == "  [thinking] 1. read §[2026-10-02] then open #123456"
    assert (ORANGE, WHITE) not in emitted  # escapes are per-span, not nested
    colors = [sgr for _, sgr in _colors("".join(emitted))]
    assert colors.count(ORANGE) == 1 and colors.count(WHITE) == 2


def test_preview_label_stays_dim_chrome_and_the_final_answer_is_untouched(reasoning_cli):
    cli, emitted = reasoning_cli
    cli._emit_reasoning_preview("plain thought")
    assert emitted[0].startswith("\x1b[2;3m")  # the "  [thinking] " label
    assert _body(emitted).strip().startswith("[thinking]")


def test_live_box_line_and_closing_tail_are_coloured(reasoning_cli):
    cli, emitted = reasoning_cli
    cli._stream_reasoning_delta("1. first step\n")
    cli._stream_reasoning_delta("open §[2026-10-02]")
    cli._close_reasoning_box()
    body = _body(emitted)
    assert "1. first step" in body and "open §[2026-10-02]" in body
    colors = [sgr for _, sgr in _colors("".join(emitted))]
    assert ORANGE in colors and WHITE in colors


def test_live_box_never_splits_a_token_across_two_prints(reasoning_cli):
    cli, emitted = reasoning_cli
    # A force-flush happens while the log mark is still arriving.
    cli._stream_reasoning_delta("thinking " + "x" * 80 + " §[2026-10-0")
    cli._stream_reasoning_delta("2] tail\n")
    cli._close_reasoning_box()
    painted = "".join(emitted)
    assert _body(emitted).count("§[2026-10-02]") == 1
    assert [chunk for chunk, _ in _colors(painted) if "§" in chunk] == ["§[2026-10-02]"]
    assert [sgr for chunk, sgr in _colors(painted) if "§" in chunk] == [WHITE]


def test_live_box_flushes_an_unbroken_run_instead_of_going_silent(reasoning_cli):
    cli, emitted = reasoning_cli
    cli._stream_reasoning_delta("y" * 900)
    cli._close_reasoning_box()
    assert "y" in _body(emitted)


# ---- Config knob: display.thinking_colors overrides the spec palette ----

import hermes_cli.thinking_colors as tc  # noqa: E402


def test_all_classes_resolve_from_config(monkeypatch):
    monkeypatch.setattr(
        tc, "_thinking_config_overrides",
        lambda: {"main": "#111111", "order": "#222222", "cmd": "#666666",
                 "log": "#333333", "pr": "#444444", "url": "#555555"},
    )
    colors = tc.resolve_showcase_colors()
    assert colors == {
        "main": "\x1b[38;2;17;17;17m",
        "order": "\x1b[38;2;34;34;34m",
        "cmd": "\x1b[38;2;102;102;102m",
        "log": "\x1b[38;2;51;51;51m",
        "pr": "\x1b[38;2;68;68;68m",
        "url": "\x1b[38;2;85;85;85m",
    }


def test_partial_config_keeps_defaults_for_missing_keys(monkeypatch):
    monkeypatch.setattr(tc, "_thinking_config_overrides", lambda: {"main": "#FF0000"})
    colors = tc.resolve_showcase_colors()
    assert colors["main"] == "\x1b[38;2;255;0;0m"
    assert colors["order"] == ORANGE  # spec default
    assert colors["cmd"] == ORANGE  # spec default


def test_invalid_config_values_fall_back_to_defaults(monkeypatch):
    monkeypatch.setattr(
        tc, "_thinking_config_overrides",
        lambda: {"main": "red", "order": "#12", "cmd": "#GGGGGG", "log": 123,
                 "pr": None, "url": "#GGGGGG"},
    )
    colors = tc.resolve_showcase_colors()
    assert colors["main"] == GREEN
    assert colors["order"] == ORANGE
    assert colors["cmd"] == ORANGE
    assert colors["log"] == WHITE
    assert colors["pr"] == WHITE
    assert colors["url"] == WHITE


def test_unreachable_config_falls_back_to_defaults(monkeypatch):
    def _boom():
        raise RuntimeError("no config")
    monkeypatch.setattr(tc, "_thinking_config_overrides", _boom)
    colors = tc.resolve_showcase_colors()
    assert colors["main"] == GREEN and colors["log"] == WHITE


def test_knob_changes_rendered_spans(monkeypatch):
    monkeypatch.setattr(tc, "_thinking_config_overrides", lambda: {"log": "#FF0000"})
    assert _colors(render_thinking_text("§[2026-10-02] done")) == [
        ("§[2026-10-02]", "\x1b[38;2;255;0;0m"), (" done", GREEN)]


def test_log_pr_url_are_independent_knobs(monkeypatch):
    monkeypatch.setattr(
        tc, "_thinking_config_overrides",
        lambda: {"log": "#333333", "pr": "#444444", "url": "#555555"},
    )
    assert _colors(render_thinking_text("§[t] #123456 https://x.co ok")) == [
        ("§[t]", "\x1b[38;2;51;51;51m"), (" ", GREEN),
        ("#123456", "\x1b[38;2;68;68;68m"), (" ", GREEN),
        ("https://x.co", "\x1b[38;2;85;85;85m"), (" ok", GREEN)]