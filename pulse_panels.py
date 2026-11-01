"""BOT-14: overview of all persistent Discord panels in one place.

Each panel keeps its location where it always did (pulse.db settings or the cog's own SQLite file);
this module only *reads* them – cog databases are opened read-only, so the overview can never create
or change a file. "Neu veröffentlichen" is offered where a tested publish function already exists
(tickets, verification, duty panel); for the others the overview names the existing setup command.
"""
from __future__ import annotations

import sqlite3

import pulse_db
import pulse_paths

BASE_DIR = pulse_paths.DATA_DIR  # Phase 13: the cogs' SQLite files live in the data directory
REPUBLISH = ("tickets", "verification", "duty")


def _cog_config(db_file: str, key: str):
    """Read one value from a cog's own config table without creating the file."""
    path = BASE_DIR / db_file
    if not path.is_file():
        return None
    try:
        con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=2)
        try:
            row = con.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
        finally:
            con.close()
        return int(row[0]) if row and str(row[0]).isdigit() else (row[0] if row else None)
    except sqlite3.Error:
        return None


def _quiz_channel(guild_id: int):
    path = BASE_DIR / "flaggenquiz.db"
    if not path.is_file():
        return None
    try:
        con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=2)
        try:
            row = con.execute("SELECT channel_id FROM quiz_config WHERE guild_id=?", (int(guild_id),)).fetchone()
        finally:
            con.close()
        return int(row[0]) if row and row[0] else None
    except sqlite3.Error:
        return None


def _entry(key, name, channel_id=None, message_id=None, how="", republish=False):
    def as_int(value):
        try:
            return int(value) if value else None
        except (TypeError, ValueError):
            return None
    return {"key": key, "name": name, "channel_id": as_int(channel_id), "message_id": as_int(message_id),
            "how": how, "republish": republish}


def collect(guild_id: int) -> list[dict]:
    """Every known panel with its stored channel/message (None = not set up)."""
    import pulse_ids
    items = []
    tickets = pulse_db.get_setting("ticket_system_config", {}) or {}
    panels = tickets.get("ticket_panels") if isinstance(tickets.get("ticket_panels"), list) else []
    for panel in panels:
        if isinstance(panel, dict):
            items.append(_entry("tickets", f"Ticket-Panel · {panel.get('name') or panel.get('title') or panel.get('id')}",
                                panel.get("channel_id"), panel.get("message_id"), "Dashboard → Tickets → Panels", True))
    legacy = tickets.get("panel_messages") if isinstance(tickets.get("panel_messages"), dict) else {}
    if tickets.get("panel_channel_id") and tickets.get("panel_message_id"):
        legacy = {**legacy, str(tickets["panel_channel_id"]): tickets["panel_message_id"]}
    for channel_id, message_id in legacy.items():
        items.append(_entry("tickets", "Ticket-Panel (Standard)", channel_id, message_id, "/ticketpanel oder Dashboard → Tickets", True))
    if not panels and not legacy:
        items.append(_entry("tickets", "Ticket-Panel", how="Dashboard → Tickets → Panels", republish=True))

    verification = pulse_db.get_setting("verification_config", {}) or {}
    items.append(_entry("verification", "Verifizierung", verification.get("published_channel_id") or verification.get("channel_id"),
                        verification.get("message_id"), "Dashboard → Verwaltung → Discord-Auftritt", True))
    duty = pulse_db.get_setting("duty_panel", {}) or {}
    items.append(_entry("duty", "Dienst-Panel", duty.get("channel_id"), duty.get("message_id"), "/dienst panel im Zielkanal", True))
    items.append(_entry("abmeldung", "Abmeldungsliste", _cog_config("abmeldungen.db", "list_channel_id"),
                        _cog_config("abmeldungen.db", "list_message_id"), "/setup_liste im Zielkanal"))
    items.append(_entry("rp", "RP-Verwaltung", _cog_config("rp_verwaltung.db", "announcement_channel_id"), None, "/setup_rp"))
    items.append(_entry("buero", "Büro-Warteraum", _cog_config("buero_system.db", "ping_channel_id"), None, "/setup_buero"))
    items.append(_entry("activity", "Activity Check", _cog_config("activity_check.db", "activity_channel_id"), None, "/setup_activitycheck"))
    items.append(_entry("teamliste", "Teamliste", _cog_config("teamliste.db", "teamlist_channel_id"), None, "/setup_teamliste"))
    items.append(_entry("quiz", "Flaggenquiz", _quiz_channel(guild_id), None, "/setup_flaggenquiz"))
    items.append(_entry("honeypot", "Honeypot-Warnung", pulse_ids.HONEYPOT_CHANNEL_ID, None, "feste ID (pulse_ids.HONEYPOT_CHANNEL_ID)"))
    return items


async def check(guild, items: list[dict], fetch_limit: int = 15) -> list[dict]:
    """Adds 'state': ok / missing_channel / missing_message / not_set_up. At most fetch_limit message lookups."""
    import discord
    fetched = 0
    for item in items:
        channel = guild.get_channel(item["channel_id"]) if (guild is not None and item["channel_id"]) else None
        if not item["channel_id"]:
            item["state"] = "not_set_up"
        elif channel is None:
            item["state"] = "missing_channel"
        elif item["message_id"] and fetched < fetch_limit and hasattr(channel, "fetch_message"):
            fetched += 1
            try:
                await channel.fetch_message(item["message_id"])
                item["state"] = "ok"
            except discord.NotFound:
                item["state"] = "missing_message"
            except discord.HTTPException:
                item["state"] = "unknown"
        else:
            item["state"] = "ok"
        item["channel_name"] = getattr(channel, "name", "")
    return items


async def republish(bot, guild, key: str) -> str:
    """Publish/repair one panel kind with its existing function. Returns a message for the dashboard."""
    if key == "tickets":
        cog = bot.get_cog("Tickets") or next((c for c in bot.cogs.values() if hasattr(c, "refresh_panels")), None)
        if cog is None:
            raise ValueError("Das Ticket-System ist nicht geladen.")
        await cog.refresh_panels()
        return "Ticket-Panels wurden aktualisiert."
    if key == "verification":
        cog = next((c for c in bot.cogs.values() if hasattr(c, "publish_panel")), None)
        if cog is None:
            raise ValueError("Die Verifizierung ist nicht geladen.")
        await cog.publish_panel(guild)
        return "Verifizierungs-Panel wurde veröffentlicht."
    if key == "duty":
        from cogs.dienst import PANEL_SETTING, DutyPanelView, panel_embed
        stored = pulse_db.get_setting(PANEL_SETTING, {}) or {}
        channel = guild.get_channel(int(stored.get("channel_id") or 0)) if stored else None
        if channel is None:
            raise ValueError("Für das Dienst-Panel ist kein Kanal gespeichert – einmal /dienst panel im Zielkanal ausführen.")
        import discord
        if stored.get("message_id"):
            try:
                old = await channel.fetch_message(int(stored["message_id"]))
                await old.edit(embed=panel_embed(guild.name), view=DutyPanelView())
                return "Dienst-Panel wurde aktualisiert."
            except discord.NotFound:
                pass
        message = await channel.send(embed=panel_embed(guild.name), view=DutyPanelView())
        pulse_db.set_setting(PANEL_SETTING, {"channel_id": channel.id, "message_id": message.id})
        return "Dienst-Panel wurde neu veröffentlicht."
    raise ValueError("Für dieses Panel gibt es kein automatisches Neuveröffentlichen – bitte den genannten Befehl nutzen.")
