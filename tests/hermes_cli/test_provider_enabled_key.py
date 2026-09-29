"""``providers.<name>.enabled`` is consumed by ``is_provider_enabled()`` before the entry is
normalized, so the normalizer's unknown-key check must not report it as ignored — the warning
read as "this key does nothing" and produced a false bug report on the key.

The gating itself lives upstream, on the original mapping; these tests pin both halves: the
warning stays quiet, and the provider is still hidden.
"""

import logging

import pytest

from hermes_cli.config import (
    _PROVIDER_NORMALIZE_WARNED,
    _normalize_custom_provider_entry,
    providers_dict_to_custom_providers,
)


def _unknown_key_messages(caplog):
    return [r.message for r in caplog.records if "unknown config keys" in r.message.lower()]


def _enabled_warnings(caplog):
    return [m for m in _unknown_key_messages(caplog) if "enabled" in m.lower()]


@pytest.fixture(autouse=True)
def _reset_warn_cache():
    # The normalizer deduplicates warnings per process lifetime; clear it so
    # each test observes a first-time warning.
    _PROVIDER_NORMALIZE_WARNED.clear()
    yield
    _PROVIDER_NORMALIZE_WARNED.clear()


@pytest.mark.parametrize("raw", [True, "true", False, "false", "no", 1, 0])
def test_enabled_key_never_reported_as_ignored(raw, caplog):
    with caplog.at_level(logging.WARNING):
        providers_dict_to_custom_providers({
            "my-provider": {
                "enabled": raw,
                "base_url": "https://example.invalid/v1",
                "api_key": "***",
            }
        })
    assert _enabled_warnings(caplog) == []


def test_genuinely_unknown_key_still_warns(caplog):
    """Accepting ``enabled`` must not soften the check for keys nothing consumes."""
    with caplog.at_level(logging.WARNING):
        providers_dict_to_custom_providers({
            "my-provider": {
                "enabled": True,
                "base_url": "https://example.invalid/v1",
                "totally_made_up": 1,
            }
        })
    messages = _unknown_key_messages(caplog)
    assert len(messages) == 1
    assert "totally_made_up" in messages[0]
    assert "enabled" not in messages[0]


def test_normalizer_drops_the_flag_from_the_legacy_shape(caplog):
    """The key is redundant on the normalized copy, which is why no consumer reads it there."""
    entry = {"enabled": True, "base_url": "https://example.invalid/v1"}
    with caplog.at_level(logging.WARNING):
        normalized = _normalize_custom_provider_entry(dict(entry), provider_key="my-provider")
    assert normalized is not None
    assert "enabled" not in normalized
    assert _enabled_warnings(caplog) == []


def test_disabled_entry_is_still_gated_not_just_unwarned(caplog):
    """The gate runs before normalization, so a disabled block never reaches the normalizer."""
    providers = {
        "on-provider": {"enabled": True, "base_url": "https://on.example.invalid/v1"},
        "off-provider": {"enabled": False, "base_url": "https://off.example.invalid/v1"},
        "off-quoted": {"enabled": "false", "base_url": "https://off2.example.invalid/v1"},
        "default-provider": {"base_url": "https://default.example.invalid/v1"},
    }
    with caplog.at_level(logging.WARNING):
        kept = providers_dict_to_custom_providers(providers)

    assert {e["provider_key"] for e in kept} == {"on-provider", "default-provider"}
    assert _enabled_warnings(caplog) == []
