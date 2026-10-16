"""Discord commands for the canonical Pulse backup/restore system.

The dashboard/webserver owns the backup format and retention policy. Keeping a
second, database-only backup loop here caused duplicate snapshots and lost the
backup channel setting on every bot restart.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

DISCORD_UPLOAD_SOFT_LIMIT = 7_500_000  # leave headroom for Discord's upload cap


async def _backup_file(guild: discord.Guild, prefix: str = "") -> str:
    import webserver

    filename = await webserver.create_backup_file_async(guild, prefix)
    return os.path.join(webserver.BACKUP_DIR, filename)


class BackupCog(commands.Cog):
    """Admin commands backed by the same complete snapshot used by the dashboard."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="setup_backup",
        description="[Admin] Speichert den Discord-Kanal für Backup-Kopien dauerhaft",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_backup(self, interaction: discord.Interaction, kanal: discord.TextChannel):
        if interaction.guild is None:
            await interaction.response.send_message("Dieser Befehl funktioniert nur auf dem Server.", ephemeral=True)
            return

        try:
            import webserver

            config = webserver.load_config()
            config["backup_discord_channel_id"] = int(kanal.id)
            webserver.save_json(webserver.CONFIG_FILE, config)
            webserver.log_audit(
                interaction.user.display_name,
                str(interaction.user.id),
                "Backup-Kanal gesetzt",
                f"Discord-Kanal-ID: {kanal.id}",
            )
        except Exception as exc:
            print(f"⚠️ Backup-Kanal konnte nicht gespeichert werden ({type(exc).__name__}).")
            await interaction.response.send_message(
                "❌ Der Backup-Kanal konnte nicht gespeichert werden. Bitte Serverlogs prüfen.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"✅ Backup-Kanal dauerhaft gespeichert: {kanal.mention}. "
            "Automatische Sicherungen werden dort veröffentlicht, sofern die Datei klein genug ist.",
            ephemeral=True,
        )

    @app_commands.command(
        name="backup_erstellen",
        description="[Admin] Erstellt ein vollständiges Discord-Server- und Pulse-Backup",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def manual_backup(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("Dieser Befehl funktioniert nur auf dem Server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            import webserver

            path = await _backup_file(interaction.guild)
            size = os.path.getsize(path)
            embed = discord.Embed(
                title="💾 Discord-Server + Pulse-Backup erstellt",
                description=(
                    "Serverstruktur, Rollen/Kanalrechte und Pulse-Daten wurden gesichert. "
                    "Die JSON-Datei liegt außerdem im Dashboard unter /backups."
                ),
                color=discord.Color.green(),
                timestamp=datetime.now(timezone.utc),
            )
            embed.add_field(name="Datei", value=os.path.basename(path), inline=False)
            embed.add_field(name="Größe", value=f"{size / (1024 * 1024):.2f} MiB", inline=True)
            if size <= DISCORD_UPLOAD_SOFT_LIMIT:
                await interaction.followup.send(
                    embed=embed,
                    file=discord.File(path, filename=os.path.basename(path)),
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            else:
                embed.color = discord.Color.orange()
                embed.add_field(
                    name="Discord-Anhang",
                    value="Die Datei ist für einen sicheren Upload zu groß; sie bleibt lokal gespeichert.",
                    inline=False,
                )
                await interaction.followup.send(
                    embed=embed,
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
        except Exception as exc:
            print(f"⚠️ Manuelles Pulse-Backup fehlgeschlagen ({type(exc).__name__}).")
            await interaction.followup.send(
                "❌ Das Backup ist fehlgeschlagen. Bitte Serverlogs prüfen; vorhandene Backups wurden nicht gelöscht.",
                ephemeral=True,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(BackupCog(bot))
