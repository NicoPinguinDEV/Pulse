import asyncio
import io
import os
from datetime import datetime

import discord
from discord.ext import commands
from discord.ui import View, Select, Button

from pulse_db import create_ticket, get_ticket, list_tickets, claim_ticket, close_ticket

os.makedirs("transcripts", exist_ok=True)


CATEGORY_META = {
    "support": ("Support", "Hilfe bei Fragen oder Problemen", "🛠️"),
    "bewerbung": ("Bewerbung", "Fragen rund um deine Bewerbung", "📝"),
    "beschwerde": ("Beschwerde", "Melde einen Vorfall oder eine Beschwerde", "⚠️"),
    "admin": ("Leitung", "Wichtige Angelegenheiten für die Leitung", "🚨"),
    "technik": ("Technik", "Technische Probleme mit Pulse oder dem Server", "💻"),
    "rp": ("RP", "Fragen und Hilfe rund um das RP", "🎮"),
}


def team_roles(guild):
    cfg = getattr(__import__("webserver"), "load_config")()
    ids = cfg.get("team_role_ids", [])
    return [r for r in guild.roles if r.id in ids]


async def transcript(channel):
    rows=[]
    async for m in channel.history(limit=None, oldest_first=True):
        stamp=m.created_at.strftime("%d.%m.%Y %H:%M:%S")
        text=m.content or "[kein Text]"
        if m.attachments:
            text += " | Anhänge: " + ", ".join(a.url for a in m.attachments)
        rows.append(f"[{stamp}] {m.author} ({m.author.id}): {text}")
    return "\n".join(rows)


class TicketSelect(Select):
    def __init__(self):
        options=[discord.SelectOption(label=v[0],description=v[1],emoji=v[2],value=k) for k,v in CATEGORY_META.items()]
        super().__init__(placeholder="Wähle eine Kategorie...",min_values=1,max_values=1,options=options)

    async def callback(self, interaction):
        guild=interaction.guild
        category=discord.utils.get(guild.categories,name="Tickets")
        if not category:
            category=await guild.create_category("Tickets",reason="Pulse Ticket-System")
        roles=team_roles(guild)
        overwrites={
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,attach_files=True),
            guild.me: discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,manage_channels=True),
        }
        for role in roles:
            overwrites[role]=discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,manage_messages=True)
        existing=[c for c in category.text_channels if c.topic and f"user:{interaction.user.id}" in c.topic and get_ticket(channel_id=c.id) and get_ticket(channel_id=c.id).get("status") != "closed"]
        if existing:
            await interaction.response.send_message(f"Du hast bereits ein offenes Ticket: {existing[0].mention}",ephemeral=True)
            return
        label=CATEGORY_META[self.values[0]][0]
        channel=await guild.create_text_channel(f"ticket-{self.values[0]}-{interaction.user.name}"[:90],category=category,overwrites=overwrites,topic=f"Pulse Ticket | user:{interaction.user.id} | category:{self.values[0]}")
        tid=create_ticket(channel.id,guild.id,interaction.user.id,interaction.user.display_name,self.values[0])
        embed=discord.Embed(title=f"{CATEGORY_META[self.values[0]][2]} {label} – Ticket",description=f"Willkommen {interaction.user.mention}!\nEin Teammitglied kann das Ticket übernehmen.\n\n**Ticket:** `{tid}`",color=discord.Color.blurple())
        embed.add_field(name="Status",value="🟢 Offen",inline=True); embed.add_field(name="Priorität",value="🟢 Normal",inline=True)
        await channel.send(embed=embed,view=TicketControlView())
        await interaction.response.send_message(f"✅ Ticket erstellt: {channel.mention}",ephemeral=True)


class TicketView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketSelect())


