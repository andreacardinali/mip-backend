import ast
from pathlib import Path

MIGRATED_PROVIDER_PATHS = (
    "llm/providers/openai_compatible.py",
    "llm/providers/ollama.py",
    "llm/providers/gemini.py",
    "llm/retry_hints.py",
    "market_sources/universe/providers/wikipedia.py",
    "market_sources/discovery/providers/movers.py",
    "market_sources/discovery/providers/apewisdom.py",
    "market_data/providers/ecb.py",
    "market_data/providers/ghostfolio.py",
    "market_data/providers/eulerpool.py",
    "market_data/providers/openfigi.py",
    "market_data/providers/fmp.py",
    "market_data/providers/yahoo.py",
    "notifications/providers/telegram.py",
)

FORBIDDEN_HTTP_METHODS = {
    "get",
    "post",
    "put",
    "patch",
    "delete",
    "head",
    "options",
    "request",
}
HTTP_MODULE_NAMES = {"httpx", "requests"}
HTTP_CLIENT_NAMES = {
    "client",
    "http_client",
    "session",
    "http_session",
}


def _attribute_root_name(node: ast.Attribute) -> str | None:
    value = node.value
    while isinstance(value, ast.Attribute):
        value = value.value
    return value.id if isinstance(value, ast.Name) else None


def _is_direct_http_call(function: ast.Attribute) -> bool:
    if function.attr not in FORBIDDEN_HTTP_METHODS:
        return False

    root_name = _attribute_root_name(function)
    if root_name in HTTP_MODULE_NAMES:
        return True

    if isinstance(function.value, ast.Name):
        return function.value.id in HTTP_CLIENT_NAMES

    if isinstance(function.value, ast.Attribute):
        return function.value.attr in HTTP_CLIENT_NAMES or function.value.attr.endswith(
            ("_client", "_session")
        )

    return False


def test_market_source_providers_do_not_call_http_clients_directly() -> None:
    provider_root = Path("market_sources")
    violations = []

    for path in provider_root.rglob("providers/*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            function = node.func
            if isinstance(function, ast.Attribute) and _is_direct_http_call(function):
                violations.append(f"{path}:{node.lineno}:{function.attr}")

    assert violations == [], (
        "Provider HTTP calls must use http_client.retry.request_with_retry: "
        + ", ".join(violations)
    )


def test_migrated_providers_use_only_shared_http_module() -> None:
    violations = []
    for relative_path in MIGRATED_PROVIDER_PATHS:
        path = Path(relative_path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(
                    alias.name.split(".")[0] in HTTP_MODULE_NAMES
                    for alias in node.names
                ):
                    violations.append(f"{path}:{node.lineno}:import")
            elif isinstance(node, ast.ImportFrom):
                if (node.module or "").split(".")[0] in HTTP_MODULE_NAMES:
                    violations.append(f"{path}:{node.lineno}:import")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if _is_direct_http_call(node.func):
                    violations.append(f"{path}:{node.lineno}:{node.func.attr}")
    assert violations == [], ", ".join(violations)
