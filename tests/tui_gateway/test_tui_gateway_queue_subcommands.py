"""``/queue`` management verbs reach the queue from the TUI/desktop surface.

The registry advertises ``subcommands=("list","edit","rm","move","clear","add")`` for
completion, but the gateway handler forwarded the whole argument to the model as a prompt, so
``/queue list`` queued the literal word "list" and every management action was unreachable
outside the classic CLI. The gateway queue holds prompts accepted mid-turn; removing one must
also retire its accept-time transcript row, or a cancelled prompt stays visible in the chat.
"""

import threading
import types

from tui_gateway import server


def _session(**extra):
    return {
        "agent": types.SimpleNamespace(),
        "session_key": "session-key",
        "history": [],
        "history_lock": threading.Lock(),
        "history_version": 0,
        "running": False,
        "transport": None,
        "attached_images": [],
        **extra,
    }


def _queue_texts(session):
    head = session.get("queued_prompt")
    entries = ([head] if head else []) + list(session.get("queued_prompts") or [])
    return [entry["text"] for entry in entries]


def _filled(session, *texts):
    server._enqueue_prompt(session, texts[0], "ws-1")
    for text in texts[1:]:
        session.setdefault("queued_prompts", []).append(
            {"text": text, "transport": "ws-1"})
    return session


def _result(response):
    return response["result"]


# ── the reported defect ─────────────────────────────────────────────────────

def test_subcommands_are_no_longer_queued_as_prompts():
    """Every advertised verb comes back as output, never as a prompt to send."""
    session = _session()
    for arg in ("list", "clear", "rm 1", "move 1 2", "edit 1 hello", "ls", "del 1"):
        result = _result(server._cmd_queue("r", {}, session, "queue", arg))
        assert result["type"] == "exec", f"/queue {arg} produced {result}"
        assert "message" not in result, f"/queue {arg} still queued a prompt: {result}"


def test_a_free_text_prompt_is_still_submitted():
    """The regression gate: what works today must keep working."""
    session = _session()
    result = _result(server._cmd_queue("r", {}, session, "queue", "summarize the diff"))
    assert result == {"type": "send", "message": "summarize the diff"}


def test_add_forces_enqueueing_prompts_that_start_with_a_verb():
    session = _session()
    result = _result(server._cmd_queue("r", {}, session, "queue", "add clear the logs"))
    assert result == {"type": "send", "message": "clear the logs"}


def test_prompts_starting_with_a_verb_word_are_still_prompts():
    for arg in ("clear the logs", "edit the config", "maybe run later"):
        session = _session()
        assert _result(server._cmd_queue("r", {}, session, "queue", arg))["type"] == "send"


def test_bare_queue_lists_the_queue():
    session = _filled(_session(), "first prompt")
    result = _result(server._cmd_queue("r", {}, session, "queue", ""))
    assert "first prompt" in result["output"]


# ── the management verbs actually mutate ────────────────────────────────────

def test_list_numbers_every_queued_prompt():
    session = _filled(_session(), "first prompt", "second prompt", "third prompt")
    output = _result(server._cmd_queue("r", {}, session, "queue", "list"))["output"]
    assert "1. first prompt" in output
    assert "2. second prompt" in output
    assert "3. third prompt" in output
    assert _queue_texts(session) == ["first prompt", "second prompt", "third prompt"]


def test_rm_removes_the_addressed_entry_only():
    session = _filled(_session(), "first prompt", "second prompt", "third prompt")
    _result(server._cmd_queue("r", {}, session, "queue", "rm 2"))
    assert _queue_texts(session) == ["first prompt", "third prompt"]


def test_clear_empties_the_queue():
    session = _filled(_session(), "first prompt", "second prompt")
    _result(server._cmd_queue("r", {}, session, "queue", "clear"))
    assert session.get("queued_prompt") is None
    assert not session.get("queued_prompts")


def test_edit_replaces_the_addressed_prompt():
    session = _filled(_session(), "first prompt", "second prompt")
    _result(server._cmd_queue("r", {}, session, "queue", "edit 2 replacement prompt"))
    assert _queue_texts(session) == ["first prompt", "replacement prompt"]


def test_move_reorders_without_losing_an_entry():
    session = _filled(_session(), "first prompt", "second prompt", "third prompt")
    _result(server._cmd_queue("r", {}, session, "queue", "move 3 1"))
    assert _queue_texts(session) == ["third prompt", "first prompt", "second prompt"]


