"""Modern/classic sending, editing and recognition (no Discord connection)."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import discord

import pulse_db
import pulse_discord_style as st
import pulse_embed_design

MODERN = pulse_embed_design.normalize_branding({"style": "modern"})
CLASSIC = pulse_embed_design.normalize_branding({"style": "classic"})
from tests.runtime_isolation import setUpModule, tearDownModule  # noqa: F401  (Phase 9: no ./pulse.db)


def style(brand):
    return patch.object(pulse_embed_design, "branding", return_value=brand)


def as_components(data):
    """Turn to_components() dicts into objects shaped like discord.py's received components."""
    if isinstance(data, list):
        return [as_components(x) for x in data]
    children = data.get("components")
    return SimpleNamespace(type=data.get("type"), content=data.get("content"),
                           children=as_components(children) if isinstance(children, list) else [])


class FakeMessage:
    def __init__(self, *, embed=None, layout=None, author_id=1):
        self.embeds = [embed] if embed is not None else []
        self.flags = SimpleNamespace(components_v2=layout is not None)
        self.components = as_components(layout.to_components()) if layout is not None else []
        self.author = SimpleNamespace(id=author_id)
        self.edits = []
        self.deleted = False

    async def edit(self, **kwargs):
        self.edits.append(kwargs)

    async def delete(self):
        self.deleted = True


class FakeChannel:
    def __init__(self):
        self.sent = []

    async def send(self, **kwargs):
        self.sent.append(kwargs)
        if "view" in kwargs and isinstance(kwargs["view"], discord.ui.LayoutView):
            return FakeMessage(layout=kwargs["view"])
        return FakeMessage(embed=kwargs.get("embed"))


def sample_embed():
    embed = discord.Embed(title="🎧 Voice-Support · Aktueller Status", description="Hallo <@123456789012345678>", color=0x12B76A)
    embed.add_field(name="Wartend", value="2")
    embed.set_footer(text="Pulse Voice-Support · Live-Status")
    return embed


class SendTests(unittest.TestCase):
    def test_modern_sends_container_without_embed_and_without_pings_from_embed_text(self):
        channel = FakeChannel()
        with style(MODERN):
            asyncio.run(st.send_styled(channel, embed=sample_embed()))
        sent = channel.sent[0]
        self.assertNotIn("embed", sent)
        self.assertIsInstance(sent["view"], discord.ui.LayoutView)
        mentions = sent["allowed_mentions"]
        self.assertFalse(mentions.users)
        self.assertFalse(mentions.roles)
        self.assertFalse(mentions.everyone)

    def test_modern_keeps_pings_that_were_in_the_content(self):
        channel = FakeChannel()
        with style(MODERN):
            asyncio.run(st.send_styled(channel, content="<@&111111111111111111> neuer Fall", embed=sample_embed()))
        mentions = channel.sent[0]["allowed_mentions"]
        self.assertEqual([r.id for r in mentions.roles], [111111111111111111])
        self.assertFalse(mentions.users)
        self.assertIn("<@&111111111111111111>", json.dumps(channel.sent[0]["view"].to_components()))

    def test_classic_is_unchanged(self):
        channel = FakeChannel()
        embed = sample_embed()
        with style(CLASSIC):
            asyncio.run(st.send_styled(channel, content="hi", embed=embed, view=None))
        self.assertIs(channel.sent[0]["embed"], embed)
        self.assertEqual(channel.sent[0]["content"], "hi")

    def test_buttons_move_into_the_container_and_keep_callbacks(self):
        class Control(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=None)
                self.clicked = False

            @discord.ui.button(label="A", custom_id="x:a")
            async def a(self, interaction, button):
                self.clicked = True

        control = Control()
        layout = st.layout_from_embed(sample_embed(), view=control)
        button = next(i for i in layout.walk_children() if isinstance(i, discord.ui.Button))
        self.assertEqual(button.custom_id, "x:a")
        asyncio.run(button.callback(SimpleNamespace()))
        self.assertTrue(control.clicked)

    def test_view_rows_groups_buttons_and_selects(self):
        view = discord.ui.View()
        for i in range(7):
            view.add_item(discord.ui.Button(label=str(i), custom_id=f"b{i}"))
        view.add_item(discord.ui.Select(custom_id="s", options=[discord.SelectOption(label="x")]))
        rows = st.view_rows(view)
        self.assertEqual([len(r) for r in rows], [5, 2, 1])
        self.assertIsInstance(rows[2][0], discord.ui.Select)


