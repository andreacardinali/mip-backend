import ast
from pathlib import Path

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