class TicketControlView(View):
    def __init__(self): super().__init__(timeout=None)

    @discord.ui.button(label="Übernehmen",style=discord.ButtonStyle.success,emoji="📥",custom_id="pulse_ticket_claim")
    async def claim(self, interaction, button):
        t=get_ticket(channel_id=interaction.channel.id)
        if not t:
            await interaction.response.send_message("Ticket nicht gefunden.",ephemeral=True); return
        cfg=__import__("webserver").load_config(); ids=cfg.get("team_role_ids",[])
        if not interaction.user.guild_permissions.administrator and not any(r.id in ids for r in interaction.user.roles):
            await interaction.response.send_message("❌ Nur Teammitglieder dürfen Tickets übernehmen.",ephemeral=True); return
        if t.get("claimed_by_id") and str(t["claimed_by_id"]) != str(interaction.user.id):
            await interaction.response.send_message(f"Dieses Ticket wurde bereits von **{t['claimed_by_name']}** übernommen.",ephemeral=True); return
        claim_ticket(t["id"],interaction.user.id,interaction.user.display_name)
        button.disabled=True; button.label=f"Übernommen von {interaction.user.display_name}"[:80]
        await interaction.message.edit(view=self)
        await interaction.response.send_message(f"✅ {interaction.user.mention} bearbeitet das Ticket jetzt.")

    @discord.ui.button(label="Schließen",style=discord.ButtonStyle.danger,emoji="🔒",custom_id="pulse_ticket_close")
    async def close(self, interaction, button):
        t=get_ticket(channel_id=interaction.channel.id)
        if not t:
            await interaction.response.send_message("Ticket nicht gefunden.",ephemeral=True); return
        cfg=__import__("webserver").load_config(); ids=cfg.get("team_role_ids",[])
        if not interaction.user.guild_permissions.administrator and not any(r.id in ids for r in interaction.user.roles) and str(t["user_id"])!=str(interaction.user.id):
            await interaction.response.send_message("❌ Dafür fehlt dir die Berechtigung.",ephemeral=True); return
        await interaction.response.send_message("🔒 Ticket wird archiviert und in 5 Sekunden geschlossen.")
        await asyncio.sleep(5)
        path=""
        try:
            text=await transcript(interaction.channel)
            local_name=f"{t['id']}.txt"
            local_path=os.path.join("transcripts",local_name)
            with open(local_path,"w",encoding="utf-8") as f: f.write(text)
            # Zusätzlich kurz im Kanal bereitstellen, solange das Ticket existiert.
            await interaction.channel.send(file=discord.File(local_path,filename=local_name))
            path=local_name
        except Exception as e:
            print(f"Transcript konnte nicht gespeichert werden: {e}")
        close_ticket(t["id"],f"Geschlossen von {interaction.user.display_name}",path)
        try: await interaction.channel.delete(reason=f"Pulse Ticket geschlossen von {interaction.user}")
        except Exception: pass


class Tickets(commands.Cog):
    def __init__(self,bot): self.bot=bot
    async def cog_load(self):
        self.bot.add_view(TicketView()); self.bot.add_view(TicketControlView())

    @commands.command(name="ticketsetup")
    @commands.has_permissions(administrator=True)
    async def ticketsetup(self,ctx):
        embed=discord.Embed(title="🎫 Pulse Support-Tickets",description="Wähle die passende Kategorie. Tickets sind privat und können vom Team übernommen werden.",color=discord.Color.blurple())
        await ctx.send(embed=embed,view=TicketView())

    @commands.command(name="ticketstats")
    @commands.has_permissions(administrator=True)
    async def ticketstats(self,ctx):
        items=list_tickets(); closed=[t for t in items if t['status']=='closed']; open_count=sum(t['status']!='closed' for t in items)
        avg=sum((t.get('rating') or 0) for t in closed if t.get('rating')) / max(1,sum(1 for t in closed if t.get('rating')))
        e=discord.Embed(title="🎫 Ticket-Statistik",color=discord.Color.blurple())
        e.add_field(name="Gesamt",value=str(len(items))); e.add_field(name="Offen",value=str(open_count)); e.add_field(name="Geschlossen",value=str(len(closed))); e.add_field(name="Ø Bewertung",value=f"{avg:.1f} ⭐")
        await ctx.send(embed=e)

async def setup(bot): await bot.add_cog(Tickets(bot))
