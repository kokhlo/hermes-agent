"""``/queue`` management verbs reach the queue from the ACP surface.

Same defect as the TUI: the adapter forwarded the whole argument to the model, so
``/queue list`` queued the literal word "list". The queue is a plain list of strings here, so
the shared grammar applies with no envelope handling.
"""

import threading

from acp_adapter.session import SessionState


def _state(*queued):
    return SessionState(session_id="s1", agent=None, queued_prompts=list(queued))


def _cmd_queue(args, state):
    from acp_adapter.server import HermesACPAgent
    # ``_cmd_queue`` reads only the queue and the runtime lock off ``state`` and calls the
    # module-level ``_queue_prompt``, so an uninitialized instance exercises it exactly.
    return HermesACPAgent._cmd_queue(object.__new__(HermesACPAgent), args, state)


def test_subcommands_manage_the_queue_instead_of_queueing_a_prompt():
    state = _state("first prompt", "second prompt")
    for arg in ("list", "clear", "rm 1", "move 1 2", "edit 1 hello"):
        out = _cmd_queue(arg, _state("first prompt", "second prompt"))
        assert "Queued for the next turn" not in out, f"/queue {arg} queued a prompt: {out}"


def test_a_free_text_prompt_is_still_queued():
    state = _state()
    out = _cmd_queue("summarize the diff", state)
    assert "Queued for the next turn" in out
    assert state.queued_prompts == ["summarize the diff"]


def test_add_forces_enqueueing_prompts_that_start_with_a_verb():
    state = _state()
    _cmd_queue("add clear the logs", state)
    assert state.queued_prompts == ["clear the logs"]


def test_prompts_starting_with_a_verb_word_are_still_prompts():
    state = _state()
    _cmd_queue("clear the logs", state)
    assert state.queued_prompts == ["clear the logs"]


def test_list_reports_every_queued_prompt():
    out = _cmd_queue("list", _state("first prompt", "second prompt"))
    assert "1. first prompt" in out and "2. second prompt" in out


def test_rm_clear_edit_and_move_mutate_the_queue():
    assert _state("a", "b", "c").queued_prompts is not None
    state = _state("a", "b", "c")
    _cmd_queue("rm 2", state)
    assert state.queued_prompts == ["a", "c"]

    state = _state("a", "b")
    _cmd_queue("edit 2 replacement", state)
    assert state.queued_prompts == ["a", "replacement"]

    state = _state("a", "b", "c")
    _cmd_queue("move 3 1", state)
    assert state.queued_prompts == ["c", "a", "b"]

    state = _state("a", "b")
    _cmd_queue("clear", state)
    assert state.queued_prompts == []


def test_malformed_invocations_answer_with_a_usage_line_and_keep_the_queue():
    for arg in ("rm", "rm 1 extra", "edit", "move", "add"):
        state = _state("only item")
        out = _cmd_queue(arg, state)
        assert out, f"/queue {arg} answered with nothing"
        assert state.queued_prompts == ["only item"], f"/queue {arg} touched the queue"


def test_a_circled_digit_is_a_usage_error_not_a_crash():
    state = _state("only item")
    assert _cmd_queue("rm ②", state)
    assert state.queued_prompts == ["only item"]


def test_an_empty_queue_lists_empty_and_shows_usage():
    out = _cmd_queue("list", _state())
    assert "empty" in out.lower()
    assert "/queue" in out