class EditAndRecognitionTests(unittest.TestCase):
    def test_edit_keeps_classic_format_and_untouched_fields(self):
        message = FakeMessage(embed=sample_embed())
        with style(MODERN):
            asyncio.run(st.edit_styled(message, embed=sample_embed()))
        edit = message.edits[0]
        self.assertIn("embed", edit)
        self.assertIs(edit["view"], discord.utils.MISSING)  # buttons stay
        self.assertIs(edit["content"], discord.utils.MISSING)

    def test_edit_keeps_container_format_even_in_classic_mode(self):
        with style(MODERN):
            message = FakeMessage(layout=st.layout_from_embed(sample_embed()))
        with style(CLASSIC):
            asyncio.run(st.edit_styled(message, embed=sample_embed()))
        edit = message.edits[0]
        self.assertNotIn("embed", edit)
        self.assertIsInstance(edit["view"], discord.ui.LayoutView)
        self.assertEqual(edit["allowed_mentions"].users, False)

    def test_card_info_and_same_content_for_both_formats(self):
        embed = sample_embed()
        classic = FakeMessage(embed=embed)
        with style(MODERN):
            modern = FakeMessage(layout=st.layout_from_embed(embed))
            for message in (classic, modern):
                info = st.card_info(message)
                self.assertEqual(info["title"], "🎧 Voice-Support · Aktueller Status")
                self.assertEqual(info["footer"], "Pulse Voice-Support · Live-Status")
                self.assertTrue(st.same_content(message, embed))
            changed = sample_embed()
            changed.set_field_at(0, name="Wartend", value="3")
            self.assertFalse(st.same_content(modern, changed))
        self.assertIsNone(st.card_info(FakeMessage()))

    def test_upsert_replaces_once_after_style_change(self):
        channel = FakeChannel()
        old = FakeMessage(embed=sample_embed())
        with style(MODERN):
            new = asyncio.run(st.upsert_styled(channel, old, embed=sample_embed()))
            self.assertTrue(old.deleted)
            self.assertTrue(st.is_v2(new))
            again = asyncio.run(st.upsert_styled(channel, new, embed=sample_embed()))
        self.assertIs(again, new)
        self.assertEqual(len(channel.sent), 1)

    def test_rating_confirmation_matches_message_format(self):
        calls = []

        async def edit_message(**kwargs):
            calls.append(kwargs)

        with style(MODERN):
            v2 = FakeMessage(layout=st.layout_from_embed(sample_embed()))
        for message in (v2, FakeMessage(embed=sample_embed())):
            interaction = SimpleNamespace(message=message, response=SimpleNamespace(edit_message=edit_message))
            asyncio.run(st.respond_edit_styled(interaction, text="Danke"))
        self.assertIsInstance(calls[0]["view"], discord.ui.LayoutView)
        self.assertNotIn("content", calls[0])
        self.assertEqual(calls[1]["content"], "Danke")


class TicketCardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.db.start()
        pulse_db.init_db()

    def tearDown(self):
        self.db.stop()
        self.tmp.cleanup()

    def test_container_ticket_status_update_uses_stored_card(self):
        from cogs import tickets
        embed = discord.Embed(title="🎫 Support", description="Hallo", color=0x5B5CF0)
        for name in ("Status", "Priorität", "Bearbeiter"):
            embed.add_field(name=name, value="alt")
        tickets.save_ticket_card("t1", embed, [])
        with style(MODERN):
            message = FakeMessage(layout=st.layout_from_embed(embed, view=tickets.TicketControlView()), author_id=99)

        class Channel:
            guild = SimpleNamespace(me=SimpleNamespace(id=99))

            def history(self, **_):
                async def gen():
                    yield message
                return gen()

        ticket = {"id": "t1", "status": "open", "claimed_by_id": "5", "claimed_by_name": "Max", "priority": "high"}
        with style(MODERN):
            asyncio.run(tickets.refresh_ticket_embed(Channel(), ticket))
        layout = json.dumps(message.edits[0]["view"].to_components(), ensure_ascii=False)
        self.assertIn("🔵 In Bearbeitung", layout)
        self.assertIn("Max", layout)
        self.assertIn("pulse_ticket_claim", layout)  # control buttons still present
        tickets.drop_ticket_card("t1")
        self.assertIsNone(pulse_db.get_setting("ticket_card:t1", None))

    def test_transcript_contains_container_text(self):
        from cogs import tickets
        with style(MODERN):
            message = FakeMessage(layout=st.layout_from_embed(sample_embed()))
        message.content = ""
        message.attachments = []
        message.created_at = discord.utils.utcnow()
        message.author = SimpleNamespace(id=1, __str__=lambda self: "Pulse")

        class Channel:
            def history(self, **_):
                async def gen():
                    yield message
                return gen()

        text = asyncio.run(tickets.transcript(Channel()))
        self.assertIn("Aktueller Status", text)
        self.assertIn("Wartend", text)


if __name__ == "__main__":
    unittest.main()
