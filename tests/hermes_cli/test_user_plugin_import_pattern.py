"""The ``__init__.py`` import pattern documented for user-installed plugins must work there.

Directory plugins are imported as their own package (rooted at the plugin dir)
for bundled and user plugins alike, so a provider import in a user plugin has to
be relative — the absolute ``plugins.<kind>.<name>.provider`` form only resolves
inside the bundled ``<repo>/plugins`` tree.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

DOC_DIR = Path(__file__).resolve().parents[2] / "website" / "docs" / "developer-guide"
GUIDES = ("web-search-provider-plugin.md", "browser-provider-plugin.md")

PROVIDER_PY = textwrap.dedent(
    '''
    from __future__ import annotations

    from agent.web_search_provider import WebSearchProvider


    class MyBackendWebSearchProvider(WebSearchProvider):
        @property
        def name(self) -> str:
            return "my-backend"

        @property
        def display_name(self) -> str:
            return "My Backend"

        def is_available(self) -> bool:
            return True

        def supports_search(self) -> bool:
            return True

        def supports_extract(self) -> bool:
            return False

        def search(self, query: str, limit: int = 5):
            return {"success": True, "data": {"web": []}}
    '''
).strip()

ABSOLUTE_INIT = (
    "from plugins.web.my_backend.provider import MyBackendWebSearchProvider\n"
    "\n"
    "\n"
    "def register(ctx) -> None:\n"
    "    ctx.register_web_search_provider(MyBackendWebSearchProvider())\n"
)
RELATIVE_INIT = ABSOLUTE_INIT.replace(
    "from plugins.web.my_backend.provider import", "from .provider import"
)


def _write_user_web_plugin(home: Path, init_source: str) -> Path:
    plugin_dir = home / "plugins" / "web" / "my_backend"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "web-my-backend",
                "version": "1.0.0",
                "description": "guide-pattern verification plugin",
                "author": "test",
                "kind": "backend",
                "provides_web_providers": ["my-backend"],
            }
        ),
        encoding="utf-8",
    )
    (plugin_dir / "provider.py").write_text(PROVIDER_PY + "\n", encoding="utf-8")
    (plugin_dir / "__init__.py").write_text(init_source, encoding="utf-8")
    (home / "config.yaml").write_text(
        yaml.safe_dump({"plugins": {"enabled": ["web-my-backend"]}}), encoding="utf-8"
    )
    return plugin_dir


@pytest.fixture
def user_home(tmp_path, monkeypatch):
    """A throwaway HERMES_HOME whose user plugin tree is the only plugin source."""
    from hermes_cli import plugins as plugins_mod

    home = tmp_path / "home"
    home.mkdir()
    empty_bundled = tmp_path / "bundled"
    empty_bundled.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "os-home"))
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(plugins_mod, "get_bundled_plugins_dir", lambda: empty_bundled)
    return home


@pytest.fixture(autouse=True)
def _clean_web_registry():
    from agent import web_search_registry

    web_search_registry._reset_for_tests()
    yield
    web_search_registry._reset_for_tests()


def _load_user_plugin(user_home: Path):
    from hermes_cli.plugins import PluginManager

    manager = PluginManager()
    manager.discover_and_load()
    return manager


def _registered(manager, name: str):
    from agent import web_search_registry

    return web_search_registry.get_provider(name, scope=manager.scope_key)


def test_absolute_import_cannot_load_a_user_plugin(user_home):
    _write_user_web_plugin(user_home, ABSOLUTE_INIT)

    manager = _load_user_plugin(user_home)

    assert _registered(manager, "my-backend") is None
    states = [s for s in manager._plugins.values() if s.error]
    assert states, "expected a load error for the absolute-import plugin"


def test_relative_import_registers_a_user_plugin(user_home):
    _write_user_web_plugin(user_home, RELATIVE_INIT)

    manager = _load_user_plugin(user_home)

    assert not [s for s in manager._plugins.values() if s.error], [
        s.error for s in manager._plugins.values() if s.error
    ]
    assert _registered(manager, "my-backend") is not None


@pytest.mark.parametrize("guide", GUIDES)
def test_guide_never_documents_an_absolute_provider_import(guide):
    text = (DOC_DIR / guide).read_text(encoding="utf-8")
    offending = [
        line for line in text.splitlines()
        if line.startswith("from plugins.") and "provider" in line
    ]
    assert not offending, f"{guide} documents an absolute provider import: {offending}"
    assert "from .provider import" in text, f"{guide} no longer shows the relative import"