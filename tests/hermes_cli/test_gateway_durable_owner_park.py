"""A supervised host owner makes our own refusal permanent, so the unit parks (#132286).

Two units left installed for one host — ``hermes gateway install`` and
``sudo hermes gateway install --system`` — means the owner's unit wins the host and serves the
profile. The other unit asks, is told "already serves profile 'default' — nothing to start", and
exits 75 (EX_TEMPFAIL) so its supervisor retries. The generated unit pairs ``Restart=always``
with ``RestartForceExitStatus=75`` and deliberately sets ``StartLimitIntervalSec=0``, so every
retry lands: the refusal counter climbs without bound and each cycle appends to
``gateway-exit-diag.log``.

The 75 contract is "the owner will go away on its own, try again". A supervised owner never does,
so those refusals park on 78 (``RestartPreventExitStatus``) instead. A shell-launched owner still
retries, which is the case the 75 exists for — and so does an owner that has already vanished,
which is why a process that is GONE must stay distinguishable from one that is merely unreadable.
"""

from __future__ import annotations

import pytest

from gateway import restart as restart_mod
from gateway.restart import (
    GATEWAY_FATAL_CONFIG_EXIT_CODE,
    GATEWAY_SERVICE_RESTART_EXIT_CODE,
    is_supervised_gateway_owner,
)


class _Owner:
    """The slice of ``HostGateway`` the exit-code decision reads."""

    def __init__(self, pid: int = 4242, label: str = "default") -> None:
        self.pid = pid
        self.profile_label = label
        self.served = ("default",)

    def describe(self) -> str:
        return f"PID {self.pid} (profiles: default)"

    def serves(self, profile: str) -> bool:
        return profile in self.served


class _Decision:
    def __init__(self, *, transient: bool, owner=None) -> None:
        self.outcome = "attach"
        self.transient = transient
        self.owner = owner


SUPERVISED_ENV = {"INVOCATION_ID": "abc123", "HERMES_SUPERVISED_CHILD": "1"}
SHELL_ENV = {"HOME": "/home/k", "TERM": "xterm-256color"}


@pytest.fixture
def gw_module():
    from hermes_cli import gateway as gw

    return gw


def _patch_owner(monkeypatch, environ):
    monkeypatch.setattr(restart_mod, "_pid_environ", lambda pid: environ)


# --- the tri-state discriminator -------------------------------------------------


def test_supervised_owner_environment_reads_durable(monkeypatch):
    _patch_owner(monkeypatch, SUPERVISED_ENV)
    assert is_supervised_gateway_owner(4242) is True


def test_shell_launched_owner_reads_transient(monkeypatch):
    _patch_owner(monkeypatch, SHELL_ENV)
    assert is_supervised_gateway_owner(4242) is False


@pytest.mark.parametrize("pid", [None, 0, -1])
def test_absent_pid_is_never_a_transient_owner(pid):
    """No PID to inspect proves nothing; it must not answer ``False`` (that means 'retry me')."""
    assert is_supervised_gateway_owner(pid) is None


def test_own_pid_is_never_inspected():
    """Asking about ourselves would read our own env and always look supervised."""
    import os

    assert is_supervised_gateway_owner(os.getpid()) is None


def test_unreadable_owner_environment_is_unknown_not_transient(monkeypatch):
    """A cross-user owner (system-scope unit, unprivileged unit asking) cannot be inspected.

    An unreadable environment is not evidence of an owner that exits, so it must never answer
    ``False`` — that would restore exactly the loop being fixed.
    """
    _patch_owner(monkeypatch, restart_mod._ENVIRON_DENIED)
    assert is_supervised_gateway_owner(4242) is None


def test_owner_process_gone_reads_transient(monkeypatch):
    """No such process (``_pid_environ`` -> ``None``): the owner can genuinely go away.

    This is the case a supervised unit must keep RETRYING: the observation that "someone serves
    me" was already stale when it was made, so parking on it would strand the profile (#118236
    shaped). It is also why the unreadable case above cannot simply answer ``False``.
    """
    _patch_owner(monkeypatch, None)
    assert is_supervised_gateway_owner(4242) is False


