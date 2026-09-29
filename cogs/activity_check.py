import sqlite3
from datetime import datetime, time
import zoneinfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

DB_NAME = "activity_check.db"
BERLIN_TZ = zoneinfo.ZoneInfo("Europe/Berlin")


def init_db():
    conn=sqlite3.connect(DB_NAME)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE IF NOT EXISTS checks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL,
        channel_id INTEGER NOT NULL,
        message_id INTEGER,
        check_date TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(guild_id, check_date)
    );
    CREATE TABLE IF NOT EXISTS responses (
        check_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        reacted_at TEXT NOT NULL,
        PRIMARY KEY(check_id,user_id)
    );
    """)
    conn.commit(); conn.close()


def set_config(key,value):
    with sqlite3.connect(DB_NAME) as c: c.execute("INSERT OR REPLACE INTO config(key,value) VALUES(?,?)",(key,str(value)))


def get_config(key):
    with sqlite3.connect(DB_NAME) as c:
        r=c.execute("SELECT value FROM config WHERE key=?",(key,)).fetchone(); return r[0] if r else None


class ActivityView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None); self.cog=cog

    @discord.ui.button(label="Aktivität bestätigen",style=discord.ButtonStyle.success,emoji="✅",custom_id="pulse_activity_confirm")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        check_id=self.cog.current_check_id(interaction.message.id)
        if not check_id:
            await interaction.response.send_message("⚠️ Dieser Activity Check ist nicht mehr aktiv.",ephemeral=True); return
        with sqlite3.connect(DB_NAME) as c:
            c.execute("INSERT OR REPLACE INTO responses(check_id,user_id,reacted_at) VALUES(?,?,?)",(check_id,interaction.user.id,datetime.now(BERLIN_TZ).isoformat()))
        await interaction.response.send_message("✅ Aktivität bestätigt.",ephemeral=True)


class ActivityCheckCog(commands.Cog):
    def __init__(self, bot):
        self.bot=bot; init_db(); self.daily_activity_check.start(); self.bot.add_view(ActivityView(self))

    def cog_unload(self): self.daily_activity_check.cancel()

    def current_check_id(self,message_id):
        with sqlite3.connect(DB_NAME) as c:
            r=c.execute("SELECT id FROM checks WHERE message_id=?",(message_id,)).fetchone(); return r[0] if r else None

    async def send_check_message(self,channel,role=None):
        today=datetime.now(BERLIN_TZ).date().isoformat()
        with sqlite3.connect(DB_NAME) as c:
            existing=c.execute("SELECT id FROM checks WHERE guild_id=? AND check_date=?",(channel.guild.id,today)).fetchone()
            if existing: return None
        role_ping=role.mention if role else ""
        embed=discord.Embed(title="⟡ Activity Check",description=("Bitte bestätige deine Aktivität über den Button.\n\n" "🕐 Tagescheck · ✅ Bestätigung · 📊 Auswertung im Bot\n\n" "Nicht reagiert = wird im Bericht als offen geführt."),color=discord.Color.blurple())
        embed.set_footer(text=f"{channel.guild.name} • {today}")
        msg=await channel.send(content=role_ping,embed=embed,view=ActivityView(self),allowed_mentions=discord.AllowedMentions(roles=bool(role)))
        with sqlite3.connect(DB_NAME) as c:
            c.execute("INSERT INTO checks(guild_id,channel_id,message_id,check_date,created_at) VALUES(?,?,?,?,?)",(channel.guild.id,channel.id,msg.id,today,datetime.now(BERLIN_TZ).isoformat()))
        return msg

    @tasks.loop(time=time(hour=6,minute=0,tzinfo=BERLIN_TZ))
    async def daily_activity_check(self):
        await self.bot.wait_until_ready()
        cid=get_config("activity_channel_id"); rid=get_config("activity_role_id")
        if not cid: return
        channel=self.bot.get_channel(int(cid))
        if not channel: return
        role=channel.guild.get_role(int(rid)) if rid and int(rid) else None
        await self.send_check_message(channel,role)

    @app_commands.command(name="setup_activitycheck",description="[Admin] Activity Check um 06:00 einrichten")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_activitycheck(self,interaction,kanal:discord.TextChannel,team_rolle:discord.Role=None):
        set_config("activity_channel_id",kanal.id); set_config("activity_role_id",team_rolle.id if team_rolle else 0)
        await interaction.response.send_message(f"✅ Activity Check: {kanal.mention} täglich 06:00" + (f" · Rolle {team_rolle.mention}" if team_rolle else ""),ephemeral=True)

    @app_commands.command(name="test_activitycheck",description="[Admin] Activity Check sofort senden")
    @app_commands.checks.has_permissions(administrator=True)
    async def test_activitycheck(self,interaction):
        cid=get_config("activity_channel_id"); rid=get_config("activity_role_id"); channel=interaction.guild.get_channel(int(cid)) if cid else interaction.channel; role=interaction.guild.get_role(int(rid)) if rid and int(rid) else None
        await interaction.response.send_message("✅ Test wird gesendet.",ephemeral=True); await self.send_check_message(channel,role)

    @app_commands.command(name="activity_status",description="Zeigt den heutigen Activity Check")
    @app_commands.checks.has_permissions(administrator=True)
    async def activity_status(self,interaction):
        today=datetime.now(BERLIN_TZ).date().isoformat()
        with sqlite3.connect(DB_NAME) as c:
            row=c.execute("SELECT id FROM checks WHERE guild_id=? AND check_date=?",(interaction.guild.id,today)).fetchone()
            if not row: await interaction.response.send_message("Heute wurde noch kein Activity Check gesendet.",ephemeral=True); return
            check_id=row[0]; n=c.execute("SELECT COUNT(*) FROM responses WHERE check_id=?",(check_id,)).fetchone()[0]
        await interaction.response.send_message(f"📊 Activity Check {today}: **{n}** Bestätigungen.",ephemeral=True)

async def setup(bot): await bot.add_cog(ActivityCheckCog(bot))
