import discord
from discord import app_commands
from discord.ext import commands

# --- FORMULAR FÜR TEAM-ABMELDUNG ---
class AbmeldungModal(discord.ui.Modal, title="Team-Abmeldung einreichen"):
    von_datum = discord.ui.TextInput(
        label="Abgemeldet von (Datum)",
        placeholder="z.B. 22.09.2026",
        required=True,
        max_length=30
    )
    bis_datum = discord.ui.TextInput(
        label="Abgemeldet bis (Datum)",
        placeholder="z.B. 30.09.2026",
        required=True,
        max_length=30
    )
    grund = discord.ui.TextInput(
        label="Grund für die Abmeldung",
        placeholder="z.B. Urlaub, Schule, Krank...",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500
    )
    vertretung = discord.ui.TextInput(
        label="Vertretung (Optional)",
        placeholder="Wer übernimmt deine Aufgaben?",
        required=False,
        max_length=100
    )

    def __init__(self, target_channel: discord.TextChannel):
        super().__init__()
        self.target_channel = target_channel

    async def on_submit(self, interaction: discord.Interaction):
        user = interaction.user

        embed = discord.Embed(
            title="📋 Neue Team-Abmeldung",
            color=discord.Color.orange(),
            timestamp=interaction.created_at
        )
        embed.set_author(name=f"{user.display_name} ({user.name})", icon_url=user.display_avatar.url)
        embed.add_field(name="👤 Teammitglied", value=user.mention, inline=True)
        embed.add_field(
            name="📅 Zeitraum", 
            value=f"**Von:** {self.von_datum.value}\n**Bis:** {self.bis_datum.value}", 
            inline=False
        )
        embed.add_field(name="📝 Grund", value=self.grund.value, inline=False)

        if self.vertretung.value:
            embed.add_field(name="🔄 Vertretung", value=self.vertretung.value, inline=False)

        embed.add_field(name="📌 Status", value="⏳ **Ausstehend / In Bearbeitung**", inline=False)
        embed.set_footer(text=f"User-ID: {user.id}")

        await self.target_channel.send(embed=embed, view=AbmeldungAdminView())
        await interaction.response.send_message(
            f"✅ Deine Abmeldung wurde eingereicht und in {self.target_channel.mention} gepostet!", 
            ephemeral=True
        )


# --- ADMIN BUTTONS ZUR GENEHMIGUNG ---
class AbmeldungAdminView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Genehmigen", 
        style=discord.ButtonStyle.success, 
        emoji="✅", 
        custom_id="abm_approve_btn"
    )
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.manage_messages and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Nur Teamleiter/Admins können dies tun.", ephemeral=True)
            return

        embed = interaction.message.embeds[0]
        embed.color = discord.Color.green()

        for i, field in enumerate(embed.fields):
            if field.name == "📌 Status":
                embed.set_field_at(
                    i, 
                    name="📌 Status", 
                    value=f"✅ **Genehmigt / Zur Kenntnis genommen** (von {interaction.user.mention})", 
                    inline=False
                )
                break

        await interaction.message.edit(embed=embed)
        await interaction.response.send_message("✅ Abmeldung als genehmigt markiert.", ephemeral=True)

    @discord.ui.button(
        label="Ablehnen", 
        style=discord.ButtonStyle.danger, 
        emoji="❌", 
        custom_id="abm_deny_btn"
    )
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.manage_messages and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Nur Teamleiter/Admins können dies tun.", ephemeral=True)
            return

        embed = interaction.message.embeds[0]
        embed.color = discord.Color.red()

        for i, field in enumerate(embed.fields):
            if field.name == "📌 Status":
                embed.set_field_at(
                    i, 
                    name="📌 Status", 
                    value=f"❌ **Abgelehnt** (von {interaction.user.mention})", 
                    inline=False
                )
                break

        await interaction.message.edit(embed=embed)
        await interaction.response.send_message("❌ Abmeldung als abgelehnt markiert.", ephemeral=True)


# --- MAIN COG ---
class Abmeldung(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.target_channel_id = None

    async def cog_load(self):
        self.bot.add_view(AbmeldungAdminView())

    @app_commands.command(name="abmelden", description="Reiche eine Team-Abmeldung ein")
    async def abmelden(self, interaction: discord.Interaction):
        target_channel = None

        if self.target_channel_id:
            target_channel = interaction.guild.get_channel(self.target_channel_id)

        if not target_channel:
            for channel in interaction.guild.text_channels:
                if "abmeldung" in channel.name.lower():
                    target_channel = channel
                    break

        if not target_channel:
            target_channel = interaction.channel

        modal = AbmeldungModal(target_channel=target_channel)
        await interaction.response.send_modal(modal)

    @app_commands.command(name="set-abmeldungs-channel", description="Legt den festen Kanal für Team-Abmeldungen fest")
    @app_commands.default_permissions(administrator=True)
    async def set_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        self.target_channel_id = channel.id
        await interaction.response.send_message(
            f"✅ Abmeldungskanal wurde auf {channel.mention} gesetzt!", 
            ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Abmeldung(bot))