def test_denied_and_gone_are_distinguished(monkeypatch):
    """``None`` (gone) and the denied sentinel (alive but uninspectable) must not collapse.

    Collapsing them is what broke ``test_a_supervised_attach_is_retried_not_parked``: a vanished
    owner was treated as durable, parking a unit whose profile would have started on retry.
    """
    assert _pid_environ_result(monkeypatch, None) is None
    assert _pid_environ_result(monkeypatch, restart_mod._ENVIRON_DENIED) is restart_mod._ENVIRON_DENIED
    assert _pid_environ_result(monkeypatch, SHELL_ENV) is SHELL_ENV


def _pid_environ_result(monkeypatch, value):
    _patch_owner(monkeypatch, value)
    return restart_mod._pid_environ(4242)


# --- the exit-code decision ------------------------------------------------------


def _exit_code(gw, monkeypatch, environ):
    _patch_owner(monkeypatch, environ)
    return gw._host_decision_exit_code(_Decision(transient=True, owner=_Owner()))


def test_supervised_owner_parks_instead_of_retrying(gw_module, monkeypatch):
    """The reported shape: the owner is a unit, so retrying cannot change the verdict."""
    assert _exit_code(gw_module, monkeypatch, SUPERVISED_ENV) == GATEWAY_FATAL_CONFIG_EXIT_CODE


def test_shell_owner_still_retries(gw_module, monkeypatch):
    """The 75 path the existing contract depends on: a manual owner can exit on its own."""
    assert _exit_code(gw_module, monkeypatch, SHELL_ENV) == GATEWAY_SERVICE_RESTART_EXIT_CODE


def test_unreadable_owner_parks_rather_than_looping(gw_module, monkeypatch):
    """Prefer a parked unit over an unbounded loop when the owner is alive but uninspectable."""
    assert _exit_code(gw_module, monkeypatch, restart_mod._ENVIRON_DENIED) \
        == GATEWAY_FATAL_CONFIG_EXIT_CODE


def test_vanished_owner_still_retries(gw_module, monkeypatch):
    """The owner is gone: retrying is the whole point, and parking would strand the profile."""
    assert _exit_code(gw_module, monkeypatch, None) == GATEWAY_SERVICE_RESTART_EXIT_CODE


def test_non_transient_verdict_parks_unchanged(gw_module, monkeypatch):
    """A config-derived refusal was already permanent; the owner probe must not alter it."""
    _patch_owner(monkeypatch, SHELL_ENV)
    decision = _Decision(transient=False, owner=_Owner())
    assert gw_module._host_decision_exit_code(decision) == GATEWAY_FATAL_CONFIG_EXIT_CODE


def test_verdict_without_owner_is_unchanged(gw_module, monkeypatch):
    """``transient`` with no owner proves nothing about supervision: keep the 75 route."""
    _patch_owner(monkeypatch, SUPERVISED_ENV)
    assert gw_module._host_decision_exit_code(_Decision(transient=True, owner=None)) \
        == GATEWAY_SERVICE_RESTART_EXIT_CODE


# --- the loop is actually bounded by the unit we generate ------------------------


def test_generated_unit_parks_the_durable_owner_verdict(gw_module):
    """End to end on the generated unit: 78 is the status it refuses to restart on.

    This is the property that stops the loop. ``Restart=always`` alone would respawn anything;
    ``RestartPreventExitStatus=78`` is the only backstop, and the unit sets
    ``StartLimitIntervalSec=0``, so without the 78 verdict systemd's rate limiter is not there to
    catch the runaway retries either.
    """
    unit = gw_module.generate_systemd_unit(system=False)
    assert f"RestartPreventExitStatus={GATEWAY_FATAL_CONFIG_EXIT_CODE}" in unit
    assert f"RestartForceExitStatus={GATEWAY_SERVICE_RESTART_EXIT_CODE}" in unit
    assert "StartLimitIntervalSec=0" in unit