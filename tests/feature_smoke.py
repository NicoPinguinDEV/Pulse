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
    for name in ("main.py", "webserver.py", "pulse_pro.py", "cogs/pulse_pro.py", "pulse_ultimate.py"):
        parse(name)

    web = read("webserver.py")
    pro = read("pulse_pro.py")
    main = read("main.py")
    ultimate = read("pulse_ultimate.py")

    tree = parse("webserver.py")
    constants = {
        node.targets[0].id: node.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Constant)
    }
    assert constants.get("TEAM_UPDATE_CHANNEL_ID") == 1531132354272170115
    assert "async def remove_warn" not in web  # action is intentionally centralized
    assert 'if action == "remove_warn":' in web
    assert "active_warns(entry)" in web
    assert "revoked_at" in web
    assert "async def reconcile_warning_roles" in web
    assert "/api/team/warn-roles" in web
    assert web.count("def normalize_warns(entry: dict) -> bool:") == 1
    assert 'SELECT user_id FROM check_members WHERE check_id=?' in web
    assert '"not_in_snapshot"' in web
    assert 'member.status' in web
    assert 'rate_limited(client_key' in web

    # Five-level team warning regression checks.
    for role_id in (
        "1489221948348043395",
        "1489222076370780232",
        "1531760107971416135",
        "1556344459422081045",
        "1556344484198088814",
    ):
        assert role_id in web
    assert "5: 1556344484198088814" in web
    assert '"count": max(0, min(int(count or 0), 5))' in web
    assert "current_count >= 5" in web
    assert "for level in sorted(ids):" in web
    assert "warning_5" in web
    assert "/3 Warnungen" not in web

    # Melonly stays internal and is editable/deletable.
    web_tree = parse("webserver.py")
    create_log_nodes = [n for n in ast.walk(web_tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "create_log"]
    assert create_log_nodes, "webserver.py: create_log fehlt"
    create_log_source = ast.get_source_segment(web, create_log_nodes[0]) or ""
    assert "send_team_update_embed" not in create_log_source
    assert 'Melonly-Eintrag erstellt' in create_log_source
    assert '@app.post("/log/edit")' in web
    assert '@app.post("/log/delete")' in web
    assert "edit_log_id" in web
    assert "users_map = {}" in web

    # Team rank and bulk role-management regression checks.
    assert '"/team/{user_id}/roles"' in web
    assert "async def team_role_manager_save" in web
    assert "Mehrfach-Rollenänderung" in web
    assert "⚙️ Team-Update: Rollenänderung" in web
    assert "⬆️ Hochstufen" in web
    assert "⬇️ Runterstufen" in web
    assert "1531132354272170115" in web
    # Required reasons for personnel actions.
    assert 'name="action_reason"' in web
    assert 'action_reason: str = Form(None)' in web
    assert "Bitte einen Grund für die Maßnahme angeben." in web
    assert "Bitte einen Grund für die Rücknahme der Verwarnung angeben." in web
    assert "Bitte einen Grund für die Rollenänderung angeben." in web
    assert "Bitte einen Grund für die Bewerbungsentscheidung angeben." in web
    assert '("Grund", action_reason, False)' in web
    assert '("📝 Grund", action_reason, False)' in web

    assert "/warns" in pro
    assert "critical=sum" in pro
    assert ">=5" in pro
    assert "Warn-Rollen 1–5" in pro
    assert "range(1,6)" in pro
    assert "/settings/pro-warn-roles" in pro
    assert "/settings/pro-warn-sync" in pro
    assert "Warn-Rollen" in pro
    assert "PULSE_VERSION" in web
    assert '"/healthz"' in web

    assert "reconcile_warning_roles" in main

    assert_function_is_async("webserver.py", "sync_warn_roles")
    assert "def register(app)" in ultimate
    assert '"/ultimate"' in ultimate
    assert '"/ultimate/team"' in ultimate
    assert '"/ultimate/cases"' in ultimate
    assert '"/ultimate/roblox"' in ultimate
    assert '"/ultimate/automations"' in ultimate
    assert '"/ultimate/api/v2/team"' in ultimate
    assert 'ultimate_automation' in ultimate
    assert "frozen" in web.lower() or "eingefroren" in web.lower()
    assert "/5600" not in web
    assert "/5600" not in read("pulse_next.py")
    assert "/3600" in read("pulse_next.py")

    print("Pulse feature smoke test: OK")


if __name__ == "__main__":
    main()
