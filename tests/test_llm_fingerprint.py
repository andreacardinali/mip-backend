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


def test_provider_fingerprint_separates_api_protocols():
    common = {
        "provider": "openai_compatible",
        "model": "test-model",
        "base_url": "https://example.test/v1",
        "json_config": {},
        "think_enabled": False,
    }
    chat = provider_config_fingerprint(**common, api_protocol="chat_completions")
    responses = provider_config_fingerprint(**common, api_protocol="responses")
    assert chat != responses
    responses_with_temperature = provider_config_fingerprint(
        **{
            **common,
            "json_config": {"temperature": 0.3},
        },
        api_protocol="responses",
    )
    assert responses != responses_with_temperature
