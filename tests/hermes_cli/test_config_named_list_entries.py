"""``hermes config set`` must reach a list entry by its ``name``, the way every other path in
``hermes_cli.config`` already treats these lists (``_items_by_unique_name``).

``custom_providers`` is a list of mappings each carrying a ``name``, and the docs/user model is
name-keyed. The writer accepted only a numeric position, so the obvious command
``hermes config set custom_providers.ygg-route.base_url ...`` died on
``TypeError: Cannot navigate into list ... segment 'ygg-route' is not a numeric index`` — a
traceback, because ``set_config_value`` only caught ``ValueError`` around the write.

A position is also the one address a user cannot rely on: entries get appended and reordered, and a
position that moved silently rewrites the wrong provider. Name resolution prefers the longest dotted
match, so a provider name containing a dot reaches the whole entry instead of its first segment, and
ambiguity (two entries sharing a name) is refused rather than resolved to whichever came first.
"""

import os
from unittest.mock import patch

import pytest
import hermes_yaml as yaml

from hermes_cli.config import (
    _MISSING,
    _get_nested,
    _unset_nested,
    get_config_value,
    set_config_value,
    unset_config_value,
)


@pytest.fixture(autouse=True)
def _isolated_hermes_home(tmp_path):
    (tmp_path / ".env").touch()
    with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
        yield tmp_path


def _write_config(home, body):
    (home / "config.yaml").write_text(body)


def _load(home):
    return yaml.safe_load((home / "config.yaml").read_text())


TWO_PROVIDERS = (
    "custom_providers:\n"
    "- name: ygg-route\n"
    "  base_url: https://old.example/v1\n"
    "  api_key: key-a\n"
    "  models:\n"
    "    fast: {}\n"
    "- name: other\n"
    "  base_url: https://other.example/v1\n"
    "  api_key: key-b\n"
)


class TestNameAddressedWrite:
    def test_named_entry_write_lands_on_that_entry(self, _isolated_hermes_home):
        _write_config(_isolated_hermes_home, TWO_PROVIDERS)

        set_config_value("custom_providers.ygg-route.base_url", "https://new.example/v1")

        entries = _load(_isolated_hermes_home)["custom_providers"]
        assert entries[0]["base_url"] == "https://new.example/v1"
        assert entries[0]["name"] == "ygg-route"

    def test_named_entry_write_leaves_siblings_untouched(self, _isolated_hermes_home):
        _write_config(_isolated_hermes_home, TWO_PROVIDERS)

        set_config_value("custom_providers.ygg-route.base_url", "https://new.example/v1")

        entries = _load(_isolated_hermes_home)["custom_providers"]
        assert entries[1] == {"name": "other", "base_url": "https://other.example/v1",
                              "api_key": "key-b"}
        # Fields the write did not name survive, nested ones included.
        assert entries[0]["api_key"] == "key-a"
        assert set(entries[0]["models"]) == {"fast"}

    def test_named_entry_write_follows_the_entry_when_the_list_is_reordered(self, _isolated_hermes_home):
        """The point of name addressing: a reordered list still gets the entry the user named."""
        _write_config(_isolated_hermes_home,
                      "custom_providers:\n"
                      "- name: other\n"
                      "  base_url: https://other.example/v1\n"
                      "- name: ygg-route\n"
                      "  base_url: https://old.example/v1\n")

        set_config_value("custom_providers.ygg-route.base_url", "https://new.example/v1")

        entries = _load(_isolated_hermes_home)["custom_providers"]
        assert entries[0]["base_url"] == "https://other.example/v1"
        assert entries[1]["base_url"] == "https://new.example/v1"

    def test_nested_key_inside_the_named_entry_is_reachable(self, _isolated_hermes_home):
        _write_config(_isolated_hermes_home, TWO_PROVIDERS)

        set_config_value("custom_providers.ygg-route.models.fast.context_length", "4096")

        entry = _load(_isolated_hermes_home)["custom_providers"][0]
        assert entry["models"]["fast"] == {"context_length": 4096}

    def test_dotted_provider_name_resolves_to_the_whole_name(self, _isolated_hermes_home):
        """``gpt5.eu`` is one entry name; greedy matching must not stop at ``gpt5``."""
        _write_config(_isolated_hermes_home,
                      "custom_providers:\n"
                      "- name: gpt5\n"
                      "  base_url: https://plain.example/v1\n"
                      "- name: gpt5.eu\n"
                      "  base_url: https://eu.example/v1\n")

        set_config_value("custom_providers.gpt5.eu.base_url", "https://eu2.example/v1")

        entries = _load(_isolated_hermes_home)["custom_providers"]
        assert entries[0]["base_url"] == "https://plain.example/v1"
        assert entries[1]["base_url"] == "https://eu2.example/v1"

    def test_numeric_index_still_writes_in_place(self, _isolated_hermes_home):
        _write_config(_isolated_hermes_home, TWO_PROVIDERS)

        set_config_value("custom_providers.1.base_url", "https://other2.example/v1")

        entries = _load(_isolated_hermes_home)["custom_providers"]
        assert entries[1]["base_url"] == "https://other2.example/v1"
        assert entries[0]["base_url"] == "https://old.example/v1"

    def test_named_write_on_a_secondary_list_shape(self, _isolated_hermes_home):
        """Not custom_providers-specific: any list of named mappings resolves the same way."""
        _write_config(_isolated_hermes_home,
                      "telegram:\n"
                      "  allowlist:\n"
                      "  - name: alice\n"
                      "    role: user\n"
                      "  - name: bob\n"
                      "    role: user\n")

        set_config_value("telegram.allowlist.alice.role", "admin")

        assert _load(_isolated_hermes_home)["telegram"]["allowlist"] == [
            {"name": "alice", "role": "admin"},
            {"name": "bob", "role": "user"},
        ]


