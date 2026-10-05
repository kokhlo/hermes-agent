"""First-party evidence for retention sweeps.

A prune that records nothing leaves a board where a missing event id and an
unexplained deletion look identical. Every sweep must leave a ``gc_runs`` row
naming its window, so an auditor can attribute the gap — and the record itself
must survive the next sweep, which is the property a board-level event could
not provide.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_ops


@pytest.fixture
def board(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    return tmp_path


def _aged_task(conn, *, status: str = "done", extra_events: int = 2) -> tuple[str, list[int]]:
    """A card whose whole history predates any sane retention.

    Returns the task id and the event ids it owns, so callers assert against
    what the board actually holds instead of a count they assumed:
    ``create_task`` emits an event of its own before any extra ones are added,
    and a fresh event stays outside the cutoff for a full second, which would
    turn the sweep into a race against the clock rather than a fixed predicate.
    Aging every row removes both traps.
    """
    tid = kb.create_task(conn, title="card")
    with kb.write_txn(conn):
        conn.execute("UPDATE tasks SET status=? WHERE id=?", (status, tid))
        for _ in range(extra_events):
            conn.execute(
                "INSERT INTO task_events (task_id, kind, payload, created_at) "
                "VALUES (?, 'note', NULL, 0)",
                (tid,),
            )
        conn.execute("UPDATE task_events SET created_at=0 WHERE task_id=?", (tid,))
    return tid, _event_ids(conn, tid)


def _event_ids(conn, tid):
    return [row["id"] for row in conn.execute(
        "SELECT id FROM task_events WHERE task_id=? ORDER BY id", (tid,)
    )]


def _gc_runs(conn):
    return kb.list_gc_runs(conn)


def _digest(ids):
    return hashlib.sha256(",".join(str(i) for i in sorted(ids)).encode()).hexdigest()


def _old_log(name: str) -> Path:
    log_dir = kb.worker_logs_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"{name}.log"
    path.write_text("log line")
    os.utime(path, (0, 0))
    return path


def test_gc_events_records_the_window_and_the_exact_ids(board):
    """The record must be enough to reconstruct the prune: which cards, which
    ids, which cutoff — and the digest must match those ids."""
    with kbc.connect_closing() as conn:
        tid, doomed = _aged_task(conn, extra_events=4)
        assert kb.gc_events(conn, older_than_seconds=1) == len(doomed)
        assert _event_ids(conn, tid) == []

        runs = _gc_runs(conn)
        assert len(runs) == 1
        run = runs[0]
        assert run["deleted_count"] == len(doomed)
        assert run["task_ids"] == [tid]
        assert run["min_event_id"] == min(doomed)
        assert run["max_event_id"] == max(doomed)
        assert run["retention_seconds"] == 1
        assert run["cutoff"] == pytest.approx(run["run_at"] - 1, abs=5)
        assert run["deleted_ids_digest"] == _digest(doomed)


def test_gc_events_record_matches_the_predicate_for_every_missing_id(board):
    """The audit an auditor actually performs: every id that vanished falls
    inside a recorded window, and nothing outside that set went missing."""
    with kbc.connect_closing() as conn:
        tid, gone = _aged_task(conn, extra_events=3)
        assert kb.gc_events(conn, older_than_seconds=1) == len(gone)

        # A live card's fresh events fall outside the same window.
        survivor = kb.create_task(conn, title="still running")
        survivor_ids = _event_ids(conn, survivor)
        assert kb.gc_events(conn, older_than_seconds=1) == 0

        runs = _gc_runs(conn)
        assert len(runs) == 2
        assert [r["deleted_count"] for r in runs] == [0, len(gone)]

        remaining = {row["id"] for row in conn.execute("SELECT id FROM task_events")}
        assert set(gone).isdisjoint(remaining)
        # Every id inside a recorded min/max really was one the sweep removed.
        recorded = {
            i
            for run in runs
            if run["min_event_id"] is not None
            for i in range(run["min_event_id"], run["max_event_id"] + 1)
        }
        assert recorded <= set(gone)
        assert _event_ids(conn, survivor) == survivor_ids


def test_a_sweep_with_nothing_to_delete_still_records_that_it_ran(board):
    """An empty sweep is evidence too: 'I pruned nothing' is what makes a later
    gap attributable to someone else."""
    with kbc.connect_closing() as conn:
        assert kb.gc_events(conn, older_than_seconds=1) == 0
        runs = _gc_runs(conn)
        assert len(runs) == 1
        assert runs[0]["deleted_count"] == 0
        assert runs[0]["task_ids"] == []
        assert runs[0]["deleted_ids_digest"] is None
        assert runs[0]["min_event_id"] is None and runs[0]["max_event_id"] is None


def test_the_evidence_survives_the_next_sweep(board):
    """Why the record lives in its own table instead of a ``gc_pruned`` event.

    A witness row written into task_events carries a done task_id, a kind the
    predicate does not exempt and a created_at that ages past the next cutoff —
    so the second sweep deletes the witness and the trail is empty again. This
    is the regression that keeps variant 2 of the issue from coming back: three
    sweeps, three readable records, and not one task_events row left to hold the
    evidence.
    """
    with kbc.connect_closing() as conn:
        swept = []
        for _ in range(3):
            _tid, ids = _aged_task(conn, extra_events=2)
            swept.append(ids)
            kb.gc_events(conn, older_than_seconds=1)

        runs = _gc_runs(conn)
        assert len(runs) == 3
        # list_gc_runs is newest-first, so the newest record heads the list.
        assert [r["deleted_count"] for r in runs] == [len(ids) for ids in reversed(swept)]
        assert {r["deleted_ids_digest"] for r in runs} == {_digest(ids) for ids in swept}
        assert conn.execute("SELECT count(*) FROM task_events").fetchone()[0] == 0


def test_the_evidence_is_not_itself_prunable(board):
    """A gc_runs row is not a task_event, so no retention window reaches it.

    The second sweep takes the widest window the CLI allows (0 seconds = every
    event older than now): it removes the aged events of a finished card while
    a running card's events survive only because the status predicate excludes
    them. Both records survive the window that removed the rows.
    """
    with kbc.connect_closing() as conn:
        _aged_task(conn, extra_events=2)
        kb.gc_events(conn, older_than_seconds=1)
        # Backdate the first record past any cutoff a later sweep could pick.
        with kb.write_txn(conn):
            conn.execute("UPDATE gc_runs SET run_at=0")

        finished, finished_ids = _aged_task(conn, extra_events=2)
        live, _live_ids = _aged_task(conn, status="running", extra_events=2)
        assert kb.gc_events(conn, older_than_seconds=0) == len(finished_ids)
        assert _event_ids(conn, finished) == []
        assert _event_ids(conn, live)
        assert len(_gc_runs(conn)) == 2


def test_a_failed_sweep_leaves_no_claim_that_rows_are_missing(board):
    """The record shares the DELETE's transaction. If the delete fails, the
    evidence must roll back with it — a board claiming rows are gone while they
    are still there is worse than a board with no record at all."""
    with kbc.connect_closing() as conn:
        tid, before = _aged_task(conn, extra_events=3)

        real_execute = conn.execute

        class Boom(RuntimeError):
            pass

        def exploding_execute(sql, *args):
            if sql.strip().upper().startswith("DELETE FROM TASK_EVENTS"):
                raise Boom("delete failed")
            return real_execute(sql, *args)

        conn.execute = exploding_execute
        try:
            with pytest.raises(Boom):
                kb.gc_events(conn, older_than_seconds=1)
        finally:
            del conn.execute

        assert _event_ids(conn, tid) == before
        assert _gc_runs(conn) == []


def test_gc_worker_logs_records_the_removed_files(board):
    """A missing worker log must be distinguishable from one never written."""
    old = _old_log("t_old")
    fresh = kb.worker_logs_dir() / "t_fresh.log"
    fresh.write_text("fresh")

    with kbc.connect_closing() as conn:
        assert kb.gc_worker_logs(older_than_seconds=1, conn=conn) == 1
        assert not old.exists()
        assert fresh.exists()
        runs = _gc_runs(conn)
        assert runs[0]["deleted_count"] == 1
        assert runs[0]["task_ids"] == ["t_old"]
        # The file sweep has no event ids, so the digest stays NULL rather than
        # claiming an id range it cannot mean.
        assert runs[0]["deleted_ids_digest"] is None
        assert runs[0]["min_event_id"] is None


def test_gc_worker_logs_without_a_connection_still_sweeps(board):
    """The conn is additive: callers that pass none keep working unchanged."""
    old = _old_log("t_old")
    assert kb.gc_worker_logs(older_than_seconds=1) == 1
    assert not old.exists()


def test_cmd_gc_records_both_sweeps(board):
    """The CLI is the path a scheduled gc actually takes; both sweeps there
    must leave evidence."""
    with kbc.connect_closing() as conn:
        _tid, ids = _aged_task(conn, extra_events=3)
    _old_log("t_old")

    args = argparse.Namespace(event_retention_days=30, log_retention_days=30)
    assert kanban_ops._cmd_gc(args) == 0

    with kbc.connect_closing() as conn:
        runs = _gc_runs(conn)
        assert any(r["deleted_ids_digest"] == _digest(ids) for r in runs)
        assert any(r["task_ids"] == ["t_old"] for r in runs)


def test_a_board_predating_the_table_gains_it_on_init(board):
    """Boards that predate the table must gain it through the normal init pass,
    not fresh ones only."""
    with kbc.connect_closing() as conn:
        conn.execute("DROP TABLE gc_runs")
    kbc.init_db()
    with kbc.connect_closing() as conn:
        assert kb.gc_events(conn, older_than_seconds=1) == 0
        assert len(_gc_runs(conn)) == 1


def test_gc_runs_json_column_round_trips(board):
    """list_gc_runs hands callers real lists, not the raw JSON text."""
    with kbc.connect_closing() as conn:
        tid, _ids = _aged_task(conn, extra_events=1)
        kb.gc_events(conn, older_than_seconds=1)
        raw = conn.execute(
            "SELECT task_ids FROM gc_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        assert json.loads(raw) == [tid]
        assert _gc_runs(conn)[0]["task_ids"] == [tid]