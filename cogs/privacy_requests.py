"""Audit #171: players file and follow their own data-subject requests privately in Discord.

/datenschutz-anfrage  – create a request for oneself (never for another person)
/datenschutz-status   – list one's own requests with status and deadline
Administrators are informed in the Pulse inbox; overdue requests remind the assignee once a day.
Pulse never changes or deletes data because of a request – people do that in the dashboard and document it.
"""
from __future__ import annotations

import asyncio
import sqlite3

import discord
from discord import app_commands
from discord.ext import commands, tasks

import pulse_db
import pulse_privacy_requests as dsr

TEXT = {
    "de": {
        "guild_only": "Datenschutzanfragen kannst du nur auf dem Server stellen.",
        "created": ("🔒 Deine Anfrage **{id}** ({kind}) ist eingegangen.\nDie Leitung beantwortet sie spätestens bis **{due}**. "
                    "Den Stand siehst du jederzeit mit `/datenschutz-status`.\nPulse ändert oder löscht dabei nichts automatisch – "
                    "ein Mensch prüft dein Anliegen."),
        "error": "❌ {message}",
        "storage": "❌ Deine Anfrage konnte gerade nicht gespeichert werden. Bitte versuche es später erneut oder öffne ein Ticket.",
        "none": "Du hast bisher keine Datenschutzanfragen gestellt.",
        "status_title": "🔒 Deine Datenschutzanfragen",
        "line": "`{id}` · {kind} · **{status}** · {due}",
        "due_open": "Antwort bis {due}",
        "due_overdue": "Frist abgelaufen – die Leitung ist informiert",
        "due_closed": "abgeschlossen",
    },
    "en": {
        "guild_only": "You can only file privacy requests on the server.",
        "created": ("🔒 Your request **{id}** ({kind}) was received.\nThe server leadership will answer by **{due}** at the latest. "
                    "Check the status any time with `/datenschutz-status`.\nPulse does not change or delete anything automatically – "
                    "a person reviews your request."),
        "error": "❌ {message}",
        "storage": "❌ Your request could not be saved right now. Please try again later or open a ticket.",
        "none": "You have not filed any privacy requests yet.",
        "status_title": "🔒 Your privacy requests",
        "line": "`{id}` · {kind} · **{status}** · {due}",
        "due_open": "answer by {due}",
        "due_overdue": "deadline passed – leadership has been notified",
        "due_closed": "closed",
    },
}
KIND_EN = {"access": "Access", "rectification": "Rectification", "erasure": "Erasure", "restriction": "Restriction",
           "objection": "Objection"}
STATUS_EN = {"received": "received", "in_progress": "in progress", "waiting": "waiting for you", "completed": "completed",
             "rejected": "rejected"}
KIND_CHOICES = [app_commands.Choice(name=label, value=key) for key, label in dsr.KINDS.items()]


def locale_for(interaction) -> str:
    try:
        import pulse_i18n
        return pulse_i18n.get_locale(interaction.user.id, getattr(interaction, "locale", None))
    except Exception:
        return "de"


def text(locale: str, key: str, **values) -> str:
    template = TEXT.get(locale, TEXT["de"]).get(key, TEXT["de"][key])
    return template.format(**values)


def kind_label(locale: str, kind: str) -> str:
    return KIND_EN.get(kind, kind) if locale == "en" else dsr.KINDS.get(kind, kind)


def status_label(locale: str, status: str) -> str:
    return STATUS_EN.get(status, status) if locale == "en" else dsr.STATUSES.get(status, status)


def due_label(item: dict) -> str:
    parsed = dsr._parse(item.get("due_at"))
    return discord.utils.format_dt(parsed, "D") if parsed else "–"


def notify_admins(guild, item: dict) -> int:
    """Inbox message to the server owner and Discord administrators (bounded, deduplicated per request)."""
    sent = 0
    for member_id, _name in dsr.admin_members(guild):
        pulse_db.notify(member_id, "🔒 Neue Datenschutzanfrage", f"{item['id']} · {item['kind_label']} – Frist {item['due_at'][:10]}",
                        "warning", f"/admin/privacy/requests/{item['id']}", f"dsr-new:{item['id']}", 86400 * 30)
        sent += 1
    return sent


