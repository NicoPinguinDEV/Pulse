"""Static feature regression checks for Pulse TeamOS.

Stdlib-only; complements the compileall CI check.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def parse(name: str) -> ast.Module:
    return ast.parse(read(name), filename=name)


def assert_function_is_async(name: str, function_name: str) -> None:
    tree = parse(name)
    matches = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == function_name
    ]
    assert matches, f"{name}: {function_name} fehlt"
    assert any(isinstance(node, ast.AsyncFunctionDef) for node in matches), f"{name}: {function_name} muss async sein"


def main() -> None:
    for name in ("main.py", "webserver.py", "pulse_pro.py", "cogs/pulse_pro.py"):
        parse(name)

    web = read("webserver.py")
    pro = read("pulse_pro.py")
    main = read("main.py")

    assert "TEAM_UPDATE_CHANNEL_ID = 1531132354272170115" in web
    assert "async def remove_warn" not in web  # action is intentionally centralized
    assert "if action == " + repr("remove_warn") + ":" in web
    assert "active_warns(entry)" in web
    assert "revoked_at" in web
    assert "async def reconcile_warning_roles" in web
    assert "/api/team/warn-roles" in web

    assert "/warns" in pro
    assert "/settings/pro-warn-roles" in pro
    assert "/settings/pro-warn-sync" in pro
    assert "Warn-Rollen" in pro

    assert "reconcile_warning_roles" in main

    assert_function_is_async("webserver.py", "sync_warn_roles")

    print("Pulse feature smoke test: OK")


if __name__ == "__main__":
    main()