class TestNameAddressedReadAndUnset:
    def test_get_reads_back_through_the_named_path(self, _isolated_hermes_home, capsys):
        _write_config(_isolated_hermes_home, TWO_PROVIDERS)

        get_config_value("custom_providers.ygg-route.base_url")

        assert "https://old.example/v1" in capsys.readouterr().out

    def test_get_after_a_named_write_returns_the_new_value(self, _isolated_hermes_home, capsys):
        """Setter and getter resolve the same entry — a write nobody can read back is not a fix."""
        _write_config(_isolated_hermes_home, TWO_PROVIDERS)
        capsys.readouterr()

        set_config_value("custom_providers.ygg-route.base_url", "https://new.example/v1")
        capsys.readouterr()
        get_config_value("custom_providers.ygg-route.base_url")

        assert "https://new.example/v1" in capsys.readouterr().out

    def test_get_does_not_fall_through_to_a_same_prefixed_entry(self, _isolated_hermes_home, capsys):
        _write_config(_isolated_hermes_home,
                      "custom_providers:\n"
                      "- name: gpt5\n"
                      "  base_url: https://plain.example/v1\n"
                      "- name: gpt5.eu\n"
                      "  base_url: https://eu.example/v1\n")

        get_config_value("custom_providers.gpt5.eu.base_url")

        out = capsys.readouterr().out
        assert "https://eu.example/v1" in out
        assert "plain.example" not in out

    def test_unset_removes_the_key_from_the_named_entry_only(self, _isolated_hermes_home):
        _write_config(_isolated_hermes_home, TWO_PROVIDERS)

        unset_config_value("custom_providers.ygg-route.api_key")

        entries = _load(_isolated_hermes_home)["custom_providers"]
        assert "api_key" not in entries[0]
        assert entries[0]["name"] == "ygg-route"
        assert entries[1]["api_key"] == "key-b"

    def test_unknown_name_reads_as_unset_and_leaves_the_file_alone(self, _isolated_hermes_home, capsys):
        """A name that matches nothing is 'unset', not 'the first entry' — for get and for unset."""
        _write_config(_isolated_hermes_home, TWO_PROVIDERS)
        cfg = {"custom_providers": [
            {"name": "ygg-route", "base_url": "https://old.example/v1"},
            {"name": "other", "base_url": "https://other.example/v1"},
        ]}

        with pytest.raises(SystemExit):
            get_config_value("custom_providers.absent.base_url")
        assert "not set" in capsys.readouterr().err
        assert _get_nested(cfg, "custom_providers.absent.base_url") is _MISSING
        assert _unset_nested(cfg, "custom_providers.absent.base_url") is False
        assert cfg["custom_providers"][0]["base_url"] == "https://old.example/v1"
        assert _load(_isolated_hermes_home)["custom_providers"][0]["base_url"] == "https://old.example/v1"

    def test_ambiguous_name_reads_as_unset_and_unsets_nothing(self, _isolated_hermes_home):
        """Refusing to guess on a write must not mean guessing on a read either."""
        cfg = {"custom_providers": [
            {"name": "dup", "base_url": "https://first.example/v1"},
            {"name": "dup", "base_url": "https://second.example/v1"},
        ]}

        assert _get_nested(cfg, "custom_providers.dup.base_url") is _MISSING
        assert _unset_nested(cfg, "custom_providers.dup.base_url") is False
        assert [entry["base_url"] for entry in cfg["custom_providers"]] == [
            "https://first.example/v1", "https://second.example/v1"]


