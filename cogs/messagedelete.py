"""Administrator message cleanup with bounded, confirmed deletion."""
from __future__ import annotations

import asyncio
from datetime import timedelta

import discord
from pulse_interactions import PulseView  # BOT-01: error IDs, double-click guard, timeout
from discord import app_commands
from discord.ext import commands


class MessageDeleteConfirmationView(PulseView):
    def __init__(
        self,
        cog: "MessageDeleteCog",
        requester_id: int,
        amount: int,
        include_older_than_14_days: bool,
    ):
        super().__init__(timeout=60)
        self.cog = cog
        self.requester_id = requester_id
        self.amount = amount
        self.include_older_than_14_days = include_older_than_14_days
        self._confirmed = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "❌ Nur die Person, die den Löschvorgang gestartet hat, kann ihn bestätigen.",
                ephemeral=True,
            )
            return False
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "❌ Für diese Aktion ist Administrator erforderlich.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(
        label="Löschen bestätigen",
        style=discord.ButtonStyle.danger,
        custom_id="pulse_messagedelete_confirm",
    )
    async def confirm(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        # Doppelklick darf nicht zweimal löschen: Flag vor dem ersten await setzen.
        if self._confirmed:
            await interaction.response.defer()
            return
        self._confirmed = True
        self.stop()
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        await interaction.response.edit_message(
            content="🧹 Löschvorgang läuft. Ältere Nachrichten werden bewusst langsam verarbeitet, um Discord-Ratelimits zu vermeiden.",
            embed=None,
            view=self,
        )
        try:
            deleted, scope = await self.cog.delete_channel_messages(
                interaction.channel,
                self.amount,
                self.include_older_than_14_days,
            )
            await interaction.followup.send(
                f"🧹 **{deleted}** Nachricht(en) gelöscht.\nBereich: {scope}",
                ephemeral=True,
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ Discord hat das Löschen verweigert. Prüfe Nachrichten verwalten und Nachrichtenverlauf anzeigen.",
                ephemeral=True,
            )
        except discord.HTTPException as exc:
            await interaction.followup.send(
                f"⚠️ Discord hat den Löschvorgang wegen eines API-/Ratelimit-Fehlers unterbrochen ({type(exc).__name__}). Bereits gelöschte Nachrichten bleiben gelöscht.",
                ephemeral=True,
            )
        except Exception as exc:
            await interaction.followup.send(
                f"❌ Der Löschvorgang wurde abgebrochen ({type(exc).__name__}). Bereits gelöschte Nachrichten bleiben gelöscht.",
                ephemeral=True,
            )

    @discord.ui.button(
        label="Abbrechen",
        style=discord.ButtonStyle.secondary,
        custom_id="pulse_messagedelete_cancel",
    )
    async def cancel(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        await interaction.response.edit_message(
            content="✅ Löschvorgang abgebrochen. Es wurden keine Nachrichten gelöscht.",
            embed=None,
            view=self,
        )


class MessageDeleteCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def delete_channel_messages(
        self,
        channel: discord.abc.Messageable,
        amount: int,
        include_older_than_14_days: bool,
    ) -> tuple[int, str]:
        cutoff = discord.utils.utcnow() - timedelta(days=14)
        reason = "Manuell bestätigter /messagedelete-Befehl durch Administrator"

        if include_older_than_14_days:
            # Discord cannot bulk-delete messages older than 14 days. Keep old
            # cleanup explicitly opt-in and capped at 100 per invocation, with
            # conservative pacing instead of the previous unbounded delete storm.
            if amount > 100:
                raise ValueError("Ältere Nachrichten sind auf 100 pro Ausführung begrenzt.")
            messages = [
                message
                async for message in channel.history(
                    limit=amount,
                    before=cutoff,
                    oldest_first=False,
                )
            ]
            deleted = 0
            for index, message in enumerate(messages):
                try:
                    await message.delete(reason=reason)
                    deleted += 1
                except discord.NotFound:
                    continue
                if index < len(messages) - 1:
                    await asyncio.sleep(1.1)
            return deleted, "Nachrichten älter als 14 Tage (Einzellöschung mit Abstand)"

        # Recent messages can be deleted in bulk by Discord; no traversal into
        # old history means no hundreds of individual DELETE requests.
        deleted_messages = await channel.purge(
            limit=amount,
            after=cutoff,
            bulk=True,
            reason=reason,
        )
        return len(deleted_messages), "Nachrichten der letzten 14 Tage"

    @app_commands.command(
        name="messagedelete",
        description="[Admin] Löscht nach Bestätigung eine begrenzte Nachrichtenanzahl",
    )
    @app_commands.describe(
        amount="Wie viele Nachrichten maximal löschen? (1–1000; Standard: 100)",
        include_older_than_14_days="Auch ältere Nachrichten einzeln und langsam löschen (maximal 100)",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def messagedelete(
        self,
        interaction: discord.Interaction,
        amount: app_commands.Range[int, 1, 1000] = 100,
        include_older_than_14_days: bool = False,
    ):
        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message(
                "❌ Dieser Befehl funktioniert nur in einem Server-Textkanal.",
                ephemeral=True,
            )
            return
        if not hasattr(interaction.channel, "purge") or not hasattr(interaction.channel, "history"):
            await interaction.response.send_message(
                "❌ Dieser Kanal unterstützt die Nachrichtenbereinigung nicht.",
                ephemeral=True,
            )
            return
        if include_older_than_14_days and amount > 100:
            await interaction.response.send_message(
                "⚠️ Alte Nachrichten können nur einzeln gelöscht werden. Begrenze amount bei aktivierter Option auf höchstens 100, damit Pulse nicht hunderte Löschanfragen auf einmal erzeugt.",
                ephemeral=True,
            )
            return

        scope = (
            "Nachrichten älter als 14 Tage (max. 100, langsam)"
            if include_older_than_14_days
            else "Nachrichten der letzten 14 Tage (Discord-Massenlöschung)"
        )
        view = MessageDeleteConfirmationView(
            self,
            interaction.user.id,
            int(amount),
            include_older_than_14_days,
        )
        await interaction.response.send_message(
            f"⚠️ **Löschung bestätigen**\nKanal: {interaction.channel.mention}\n"
            f"Maximal: **{int(amount)}** Nachricht(en)\nBereich: **{scope}**\n\n"
            "Der Vorgang startet erst, wenn du auf **Löschen bestätigen** klickst.",
            view=view,
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(MessageDeleteCog(bot))