async def submit(interaction, kind: str, details: str) -> None:
    locale = locale_for(interaction)
    guild = getattr(interaction, "guild", None)
    if guild is None:
        await interaction.response.send_message(text(locale, "guild_only"), ephemeral=True)
        return
    user = interaction.user
    try:
        item = await asyncio.to_thread(
            dsr.create_request, guild.id, user.id, getattr(user, "display_name", str(user)), kind, details or "",
            channel="discord", created_by_id=user.id, created_by_name=getattr(user, "display_name", str(user)))
    except ValueError as exc:
        await interaction.response.send_message(text(locale, "error", message=str(exc)), ephemeral=True)
        return
    except (sqlite3.Error, OSError):
        await interaction.response.send_message(text(locale, "storage"), ephemeral=True)
        return
    try:
        notify_admins(guild, item)
    except (sqlite3.Error, OSError) as exc:
        print(f"⚠️ Datenschutzanfrage {item['id']}: Admin-Hinweis fehlgeschlagen ({type(exc).__name__}).")
    await interaction.response.send_message(
        text(locale, "created", id=item["id"], kind=kind_label(locale, kind), due=due_label(item)), ephemeral=True)


async def show_status(interaction) -> None:
    locale = locale_for(interaction)
    guild = getattr(interaction, "guild", None)
    if guild is None:
        await interaction.response.send_message(text(locale, "guild_only"), ephemeral=True)
        return
    try:
        rows = await asyncio.to_thread(dsr.list_requests, guild.id, state="all", user_id=interaction.user.id)
    except (sqlite3.Error, OSError):
        await interaction.response.send_message(text(locale, "storage"), ephemeral=True)
        return
    if not rows:
        await interaction.response.send_message(text(locale, "none"), ephemeral=True)
        return
    lines = []
    for item in rows[:15]:
        if not item["is_open"]:
            due = text(locale, "due_closed")
        elif item["overdue"]:
            due = text(locale, "due_overdue")
        else:
            due = text(locale, "due_open", due=due_label(item))
        lines.append(text(locale, "line", id=item["id"], kind=kind_label(locale, item["kind"]),
                          status=status_label(locale, item["status"]), due=due))
    await interaction.response.send_message(text(locale, "status_title") + "\n" + "\n".join(lines), ephemeral=True)


class PrivacyRequests(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.overdue_loop.start()

    async def cog_unload(self) -> None:
        self.overdue_loop.cancel()

    @app_commands.command(name="datenschutz-anfrage",
                          description="Auskunft, Berichtigung oder Löschung deiner gespeicherten Daten beantragen (privat)")
    @app_commands.describe(art="Worum geht es?", beschreibung="Optional: welche Daten oder welcher Bereich (bei Berichtigung Pflicht)")
    @app_commands.choices(art=KIND_CHOICES)
    @app_commands.guild_only()
    async def datenschutz_anfrage(self, interaction: discord.Interaction, art: app_commands.Choice[str],
                                  beschreibung: app_commands.Range[str, 0, dsr.MAX_DETAILS] = ""):
        await submit(interaction, art.value, beschreibung)

    @app_commands.command(name="datenschutz-status", description="Zeigt den Stand deiner eigenen Datenschutzanfragen (privat)")
    @app_commands.guild_only()
    async def datenschutz_status(self, interaction: discord.Interaction):
        await show_status(interaction)

    @tasks.loop(hours=6)
    async def overdue_loop(self):
        for guild in list(self.bot.guilds):
            try:
                admins = [member_id for member_id, _name in dsr.admin_members(guild)]
                await asyncio.to_thread(dsr.overdue_reminders, guild.id, admins)
            except (sqlite3.Error, OSError) as exc:
                print(f"⚠️ Datenschutz-Fristprüfung fehlgeschlagen: {type(exc).__name__}.")

    @overdue_loop.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(PrivacyRequests(bot))
