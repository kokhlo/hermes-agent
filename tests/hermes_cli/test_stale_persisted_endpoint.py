"""A persisted session endpoint is a record, not an instruction (#132640).

``gateway_runtime``/``model_config`` store the address a chat last ran so a resume can restore it.
When config later moves that provider's endpoint, the stored address is retired: every resumed turn
kept dialling it, and because it survives in ``state.db`` no config read or gateway restart clears
it. Both resume readers now drop an endpoint that contradicts the configured route and let the
provider resolve its own.
"""

from hermes_cli.route_identity import configured_endpoint_for, stale_persisted_endpoint

DEAD = "http://192.168.0.16:6068/v1"
LIVE = "http://192.168.0.16:6066/v1"


class TestConfiguredEndpointFor:
    def test_model_base_url_is_the_endpoints_provider_route(self):
        config = {"model": {"provider": "custom", "base_url": LIVE}}
        assert configured_endpoint_for("custom", config) == LIVE

    def test_model_base_url_does_not_leak_into_another_provider(self):
        config = {"model": {"provider": "openai", "base_url": LIVE}}
        assert configured_endpoint_for("anthropic", config) != LIVE

    def test_named_providers_entry_supplies_the_endpoint(self):
        config = {"providers": {"my-llm": {"base_url": LIVE, "key_env": "MY_LLM_KEY"}}}
        assert configured_endpoint_for("my-llm", config) == LIVE

    def test_unknown_provider_names_no_endpoint(self):
        assert configured_endpoint_for("", {}) == ""
        assert configured_endpoint_for("no-such-provider-xyz", {}) == ""

    def test_providers_entry_without_an_endpoint_names_none(self):
        # Tuning for a built-in, not a custom-endpoint definition: nothing config owns here.
        config = {"providers": {"bedrock": {"stale_timeout_seconds": 600}}}
        assert configured_endpoint_for("bedrock", config) == ""


class TestStalePersistedEndpoint:
    def test_endpoint_config_moved_onto_is_stale(self):
        config = {"model": {"provider": "custom", "base_url": LIVE}}
        assert stale_persisted_endpoint("custom", DEAD, config) is True

    def test_endpoint_still_matching_config_is_kept(self):
        config = {"model": {"provider": "custom", "base_url": LIVE}}
        assert stale_persisted_endpoint("custom", LIVE, config) is False

    def test_equivalent_spellings_are_not_stale(self):
        config = {"model": {"provider": "custom", "base_url": LIVE}}
        assert stale_persisted_endpoint("custom", LIVE + "/", config) is False
        assert stale_persisted_endpoint("custom", LIVE.replace("http://", "HTTP://"), config) is False

    def test_named_provider_re_aimed_in_config_is_stale(self):
        config = {"providers": {"my-llm": {"base_url": LIVE, "key_env": "MY_LLM_KEY"}}}
        assert stale_persisted_endpoint("my-llm", DEAD, config) is True

    def test_session_pinned_to_a_proxy_for_a_registered_provider_is_kept(self):
        # Nothing in config was corrected, so the row's endpoint is a deliberate route rather than
        # a snapshot of a setting that moved — a built-in provider's registry endpoint must not be
        # read as "configured" and used to overrule it.
        assert stale_persisted_endpoint("openai-codex", "https://proxy.example.com/v1", {}) is False

    def test_no_persisted_endpoint_is_never_stale(self):
        config = {"model": {"provider": "custom", "base_url": LIVE}}
        assert stale_persisted_endpoint("custom", "", config) is False
        assert stale_persisted_endpoint("custom", None, config) is False

    def test_provider_with_no_configured_endpoint_keeps_its_snapshot(self):
        # Nothing current claims the URL, so the row is the only record of where this chat ran.
        assert stale_persisted_endpoint("no-such-provider-xyz", DEAD, {}) is False