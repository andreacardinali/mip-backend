from llm.fingerprint import canonical_json, fingerprint, provider_config_fingerprint


def test_fingerprint_is_order_independent_for_mappings():
    assert fingerprint({"b": 2, "a": 1}) == fingerprint({"a": 1, "b": 2})
    assert canonical_json({"b": 2, "a": 1}) == '{"a":1,"b":2}'


def test_provider_fingerprint_ignores_operational_settings_by_contract():
    first = provider_config_fingerprint(
        provider="ollama",
        model="qwen",
        base_url="http://ollama",
        json_config={"options": {"num_ctx": 8192}},
        think_enabled=False,
    )
    second = provider_config_fingerprint(
        provider="ollama",
        model="qwen",
        base_url="http://ollama",
        json_config={"options": {"num_ctx": 4096}},
        think_enabled=False,
    )
    assert first != second
