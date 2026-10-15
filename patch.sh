#!/bin/bash

set -e

python3 <<'PY'
from pathlib import Path

p = Path("providers/ghostfolio.py")
s = p.read_text()

#
# aggiunge import re
#

if "import re" not in s:

    s = s.replace(
        "import os\n",
        "import os\nimport re\n"
    )

#
# helper clean_url()
#

anchor = "class GhostfolioProvider:\n"

helper = '''
class GhostfolioProvider:

    @staticmethod
    def _clean_url(value):

        if not value:
            return None

        match = re.search(
            r'href="([^"]+)"',
            value
        )

        if match:

            return match.group(1)

        return value
'''

if (
    "def _clean_url(" not in s
    and
    anchor in s
):
    s = s.replace(
        anchor,
        helper,
        1
    )

#
# sostituisce website mapping
#

old = '''
            "website":
                profile.get(
                    "url"
                ),
'''

new = '''
            "website":
                self._clean_url(
                    profile.get(
                        "url"
                    )
                ),
'''

s = s.replace(
    old,
    new
)

p.write_text(s)

print("ghostfolio.py patched")
PY

echo
echo "✅ website cleanup enabled"
echo