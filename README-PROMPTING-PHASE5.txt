MIP shared prompting component, phase 5

Purpose
-------
Centralize prompt loading, caching, path validation, and template rendering.
Portfolio Intelligence keeps ownership of its prompt files but no longer reads
them directly from branch code.

New component
-------------
prompting/__init__.py
prompting/repository.py
prompting/template.py

Updated Portfolio Intelligence files
------------------------------------
jobs/portfolio_intelligence/context.py
jobs/portfolio_intelligence/runtime.py
jobs/portfolio_intelligence/branches/daily_mover.py
jobs/portfolio_intelligence/branches/upcoming_catalyst.py
jobs/portfolio_intelligence/branches/breaking_catalyst.py

Prompt files remain in
----------------------
jobs/portfolio_intelligence/prompts/

Design
------
- FileSystemPromptRepository receives a root directory.
- Prompt names may omit .txt.
- Traversal outside the configured root is rejected.
- Templates are cached for the life of the job context.
- PromptTemplate.render() reports missing values with the prompt name.
- Branches receive the repository through JobContext dependency injection.

Installation
------------
cd /opt/mip/mip-backend
unzip -o mip-prompting-phase5.zip -d /opt/mip/mip-backend

Validation
----------
python -m compileall prompting jobs/portfolio_intelligence
python -m unittest tests.test_prompting

grep -RIn --exclude-dir=.git --exclude-dir=__pycache__   -E "prompts/.*read_text|Path\(__file__\).*prompts"   jobs/portfolio_intelligence

The grep output must be empty.

Restart
-------
systemctl restart mip-scheduler
journalctl -u mip-scheduler -n 100 --no-pager

Database
--------
No SQL migration is required.
