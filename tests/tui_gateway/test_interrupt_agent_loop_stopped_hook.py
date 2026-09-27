"""The TUI/desktop ``session.interrupt`` path is a sibling of the gateway's
``_interrupt_and_clear_session``: when a live turn is stopped, plugins holding
per-turn external resources need the same ``agent_loop_stopped`` signal.
"""

import threading
from unittest.mock import MagicMock, patch

from hermes_constants import get_hermes_home


def _make_session(running: bool, profile_home: str | None = None) -> dict:
    session = {
        "history_lock": threading.Lock(),
        "running": running,
        "queued_prompt": None,
        "session_key": "agent:main:tui:dm:s1",
        "agent": MagicMock(),
        "_run_thread": None,
    }
    if profile_home is not None:
        session["profile_home"] = profile_home
    return session


def _hook_calls(mock_invoke_hook):
    return [
        call
        for call in mock_invoke_hook.call_args_list
        if call.args and call.args[0] == "agent_loop_stopped"
    ]


@patch("hermes_cli.plugins.invoke_hook")
def test_interrupt_running_turn_fires_agent_loop_stopped(mock_invoke_hook):
    from tui_gateway import server

    session = _make_session(running=True)
    with patch.object(server, "_clear_pending"):
        server._interrupt_session_turn("s1", session)

    calls = _hook_calls(mock_invoke_hook)
    assert len(calls) == 1
    assert calls[0].kwargs == {
        "session_key": "agent:main:tui:dm:s1",
        "profile_home": str(get_hermes_home()),
        "platform": "tui",
        "reason": "user_stop",
        "invalidation_reason": "session_interrupt",
    }


@patch("hermes_cli.plugins.invoke_hook")
def test_interrupt_idle_session_does_not_fire_hook(mock_invoke_hook):
    """No live turn -> nothing for a plugin to cancel -> no hook noise."""
    from tui_gateway import server

    session = _make_session(running=False)
    with patch.object(server, "_clear_pending"):
        server._interrupt_session_turn("s1", session)

    assert _hook_calls(mock_invoke_hook) == []


@patch("hermes_cli.plugins.invoke_hook")
def test_hook_failure_does_not_break_interrupt(mock_invoke_hook):
    """A misbehaving plugin must never prevent the interrupt itself."""
    mock_invoke_hook.side_effect = RuntimeError("plugin exploded")
    from tui_gateway import server

    session = _make_session(running=True)
    with patch.object(server, "_clear_pending"):
        server._interrupt_session_turn("s1", session)

    # The cancel flag was still set despite the hook blowing up.
    assert session["_turn_cancel_requested"] is True


def test_interrupt_hook_observer_runs_in_selected_profile_scope(tmp_path, monkeypatch):
    """Two profile stores sharing one durable session key: the observer's ambient
    home during the hook must be the SELECTED session's profile, not the backend's
    launch profile (get_hermes_home() inside the callback reads the scope)."""
    from hermes_cli import plugins
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    from tui_gateway import server

    launch = tmp_path / "launch"
    selected = tmp_path / "selected"
    launch.mkdir()
    selected.mkdir()
    token = set_hermes_home_override(launch)
    seen = []
    manager = plugins.PluginManager()
    monkeypatch.setattr(plugins, "_delivery_manager", lambda: manager)
    ctx = plugins.PluginContext(
        plugins.PluginManifest(name="profile-observer"), manager
    )
    ctx.register_hook(
        "agent_loop_stopped",
        lambda **event: seen.append((
            get_hermes_home().resolve(),
            event.get("profile_home"),
        )),
    )
    session = _make_session(running=True, profile_home=str(selected))
    try:
        with patch.object(server, "_clear_pending"):
            server._interrupt_session_turn("s1", session)
    finally:
        reset_hermes_home_override(token)

    assert len(seen) == 1
    assert seen[0][0] == selected.resolve()
    assert seen[0][1] == str(selected)


@patch("hermes_cli.plugins.invoke_hook")
def test_interrupt_rpc_path_fires_hook_in_session_profile_scope(
    mock_invoke_hook, tmp_path, monkeypatch
):
    """The session.interrupt RPC handler itself (not just the helper) dispatches
    the hook inside the session's profile scope; same-key stores disambiguate."""
    from tui_gateway import server

    launch = tmp_path / "launch"
    selected = tmp_path / "selected"
    launch.mkdir()
    selected.mkdir()
    sid = "profiled-tab"
    session = _make_session(running=True, profile_home=str(selected))
    server._sessions[sid] = session
    monkeypatch.setattr(server, "_load_cfg", lambda: {})
    try:
        response = server.handle_request({
            "id": "intr",
            "method": "session.interrupt",
            "params": {"session_id": sid},
        })
        assert response["result"]["status"] == "interrupted"
        calls = _hook_calls(mock_invoke_hook)
        assert len(calls) == 1
        assert calls[0].kwargs["profile_home"] == str(selected)
        assert calls[0].kwargs["session_key"] == "agent:main:tui:dm:s1"
    finally:
        server._sessions.pop(sid, None)
