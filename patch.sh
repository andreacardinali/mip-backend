#!/bin/bash
set -e

BACKEND=/opt/mip/mip-backend

echo "[1/3] Patching providers/manager.py"

python3 <<'PY'
from pathlib import Path

f = Path("/opt/mip/mip-backend/providers/manager.py")
txt = f.read_text()

txt = txt.replace(
"""def get_profile(
        self,
        symbol,
        **kwargs
    ):""",
"""def get_profile(
        self,
        symbol,
        refresh_mode=False,
        **kwargs
    ):"""
)

txt = txt.replace(
"""        try:

            metadata = (
                self.fmp
                .find_symbol_metadata(
                    symbol
                )
            )

            if metadata:

                profile["exchange"] = (
                    metadata.get(
                        "exchange"
                    )
                )

                profile["exchangeFullName"] = (
                    metadata.get(
                        "exchangeFullName"
                    )
                )

        except Exception as ex:

            print(
                f"FMP enrichment failed: {ex}"
            )""",
"""        if not refresh_mode:

            try:

                metadata = (
                    self.fmp
                    .find_symbol_metadata(
                        symbol
                    )
                )

                if metadata:

                    profile["exchange"] = (
                        metadata.get(
                            "exchange"
                        )
                    )

                    profile["exchangeFullName"] = (
                        metadata.get(
                            "exchangeFullName"
                        )
                    )

            except Exception as ex:

                print(
                    f"FMP enrichment failed: {ex}"
                )"""
)

f.write_text(txt)
print("manager.py patched")
PY


echo "[2/3] Patching scripts/refresh_entities.py"

python3 <<'PY'
from pathlib import Path

f = Path("/opt/mip/mip-backend/scripts/refresh_entities.py")
txt = f.read_text()

txt = txt.replace(
"""def refresh_entities(
    mode: str = "MISSING",
    symbol: str | None = None,
    create_if_missing: bool = False,
):""",
"""def refresh_entities(
    mode: str = "MISSING",
    symbol: str | None = None,
    create_if_missing: bool = False,
    refresh_mode: bool = True,
):"""
)

txt = txt.replace(
""".get_profile(
                        row.symbol
                    )""",
""".get_profile(
                        row.symbol,
                        refresh_mode=refresh_mode
                    )"""
)

f.write_text(txt)
print("refresh_entities.py patched")
PY


echo "[3/3] Patching routers/admin.py"

python3 <<'PY'
from pathlib import Path

f = Path("/opt/mip/mip-backend/routers/admin.py")
txt = f.read_text()

old = """        create_if_missing=payload.get(
            "create_if_missing",
            False
        ),
    )"""

new = """        create_if_missing=payload.get(
            "create_if_missing",
            False
        ),
        refresh_mode=payload.get(
            "refresh_mode",
            True
        ),
    )"""

txt = txt.replace(old, new)

f.write_text(txt)
print("admin.py patched")
PY

echo
echo "[OK] Patch completed"