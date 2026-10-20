"""Ticket categories: creation/claimed Discord category, moving, welcome layout, view safety."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import discord

import pulse_db
import pulse_embed_design
from cogs import tickets

MODERN = pulse_embed_design.normalize_branding({"style": "modern"})


class FakeCategory(discord.CategoryChannel):
    """isinstance(…, CategoryChannel) without a Discord connection."""
    def __init__(self, cid, channels=0):  # noqa: D401 – no super().__init__
        self._fake_id = cid
        self._fake_channels = [object()] * channels

    id = property(lambda self: self._fake_id)
    channels = property(lambda self: self._fake_channels)


class FakeChannel:
    def __init__(self, guild, category_id=None, error=None):
        self.guild = guild
        self.category_id = category_id
        self.edits = []
        self.error = error

    async def edit(self, **kwargs):
        if self.error:
            raise self.error
        self.edits.append(kwargs)
        self.category_id = kwargs["category"].id


def guild_with(*categories):
    lookup = {c.id: c for c in categories}
    return SimpleNamespace(get_channel=lambda cid: lookup.get(cid))


def definitions(parent=111, claimed=222):
    return {"support": {"id": "support", "parent_category_id": parent, "claimed_category_id": claimed}}


class MoveTests(unittest.TestCase):
    def run_move(self, channel, claimed, defs=None):
        with patch.object(tickets, "ticket_category_definitions", return_value=defs or definitions()), patch("builtins.print"):
            return asyncio.run(tickets.move_ticket_channel(channel, {"id": "t1", "category": "support"}, claimed=claimed))

    def test_claim_moves_to_claimed_category_keeping_permissions(self):
        open_cat, claimed_cat = FakeCategory(111), FakeCategory(222)
        channel = FakeChannel(guild_with(open_cat, claimed_cat), category_id=111)
        self.assertEqual(self.run_move(channel, True), "moved")
        self.assertIs(channel.edits[0]["category"], claimed_cat)
        self.assertFalse(channel.edits[0]["sync_permissions"])
        self.assertEqual(self.run_move(channel, False), "moved")  # released → back to creation category
        self.assertIs(channel.edits[1]["category"], open_cat)

    def test_unconfigured_unchanged_full_and_errors_are_safe(self):
        channel = FakeChannel(guild_with(FakeCategory(222)), category_id=111)
        self.assertEqual(self.run_move(channel, True, definitions(claimed=0)), "not_configured")
        self.assertEqual(self.run_move(FakeChannel(guild_with(FakeCategory(222)), category_id=222), True), "unchanged")
        self.assertEqual(self.run_move(FakeChannel(guild_with(FakeCategory(222, channels=50)), category_id=111), True), "full")
        forbidden = discord.Forbidden(SimpleNamespace(status=403, reason="x"), "no")
        self.assertEqual(self.run_move(FakeChannel(guild_with(FakeCategory(222)), category_id=111, error=forbidden), True), "failed")
        self.assertEqual(self.run_move(channel, True, definitions(claimed="kaputt")), "not_configured")


class ViewSafetyTests(unittest.TestCase):
    def test_control_view_is_fresh_and_shows_claim_state(self):
        registered = tickets.TicketControlView()
        fresh = tickets.control_view_for({"claimed_by_id": "5", "claimed_by_name": "Max"})
        self.assertIsNot(fresh, registered)
        claim = next(i for i in fresh.children if i.custom_id == "pulse_ticket_claim")
        self.assertTrue(claim.disabled)
        self.assertIn("Max", claim.label)
        # Converting the fresh copy never touches a registered instance.
        tickets.build_ticket_welcome_layout(discord.Embed(title="T"), fresh, {})
        self.assertTrue(all(item.view is registered for item in registered.children))


class WelcomeLayoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.db.start()
        pulse_db.init_db()

    def tearDown(self):
        self.db.stop()
        self.tmp.cleanup()

    def welcome_embed(self):
        embed = discord.Embed(title="💢 HIGHTEAM-TICKET", description="Willkommen!\n\n**Ticket-ID:** `t1` · **Kategorie:** 💢 Highteam")
        for name, value in (("Status", "🟢 Offen"), ("Priorität", "🟢 Normal"), ("Bearbeiter", "Noch nicht übernommen"),
                            ("📝 Roblox-Name", "Max123")):
            embed.add_field(name=name, value=value)
        return embed

    def test_layout_structure(self):
        meta = {"ticket_id": "t1", "category": "Highteam", "emoji": "💢", "creator_id": "123456789012345678"}
        layout = tickets.build_ticket_welcome_layout(self.welcome_embed(), tickets.control_view_for({}), meta)
        text = json.dumps(layout.to_components(), ensure_ascii=False)
        self.assertIn("## 💢 HIGHTEAM-TICKET", text)
        self.assertIn("### 📝 Deine Angaben", text)
        self.assertIn("> Max123", text)
        self.assertIn("### 📌 Ticket-Übersicht", text)
        self.assertIn("<@123456789012345678>", text)
        self.assertNotIn("**Ticket-ID:** `t1` ·", text)  # the classic tail is not duplicated
        self.assertIn("pulse_ticket_close", text)

    def test_claim_refresh_rebuilds_overview_in_container(self):
        from pulse_discord_style import layout_from_embed
        embed = self.welcome_embed()
        meta = {"ticket_id": "t1", "category": "Highteam", "emoji": "💢", "creator_id": "123456789012345678"}
        tickets.save_ticket_card("t1", embed, [], meta)

        def as_components(data):
            if isinstance(data, list):
                return [as_components(x) for x in data]
            kids = data.get("components")
            return SimpleNamespace(content=data.get("content"), children=as_components(kids) if isinstance(kids, list) else [])

        with patch.object(pulse_embed_design, "branding", return_value=MODERN):
            layout = layout_from_embed(embed, view=tickets.control_view_for({}))
        message = SimpleNamespace(author=SimpleNamespace(id=99), embeds=[], flags=SimpleNamespace(components_v2=True),
                                  components=as_components(layout.to_components()), edits=[])

        async def edit(**kwargs):
            message.edits.append(kwargs)
        message.edit = edit

        class Channel:
            guild = SimpleNamespace(me=SimpleNamespace(id=99))

            def history(self, **_):
                async def gen():
                    yield message
                return gen()

        ticket = {"id": "t1", "category": "highteam", "status": "open", "claimed_by_id": "5", "claimed_by_name": "Max", "priority": "high"}
        with patch.object(pulse_embed_design, "branding", return_value=MODERN):
            asyncio.run(tickets.refresh_ticket_embed(Channel(), ticket))
        text = json.dumps(message.edits[0]["view"].to_components(), ensure_ascii=False)
        self.assertIn("### 📌 Ticket-Übersicht", text)
        self.assertIn("🔵 In Bearbeitung", text)
        self.assertIn("Übernommen · Max", text)


class CategorySaveTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        import webserver as ws
        self.ws = ws
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))]
        for p in self.patches:
            p.start()
        pulse_db.init_db()
        self.client = TestClient(ws.app)
        cats = {111: FakeCategory(111), 222: FakeCategory(222)}
        for cat in cats.values():
            object.__setattr__(cat, "_fake_guild", None)
        self.guild = SimpleNamespace(id=1, name="G", get_channel=lambda cid: cats.get(cid), get_role=lambda _: None)
        for cat in cats.values():
            type(cat).guild = property(lambda c, g=self.guild: g)

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def post(self, **extra):
        perms = {k: True for k in self.ws.PERM_KEYS}
        perms["is_admin"] = True
        ctx = SimpleNamespace(user={"id": "1", "global_name": "A"}, guild=self.guild, guild_name="G", perms=perms,
                              config={}, member=None, msg="", msg_ok=True)
        data = {"name": "Highteam", "prefix": "highteam", "emoji": "💢", "description": "Vertraulich", "max_open": "3",
                "group": "team", "category_id": "", "parent_category_id": "111"}
        data.update(extra)
        with patch.object(self.ws, "auth", lambda *a, **k: ctx), patch.object(self.ws, "log_audit", lambda *a, **k: None):
            return self.client.post("/tickets/categories/save", data=data, follow_redirects=False)

    def saved(self):
        items = pulse_db.get_setting("ticket_system_config", {}).get("ticket_categories", [])
        return next(i for i in items if i.get("name") == "Highteam")

    def test_claimed_category_is_saved_and_validated(self):
        self.assertEqual(self.post(builder_v2="1", claimed_category_id="222").status_code, 303)
        self.assertEqual(self.saved()["claimed_category_id"], 222)
        bad = self.post(builder_v2="1", claimed_category_id="999")
        self.assertIn("t=err", bad.headers["location"])

    def test_older_form_keeps_stored_claimed_category(self):
        self.post(builder_v2="1", claimed_category_id="222")
        key = self.saved()["id"]
        self.post(category_id=key)  # older form without the field
        self.assertEqual(self.saved()["claimed_category_id"], 222)

    def post_files(self, files=None, **extra):
        perms = {k: True for k in self.ws.PERM_KEYS}
        perms["is_admin"] = True
        ctx = SimpleNamespace(user={"id": "1", "global_name": "A"}, guild=self.guild, guild_name="G", perms=perms,
                              config={}, member=None, msg="", msg_ok=True)
        data = {"name": "Highteam", "prefix": "highteam", "emoji": "💢", "description": "Vertraulich", "max_open": "3",
                "group": "team", "category_id": "", "parent_category_id": "111", "builder_v2": "1"}
        data.update(extra)
        with patch.object(self.ws, "auth", lambda *a, **k: ctx), patch.object(self.ws, "log_audit", lambda *a, **k: None):
            return self.client.post("/tickets/categories/save", data=data, files=files or {}, follow_redirects=False)

    def test_category_banner_upload_replace_remove(self):
        png = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
        with patch.object(self.ws, "TICKET_PANEL_IMAGE_DIR", Path(self.tmp.name) / "panels"):
            self.post_files({"welcome_banner_upload": ("b.png", png, "image/png")})
            first = self.saved()["welcome_banner_file"]
            key = self.saved()["id"]
            self.assertTrue(first.startswith(f"cat-{key}_welcome_banner_"))
            self.assertTrue((Path(self.tmp.name) / "panels" / first).is_file())
            self.post_files({"welcome_banner_upload": ("c.png", png, "image/png")}, category_id=key)
            second = self.saved()["welcome_banner_file"]
            self.assertNotEqual(first, second)
            self.assertFalse((Path(self.tmp.name) / "panels" / first).exists())
            self.post_files(category_id=key)  # saving without a file keeps it
            self.assertEqual(self.saved()["welcome_banner_file"], second)
            bad = self.post_files({"welcome_banner_upload": ("x.png", b"kein bild", "image/png")}, category_id=key)
            self.assertIn("t=err", bad.headers["location"])
            self.post_files(category_id=key, remove_welcome_banner="1")
            self.assertEqual(self.saved()["welcome_banner_file"], "")
            self.assertFalse((Path(self.tmp.name) / "panels" / second).exists())

    def test_category_banner_url_validation(self):
        bad = self.post_files(welcome_banner_url="http://unsicher.example/b.png")
        self.assertIn("t=err", bad.headers["location"])
        self.post_files(welcome_banner_url="https://cdn.example/b.png")
        self.assertEqual(self.saved()["welcome_banner_url"], "https://cdn.example/b.png")


class BannerChoiceTests(unittest.TestCase):
    def test_category_banner_wins_over_panel(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("cat-x_welcome_banner_aa.png", "panel_welcome_banner_bb.png"):
                (Path(tmp) / name).write_bytes(b"x")
            with patch.object(tickets, "PANEL_IMAGE_DIR", tmp):
                choose = tickets.welcome_banner_choice
                cat_file = {"welcome_banner_file": "cat-x_welcome_banner_aa.png"}
                panel_file = {"welcome_banner_file": "panel_welcome_banner_bb.png", "welcome_banner_url": "https://p.example/b.png"}
                self.assertTrue(choose(cat_file, panel_file)[0].endswith("cat-x_welcome_banner_aa.png"))
                self.assertEqual(choose({"welcome_banner_url": "https://c.example/b.png"}, panel_file), (None, "https://c.example/b.png"))
                self.assertTrue(choose({}, panel_file)[0].endswith("panel_welcome_banner_bb.png"))
                self.assertEqual(choose({}, {"welcome_banner_url": "https://p.example/b.png"}), (None, "https://p.example/b.png"))
                self.assertEqual(choose({"welcome_banner_url": "javascript:x"}, {}), (None, ""))
                self.assertEqual(choose({"welcome_banner_file": "../../pulse.db"}, {}), (None, ""))


if __name__ == "__main__":
    unittest.main()
