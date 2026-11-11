"""Two-person approval and scheduled publication for team announcements."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

import pulse_db


LOCAL_ZONE = ZoneInfo("Europe/Berlin")
MIN_LEAD = timedelta(minutes=10)
MAX_HORIZON = timedelta(days=90)
STATUS_LABELS = {
    "draft": "Entwurf · wartet auf Freigabe",
    "approved": "Freigegeben · wartet auf Veröffentlichung",
    "publishing": "Wird veröffentlicht · bitte Ergebnis prüfen",
    "published": "Veröffentlicht",
    "failed": "Fehlgeschlagen · manuelle Prüfung nötig",
    "cancelled": "Abgebrochen",
}
KIND_LABELS = {"info": "Info", "warning": "Wichtig", "urgent": "Dringend"}


def parse_scheduled_at(value: str, now: datetime | None = None) -> datetime:
    """Parse a Europe/Berlin wall-clock time and return an aware UTC instant."""
    try:
        naive = datetime.strptime(str(value or "").strip(), "%Y-%m-%d %H:%M")
    except ValueError as exc:
        raise ValueError("Bitte Datum und Uhrzeit als YYYY-MM-DD HH:MM eingeben (Berliner Zeit).") from exc
    local = naive.replace(tzinfo=LOCAL_ZONE)
    # Reject nonexistent local times during the spring DST jump instead of silently shifting them.
    roundtrip = local.astimezone(timezone.utc).astimezone(LOCAL_ZONE).replace(tzinfo=None)
    if roundtrip != naive:
        raise ValueError("Diese Uhrzeit existiert wegen der Zeitumstellung nicht. Bitte eine andere Uhrzeit wählen.")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    scheduled = local.astimezone(timezone.utc)
    if scheduled < current + MIN_LEAD:
        raise ValueError("Die Veröffentlichung muss mindestens 10 Minuten in der Zukunft liegen.")
    if scheduled > current + MAX_HORIZON:
        raise ValueError("Ankündigungen können höchstens 90 Tage im Voraus geplant werden.")
    return scheduled


class ScheduledAnnouncements(commands.Cog):
    """Creates drafts, requires a second-person approval and publishes at most once."""

    teamankuendigung = app_commands.Group(
        name="teamankuendigung",
        description="Team-Ankündigungen vorbereiten, freigeben und zeitgesteuert veröffentlichen.",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.delivery_loop.start()

    def cog_unload(self):
        self.delivery_loop.cancel()

    @staticmethod
    def _authorized(interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            return False
        try:
            import webserver
            config = webserver.load_config()
            permissions, member = webserver.compute_perms(
                interaction.guild, interaction.user.id, config
            )
            return bool(
                member
                and (
                    permissions.get("can_manage_announcements")
                    or permissions.get("can_promote")
                    or permissions.get("is_admin")
                )
            )
        except Exception:
            return False

    async def _require_authorized(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Dieser Befehl ist nur auf einem Discord-Server verfügbar.", ephemeral=True
            )
            return False
        if not self._authorized(interaction):
            await interaction.response.send_message(
                "❌ Dafür fehlt dir die Berechtigung zum Verwalten von Team-Ankündigungen.",
                ephemeral=True,
            )
            return False
        return True

    @teamankuendigung.command(
        name="planen",
        description="Erstellt einen Ankündigungsentwurf zur Freigabe und späteren Veröffentlichung.",
    )
    @app_commands.describe(
        titel="Titel der Ankündigung (max. 160 Zeichen)",
        nachricht="Vollständiger Ankündigungstext (max. 4.000 Zeichen)",
        datum_zeit="Veröffentlichung in Berliner Zeit: YYYY-MM-DD HH:MM",
        prioritaet="Darstellung im Team-Updates-Kanal",
    )
    @app_commands.choices(prioritaet=[
        app_commands.Choice(name="Info", value="info"),
        app_commands.Choice(name="Wichtig", value="warning"),
        app_commands.Choice(name="Dringend", value="urgent"),
    ])
    async def planen(
        self,
        interaction: discord.Interaction,
        titel: str,
        nachricht: str,
        datum_zeit: str,
        prioritaet: app_commands.Choice[str] | None = None,
    ):
        if not await self._require_authorized(interaction):
            return
        title = " ".join(str(titel or "").split())[:160]
        body = str(nachricht or "").strip()[:4000]
        if not title or not body:
            await interaction.response.send_message(
                "Titel und Nachricht dürfen nicht leer sein.", ephemeral=True
            )
            return
        try:
            scheduled = parse_scheduled_at(datum_zeit)
            row = await asyncio.to_thread(
                pulse_db.create_scheduled_announcement,
                interaction.guild.id,
                title,
                body,
                interaction.user.id,
                interaction.user.display_name,
                prioritaet.value if prioritaet else "info",
                scheduled.isoformat(timespec="seconds"),
            )
        except ValueError as exc:
            await interaction.response.send_message("❌ " + str(exc), ephemeral=True)
            return
        local_time = scheduled.astimezone(LOCAL_ZONE).strftime("%d.%m.%Y um %H:%M Uhr")
        await interaction.response.send_message(
            f"📝 Entwurf **{row['id']}** gespeichert.\n"
            f"Geplant: **{local_time}** (Europe/Berlin) · "
            f"Priorität: **{KIND_LABELS[row['kind']]}**.\n"
            "Die Ankündigung wird erst nach Freigabe durch eine andere berechtigte Person "
            "zum geplanten Zeitpunkt veröffentlicht. Nutze den Befehl /teamankuendigung freigeben.",
            ephemeral=True,
        )

    @teamankuendigung.command(
        name="freigeben",
        description="Gibt einen Ankündigungsentwurf frei (Vier-Augen-Prinzip).",
    )
    @app_commands.describe(entwurf_id="ID aus /teamankuendigung planen")
    async def freigeben(self, interaction: discord.Interaction, entwurf_id: str):
        if not await self._require_authorized(interaction):
            return
        try:
            row = await asyncio.to_thread(
                pulse_db.approve_scheduled_announcement,
                entwurf_id,
                interaction.guild.id,
                interaction.user.id,
                interaction.user.display_name,
            )
        except ValueError as exc:
            await interaction.response.send_message("❌ " + str(exc), ephemeral=True)
            return
        import webserver
        try:
            webserver.log_audit(
                interaction.user.display_name,
                str(interaction.user.id),
                "Team-Ankündigung freigegeben",
                f"{row['id']} · Veröffentlichungszeit {row['scheduled_at']}",
            )
        except Exception:
            pass
        local_time = datetime.fromisoformat(row["scheduled_at"]).astimezone(LOCAL_ZONE)
        await interaction.response.send_message(
            f"✅ Entwurf **{row['id']}** freigegeben. Pulse veröffentlicht ihn am "
            f"**{local_time:%d.%m.%Y um %H:%M}** (Europe/Berlin).",
            ephemeral=True,
        )

    @teamankuendigung.command(
        name="liste",
        description="Zeigt geplante, offene und zuletzt veröffentlichte Ankündigungen.",
    )
    async def liste(self, interaction: discord.Interaction):
        if not await self._require_authorized(interaction):
            return
        rows = await asyncio.to_thread(
            pulse_db.list_scheduled_announcements, interaction.guild.id, 15
        )
        if not rows:
            await interaction.response.send_message(
                "Es gibt aktuell keine geplanten Team-Ankündigungen.", ephemeral=True
            )
            return
        lines = []
        for row in rows:
            scheduled = datetime.fromisoformat(row["scheduled_at"]).astimezone(LOCAL_ZONE)
            lines.append(
                f"**{row['id']}** · {discord.utils.escape_markdown(row['title'])}\n"
                f"{STATUS_LABELS.get(row['status'], row['status'])} · "
                f"{scheduled:%d.%m.%Y %H:%M} Uhr · "
                f"{KIND_LABELS.get(row['kind'], 'Info')}"
            )
            if row.get("last_error"):
                lines.append(f"  Hinweis: {discord.utils.escape_markdown(row['last_error'][:180])}")
        await interaction.response.send_message(
            "📢 **Team-Ankündigungsplaner**\n" + "\n\n".join(lines),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @teamankuendigung.command(
        name="abbrechen",
        description="Bricht einen noch nicht veröffentlichten Entwurf oder Termin ab.",
    )
    @app_commands.describe(entwurf_id="ID aus der Ankündigungsliste")
    async def abbrechen(self, interaction: discord.Interaction, entwurf_id: str):
        if not await self._require_authorized(interaction):
            return
        cancelled = await asyncio.to_thread(
            pulse_db.cancel_scheduled_announcement, entwurf_id, interaction.guild.id
        )
        if not cancelled:
            await interaction.response.send_message(
                "❌ Ankündigung nicht gefunden oder nicht mehr abbrechbar. "
                "Bereits veröffentlichte oder gerade veröffentlichende Einträge bleiben unverändert.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"✅ Ankündigung **{entwurf_id}** wurde abgebrochen.", ephemeral=True
        )

    @tasks.loop(seconds=30)
    async def delivery_loop(self):
        try:
            due = await asyncio.to_thread(pulse_db.claim_due_scheduled_announcements, None, 20)
        except Exception as exc:
            print(f"⚠️ Team-Ankündigungsplaner: Fällige Einträge konnten nicht geladen werden ({type(exc).__name__}).")
            return
        for row in due:
            try:
                guild = self.bot.get_guild(int(row["guild_id"]))
                if guild is None:
                    await asyncio.to_thread(
                        pulse_db.fail_scheduled_announcement, row["id"], "Discord-Server ist für Pulse nicht verfügbar."
                    )
                    continue
                import webserver
                color = (
                    discord.Color.red() if row["kind"] == "urgent"
                    else discord.Color.orange() if row["kind"] == "warning"
                    else discord.Color.blurple()
                )
                author = f"{row['author_name']} · freigegeben von {row.get('approved_by_name') or 'Teamleitung'}"
                sent = await webserver.send_team_update_embed(
                    guild,
                    row["title"],
                    row["content"],
                    color,
                    actor=author,
                    action=f"Geplante Ankündigung {row['id']}",
                )
                if sent is None:
                    await asyncio.to_thread(
                        pulse_db.fail_scheduled_announcement,
                        row["id"],
                        "Team-Updates-Kanal nicht erreichbar oder Discord hat die Veröffentlichung abgelehnt.",
                    )
                    continue
                history_id = await asyncio.to_thread(
                    pulse_db.complete_scheduled_announcement, row["id"], sent.id
                )
                if not history_id:
                    # Keep the publishing lock. An uncertain result must never be resent automatically.
                    print(f"⚠️ Geplante Ankündigung {row['id']} wurde gesendet, aber nicht finalisiert. Bitte manuell prüfen.")
                    continue
                config = webserver.load_config()
                team_role_ids = set()
                for raw_role_id in config.get("team_role_ids", []):
                    try:
                        team_role_ids.add(int(raw_role_id))
                    except (TypeError, ValueError):
                        continue
                notify_kind = "warning" if row["kind"] in {"warning", "urgent"} else "info"
                for member in list(getattr(guild, "members", []) or [])[:5000]:
                    if member.bot or not any(role.id in team_role_ids for role in member.roles):
                        continue
                    try:
                        await asyncio.to_thread(
                            pulse_db.notify,
                            member.id,
                            f"📢 {row['title']}",
                            row["content"],
                            notify_kind,
                            "/announcements",
                            f"announcement:{history_id}",
                            86400,
                        )
                    except Exception as exc:
                        print(f"⚠️ Ankündigungs-Inbox für Mitglied {member.id} fehlgeschlagen ({type(exc).__name__}).")
                try:
                    webserver.log_audit(
                        "Pulse",
                        "system",
                        "Geplante Team-Ankündigung veröffentlicht",
                        f"{row['id']} · Discord-Nachricht {sent.id}",
                    )
                except Exception:
                    pass
            except Exception as exc:
                # Do not automatically retry after an ambiguous network/Discord result.
                await asyncio.to_thread(
                    pulse_db.fail_scheduled_announcement,
                    row["id"],
                    f"Veröffentlichung konnte nicht sicher bestätigt werden ({type(exc).__name__}). Manuell prüfen.",
                )
                print(f"⚠️ Geplante Ankündigung {row['id']}: {type(exc).__name__}")

    @delivery_loop.before_loop
    async def before_delivery_loop(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(ScheduledAnnouncements(bot))
