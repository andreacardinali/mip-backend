#!/usr/bin/env python3
"""Create a shareable, anonymized copy of a dotenv file.

Default behavior is intentionally conservative: every non-empty value is
replaced while variable names, comments, ordering, blank lines, and optional
`export` prefixes are preserved.

Usage:
    python anonymize_env.py .env
    python anonymize_env.py .env .env.anonymized
    python anonymize_env.py .env --show-values-shape

The input file is never modified.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ASSIGNMENT_RE = re.compile(
    r"^(?P<indent>\s*)(?P<export>export\s+)?"
    r"(?P<key>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?P<separator>\s*=\s*)(?P<value>.*)$"
)

SENSITIVE_KEY_RE = re.compile(
    r"(?:^|_)"
    r"(?:SECRET|TOKEN|PASSWORD|PASSWD|PASS|API_KEY|PRIVATE_KEY|"
    r"CLIENT_SECRET|ACCESS_KEY|CREDENTIALS?|AUTH|JWT|DSN|DATABASE_URL|"
    r"CONNECTION_STRING|WEBHOOK|CHAT_ID|BOT_TOKEN|COOKIE|SESSION)"
    r"(?:$|_)",
    re.IGNORECASE,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a safe copy of a .env file for review."
    )
    parser.add_argument("input", type=Path, help="Source .env file")
    parser.add_argument(
        "output",
        nargs="?",
        type=Path,
        help="Output path; default: <input>.anonymized",
    )
    parser.add_argument(
        "--show-values-shape",
        action="store_true",
        help=(
            "Preserve harmless booleans/numbers and replace other values with "
            "typed placeholders. Sensitive-looking keys are always redacted."
        ),
    )
    return parser.parse_args()


def split_inline_comment(raw: str) -> tuple[str, str]:
    """Split an unquoted inline comment while respecting quotes and escapes."""
    quote: str | None = None
    escaped = False

    for index, char in enumerate(raw):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char == "#" and (index == 0 or raw[index - 1].isspace()):
            value = raw[:index].rstrip()
            comment = raw[index:]
            spacing = raw[len(value) : index]
            return value, spacing + comment

    return raw.rstrip(), raw[len(raw.rstrip()) :]


def remove_outer_quotes(value: str) -> tuple[str, str | None]:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {"'", '"'}:
        return stripped[1:-1], stripped[0]
    return stripped, None


def placeholder_for(key: str, value: str, show_shape: bool) -> str:
    inner, quote = remove_outer_quotes(value)

    if inner == "":
        replacement = ""
    elif not show_shape or SENSITIVE_KEY_RE.search(key):
        replacement = "<REDACTED>"
    elif inner.lower() in {"true", "false", "yes", "no", "on", "off"}:
        replacement = inner
    elif re.fullmatch(r"[+-]?\d+(?:\\.\d+)?", inner):
        replacement = inner
    elif re.fullmatch(r"[+-]?\d+(?:\\.\d+)?(?:ms|s|m|h|d)", inner, re.I):
        replacement = inner
    elif inner.startswith(("http://", "https://")):
        replacement = "<REDACTED_URL>"
    elif inner.startswith(("postgresql://", "postgres://", "mysql://", "redis://")):
        replacement = "<REDACTED_DSN>"
    elif "," in inner:
        replacement = "<REDACTED_LIST>"
    else:
        replacement = "<REDACTED>"

    if quote and replacement:
        return f"{quote}{replacement}{quote}"
    return replacement


def anonymize_line(line: str, show_shape: bool) -> str:
    newline = ""
    body = line
    if body.endswith("\r\n"):
        body, newline = body[:-2], "\r\n"
    elif body.endswith("\n"):
        body, newline = body[:-1], "\n"

    stripped = body.lstrip()
    if not stripped or stripped.startswith("#"):
        return body + newline

    match = ASSIGNMENT_RE.match(body)
    if not match:
        # Preserve unknown syntax as a warning comment, never leak its content.
        return "# [ANONYMIZER: unrecognized line removed]" + newline

    raw_value, comment = split_inline_comment(match.group("value"))
    replacement = placeholder_for(
        match.group("key"),
        raw_value,
        show_shape,
    )

    return (
        f"{match.group('indent')}"
        f"{match.group('export') or ''}"
        f"{match.group('key')}"
        f"{match.group('separator')}"
        f"{replacement}"
        f"{comment}"
        f"{newline}"
    )


def default_output_path(input_path: Path) -> Path:
    return input_path.with_name(input_path.name + ".anonymized")


def main() -> int:
    args = parse_args()
    source = args.input.expanduser().resolve()
    destination = (
        args.output.expanduser().resolve()
        if args.output
        else default_output_path(source)
    )

    if not source.is_file():
        print(f"Error: input file not found: {source}", file=sys.stderr)
        return 2
    if source == destination:
        print("Error: output must differ from input", file=sys.stderr)
        return 2
    if destination.exists():
        print(f"Error: output already exists: {destination}", file=sys.stderr)
        return 2

    content = source.read_text(encoding="utf-8")
    output = "".join(
        anonymize_line(line, args.show_values_shape)
        for line in content.splitlines(keepends=True)
    )

    # Handle a final line without newline when splitlines returns no terminator.
    if content and not content.endswith(("\n", "\r")) and output.endswith("\n"):
        output = output[:-1]

    destination.write_text(output, encoding="utf-8")
    try:
        destination.chmod(0o600)
    except OSError:
        pass

    variable_count = sum(
        1
        for line in content.splitlines()
        if ASSIGNMENT_RE.match(line) and not line.lstrip().startswith("#")
    )
    print(f"Created: {destination}")
    print(f"Variables processed: {variable_count}")
    print("Original file was not modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
