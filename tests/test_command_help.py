"""BOT-04: setup commands hidden from ordinary members; help generated from the command tree."""
import asyncio
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


class AliasGroupTests(unittest.TestCase):
    """BOT-04 (E-11): /team and /setup built from the existing commands; old names stay."""

    def build(self):
        from discord.ext import commands

        import pulse_command_groups as cg

        class Demo(commands.Cog):
            def __init__(self):
                self.calls = []

            @app_commands.command(name="team-kick", description="Nur Teamrollen entfernen")
            @app_commands.describe(grund="Pflichtgrund")
            async def team_kick(self, interaction: discord.Interaction, member: str, grund: str = ""):
                self.calls.append((member, grund))

            @app_commands.command(name="setup_rp", description="RP-Panel einrichten")
            async def setup_rp(self, interaction: discord.Interaction):
                self.calls.append("rp")

            @app_commands.command(name="promote", description="Befördern")
            async def promote(self, interaction: discord.Interaction, member: str):
                self.calls.append(("promote", member))

        async def go():
            bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
            cog = Demo()
            await bot.add_cog(cog)
            missing = cg.add_alias_groups(bot.tree)
            return bot, cog, missing
        return asyncio.run(go())

    def test_groups_reuse_the_original_callbacks(self):
        bot, cog, missing = self.build()
        self.assertIn("warn", missing)                                   # not loaded here → reported, not invented
        team = bot.tree.get_command("team")
        kick = team.get_command("kick")
        self.assertEqual([p.name for p in kick.parameters], ["member", "grund"])
        self.assertEqual(kick.parameters[1].description, "Pflichtgrund")
        asyncio.run(kick._do_call(None, {"member": "Max", "grund": "Inaktiv"}))
        self.assertEqual(cog.calls, [("Max", "Inaktiv")])
        self.assertIsNotNone(bot.tree.get_command("team-kick"))          # old name still works
        self.assertTrue(bot.tree.get_command("setup").default_permissions.manage_guild)
        import pulse_command_groups as cg
        self.assertEqual(cg.add_alias_groups(bot.tree), [])               # second call changes nothing

    def test_help_lists_aliases_once_with_original_rights(self):
        bot, _, _ = self.build()
        items = ch.entries(bot.tree)
        names = [i[0] for i in items]
        self.assertIn("team kick", names)
        self.assertNotIn("team-kick", names)
        member = [c for rows in dict(ch.build_help(items, {"can_view_dashboard": True}, discord.Permissions.none())).values() for c, _ in rows]
        self.assertNotIn("/team befoerdern", member)                    # same rule as /promote: leadership only
        lead = [c for rows in dict(ch.build_help(items, {"can_promote": True}, discord.Permissions.none())).values() for c, _ in rows]
        self.assertIn("/team befoerdern", lead)

    def test_alias_targets_exist(self):
        import ast
        from pathlib import Path

        import pulse_command_groups as cg
        root = Path(ch.__file__).parent
        names = set()
        for f in root.glob("cogs/*.py"):
            for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "command":
                    names.update(ast.literal_eval(k.value) for k in node.keywords if k.arg == "name")
        targets = {orig for _, (_, mapping) in cg.ALIASES.items() for orig in mapping.values()}
        self.assertEqual(sorted(targets - names), [])


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
