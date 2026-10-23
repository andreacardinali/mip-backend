from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROVIDERS = ROOT / "market_data" / "providers"


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return source.replace(old, new, 1)


def add_base_contract(path: Path) -> None:
    if path.name in {"__init__.py", "base.py", "yahoo.py"}:
        return
    source = path.read_text()
    if "def get_profile(" not in source or "MarketDataProvider" in source:
        return
    tree = ast.parse(source)
    classes = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and any(
            isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            and child.name in {"get_profile", "get_quote"}
            for child in node.body
        )
    ]
    if not classes:
        return
    lines = source.splitlines(keepends=True)
    insert_at = max(
        (
            i + 1
            for i, line in enumerate(lines)
            if line.startswith("from ") or line.startswith("import ")
        ),
        default=0,
    )
    lines.insert(
        insert_at, "from market_data.providers.base import MarketDataProvider\n"
    )
    source = "".join(lines)
    for cls in classes:
        pattern = re.compile(
            rf"^class\s+{re.escape(cls.name)}(?:\(([^)]*)\))?:", re.MULTILINE
        )
        match = pattern.search(source)
        if not match:
            raise RuntimeError(f"Cannot patch class {cls.name} in {path}")
        bases = (match.group(1) or "").strip()
        new_bases = f"{bases}, MarketDataProvider" if bases else "MarketDataProvider"
        source = (
            source[: match.start()]
            + f"class {cls.name}({new_bases}):"
            + source[match.end() :]
        )
    path.write_text(source)
    print(f"Uniform contract: {path.relative_to(ROOT)}")


def patch_ghostfolio() -> None:
    path = PROVIDERS / "ghostfolio.py"
    source = path.read_text()
    if "def _get_profile_once(" in source:
        print("Ghostfolio auto-refresh already installed")
        return
    source = replace_once(
        source,
        "    def get_profile(self, instrument):\n",
        "    def _get_profile_once(self, instrument):\n",
        "Ghostfolio get_profile",
    )
    marker = "    def get_quote(self, instrument):\n"
    wrapper = (
        "    def get_profile(self, instrument):\n"
        '        """Get a profile and transparently prime Ghostfolio when missing."""\n'
        "        try:\n"
        "            return self._get_profile_once(instrument)\n"
        "        except InstrumentNotFoundError:\n"
        "            if not self.refresh_profile(instrument):\n"
        "                raise\n"
        "            # A quick second read supports synchronous gathering. The existing\n"
        "            # queue retry handles asynchronous gathering without leaking\n"
        "            # provider-specific behavior to callers.\n"
        "            return self._get_profile_once(instrument)\n\n"
    )
    source = replace_once(source, marker, wrapper + marker, "Ghostfolio quote marker")
    source = replace_once(
        source,
        '    def refresh_profile(self, instrument, *, data_source="YAHOO"):\n',
        '    def refresh_profile(self, instrument, *, data_source="YAHOO") -> bool:\n',
        "Ghostfolio refresh signature",
    )
    source = replace_once(
        source,
        "            retry_non_idempotent=False,\n        )\n\n    def _authenticate",
        "            retry_non_idempotent=False,\n        )\n        return True\n\n    def _authenticate",
        "Ghostfolio refresh return",
    )
    path.write_text(source)
    print("Ghostfolio transparent refresh installed")


def patch_factory() -> None:
    path = ROOT / "market_data" / "factory.py"
    source = path.read_text()
    import_line = "from market_data.providers.yahoo import YahooProvider"
    if import_line not in source:
        imports = list(
            re.finditer(r"^from market_data\.providers\..+$", source, re.MULTILINE)
        )
        if not imports:
            raise RuntimeError("Provider imports not found in factory.py")
        end = imports[-1].end()
        source = source[:end] + "\n" + import_line + source[end:]
    if "YahooProvider(" not in source:
        candidates = list(
            re.finditer(
                r"^(\s*)(?:Ghostfolio|Eulerpool|Fmp|OpenFigi|OpenFIGI)Provider\(",
                source,
                re.MULTILINE,
            )
        )
        if not candidates:
            raise RuntimeError("Provider list not found in factory.py")
        match = candidates[0]
        source = (
            source[: match.start()]
            + f"{match.group(1)}YahooProvider(),\n"
            + source[match.start() :]
        )
    path.write_text(source)
    print("YahooProvider registered")


for provider_file in sorted(PROVIDERS.glob("*.py")):
    add_base_contract(provider_file)
patch_ghostfolio()
patch_factory()
for path in [
    PROVIDERS / "base.py",
    PROVIDERS / "yahoo.py",
    PROVIDERS / "ghostfolio.py",
    ROOT / "market_data" / "factory.py",
]:
    ast.parse(path.read_text(), filename=str(path))
print("Refactor applied; syntax validation passed")
