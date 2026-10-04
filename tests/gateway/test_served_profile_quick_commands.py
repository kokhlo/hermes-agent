"""A multiplexed gateway must consult the quick commands of the profile whose bot was addressed.

Under ``gateway.multiplex_profiles`` the runner's ``config`` is the launch profile's alone, so a quick
command the user defined in the ``config.yaml`` of a served profile was never looked up and answered
"Unknown command" — even though the gateway had already loaded that very file into ``_profile_configs``
(#132517).

The lookup itself is covered through ``_hm_quick_commands(source)`` on a real ``GatewayRunner`` built the
way test_slash_access_dispatch.py builds one, and the alias branch is driven end to end through
``_hm_resolve_command``. Quick commands are deliberately exercised as ``type: alias`` rather than
``type: exec``: ``exec`` shells out and trips the real-home I/O guard, which would make the assertion
depend on the host's HERMES_HOME rather than on the profile resolution under test.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.event import MessageEvent
from gateway.session import SessionEntry, SessionSource
from gateway.session_identity import RoutingIdentity


def _make_source(
    *,
    platform: Platform = Platform.TELEGRAM,
    user_id: str = "user1",
    chat_type: str = "dm",
    chat_id: str = "c1",
    profile: str | None = None,
) -> SessionSource:
    return SessionSource(
        platform=platform,
        user_id=user_id,
        chat_id=chat_id,
        user_name=f"name-{user_id}",
        chat_type=chat_type,
        profile=profile,
    )


def _routed_to(profile: str, *, transport: str) -> SessionSource:
    """A source the multiplexer routed to the served *profile* while the receiving bot is *transport*."""
    source = _make_source(profile=profile)
    source._identity = RoutingIdentity(
        transport_profile=transport,
        runtime_profile=profile,
        authorization_home=Path("/profiles/primary"),
        runtime_home=Path(f"/profiles/{profile}"),
    )
    return source


def _make_event(text: str, source: SessionSource) -> MessageEvent:
    return MessageEvent(text=text, source=source, message_id="m1")


def _make_runner(*, multiplex_profiles: bool = False, platform_extra: dict | None = None,
                 platform: Platform = Platform.TELEGRAM, served_quick_commands: dict | None = None,
                 served_profile: str = "b"):
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        multiplex_profiles=multiplex_profiles,
        platforms={
            platform: PlatformConfig(enabled=True, token="***", extra=platform_extra or {})
        },
    )
    adapter = MagicMock()
    adapter.send = AsyncMock()
    runner.adapters = {platform: adapter}
    runner._voice_mode = {}
    runner.hooks = SimpleNamespace(
        emit=AsyncMock(), emit_collect=AsyncMock(return_value=[]), loaded_hooks=False,
    )
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = SessionEntry(
        session_key="agent:primary:telegram:dm:c1",
        session_id="sess-1",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=platform,
        chat_type="dm",
        total_tokens=0,
    )
    runner.session_store.load_transcript.return_value = []
    runner.session_store.has_any_sessions.return_value = True
    runner.session_store.append_to_transcript = MagicMock()
    runner.session_store.rewrite_transcript = MagicMock()
    runner.session_store.update_session = MagicMock()
    runner._running_agents = {}
    runner._primary_profile_name = "primary"
    runner._profile_configs = {}
    if served_quick_commands is not None:
        served = GatewayConfig()
        served.quick_commands = served_quick_commands
        runner._profile_configs = {served_profile: served}
    runner._running_agents_ts = {}
    runner._session_run_generation = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._session_sources = {}
    runner._session_db = MagicMock()
    runner._session_db.get_session_title.return_value = None
    runner._session_db.get_session.return_value = None
    runner._reasoning_config = None
    runner._provider_routing = {}
    runner._fallback_model = None
    runner._show_reasoning = False
    runner._is_user_authorized = lambda _source: True
    runner._set_session_env = lambda _context: None
    runner._should_send_voice_reply = lambda *_args, **_kwargs: False
    runner._send_voice_reply = AsyncMock()
    runner._capture_gateway_honcho_if_configured = lambda *args, **kwargs: None
    runner._emit_gateway_run_progress = AsyncMock()
    return runner


# ---------------------------------------------------------------------------
# The reported bug: the served profile's own quick_commands were never read
# ---------------------------------------------------------------------------


def test_served_profile_quick_commands_resolve():
    """The quick command lives only in served profile b's config.yaml and b's bot received the message, so
    b's mapping is the one consulted. Before the fix this returned {} — the launch config's — and the
    message fell through to "Unknown command"."""
    runner = _make_runner(
        multiplex_profiles=True,
        served_quick_commands={"deep": {"type": "alias", "target": "/model gpt-5.5 --session"}},
    )

    resolved = runner._hm_quick_commands(_routed_to("b", transport="b"))

    assert resolved == {"deep": {"type": "alias", "target": "/model gpt-5.5 --session"}}


def test_launch_profile_quick_commands_still_resolve():
    """The launch profile's own bot keeps reading the launch config, served profile or not."""
    runner = _make_runner(
        multiplex_profiles=True,
        served_quick_commands={"deep": {"type": "alias", "target": "/model gpt-5.5 --session"}},
    )
    runner.config.quick_commands = {"shallow": {"type": "alias", "target": "/model gpt-4.5"}}

    resolved = runner._hm_quick_commands(_make_source())

    assert resolved == {"shallow": {"type": "alias", "target": "/model gpt-4.5"}}


