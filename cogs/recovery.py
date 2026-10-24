"""Discord commands for the explicit, opt-in backup-server recovery flow."""
from __future__ import annotations

import asyncio
import time

import discord
import httpx
from discord import app_commands
from discord.ext import commands

from pulse_recovery import TOKEN_TIMEOUT


class RecoveryCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._last_sync_by_user: dict[int, float] = {}

    @app_commands.command(
        name="recovery-sync",
        description="[Admin] Fügt freigegebene Nutzer dem Backup-Server hinzu – ausschließlich mit Bürgerrolle",
    )
    @app_commands.describe(
        confirm="Muss aktiv auf Ja gesetzt werden, damit ein Wiederherstellungs-Sync ausgeführt wird",
        max_users="Maximalzahl der Nutzer pro Lauf (1–100)",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def recovery_sync(
        self,
        interaction: discord.Interaction,
        confirm: bool = False,
        max_users: app_commands.Range[int, 1, 100] = 25,
    ):
        if interaction.guild is None:
            await interaction.response.send_message("Dieser Befehl funktioniert nur in einem Discord-Server.", ephemeral=True)
            return
        import pulse_recovery
        import webserver as ws

        settings = pulse_recovery.load_settings()
        allowed_guild_ids = {str(ws.GUILD_ID), settings["backup_guild_id"]}
        if str(interaction.guild.id) not in allowed_guild_ids:
            await interaction.response.send_message(
                "Dieser Befehl ist nur im Hauptserver oder im konfigurierten Backup-Server erlaubt.",
                ephemeral=True,
            )
            return
        if not confirm:
            await interaction.response.send_message(
                "⚠️ Es wurde nichts geändert. Prüfe Zielserver und Bürgerrolle. Starte danach erneut mit "
                "`confirm: Ja`. Der Befehl entfernt niemanden aus dem Hauptserver und vergibt keine Team- oder Moderationsrollen.",
                ephemeral=True,
            )
            return
        if not settings["enabled"]:
            await interaction.response.send_message("Die Backup-Wiederherstellung ist deaktiviert.", ephemeral=True)
            return
        now = time.monotonic()
        if now - self._last_sync_by_user.get(interaction.user.id, -60.0) < 60:
            await interaction.response.send_message("Bitte warte mindestens 60 Sekunden zwischen zwei Recovery-Syncs.", ephemeral=True)
            return
        self._last_sync_by_user[interaction.user.id] = now
        await interaction.response.defer(ephemeral=True, thinking=True)

        target, role, problems = pulse_recovery.target_status(self.bot, settings)
        if problems:
            await interaction.followup.send("❌ Backup-Server nicht einsatzbereit: " + " ".join(problems), ephemeral=True)
            return
        people = pulse_recovery.list_linked_users(int(max_users))
        if not people:
            await interaction.followup.send(
                "Es gibt keine gültigen, ausdrücklich freigegebenen Recovery-Konten. Nutzer müssen den Backup-Zugang selbst autorisieren.",
                ephemeral=True,
            )
            return

        added = 0
        already = 0
        failed = 0
        failures: list[str] = []
        async with httpx.AsyncClient(timeout=TOKEN_TIMEOUT) as client:
            for index, person in enumerate(people):
                ok, message = await pulse_recovery.rejoin_user(person["user_id"], person["refresh_token"], self.bot, client)
                if ok:
                    if message.startswith("bereits vorhanden"):
                        already += 1
                    else:
                        added += 1
                else:
                    failed += 1
                    if len(failures) < 5:
                        failures.append(f"`{person['user_id']}`: {message}")
                # The add-member endpoint has a per-guild bucket; pace batches to reduce 429s.
                if index + 1 < len(people):
                    await asyncio.sleep(1.0)

        try:
            ws.log_audit(
                getattr(interaction.user, "display_name", str(interaction.user.id)),
                str(interaction.user.id),
                "Backup-Recovery-Sync ausgeführt",
                f"Backup-Guild {target.id}; Bürgerrolle {role.id}; geprüft {len(people)}; neu {added}; vorhanden {already}; Fehler {failed}",
            )
        except Exception:
            pass
        lines = [
            "🛡️ **Recovery-Sync abgeschlossen**",
            f"Geprüft: {len(people)} · Neu hinzugefügt: {added} · Bereits vorhanden: {already} · Fehler: {failed}",
            f"Ziel: **{target.name}** · vergeben wird ausschließlich **{role.name}**.",
            "Es werden keine Mitglieder aus dem Hauptserver gekickt und keine bestehenden Rollen entfernt. Neue Mitglieder erhalten keine Rollen aus dem Hauptserver.",
        ]
        if failures:
            lines.append("Einige Fehler: " + " | ".join(failures))
        await interaction.followup.send("\n".join(lines), ephemeral=True)

    @app_commands.command(
        name="recovery-abmelden",
        description="Beendet deine freiwillige Pulse-Backup-Freigabe und löscht den gespeicherten Recovery-Zugang",
    )
    async def recovery_unlink(self, interaction: discord.Interaction):
        import pulse_recovery
        import webserver as ws
        uid = str(interaction.user.id)
        refresh_token = pulse_recovery.get_refresh_token(uid)
        if not refresh_token:
            await interaction.response.send_message("Für dein Konto ist keine aktive Recovery-Freigabe gespeichert.", ephemeral=True)
            return
        revoked = False
        try:
            async with httpx.AsyncClient(timeout=TOKEN_TIMEOUT) as client:
                response = await client.post(
                    "https://discord.com/api/v10/oauth2/token/revoke",
                    data={"client_id": ws.CLIENT_ID, "client_secret": ws.CLIENT_SECRET, "token": refresh_token,
                          "token_type_hint": "refresh_token"},
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                revoked = response.status_code in (200, 204)
        except httpx.HTTPError:
            pass
        pulse_recovery.unlink(uid)
        try:
            ws.log_audit(getattr(interaction.user, "display_name", uid), uid,
                         "Backup-Recovery-Freigabe widerrufen", "Gespeicherter OAuth-Recovery-Zugang gelöscht.")
        except Exception:
            pass
        status = "Discord-Freigabe widerrufen" if revoked else "lokaler Recovery-Zugang gelöscht"
        await interaction.response.send_message(
            f"✅ Deine Recovery-Freigabe wurde beendet ({status}). Pulse wird dich nicht mehr automatisch zum Backup-Server hinzufügen. "
            "Falls du den Backup-Server ebenfalls verlassen möchtest, kannst du ihn zusätzlich selbst verlassen.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(RecoveryCog(bot))
