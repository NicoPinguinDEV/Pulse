"""Regression tests for Discord interaction acknowledgement and redundant PATCHes."""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def get_method(source: str, name: str) -> str:
    tree = ast.parse(source)
    matches = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name
    ]
    if not matches:
        raise AssertionError(f"Method {name!r} missing")
    return ast.get_source_segment(source, matches[0]) or ""


class DiscordLogRegressionTests(unittest.TestCase):
    def test_activity_check_defers_before_database_access_and_uses_followup(self):
        source = read_source("cogs/activity_check.py")
        method = get_method(source, "confirm")
        self.assertIn("interaction.response.defer(ephemeral=True, thinking=True)", method)
        self.assertLess(
            method.index("interaction.response.defer"),
            method.index("self.cog.current_check_id"),
        )
        self.assertIn("interaction.followup.send", method)
        self.assertIn("except discord.NotFound", method)
        self.assertIn("getattr(exc, \"code\", None) == 10062", method)
        self.assertNotIn('interaction.response.send_message("✅ Aktivität', method)

    def test_abmeldung_preflights_nickname_permissions_and_suppresses_retries(self):
        source = read_source("cogs/abmeldung.py")
        helper = get_method(source, "_can_edit_member_nickname")
        apply = get_method(source, "apply_user_status")
        reset = get_method(source, "reset_user_status")
        self.assertIn("manage_nicknames", helper)
        self.assertIn("member.top_role.position >= bot_member.top_role.position", helper)
        self.assertIn("self._nickname_forbidden", helper)
        self.assertIn("suffix not in member.display_name", apply)
        self.assertIn("self._nickname_forbidden.add(key)", apply)
        self.assertIn("self._nickname_forbidden.add(key)", reset)
        self.assertNotIn('print(f"⚠️ Keine Rechte, um den Namen von {member} zu ändern.")', source)

    def test_abmeldung_live_list_skips_identical_embed_patch(self):
        source = read_source("cogs/abmeldung.py")
        method = get_method(source, "update_live_list")
        self.assertIn("len(message.embeds) == len(embeds)", method)
        self.assertIn("_embed_signature(old) == _embed_signature(new)", method)
        self.assertLess(
            method.index("_embed_signature(old) == _embed_signature(new)"),
            method.index("await message.edit(embeds=embeds"),
        )

    def test_teamlist_compares_complete_embed_before_patching(self):
        source = read_source("cogs/teamliste.py")
        method = get_method(source, "update_teamlist")
        self.assertIn("_embed_signature(msg.embeds[0]) == _embed_signature(embed)", method)
        self.assertLess(
            method.index("_embed_signature(msg.embeds[0]) == _embed_signature(embed)"),
            method.index("await msg.edit(embed=embed)"),
        )


if __name__ == "__main__":
    unittest.main()
