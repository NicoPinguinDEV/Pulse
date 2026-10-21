"""BOT-04: setup commands hidden from ordinary members; help generated from the command tree."""
import unittest

import discord
from discord import app_commands

import pulse_command_help as ch


def tree_with(*names, group=None):
    tree = app_commands.CommandTree(discord.Client(intents=discord.Intents.none()))

    async def cb(interaction: discord.Interaction):
        pass

    for name in names:
        tree.add_command(app_commands.Command(name=name, description=f"Beschreibung {name}", callback=cb))
    if group:
        g = app_commands.Group(name=group, description="Gruppe")
        g.add_command(app_commands.Command(name="start", description="Startet", callback=cb))
        tree.add_command(g)
    return tree


class VisibilityTests(unittest.TestCase):
    def test_setup_commands_get_default_permissions_others_not(self):
        tree = tree_with("setup_buero", "teamrolle_entfernen", "messagedelete", "warn", "pulse")
        self.assertEqual(ch.apply_visibility(tree), 3)
        by_name = {c.name: c.default_permissions for c in tree.get_commands()}
        self.assertTrue(by_name["setup_buero"].manage_guild)
        self.assertTrue(by_name["teamrolle_entfernen"].manage_roles)
        self.assertTrue(by_name["messagedelete"].manage_messages)
        self.assertIsNone(by_name["warn"])   # team commands stay visible; Pulse checks rights itself
        self.assertIsNone(by_name["pulse"])

    def test_own_default_permissions_are_kept(self):
        tree = tree_with("setup_rp")
        cmd = tree.get_command("setup_rp")
        cmd.default_permissions = discord.Permissions(administrator=True)
        ch.apply_visibility(tree)
        self.assertTrue(cmd.default_permissions.administrator)


class HelpTests(unittest.TestCase):
    def test_help_is_generated_and_filtered(self):
        tree = tree_with("pulse", "warn", "promote", "setup_buero", "neuer_befehl", group="dienst")
        ch.apply_visibility(tree)
        items = ch.entries(tree)
        member = dict(ch.build_help(items, {"can_view_dashboard": True}, discord.Permissions.none()))
        flat = [c for rows in member.values() for c, _ in rows]
        self.assertIn("/dienst start", flat)        # groups appear automatically (before: hand-written list)
        self.assertIn("/neuer_befehl", flat)        # new commands appear without editing the help
        self.assertNotIn("/warn", flat)
        self.assertNotIn("/promote", flat)
        self.assertNotIn("/setup_buero", flat)
        lead = dict(ch.build_help(items, {"can_promote": True}, discord.Permissions(manage_guild=True)))
        flat = [c for rows in lead.values() for c, _ in rows]
        self.assertIn("/promote", flat)
        self.assertIn("/warn", flat)
        self.assertIn("/setup_buero", flat)
        self.assertIn("⏱ Dienst & ich", member)

    def test_every_listed_command_exists(self):
        import ast
        from pathlib import Path
        root = Path(ch.__file__).parent
        names = set()
        for f in root.glob("cogs/*.py"):
            for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "command":
                    names.update(ast.literal_eval(k.value) for k in node.keywords if k.arg == "name")
                if isinstance(node, ast.ClassDef):
                    names.update(ast.literal_eval(k.value) for k in node.keywords if k.arg == "group_name")
        listed = ch.MANAGE_GUILD | ch.MANAGE_ROLES | ch.MANAGE_MESSAGES | set(ch.NEEDS)
        self.assertEqual(sorted(listed - names), [])   # no typo hides nothing


if __name__ == "__main__":
    unittest.main()
