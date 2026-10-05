"""A prune/archive preview must show the values its filters selected on.

`list_prune_candidates` projected 9 columns while its WHERE clause could select on 22, so a
`--min-cost 0.005` run confirmed a deletion without printing a single cost figure — and
`--before`/`--after` announced "started before T" above a column rendering last activity, so the
date read back was not the date the bound was on. Both are driven here through the production
`cmd_sessions` entry point against a REAL session store.
"""

import time
from argparse import Namespace

import pytest

from hermes_state import SessionDB


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A real SessionDB the CLI resolves to, with ended rows carrying the filterable dimensions."""
    import hermes_state

    db_path = tmp_path / "state.db"
    monkeypatch.setattr(hermes_state, "_default_db_path", lambda: db_path)
    store = SessionDB(db_path=db_path)
    _seed(store)
    yield store
    store.close()


def _seed(store):
    now = time.time()
    for sid, cols in (
        ("20260907_215032_a5eebd", dict(
            source="desktop", title="Long chat", message_count=177,
            started_at=now - 30 * 86400, last_activity_at=now - 4 * 86400,
            input_tokens=120000, output_tokens=8000, tool_call_count=42,
            estimated_cost_usd=0.0060, billing_provider="openrouter",
            git_branch="fix/sessions-preview", end_reason="user", cwd="/Users/me/proj",
            user_id="u-42", chat_id="c-7", chat_type="dm")),
        ("20260831_165124_0644f6", dict(
            source="cli", title="Greeting", message_count=139,
            started_at=now - 35 * 86400, last_activity_at=now - 5 * 86400,
            input_tokens=4000, output_tokens=900, tool_call_count=3,
            actual_cost_usd=0.0110, end_reason="user")),
    ):
        store.create_session(sid, source=cols["source"])
        store.set_session_title(sid, cols["title"])
        store.end_session(sid, cols["end_reason"])
        sets = ", ".join(f"{k} = ?" for k in cols)
        store._conn.execute(
            f"UPDATE sessions SET {sets} WHERE id = ?", (*cols.values(), sid))
    store._conn.commit()


def _run(db, monkeypatch, capsys, *flags):
    """`hermes sessions prune --dry-run <flags>` through the production entry point."""
    import hermes_cli.sessions_cmd as sessions_cmd

    args = Namespace(
        sessions_action="prune", dry_run=True, yes=True, never_active=False,
        include_archived=False, include_pinned=False,
        **{name: None for name in sessions_cmd._FILTER_ARGS})
    for i in range(0, len(flags), 2):
        setattr(args, flags[i], flags[i + 1])
    assert sessions_cmd.cmd_sessions(args) is None
    return capsys.readouterr().out


def _row(out, sid):
    return next(line for line in out.splitlines() if line.strip().startswith(sid))


def test_min_cost_preview_shows_the_cost_it_filtered_on(db, monkeypatch, capsys):
    out = _run(db, monkeypatch, capsys, "min_cost", 0.005)

    assert "Cost" in out
    # Estimated and billed stay distinguishable: an unlabelled figure reads as the amount charged,
    # and the estimate can undercount real tier-priced billing several times (#109976).
    assert "$0.0060 est." in _row(out, "20260907_215032_a5eebd")
    assert "$0.0110 actual" in _row(out, "20260831_165124_0644f6")


def test_min_tokens_and_min_tool_calls_preview_show_both(db, monkeypatch, capsys):
    out = _run(db, monkeypatch, capsys, "min_tokens", 1000, "min_tool_calls", 1)

    row = _row(out, "20260907_215032_a5eebd")
    assert "Tokens" in out and "Calls" in out
    assert "128,000" in row  # 120000 in + 8000 out, the value the bound selects on
    assert "42" in row


def test_start_time_window_previews_started_at_not_last_activity(db, monkeypatch, capsys):
    # last_activity_at is 4d ago, started_at is 30d ago: the two bounds see different rows, so the
    # printed date has to be the one the window filtered on.
    out = _run(db, monkeypatch, capsys, "before", "20d")

    assert "Started" in out
    assert "Last Active" not in out
    assert "oldest start" in out and "newest start" in out
    started = _row(out, "20260907_215032_a5eebd")
    stored = db.get_session("20260907_215032_a5eebd")["started_at"]
    assert time.strftime("%Y-%m-%d %H:%M", time.localtime(stored)) in started


def test_activity_window_keeps_last_active_and_no_extra_columns(db, monkeypatch, capsys):
    out = _run(db, monkeypatch, capsys, "older_than", "1d")

    assert "Last Active" in out
    assert "oldest activity" in out
    # Dimensions no active filter selects on stay off the row.
    for absent in ("Cost", "Tokens", "Calls", "Branch", "Chat type"):
        assert absent not in out


def test_dimension_filters_print_the_dimension_they_matched(db, monkeypatch, capsys):
    out = _run(db, monkeypatch, capsys, "branch", "fix/sessions")

    row = _row(out, "20260907_215032_a5eebd")
    assert "Branch" in out and "fix/sessions" in row


def test_candidate_row_carries_every_filterable_dimension(db):
    """The preview can only show what the projection fetches — costs were never selected at all."""
    row = db.list_prune_candidates(older_than_days=None)[0]

    for key in ("input_tokens", "output_tokens", "tool_call_count", "actual_cost_usd",
                "estimated_cost_usd", "billing_provider", "git_branch", "end_reason", "pinned",
                "cwd", "user_id", "chat_id", "chat_type", "ended_at", "archived"):
        assert key in row, key


def test_rest_dry_run_row_is_not_narrower_than_the_cli_projection(db):
    """`POST /api/sessions/prune` with dry_run reads the same projection, so its row keys must
    match it — otherwise the API confirms a min_cost prune with no cost on the row."""
    from hermes_cli.web_routers.sessions import _PRUNE_ROW_KEYS

    row = db.list_prune_candidates(older_than_days=None)[0]

    assert set(_PRUNE_ROW_KEYS) <= set(row)
    for key in ("actual_cost_usd", "estimated_cost_usd", "tool_call_count", "git_branch", "chat_type"):
        assert key in _PRUNE_ROW_KEYS, key
