# Market Analysis runtime fingerprint fix

Completes the Phase 3 runtime migration. The worker and service now use `enrichment_fingerprint` end to end and no longer pass `prompt_id`, `prompt_version`, or `PROMPT_VERSION`.
