"""``$HERMES_BIN`` is the one child-launch rung no path can re-derive.

Under a package-manager environment the gateway keeps serving from
``installs/<key>/environments/<gen>/workspace``, which publishes no launcher
beside its own tree. Both child-launch lanes that resolve their launcher from
the filesystem — the cron Bot Chat delivery and the bot relay — used to miss
the override and hand the child a phantom install (#125537, #133325). The
dispatcher has honoured it since #111569's sibling work; these tests pin that
the two lanes now agree with it.

Windows batch shims stay refused (``cmd.exe`` reinterprets an otherwise literal
argument vector), and a bare name must never resolve to a file in the current
directory.
"""

import os
import stat
import sys
from pathlib import Path

import pytest

from hermes_cli import _hermes_bin_env as env_bin


# ── the override itself ──────────────────────────────────────────────────────


def test_absent_override_resolves_to_nothing(monkeypatch):
    """No override must leave every caller on its own fallbacks."""
    monkeypatch.delenv("HERMES_BIN", raising=False)
    assert env_bin.hermes_bin_from_env() is None

    monkeypatch.setenv("HERMES_BIN", "   ")
    assert env_bin.hermes_bin_from_env() is None


def test_path_like_override_becomes_absolute(tmp_path, monkeypatch):
    """A relative path is resolved against the CWD, not passed through: a child
    that starts elsewhere would otherwise read a different file."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HERMES_BIN", "bin/hermes")
    assert env_bin.hermes_bin_from_env() == str(tmp_path / "bin" / "hermes")

    monkeypatch.setenv("HERMES_BIN", str(tmp_path / "hermes"))
    assert env_bin.hermes_bin_from_env() == str(tmp_path / "hermes")


def test_tilde_override_expands(monkeypatch):
    monkeypatch.setenv("HOME", "/opt/hermes home")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/opt/hermes home")))
    monkeypatch.setenv("HERMES_BIN", "~/bin/hermes")
    assert env_bin.hermes_bin_from_env() == "/opt/hermes home/bin/hermes"


def test_bare_name_keeps_path_semantics(tmp_path, monkeypatch):
    """A bare name is a command to find on PATH, not a file next to the caller."""
    bindir = tmp_path / "published"
    bindir.mkdir()
    launcher = bindir / "hermes"
    launcher.touch()
    monkeypatch.setenv("HERMES_BIN", "hermes")
    monkeypatch.setenv("PATH", str(bindir))
    assert env_bin.hermes_bin_from_env() == str(launcher)


def test_unresolvable_bare_name_yields_nothing(monkeypatch):
    """A name PATH does not name must fall through to the caller's own rung,
    never to a literal that the child would fail to exec."""
    monkeypatch.setenv("HERMES_BIN", "hermes-that-does-not-exist")
    monkeypatch.setenv("PATH", os.pathsep.join(["", "."]))
    assert env_bin.hermes_bin_from_env() is None


def test_current_directory_is_never_searched(tmp_path, monkeypatch):
    """``shutil.which`` searches the CWD first on Windows for a bare name; a
    delivery lane must not adopt a file that merely sits where it was invoked."""
    monkeypatch.chdir(tmp_path)
    decoy = tmp_path / "hermes"
    decoy.touch()
    monkeypatch.setenv("HERMES_BIN", "hermes")
    monkeypatch.setenv("PATH", os.pathsep.join(["", "."]))
    assert env_bin.hermes_bin_from_env() is None


def _executable(directory, name):
    """An executable candidate: the Windows branch filters on ``os.X_OK``, which
    ``Path.touch()`` does not set — real launchers ship the bit, so the fixture
    must too."""
    path = directory / name
    path.touch()
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.mark.platforms("windows")
def test_batch_shim_override_is_refused(tmp_path, monkeypatch):
    """``cmd.exe`` reinterprets an otherwise literal argv — a ``.cmd``/``.bat``
    override must not become the child's entry point."""
    monkeypatch.setattr(env_bin, "_is_windows", lambda: True)
    monkeypatch.setenv("HERMES_BIN", str(_executable(tmp_path, "hermes.cmd")))
    assert env_bin.hermes_bin_from_env() is None


@pytest.mark.platforms("windows")
def test_batch_shim_is_refused_when_path_resolves_only_to_one(tmp_path, monkeypatch):
    """A PATH whose only candidate is a shim resolves to nothing usable, so the
    caller keeps its own launcher instead of exec'ing through ``cmd.exe``.

    The name is looked up as ``command + PATHEXT`` suffix, and Windows' default
    order is ``.COM;.EXE;.BAT;.CMD`` — a real .exe is reached first. Only the
    refusal is under test here, so the fixture offers a single candidate and
    depends on neither that ordering nor the filesystem's case sensitivity.
    """
    bindir = tmp_path / "published"
    bindir.mkdir()
    _executable(bindir, "hermes.cmd")
    monkeypatch.setattr(env_bin, "_is_windows", lambda: True)
    monkeypatch.setenv("HERMES_BIN", "hermes")
    monkeypatch.setenv("PATHEXT", ".cmd")
    monkeypatch.setenv("PATH", str(bindir))
    assert env_bin.hermes_bin_from_env() is None


# ── parity with the dispatcher, which established the order ──────────────────