def test_served_profile_does_not_borrow_launch_commands():
    """A served profile must not pick up the launch profile's mapping. Copying the block into the launch
    home is exactly the workaround that made one edit serve every profile — the coupling #132517 names."""
    runner = _make_runner(
        multiplex_profiles=True,
        served_quick_commands={"deep": {"type": "alias", "target": "/model gpt-5.5 --session"}},
    )
    runner.config.quick_commands = {"shallow": {"type": "alias", "target": "/model gpt-4.5"}}

    resolved = runner._hm_quick_commands(_routed_to("b", transport="b"))

    assert "shallow" not in resolved


def test_uncached_served_profile_falls_back_to_launch():
    """A served profile whose config is not cached falls back to the launch config instead of raising, so
    nothing that resolves today stops resolving. Deliberately the opposite posture from the slash gate,
    which fails closed because that is an authorization decision rather than a shortcut lookup."""
    runner = _make_runner(multiplex_profiles=True)
    runner.config.quick_commands = {"shallow": {"type": "alias", "target": "/model gpt-4.5"}}

    resolved = runner._hm_quick_commands(_routed_to("ghost", transport="ghost"))

    assert resolved == {"shallow": {"type": "alias", "target": "/model gpt-4.5"}}


def test_single_profile_gateway_ignores_routed_source():
    """Without multiplexing there is no second config to consult, so a source carrying a profile name
    must not change what resolves — the common non-multiplex setup is untouched."""
    runner = _make_runner(
        multiplex_profiles=False,
        served_quick_commands={"deep": {"type": "alias", "target": "/model gpt-5.5 --session"}},
    )
    runner.config.quick_commands = {"shallow": {"type": "alias", "target": "/model gpt-4.5"}}

    resolved = runner._hm_quick_commands(_routed_to("b", transport="b"))

    assert resolved == {"shallow": {"type": "alias", "target": "/model gpt-4.5"}}


def test_no_source_reads_launch_config():
    """Callers that genuinely have no routed source (startup paths, the launch bot itself) keep the old
    launch-config behaviour."""
    runner = _make_runner(
        multiplex_profiles=True,
        served_quick_commands={"deep": {"type": "alias", "target": "/model gpt-5.5 --session"}},
    )
    runner.config.quick_commands = {"shallow": {"type": "alias", "target": "/model gpt-4.5"}}

    assert runner._hm_quick_commands() == {"shallow": {"type": "alias", "target": "/model gpt-4.5"}}


def test_malformed_quick_commands_still_yield_empty_dict():
    """The unset/malformed guard from before the fix is intact for the served profile's config too."""
    runner = _make_runner(multiplex_profiles=True)
    served = GatewayConfig()
    served.quick_commands = ["not", "a", "mapping"]
    runner._profile_configs = {"b": served}

    assert runner._hm_quick_commands(_routed_to("b", transport="b")) == {}


# ---------------------------------------------------------------------------
# End to end through the alias branch the reporter exercised
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_served_profile_alias_expands_to_its_own_target():
    """The reporter's /deep → /model alias, driven through the real resolve step: the alias is found in
    the served profile and rewritten to that profile's target, so dispatch continues on /model instead of
    reporting an unknown command."""
    runner = _make_runner(
        multiplex_profiles=True,
        served_quick_commands={"deep": {"type": "alias", "target": "/model gpt-5.5 --provider openrouter"}},
    )
    source = _routed_to("b", transport="b")
    event = _make_event("/deep", source)

    _handled, _result, command, canonical = await runner._hm_resolve_command(event, source, "quick-key")

    assert command == "model"
    assert canonical == "model"
    assert event.text == "/model gpt-5.5 --provider openrouter"


@pytest.mark.asyncio
async def test_absent_alias_stays_unknown():
    """A name no profile defines is still unknown — the fix widens where a command may come from, it does
    not invent matches."""
    runner = _make_runner(
        multiplex_profiles=True,
        served_quick_commands={"deep": {"type": "alias", "target": "/model gpt-5.5 --session"}},
    )
    source = _routed_to("b", transport="b")
    event = _make_event("/nonexistent", source)

    _handled, _result, command, canonical = await runner._hm_resolve_command(event, source, "quick-key")

    assert command == "nonexistent"
    assert event.text == "/nonexistent"