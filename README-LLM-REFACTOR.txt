MIP shared LLM component refactor
Base commit: ae55cbbab2853b93103eba45481da6b516c12daf

Purpose
-------
Move the provider-independent LLM stack out of jobs/portfolio_intelligence/llm
and into the root llm package so scheduler jobs and backend routes can reuse it.

New shared package
------------------
llm/base.py
llm/config.py
llm/factory.py
llm/parsing.py
llm/retry.py
llm/providers/gemini.py
llm/providers/ollama.py
llm/providers/openai_compatible.py

Portfolio Intelligence changes
------------------------------
- IntelligenceConfig now owns two nested LLMConfig values only:
  research_llm and validation_llm.
- Provider validation and provider-specific environment loading live in llm/config.py.
- runtime.py injects shared providers using llm.factory.create_provider().
- JobContext imports LLMProvider from the shared component.
- The three branches must import LLMRequest from llm.base.

Installation
------------
Extract from /opt/mip/mip-backend.
Apply the three import replacements documented under PATCHES.
Delete jobs/portfolio_intelligence/llm after all imports have been migrated.

Commands
--------
unzip -o mip-llm-refactor.zip -d /opt/mip/mip-backend
sed -i 's/from jobs.portfolio_intelligence.llm.base import LLMRequest/from llm.base import LLMRequest/' jobs/portfolio_intelligence/branches/*.py
rm -rf jobs/portfolio_intelligence/llm
python -m compileall llm jobs/portfolio_intelligence
systemctl restart mip-scheduler

No database migration is required.
The existing INTELLIGENCE_LLM_* and INTELLIGENCE_VALIDATION_LLM_* variable names remain valid.
