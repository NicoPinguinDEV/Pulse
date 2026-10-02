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
    CREATE TABLE IF NOT EXISTS check_members (
        check_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        user_name TEXT NOT NULL,
        role_name TEXT DEFAULT "",
        added_at TEXT NOT NULL,
        PRIMARY KEY(check_id,user_id)
    );
    CREATE INDEX IF NOT EXISTS idx_check_members_check ON check_members(check_id);
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
            has_snapshot = c.execute("SELECT 1 FROM check_members WHERE check_id=? LIMIT 1", (check_id,)).fetchone()
            if has_snapshot:
                member_row = c.execute("SELECT 1 FROM check_members WHERE check_id=? AND user_id=?", (check_id, interaction.user.id)).fetchone()
                if not member_row:
                    await interaction.response.send_message("⚠️ Du warst bei diesem Activity Check nicht als Teammitglied erfasst.", ephemeral=True)
                    return
            c.execute("INSERT OR REPLACE INTO responses(check_id,user_id,reacted_at) VALUES(?,?,?)",(check_id,interaction.user.id,datetime.now(BERLIN_TZ).isoformat()))
        await interaction.response.send_message("✅ Aktivität für diesen Activity Check bestätigt.",ephemeral=True)


class ActivityCheckCog(commands.Cog):
    def eligible_members(self, guild, role=None):
        """Erstellt den festen Team-Snapshot für jeden einzelnen Activity Check."""
        if role:
            return [m for m in role.members if not m.bot]
        try:
            import json
            with open("config.json", "r", encoding="utf-8") as f:
                cfg = json.load(f)
            role_ids = {int(x) for x in cfg.get("team_role_ids", [])}
        except Exception:
            role_ids = set()
        return [m for m in guild.members if not m.bot and any(r.id in role_ids for r in m.roles)]


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
        eligible = self.eligible_members(channel.guild, role)
        role_ping=role.mention if role else ""
        embed=discord.Embed(title="⟡ Activity Check",description=("Bitte bestätige deine Aktivität über den Button.\n\n" "🕐 Tagescheck · ✅ Bestätigung · 📊 Auswertung im Bot\n\n" "Nicht reagiert = wird im Bericht als offen geführt."),color=discord.Color.blurple())
        embed.set_footer(text=f"{channel.guild.name} • {today}")
        msg=await channel.send(content=role_ping,embed=embed,view=ActivityView(self),allowed_mentions=discord.AllowedMentions(roles=bool(role)))
        with sqlite3.connect(DB_NAME) as c:
            cur = c.execute(
                "INSERT INTO checks(guild_id,channel_id,message_id,check_date,created_at) VALUES(?,?,?,?,?)",
                (channel.guild.id,channel.id,msg.id,today,datetime.now(BERLIN_TZ).isoformat()),
            )
            check_id = cur.lastrowid
            created_at = datetime.now(BERLIN_TZ).isoformat()
            for member in eligible:
                c.execute(
                    "INSERT OR REPLACE INTO check_members(check_id,user_id,user_name,role_name,added_at) VALUES(?,?,?,?,?)",
                    (check_id, member.id, member.display_name, member.top_role.name if member.top_role else "", created_at),
                )
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

    @app_commands.command(name="activity_status",description="Zeigt den heutigen Activity Check inklusive offener Teamler")
    @app_commands.checks.has_permissions(administrator=True)
    async def activity_status(self,interaction):
        today=datetime.now(BERLIN_TZ).date().isoformat()
        with sqlite3.connect(DB_NAME) as c:
            c.row_factory=sqlite3.Row
            row=c.execute("SELECT id FROM checks WHERE guild_id=? AND check_date=?",(interaction.guild.id,today)).fetchone()
            if not row:
                await interaction.response.send_message("Heute wurde noch kein Activity Check gesendet. Im Pulse-Dashboard findest du die Auswertung unter **Activity Check**.",ephemeral=True); return
            check_id=row['id']
            responded={int(r['user_id']) for r in c.execute("SELECT user_id FROM responses WHERE check_id=?",(check_id,)).fetchall()}
            role_id=get_config('activity_role_id')
        role=interaction.guild.get_role(int(role_id)) if role_id and str(role_id).isdigit() else None
        members=[m for m in (role.members if role else interaction.guild.members) if not m.bot]
        confirmed=sorted([m for m in members if m.id in responded],key=lambda m:m.display_name.lower())
        open_members=sorted([m for m in members if m.id not in responded],key=lambda m:m.display_name.lower())
        def names(items):
            text=', '.join(m.display_name for m in items) or '—'
            return text if len(text)<=1000 else text[:997]+'…'
        embed=discord.Embed(title=f'📊 Activity Check · {today}',color=discord.Color.blurple())
        embed.add_field(name=f'✅ Bestätigt · {len(confirmed)}',value=names(confirmed),inline=False)
        embed.add_field(name=f'⏳ Noch offen · {len(open_members)}',value=names(open_members),inline=False)
        embed.set_footer(text='Für die vollständige Liste: Pulse Dashboard → Activity Check')
        await interaction.response.send_message(embed=embed,ephemeral=True)

async def setup(bot): await bot.add_cog(ActivityCheckCog(bot))
