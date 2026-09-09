"""config.get full: PyYAML timestamps must reach the TUI as JSON-safe ISO-8601 text (issue #106182).

An unquoted YAML scalar like ``granted_at: 2026-08-17T14:50:10Z`` parses to
``datetime.datetime``; the stdio/ws transports serialize responses with plain
``json.dumps``, so the child died with ``TypeError: Object of type datetime is
not JSON serializable`` and the TUI showed ``gateway exited (1)``.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

import tui_gateway.server as server


def _write_cfg(home: Path, extra_yaml: str) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        "terminal:\n  cwd: /workspace/default\n" + extra_yaml, encoding="utf-8"
    )


def _reset_cfg_cache() -> None:
    server._cfg_cache = None
    server._cfg_mtime = None
    server._cfg_path = None


def _get_full(monkeypatch, tmp_path: Path) -> dict:
    home = tmp_path / "launch"
    _write_cfg(
        home,
        "custom_section:\n  granted_at: 2026-08-17T14:50:10Z\n  expires: 2026-12-31\n",
    )
    monkeypatch.setattr(server, "_hermes_home", home)
    monkeypatch.setattr(server, "_profile_home", lambda name: None)
    _reset_cfg_cache()
    return server._methods["config.get"]("rid-full", {"key": "full"})


def test_config_get_full_normalizes_yaml_datetimes_to_iso_text(monkeypatch, tmp_path):
    resp = _get_full(monkeypatch, tmp_path)

    section = resp["result"]["config"]["custom_section"]
    assert section["granted_at"] == "2026-08-17T14:50:10+00:00"
    assert section["expires"] == "2026-12-31"

    # The transport serializes with plain json.dumps — the full response must survive it.
    encoded = json.dumps(resp, ensure_ascii=False)
    assert "2026-08-17T14:50:10+00:00" in encoded


def test_config_get_full_keeps_plain_values_untouched(monkeypatch, tmp_path):
    resp = _get_full(monkeypatch, tmp_path)

    assert resp["result"]["config"]["terminal"]["cwd"] == "/workspace/default"
    assert json.dumps(resp, ensure_ascii=False)


def test_yaml_safe_load_still_parses_timestamps(monkeypatch, tmp_path):
    # Sanity for the regression premise: PyYAML really loads these scalars as datetime.
    loaded = yaml.safe_load("granted_at: 2026-08-17T14:50:10Z\nexpires: 2026-12-31\n")
    assert isinstance(loaded["granted_at"], __import__("datetime").datetime)
    assert isinstance(loaded["expires"], __import__("datetime").date)
