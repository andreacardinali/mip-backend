# Run and LLM request limit naming

Portfolio Intelligence loads all candidates and uses the former `BATCH_SIZE` only in `batched(..., size)` when constructing research LLM requests. The canonical replacement is therefore:

`PORTFOLIO_INTELLIGENCE_LLM_REQUEST_MAX_CANDIDATES=4`

The package also applies the same convention to News Enrichment and Market Analysis Enrichment.
