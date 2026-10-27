from llm.fingerprint import fingerprint


def test_news_contract_combines_template_and_schema():
    assert fingerprint(
        {"template": "a", "response_schema": {"type": "object"}}
    ) != fingerprint({"template": "b", "response_schema": {"type": "object"}})