def test_matches_dispatcher_resolution_order(tmp_path, monkeypatch):
    """``kanban_db_dispatch._resolve_hermes_argv`` is the reference order. The two
    must not drift: same override, same refusals, same bare-name semantics."""
    from hermes_cli import kanban_db_dispatch as kbd

    launcher = tmp_path / "hermes"
    launcher.touch()
    monkeypatch.setenv("HERMES_BIN", str(launcher))
    assert kbd._resolve_hermes_argv() == [str(launcher)]
    assert env_bin.hermes_bin_from_env() == str(launcher)

    monkeypatch.setenv("HERMES_BIN", "hermes-absent-on-path")
    monkeypatch.setenv("PATH", os.pathsep.join(["", "."]))
    # The dispatcher falls back to the module form; the shared rung only reports
    # that the override named nothing, leaving the lane's own fallback in place.
    assert kbd._resolve_hermes_argv() == [sys.executable, "-m", "hermes_cli.main"]
    assert env_bin.hermes_bin_from_env() is None


# ── cron Bot Chat delivery ───────────────────────────────────────────────────


def _delivery_argv(monkeypatch, tmp_path, *, env_bin_value):
    """Drive ``_deliver_to_bot_chat`` to the CLI lane and return the child's argv."""
    import subprocess

    from cron import scheduler_delivery as delivery

    captured = {}

    def fake_run(argv, env, report_path, timeout):
        captured["argv"] = list(argv)
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

    # A throwaway home: the lane resolves the delivery target from the profile
    # directory, and this test must not read or write the developer's own.
    home = tmp_path / "hermes-home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(delivery, "_run_bot_chat_turn", fake_run)
    monkeypatch.setattr(delivery.shutil, "which", lambda name: "/usr/bin/hermes")
    if env_bin_value is None:
        monkeypatch.delenv("HERMES_BIN", raising=False)
    else:
        monkeypatch.setenv("HERMES_BIN", env_bin_value)

    assert delivery._deliver_to_bot_chat({"id": "j1", "name": "digest"}, "the output", "") is None
    assert captured["argv"], "delivery never reached the subprocess lane"
    return captured["argv"]


def test_cron_delivery_prefers_the_override_over_the_running_interpreter(monkeypatch, tmp_path):
    """A launcher named by ``$HERMES_BIN`` wins over ``sys.executable -m``: under a
    package-manager workspace the latter re-executes the workspace as its root."""
    launcher = tmp_path / "published" / "hermes"
    launcher.parent.mkdir()
    launcher.touch()
    argv = _delivery_argv(monkeypatch, tmp_path, env_bin_value=str(launcher))
    assert argv[0] == str(launcher)
    assert argv[1:3] != ["-m", "hermes_cli.main"]


def test_cron_delivery_without_override_keeps_the_running_install(monkeypatch, tmp_path):
    """Unchanged default: the running interpreter's module form, never whatever
    ``hermes`` PATH names (#111569)."""
    argv = _delivery_argv(monkeypatch, tmp_path, env_bin_value=None)
    assert argv[:3] == [sys.executable, "-m", "hermes_cli.main"]


def test_cron_delivery_ignores_an_unusable_override(monkeypatch, tmp_path):
    """A name PATH does not resolve must not become the child's argv[0]."""
    argv = _delivery_argv(monkeypatch, tmp_path, env_bin_value="hermes-absent-on-path")
    assert argv[:3] == [sys.executable, "-m", "hermes_cli.main"]


# ── bot relay ────────────────────────────────────────────────────────────────


@pytest.fixture
def relay_launchers(tmp_path, monkeypatch):
    """An install whose published launcher exists, so the override has something
    it must outrank."""
    from tools import bot_relay

    root = tmp_path / "source install"
    module = root / "tools" / "bot_relay.py"
    module.parent.mkdir(parents=True)
    module.touch()
    monkeypatch.setattr(bot_relay, "__file__", str(module))
    name = "hermes.exe" if sys.platform == "win32" else "hermes"
    published = root / ".hermes" / "bin" / name
    published.parent.mkdir(parents=True)
    published.touch()
    return bot_relay, published


def test_relay_prefers_the_override_over_the_published_launcher(relay_launchers, tmp_path):
    bot_relay, published = relay_launchers
    override = tmp_path / "elsewhere" / "hermes"
    override.parent.mkdir()
    override.touch()
    os.environ["HERMES_BIN"] = str(override)
    try:
        argv = bot_relay.local_delivery_command("researcher", "message with spaces.txt")
    finally:
        os.environ.pop("HERMES_BIN", None)
    assert argv[0] == str(override)
    assert str(published) not in argv


def test_relay_keeps_its_launcher_without_an_override(relay_launchers):
    bot_relay, published = relay_launchers
    os.environ.pop("HERMES_BIN", None)
    assert bot_relay.local_delivery_command("default", "body.txt")[0] == str(published)


def test_relay_ignores_an_unusable_override(relay_launchers, monkeypatch):
    """The published launcher still wins when the override names nothing — the
    batch-shim and unresolvable-name refusals must not empty the lane."""
    bot_relay, published = relay_launchers
    monkeypatch.setenv("HERMES_BIN", "hermes-absent-on-path")
    monkeypatch.setenv("PATH", os.pathsep.join(["", "."]))
    assert bot_relay.local_delivery_command("default", "body.txt")[0] == str(published)
