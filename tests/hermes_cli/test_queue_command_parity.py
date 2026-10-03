"""Parity harness: the shared grammar must classify exactly what the CLI classifies today.

Compares ``parse_queue_command`` against the CLI's own ``_QUEUE_VERBS`` routing rule over a
corpus of invocations. A divergence here means the refactor would change behavior on a
surface that already works, so this runs as a real test rather than a one-off check.
"""

import pytest

from hermes_cli.cli_loops_mixin import _QUEUE_VERBS, _parse_queue_index
from hermes_cli.queue_command import (
    ADD, CLEAR, EDIT, ENQUEUE, LIST, MOVE, RM, parse_queue_command,
)

# The CLI addresses its queue by handler name; map those onto the canonical verbs.
_HANDLER_TO_ACTION = {
    "_queue_list": LIST, "_queue_clear": CLEAR, "_queue_edit": EDIT,
    "_queue_remove": RM, "_queue_move": MOVE,
}


def _cli_classify(payload: str) -> str:
    """The CLI's own routing decision, verbatim from ``_cmd_queue``.

    ``payload`` arrives stripped: the CLI reads it through ``_slash_args``, which strips
    before the handler ever sees it.
    """
    payload = payload.strip()
    if not payload:
        return LIST
    verb, _, rest = payload.partition(" ")
    verb, rest = verb.lower(), rest.strip()
    if verb == "add":
        return ENQUEUE if rest else ADD
    entry = _QUEUE_VERBS.get(verb)
    if entry is None:
        return ENQUEUE
    handler, takes_index = entry
    is_management = handler is not None and (
        rest.split(None, 1)[0].isdigit() if takes_index and rest else not rest
    )
    if is_management:
        return _HANDLER_TO_ACTION[handler]
    return ENQUEUE


_CORPUS = [
    "",
    "hello world",
    "list",
    "ls",
    "show",
    "clear",
    "edit",
    "rm",
    "move",
    "add",
    "add clear the logs",
    "maybe run later",
    "clear the logs",
    "edit the config",
    "rm 1",
    "rm 1 extra",
    "rm 5",
    "edit 1 hello",
    "edit 1 hello world",
    "edit 3 nope",
    "move 1 2",
    "move 2 1",
    "move 1 9",
    "move 1",
    "LIST",
    "Clear",
    "  list  ",
    "rm ②",
    "rm ²",
    "rm " + "9" * 5000,
    "edit ² new text",
    "move ⑦ 1",
    "move 1 ⑦",
    "del 1",
    "pop 1",
    "delete 1",
    "remove 1",
    "set 1 new",
    "ls extra",
    "show extra",
    "list 1 2 3",
    "clear 1",
    "rm 0",
    "edit 0 x",
    "move 0 1",
]


@pytest.mark.parametrize("payload", _CORPUS, ids=lambda p: repr(p)[:40])
def test_shared_grammar_classifies_like_the_cli(payload):
    """Every invocation routes to the same action the CLI routes it to today."""
    assert parse_queue_command(payload).action == _cli_classify(payload)


@pytest.mark.parametrize("payload", ["rm 1", "edit 1 x", "move 1 2", "rm ②", "edit ² x"])
def test_index_parsing_matches_the_cli_helper(payload):
    """The hardened index parser is the CLI's, not a lookalike."""
    command = parse_queue_command(payload)
    expected_index = _parse_queue_index(payload.split(None, 1)[1].split()[0])
    if command.action == MOVE:
        assert command.index == expected_index
        assert command.destination == _parse_queue_index(payload.split()[2])
    elif not command.error:
        assert command.index == expected_index


def test_add_with_no_prompt_is_an_error_not_a_prompt():
    assert parse_queue_command("add").error is True
    assert parse_queue_command("add").action == ADD


def test_rm_with_trailing_words_is_an_error_not_a_prompt():
    command = parse_queue_command("rm 1 extra")
    assert command.action == RM and command.error is True


def test_edit_needs_an_index_and_a_body():
    assert parse_queue_command("edit").error is True
    assert parse_queue_command("edit 1").error is True
    parsed = parse_queue_command("edit 2 replacement prompt")
    assert (parsed.index, parsed.payload) == (2, "replacement prompt")


def test_move_needs_two_usable_indices():
    assert parse_queue_command("move").error is True
    assert parse_queue_command("move 1").error is True
    assert parse_queue_command("move 1 ⑦").error is True
    # Non-digit operands are the start of a prompt, exactly as the CLI routes them.
    assert parse_queue_command("move a b").action == ENQUEUE


def test_out_of_range_indices_still_parse_as_management():
    """``rm 5`` on a one-item queue is management, so the surface answers with not-found."""
    assert parse_queue_command("rm 5").action == RM
    assert parse_queue_command("move 1 9").action == MOVE