"""Search, repeat and plainly explain older Discord announcements without an AI API."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import discord
from pulse_embed_design import color as _pulse_color
from discord import app_commands
from discord.ext import commands

from pulse_announcement_utils import (
    build_announcement_explanation,
    clean_announcement_text,
    score_announcement,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCAN_LIMIT = 2000
MAX_SCAN_LIMIT = 5000
MENTIONS_NONE = discord.AllowedMentions.none()


def split_discord_text(text: str, limit: int = 1800) -> list[str]:
    """Split long announcement text without silently discarding its content."""
    remaining = text or ""
    chunks: list[str] = []
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit + 1)
        if split_at < limit // 2:
            split_at = remaining.rfind(" ", 0, limit + 1)
        if split_at <= 0:
            split_at = limit
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:]
    if remaining:
        chunks.append(remaining)
    return chunks


class AnnouncementArchive(commands.Cog):
    """Reads a configured history channel only when a slash command is used."""

    ankuendigung = app_commands.Group(
        name="ankuendigung",
        description="Alte Server-Ankündigungen suchen, wiederholen und erklären.",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config_path = Path(
            os.getenv(
                "PULSE_ANNOUNCEMENT_ARCHIVE_CONFIG",
                str(PROJECT_ROOT / "pulse_announcement_archive.json"),
            )
        )

    def _read_config(self) -> dict[str, Any]:
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {"guilds": {}}
        if not isinstance(data, dict) or not isinstance(data.get("guilds"), dict):
            return {"guilds": {}}
        return data

    def _write_config(self, data: dict[str, Any]) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.config_path.with_suffix(self.config_path.suffix + ".tmp")
        temporary_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary_path, self.config_path)

    async def _source_channel(
        self, guild: discord.Guild | None
    ) -> tuple[discord.TextChannel | None, int, str | None]:
        if guild is None:
            return None, DEFAULT_SCAN_LIMIT, "Dieser Befehl kann nur auf einem Discord-Server verwendet werden."
        entry = self._read_config().get("guilds", {}).get(str(guild.id), {})
        if not isinstance(entry, dict) or not entry.get("channel_id"):
            return (
                None,
                DEFAULT_SCAN_LIMIT,
                "Die Ankündigungssuche ist noch nicht eingerichtet. Ein Administrator muss "
                "zuerst /ankuendigung setup mit eurem Ankündigungskanal ausführen.",
            )

        try:
            channel_id = int(entry["channel_id"])
            scan_limit = max(100, min(MAX_SCAN_LIMIT, int(entry.get("scan_limit", DEFAULT_SCAN_LIMIT))))
        except (TypeError, ValueError):
            return None, DEFAULT_SCAN_LIMIT, "Die gespeicherte Ankündigungskonfiguration ist ungültig. Bitte erneut einrichten."

        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            try:
                fetched = await self.bot.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                fetched = None
            channel = fetched if isinstance(fetched, discord.TextChannel) else None
        if channel is None or channel.guild.id != guild.id:
            return None, scan_limit, "Der gespeicherte Ankündigungskanal wurde nicht gefunden. Bitte /ankuendigung setup erneut ausführen."

        bot_member = guild.me
        if bot_member is None and self.bot.user is not None:
            bot_member = guild.get_member(self.bot.user.id)
        if bot_member is None:
            return None, scan_limit, "Pulse konnte seine Serverberechtigungen nicht prüfen."
        permissions = channel.permissions_for(bot_member)
        if not permissions.view_channel or not permissions.read_message_history:
            return (
                None,
                scan_limit,
                f"Pulse benötigt in {channel.mention} die Rechte „Kanal ansehen“ und „Nachrichtenverlauf anzeigen“.",
            )
        return channel, scan_limit, None

    @staticmethod
    def _message_material(message: discord.Message) -> str:
        parts: list[str] = []
        if message.content:
            parts.append(message.content)
        for embed in message.embeds:
            if embed.title:
                parts.append(embed.title)
            if embed.description:
                parts.append(embed.description)
            for field in embed.fields:
                parts.append(f"{field.name}: {field.value}")
        for attachment in message.attachments:
            parts.append(f"Anhang: {attachment.filename}")
        return "\n".join(part for part in parts if part).strip()

    @staticmethod
    def _title_for(message: discord.Message, material: str) -> str:
        for embed in message.embeds:
            if embed.title:
                return embed.title.strip()[:100]
        for line in material.splitlines():
            title = clean_announcement_text(line).strip()
            if title:
                return title.lstrip("# ").strip()[:100]
        return "Frühere Ankündigung"

    async def _search_messages(
        self, channel: discord.TextChannel, query: str, scan_limit: int
    ) -> list[tuple[int, discord.Message, str]]:
        matches: list[tuple[int, discord.Message, str]] = []
        async for message in channel.history(limit=scan_limit, oldest_first=False):
            material = self._message_material(message)
            if not material:
                continue
            score = score_announcement(query, material)
            if score:
                matches.append((score, message, material))
        matches.sort(key=lambda item: (item[0], item[1].created_at), reverse=True)
        return matches

    def _render_matches(
        self, matches: list[tuple[int, discord.Message, str]], limit: int = 4
    ) -> str:
        lines = ["🔎 **Gefundene ältere Ankündigungen**"]
        for index, (_score, message, material) in enumerate(matches[:limit], start=1):
            title = discord.utils.escape_markdown(self._title_for(message, material))
            title = discord.utils.escape_mentions(title)
            snippet = clean_announcement_text(material).replace("\n", " ")
            snippet = discord.utils.escape_mentions(snippet)
            if len(snippet) > 180:
                snippet = snippet[:177].rsplit(" ", 1)[0] + "…"
            timestamp = int(message.created_at.timestamp())
            lines.append(
                f"**{index}. {title}** · <t:{timestamp}:d>\n"
                f"🔗 [Original öffnen]({message.jump_url})\n"
                f"> {snippet or 'Kein Textvorschau verfügbar.'}"
            )
        text = "\n\n".join(lines)
        return text if len(text) <= 1900 else text[:1897] + "…"

    async def _get_results(
        self, interaction: discord.Interaction, query: str
    ) -> tuple[list[tuple[int, discord.Message, str]], str | None, int]:
        channel, scan_limit, error = await self._source_channel(interaction.guild)
        if error or channel is None:
            return [], error or "Der Ankündigungskanal ist nicht verfügbar.", scan_limit
        try:
            matches = await self._search_messages(channel, query, scan_limit)
        except discord.Forbidden:
            return [], f"Pulse darf den Verlauf in {channel.mention} nicht lesen.", scan_limit
        except discord.HTTPException:
            return [], "Discord konnte den Ankündigungsverlauf gerade nicht laden. Bitte versuche es später erneut.", scan_limit
        return matches, None, scan_limit

    @ankuendigung.command(
        name="setup",
        description="[Admin] Legt den Kanal für ältere Ankündigungen fest.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(
        kanal="Kanal, in dem ältere Ankündigungen veröffentlicht wurden",
        suchbereich="Wie viele jüngste Nachrichten durchsucht werden sollen (100–5000)",
    )
    async def configure_archive(
        self,
        interaction: discord.Interaction,
        kanal: discord.TextChannel,
        suchbereich: app_commands.Range[int, 100, MAX_SCAN_LIMIT] = DEFAULT_SCAN_LIMIT,
    ):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "Dieser Befehl kann nur auf einem Discord-Server verwendet werden.",
                ephemeral=True,
            )
            return
        bot_member = guild.me
        if bot_member is None and self.bot.user is not None:
            bot_member = guild.get_member(self.bot.user.id)
        if bot_member is None:
            await interaction.response.send_message(
                "Pulse konnte seine Kanalberechtigungen nicht prüfen.", ephemeral=True
            )
            return
        permissions = kanal.permissions_for(bot_member)
        if not permissions.view_channel or not permissions.read_message_history:
            await interaction.response.send_message(
                f"Pulse benötigt in {kanal.mention} die Rechte „Kanal ansehen“ und „Nachrichtenverlauf anzeigen“.",
                ephemeral=True,
            )
            return

        data = self._read_config()
        guilds = data.setdefault("guilds", {})
        guilds[str(guild.id)] = {
            "channel_id": kanal.id,
            "scan_limit": int(suchbereich),
        }
        try:
            self._write_config(data)
        except OSError as exc:
            print(f"⚠️ Pulse-Ankündigungsarchiv: Konfiguration konnte nicht gespeichert werden ({type(exc).__name__}).")
            await interaction.response.send_message(
                "Die Konfiguration konnte nicht gespeichert werden. Bitte prüfe, ob Pulse auf seinem Dateisystem schreiben darf.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"✅ Ankündigungsarchiv eingerichtet.\n"
            f"📢 Quellkanal: {kanal.mention}\n"
            f"🔎 Suchbereich: die letzten {int(suchbereich)} Nachrichten\n\n"
            "Mit /ankuendigung suchen, /ankuendigung erklaeren und "
            "/ankuendigung wiederholen könnt ihr das Archiv nutzen. "
            "Die Suche benötigt keine zusätzliche KI-API.",
            ephemeral=True,
        )

    @ankuendigung.command(
        name="suchen",
        description="Findet ältere Ankündigungen anhand eines Stichworts.",
    )
    @app_commands.describe(stichwort="Zum Beispiel Fahrzeugfarben, Autos, Tuning oder Regeln")
    async def search_announcement(self, interaction: discord.Interaction, stichwort: str):
        await interaction.response.defer(ephemeral=True, thinking=True)
        matches, error, scan_limit = await self._get_results(interaction, stichwort.strip())
        if error:
            await interaction.followup.send(error, ephemeral=True)
            return
        if not matches:
            await interaction.followup.send(
                f"Ich habe im festgelegten Suchbereich von {scan_limit} Nachrichten keine passende Ankündigung gefunden. "
                "Versuche ein anderes Stichwort oder lass den Suchbereich per /ankuendigung setup erhöhen.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            self._render_matches(matches), ephemeral=True, allowed_mentions=MENTIONS_NONE
        )

    @ankuendigung.command(
        name="erklaeren",
        description="Bereitet eine ältere Ankündigung in einfacher Sprache auf.",
    )
    @app_commands.describe(stichwort="Thema der Ankündigung, zum Beispiel Fahrzeugfarben")
    async def explain_announcement(self, interaction: discord.Interaction, stichwort: str):
        await interaction.response.defer(ephemeral=True, thinking=True)
        matches, error, _scan_limit = await self._get_results(interaction, stichwort.strip())
        if error:
            await interaction.followup.send(error, ephemeral=True)
            return
        if not matches:
            await interaction.followup.send(
                "Ich habe keine passende Ankündigung gefunden. Versuche ein anderes Stichwort oder wende dich an das Serverteam.",
                ephemeral=True,
            )
            return
        if len(matches) > 1 and matches[0][0] == matches[1][0]:
            await interaction.followup.send(
                "Es gibt mehrere gleich passende Ankündigungen. Bitte nutze ein genaueres Stichwort:\n\n"
                + self._render_matches(matches, limit=3),
                ephemeral=True,
                allowed_mentions=MENTIONS_NONE,
            )
            return

        _score, message, material = matches[0]
        title = self._title_for(message, material)
        explanation = build_announcement_explanation(title, material)
        embed = discord.Embed(
            title=f"🧾 Einfach erklärt: {title[:230]}",
            description=explanation[:4000],
            color=_pulse_color("info"),
        )
        embed.add_field(
            name="Original-Ankündigung",
            value=f"[Nachricht vom <t:{int(message.created_at.timestamp())}:f> öffnen]({message.jump_url})",
            inline=False,
        )
        embed.set_footer(text="Automatisch aufbereitet · keine neuen Serverregeln")
        await interaction.followup.send(embed=embed, ephemeral=True, allowed_mentions=MENTIONS_NONE)

    @ankuendigung.command(
        name="wiederholen",
        description="Veröffentlicht eine ältere Ankündigung erneut.",
    )
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.describe(
        stichwort="Thema der alten Ankündigung",
        zielkanal="Kanal, in dem die Wiederholung veröffentlicht werden soll (optional)",
    )
    async def repeat_announcement(
        self,
        interaction: discord.Interaction,
        stichwort: str,
        zielkanal: discord.TextChannel | None = None,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        matches, error, _scan_limit = await self._get_results(interaction, stichwort.strip())
        if error:
            await interaction.followup.send(error, ephemeral=True)
            return
        if not matches:
            await interaction.followup.send(
                "Ich habe keine passende Ankündigung gefunden. Versuche ein genaueres Stichwort.",
                ephemeral=True,
            )
            return
        if len(matches) > 1 and matches[0][0] == matches[1][0]:
            await interaction.followup.send(
                "Mehrere alte Ankündigungen passen gleich gut. Aus Sicherheitsgründen habe ich nichts erneut veröffentlicht.\n\n"
                + self._render_matches(matches, limit=3)
                + "\n\nBitte verwende ein genaueres Stichwort.",
                ephemeral=True,
                allowed_mentions=MENTIONS_NONE,
            )
            return

        _score, original, material = matches[0]
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("Dieser Befehl funktioniert nur auf einem Server.", ephemeral=True)
            return
        target = zielkanal or interaction.channel
        if not isinstance(target, discord.TextChannel) or target.guild.id != guild.id:
            await interaction.followup.send("Bitte wähle einen Textkanal auf diesem Server.", ephemeral=True)
            return

        bot_member = guild.me
        if bot_member is None and self.bot.user is not None:
            bot_member = guild.get_member(self.bot.user.id)
        if bot_member is None:
            await interaction.followup.send("Pulse konnte seine Kanalberechtigungen nicht prüfen.", ephemeral=True)
            return
        permissions = target.permissions_for(bot_member)
        if not permissions.view_channel or not permissions.send_messages:
            await interaction.followup.send(
                f"Pulse benötigt in {target.mention} die Rechte „Kanal ansehen“ und „Nachrichten senden“.",
                ephemeral=True,
            )
            return

        title = self._title_for(original, material)
        timestamp = int(original.created_at.timestamp())
        header = (
            f"🔁 **Wiederholung einer früheren Ankündigung**\n"
            f"📌 **{discord.utils.escape_mentions(title)}**\n"
            f"📅 Ursprünglich veröffentlicht: <t:{timestamp}:f> · "
            f"[Original ansehen]({original.jump_url})"
        )
        body = original.content or ""
        if original.attachments:
            attachment_links = "\n".join(
                f"📎 [{attachment.filename}]({attachment.url})"
                for attachment in original.attachments
            )
            body = f"{body}\n\n{attachment_links}".strip()

        copied_embeds = [discord.Embed.from_dict(embed.to_dict()) for embed in original.embeds]
        if copied_embeds and not permissions.embed_links:
            body = self._message_material(original)
            if original.attachments:
                body += "\n\n" + "\n".join(
                    f"Anhang: {attachment.url}" for attachment in original.attachments
                )
            copied_embeds = []

        chunks = split_discord_text(discord.utils.escape_mentions(body))
        try:
            if not chunks and copied_embeds:
                await target.send(
                    content=header,
                    embeds=copied_embeds,
                    allowed_mentions=MENTIONS_NONE,
                )
            else:
                await target.send(content=header, allowed_mentions=MENTIONS_NONE)
                for index, chunk in enumerate(chunks):
                    await target.send(
                        content=chunk,
                        embeds=copied_embeds if index == 0 and copied_embeds else [],
                        allowed_mentions=MENTIONS_NONE,
                    )
                if not chunks and not copied_embeds:
                    await target.send(
                        content=f"Der Originaltext war leer. [Original-Ankündigung öffnen]({original.jump_url})",
                        allowed_mentions=MENTIONS_NONE,
                    )
        except discord.Forbidden:
            await interaction.followup.send(
                f"Discord hat das Veröffentlichen in {target.mention} verweigert. Prüfe die Kanalrechte.",
                ephemeral=True,
            )
            return
        except discord.HTTPException as exc:
            print(f"⚠️ Pulse-Ankündigungsarchiv: Wiederholung fehlgeschlagen ({type(exc).__name__}).")
            await interaction.followup.send(
                "Die Wiederholung konnte nicht vollständig veröffentlicht werden. Bitte prüfe den Zielkanal.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"✅ Die frühere Ankündigung **{discord.utils.escape_mentions(title)}** wurde in {target.mention} erneut veröffentlicht. "
            "Die Nachricht ist deutlich als Wiederholung gekennzeichnet; Erwähnungen werden nicht ausgelöst.",
            ephemeral=True,
            allowed_mentions=MENTIONS_NONE,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(AnnouncementArchive(bot))
