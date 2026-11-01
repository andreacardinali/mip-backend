"""Contracts against the available DB dump, not proof of live DB validation."""

import re
from pathlib import Path


def schema_sql():
    return (Path(__file__).resolve().parents[1] / "mip_schema.sql").read_text(
        encoding="utf-8"
    )


def schema_functions(name):
    pattern = (
        rf"^CREATE FUNCTION public\.{re.escape(name)}\(.*?"
        r"\bAS (\$(?:[A-Za-z_][A-Za-z_0-9]*)?\$).*?^\1;"
    )
    return [
        match.group()
        for match in re.finditer(pattern, schema_sql(), re.MULTILINE | re.DOTALL)
    ]


def schema_function(name):
    functions = schema_functions(name)
    assert len(functions) == 1, f"Expected one dumped definition for {name}"
    return functions[0]