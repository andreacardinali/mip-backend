# Environment naming migration

This package intentionally switches to the canonical variable names documented in `.env.example`.
Copy `.env.example` to `.env`, replace every `<REQUIRED_SECRET>`, and do not retain legacy names.

Key changes:
- public configuration uses `QUEUE`, not `PERSISTENT_QUEUE`;
- all LLM providers support `JSON_CONFIG`;
- timeout and retry duration names include `SECONDS`;
- LLM consumer prefixes identify the exact functional domain;
- output token limits are named `MAX_OUTPUT_TOKENS`;
- prompt identity uses `PROMPT_ID`, without version labels.

Test migration:
- tests now import `deep_merge_dicts` from its canonical module `llm.json_config`;
- the legacy `_deep_merge_dicts` provider symbol has been removed;
- tests use the canonical `default_timeout_seconds` argument.

Additional test migration:
- cache configuration tests use `default_timeout_seconds`;
- think configuration tests use `THINK_ENABLED` and `config.think_enabled`;
- no compatibility wrappers or legacy aliases are retained.
