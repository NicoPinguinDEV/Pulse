import discord
from discord.ext import commands

class AdvancedLogs(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.log_channel_id = 0  # Trage hier deine Log-Channel-ID ein

    async def get_log_channel(self, guild):
        return guild.get_channel(self.log_channel_id)

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        if before.author.bot or before.content == after.content:
            return
        channel = await self.get_log_channel(before.guild)
        if channel:
            embed = discord.Embed(title="Nachricht bearbeitet", color=discord.Color.orange(), timestamp=discord.utils.utcnow())
            embed.add_field(name="Autor", value=before.author.mention, inline=False)
            embed.add_field(name="Vorher", value=before.content[:1024] or "Kein Text", inline=False)
            embed.add_field(name="Nachher", value=after.content[:1024] or "Kein Text", inline=False)
            await channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        channel = await self.get_log_channel(member.guild)
        if not channel:
            return
        
        if before.channel is None and after.channel is not None:
            embed = discord.Embed(title=" Sprachkanal betreten", description=f"{member.mention} hat **{after.channel.name}** betreten.", color=discord.Color.green(), timestamp=discord.utils.utcnow())
            await channel.send(embed=embed)
        elif before.channel is not None and after.channel is None:
            embed = discord.Embed(title=" Sprachkanal verlassen", description=f"{member.mention} hat **{before.channel.name}** verlassen.", color=discord.Color.red(), timestamp=discord.utils.utcnow())
            await channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if before.roles != after.roles:
            channel = await self.get_log_channel(before.guild)
            if channel:
                added = [r.mention for r in after.roles if r not in before.roles]
                removed = [r.mention for r in before.roles if r not in after.roles]
                embed = discord.Embed(title="Rollen geändert", color=discord.Color.blue(), timestamp=discord.utils.utcnow())
                embed.add_field(name="Mitglied", value=after.mention, inline=False)
                if added:
                    embed.add_field(name="Hinzugefügt", value=", ".join(added), inline=False)
                if removed:
                    embed.add_field(name="Entfernt", value=", ".join(removed), inline=False)
                await channel.send(embed=embed)

async def setup(bot):
    await bot.add_cog(AdvancedLogs(bot))