def test_out_of_range_indices_leave_the_queue_untouched():
    session = _filled(_session(), "only item")
    for arg in ("rm 5", "edit 3 nope", "move 1 9"):
        _result(server._cmd_queue("r", {}, session, "queue", arg))
    assert _queue_texts(session) == ["only item"]


def test_malformed_invocations_answer_with_a_usage_line():
    session = _filled(_session(), "only item")
    for arg in ("rm", "rm 1 extra", "edit", "edit 1", "move", "move 1", "add"):
        result = _result(server._cmd_queue("r", {}, session, "queue", arg))
        assert result["type"] == "exec", f"/queue {arg} produced {result}"
        assert result["output"], f"/queue {arg} answered with nothing"
        assert _queue_texts(session) == ["only item"], f"/queue {arg} touched the queue"


def test_a_circled_digit_is_a_usage_error_not_a_crash():
    """``isdigit()`` accepts ``②``; int() does not. The index parser must not raise."""
    session = _filled(_session(), "only item")
    result = _result(server._cmd_queue("r", {}, session, "queue", "rm ②"))
    assert result["type"] == "exec"
    assert _queue_texts(session) == ["only item"]


def test_an_empty_queue_lists_empty_and_shows_usage():
    result = _result(server._cmd_queue("r", {}, _session(), "queue", "list"))
    assert "empty" in result["output"].lower()
    assert "/queue" in result["output"]


def test_a_missing_session_lists_empty_instead_of_raising():
    result = _result(server._cmd_queue("r", {}, None, "queue", "list"))
    assert result["type"] == "exec"


# ── cancelled prompts must not linger in the transcript ─────────────────────

class _DB:
    def __init__(self):
        self.deactivated = []
        self.rewritten = []
        self.live = {7: "original prompt"}

    def resolve_active_row_id(self, _session_id, row_id):
        return row_id if row_id in self.live else None

    def deactivate_message(self, session_id, row_id):
        self.deactivated.append((session_id, row_id))
        self.live.pop(row_id, None)
        return 1

    def set_user_message_content(self, session_id, row_id, content):
        self.rewritten.append((session_id, row_id, content))
        return 1


class _DBCtx:
    def __init__(self, db):
        self._db = db

    def __enter__(self):
        return self._db

    def __exit__(self, *_exc):
        return False


def _session_with_rows(db):
    session = _session()
    server._session_db = lambda _s: _DBCtx(db)
    server._submit_row_owner_key = lambda staged, _s: staged.get("_session_key") or "session-key"
    session["_filled"] = _filled(session, "first prompt", "second prompt")
    for index, entry in enumerate(
            [session["_filled"]["queued_prompt"], *session["_filled"]["queued_prompts"]], 1):
        entry["_submit_user_row"] = {"_row_id": 6 + index, "content": entry["text"]}
    session["_filled"]["queued_prompt"]["_submit_user_row"]["_row_id"] = 7
    db.live = {7: "first prompt", 8: "second prompt"}
    return session


def test_removing_a_queued_prompt_retires_its_transcript_row():
    db = _DB()
    session = _session_with_rows(db)
    _result(server._cmd_queue("r", {}, session, "queue", "rm 1"))
    assert db.deactivated == [("session-key", 7)]
    assert 8 not in [row for _sid, row in db.deactivated]


def test_clearing_the_queue_retires_every_row():
    db = _DB()
    session = _session_with_rows(db)
    _result(server._cmd_queue("r", {}, session, "queue", "clear"))
    assert sorted(db.deactivated) == [("session-key", 7), ("session-key", 8)]


def test_editing_a_queued_prompt_rewrites_its_row_instead_of_retiring_it():
    db = _DB()
    session = _session_with_rows(db)
    _result(server._cmd_queue("r", {}, session, "queue", "edit 2 replacement prompt"))
    assert db.deactivated == [], "an edit must not retire the row"
    assert db.rewritten == [("session-key", 8, "replacement prompt")]


def test_listing_never_touches_the_transcript():
    db = _DB()
    session = _session_with_rows(db)
    _result(server._cmd_queue("r", {}, session, "queue", "list"))
    assert db.deactivated == [] and db.rewritten == []