"""Honeypot warning in the configured style; team-status API matches its page permission."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import discord

import pulse_discord_style as st
import pulse_embed_design
from cogs import honeypot

MODERN = pulse_embed_design.normalize_branding({"style": "modern"})
BOT_ID = 7


def as_components(data):
    if isinstance(data, list):
        return [as_components(x) for x in data]
    children = data.get("components")
    return SimpleNamespace(type=data.get("type"), content=data.get("content"),
                           children=as_components(children) if isinstance(children, list) else [])


class Msg:
    def __init__(self, *, embed=None, layout=None, author=BOT_ID):
        self.embeds = [embed] if embed is not None else []
        self.flags = SimpleNamespace(components_v2=layout is not None)
        self.components = as_components(layout.to_components()) if layout is not None else []
        self.author = SimpleNamespace(id=author)
        self.deleted = False
        self.edits = []

    async def delete(self):
        self.deleted = True

    async def edit(self, **kwargs):
        self.edits.append(kwargs)


class Channel:
    def __init__(self, messages):
        self.messages = messages
        self.sent = []

    def history(self, limit=50):
        async def gen():
            for m in self.messages:
                yield m
        return gen()

    async def send(self, **kwargs):
        self.sent.append(kwargs)
        return Msg(layout=kwargs.get("view"))


def old_warning():
    embed = discord.Embed(title="🚨 HONEYPOT – HIER AUF KEINEN FALL SCHREIBEN!", description="alt")
    embed.set_footer(text=honeypot.HONEYPOT_WARNING_MARKER)
    return Msg(embed=embed)


class HoneypotTests(unittest.TestCase):
    def run_ready(self, channel):
        cog = honeypot.Honeypot(SimpleNamespace(user=SimpleNamespace(id=BOT_ID)))
        cog._get_honeypot_channel = lambda: asyncio.sleep(0, result=channel)
        with patch.object(pulse_embed_design, "branding", return_value=MODERN), patch("builtins.print"):
            asyncio.run(cog.on_ready())

    def test_old_embed_warning_is_replaced_exactly_once(self):
        old = old_warning()
        channel = Channel([old])
        self.run_ready(channel)
        self.assertTrue(old.deleted)
        self.assertEqual(len(channel.sent), 1)
        self.assertIsInstance(channel.sent[0]["view"], discord.ui.LayoutView)
        text = str(channel.sent[0]["view"].to_components())
        self.assertNotIn(honeypot.HONEYPOT_WARNING_MARKER, text)
        self.assertIn("permanenten Server-Ban", text)

    def test_current_warning_needs_no_api_call_and_duplicates_are_removed(self):
        with patch.object(pulse_embed_design, "branding", return_value=MODERN):
            current = Msg(layout=st.layout_from_embed(honeypot.build_honeypot_embed()))
            duplicate = Msg(layout=st.layout_from_embed(honeypot.build_honeypot_embed()))
        foreign = old_warning()
        foreign.author = SimpleNamespace(id=999)  # not ours: never touched
        channel = Channel([current, duplicate, foreign])
        self.run_ready(channel)
        self.assertEqual(channel.sent, [])
        self.assertEqual(current.edits, [])
        self.assertFalse(current.deleted)
        self.assertTrue(duplicate.deleted)
        self.assertFalse(foreign.deleted)

    def test_missing_warning_is_sent(self):
        channel = Channel([])
        self.run_ready(channel)
        self.assertEqual(len(channel.sent), 1)


class TeamStatusApiTests(unittest.TestCase):
    def setUp(self):
        from fastapi import HTTPException
        from fastapi.testclient import TestClient
        import webserver as ws
        self.ws = ws
        self.client = TestClient(ws.app)
        self.HTTPException = HTTPException

    def call(self, perms):
        guild = SimpleNamespace(id=1, name="G", members=[], roles=[], get_member=lambda _: None, get_role=lambda _: None)

        def fake_auth(request, session, perm="can_view_dashboard", admin=False):
            if perm and not perms.get(perm):
                raise self.HTTPException(status_code=403, detail="no")
            return SimpleNamespace(user={"id": "1"}, guild=guild, guild_name="G", perms=perms,
                                   config={"team_role_ids": []}, member=None, msg="", msg_ok=True)

        with patch.object(self.ws, "auth", fake_auth):
            return self.client.get("/api/team/discord-status")

    def test_leader_with_promote_right_gets_live_status(self):
        self.assertEqual(self.call({"can_promote": True}).status_code, 200)

    def test_without_any_right_still_forbidden(self):
        self.assertEqual(self.call({"can_view_dashboard": True}).status_code, 403)

    def test_robots_and_noindex(self):
        response = self.client.get("/robots.txt")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Disallow: /", response.text)
        self.assertEqual(response.headers.get("X-Robots-Tag"), "noindex, nofollow")


if __name__ == "__main__":
    unittest.main()