class TestAmbiguityAndTyposAreRefused:
    def test_typo_in_the_name_lists_the_entries_that_exist(self, _isolated_hermes_home, capsys):
        _write_config(_isolated_hermes_home, TWO_PROVIDERS)

        with pytest.raises(SystemExit):
            set_config_value("custom_providers.ygg-rout.base_url", "https://new.example/v1")

        err = capsys.readouterr().err
        assert "ygg-rout" in err
        assert "ygg-route" in err
        assert "other" in err
        # Nothing written.
        assert _load(_isolated_hermes_home)["custom_providers"][0]["base_url"] == "https://old.example/v1"

    def test_typo_does_not_raise_a_bare_traceback(self, _isolated_hermes_home, capsys):
        """The reported symptom: the refusal arrives as an internal-looking exception instead of a
        message naming the bad path."""
        _write_config(_isolated_hermes_home, TWO_PROVIDERS)

        with pytest.raises(SystemExit):
            set_config_value("custom_providers.absent.base_url", "x")

        assert "Traceback" not in capsys.readouterr().err

    def test_duplicate_names_are_refused_rather_than_guessed(self, _isolated_hermes_home, capsys):
        """Two entries with one name cannot be told apart; picking the first would rewrite the
        wrong provider's endpoint."""
        _write_config(_isolated_hermes_home,
                      "custom_providers:\n"
                      "- name: dup\n"
                      "  base_url: https://first.example/v1\n"
                      "- name: dup\n"
                      "  base_url: https://second.example/v1\n")

        with pytest.raises(SystemExit):
            set_config_value("custom_providers.dup.base_url", "https://new.example/v1")

        assert "numeric index" in capsys.readouterr().err
        assert _load(_isolated_hermes_home)["custom_providers"][0]["base_url"] == "https://first.example/v1"

    def test_list_without_named_entries_reports_that(self, _isolated_hermes_home, capsys):
        _write_config(_isolated_hermes_home, "platform_toolsets:\n  line:\n  - clarify\n  - file\n")

        with pytest.raises(SystemExit):
            set_config_value("platform_toolsets.line.clarify", "on")

        assert "numeric index" in capsys.readouterr().err

    def test_unnamed_dict_entry_falls_back_to_the_refusal(self, _isolated_hermes_home, capsys):
        """A list whose entries carry no ``name`` has nothing to match, so the write is refused with
        the numeric-index instruction rather than silently creating a ``name`` key."""
        _write_config(_isolated_hermes_home,
                      "custom_providers:\n"
                      "- base_url: https://anon.example/v1\n")

        with pytest.raises(SystemExit):
            set_config_value("custom_providers.anonymous.base_url", "https://new.example/v1")

        err = capsys.readouterr().err
        assert "anonymous" in err
        assert _load(_isolated_hermes_home)["custom_providers"] == [
            {"base_url": "https://anon.example/v1"}]

    def test_index_past_the_end_is_refused_not_appended(self, _isolated_hermes_home, capsys):
        """Position addressing never grows a list — unchanged by name resolution."""
        _write_config(_isolated_hermes_home, TWO_PROVIDERS)

        with pytest.raises(SystemExit):
            set_config_value("custom_providers.7.base_url", "https://new.example/v1")

        assert len(_load(_isolated_hermes_home)["custom_providers"]) == 2