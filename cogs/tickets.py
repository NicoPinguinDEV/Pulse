from __future__ import annotations

import asyncio
import os
import re

import discord
from discord.ext import commands
from discord.ui import Button, Modal, Select, TextInput, View

from pulse_db import (
    claim_ticket,
    close_ticket,
    create_ticket,
    get_ticket,
    list_tickets,
    record_event,
    unclaim_ticket,
    update_ticket_priority,
)

os.makedirs("transcripts", exist_ok=True)

CATEGORY_META = {
    "support": ("Support", "Hilfe bei Fragen oder Problemen", "🛠️"),
    "bewerbung": ("Bewerbung", "Fragen rund um deine Bewerbung", "📝"),
    "beschwerde": ("Beschwerde", "Melde einen Vorfall oder eine Beschwerde", "⚠️"),
    "admin": ("Leitung", "Wichtige Angelegenheiten für die Leitung", "🚨"),
    "technik": ("Technik", "Technische Probleme mit Pulse oder dem Server", "💻"),
    "rp": ("RP", "Fragen und Hilfe rund um das RP", "🎮"),
}

PRIORITIES = ["low", "normal", "high", "urgent"]
PRIORITY_LABELS = {"low": "⚪ Niedrig", "normal": "🟢 Normal", "high": "🟠 Hoch", "urgent": "🚨 Dringend"}


def web_cfg():
    import webserver
    return webserver.load_config()


def team_roles(guild):
    ids = set(web_cfg().get("team_role_ids", []))
    return [r for r in guild.roles if r.id in ids]


def is_team(member) -> bool:
    if member.guild_permissions.administrator:
        return True
    ids = set(web_cfg().get("team_role_ids", []))
    return any(r.id in ids for r in getattr(member, "roles", []))


def safe_channel_name(name: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", name.lower()).strip("-")
    return value[:28] or "user"


def priority_embed_value(priority: str) -> str:
    return PRIORITY_LABELS.get(priority, "🟢 Normal")


async def transcript(channel):
    rows = []
    async for m in channel.history(limit=None, oldest_first=True):
        stamp = m.created_at.strftime("%d.%m.%Y %H:%M:%S")
        text = m.content or "[kein Text]"
        if m.attachments:
            text += " | Anhänge: " + ", ".join(a.url for a in m.attachments)
        if m.embeds:
            text += " | Embeds: " + ", ".join((e.title or "Embed") for e in m.embeds)
        rows.append(f"[{stamp}] {m.author} ({m.author.id}): {text}")
    return "\n".join(rows)


async def refresh_ticket_embed(channel, ticket: dict, control_view=None):
    label, _, emoji = CATEGORY_META.get(ticket.get("category"), (ticket.get("category", "Ticket"), "", "🎫"))
    embed = discord.Embed(
        title=f"{emoji} {label} – Ticket",
        description=f"Ticket-ID: `{ticket['id']}`\nAnliegen: **{ticket['user_name']}**",
        color=discord.Color.red() if ticket.get("priority") == "urgent" else discord.Color.blurple(),
    )
    status = "✅ Geschlossen" if ticket.get("status") == "closed" else "🔵 In Bearbeitung" if ticket.get("claimed_by_id") else "🟢 Offen"
    claimed = ticket.get("claimed_by_name") or "Niemand"
    embed.add_field(name="Status", value=status, inline=True)
    embed.add_field(name="Priorität", value=priority_embed_value(ticket.get("priority", "normal")), inline=True)
    embed.add_field(name="Bearbeiter", value=claimed, inline=True)
    embed.set_footer(text="Pulse Ticket-System · Nutze die Buttons für die Bearbeitung")
    # The setup message is the first bot message in a new ticket.
    try:
        async for m in channel.history(limit=20, oldest_first=True):
            if m.author.id == channel.guild.me.id and m.embeds:
                await m.edit(embed=embed, view=control_view or TicketControlView())
                return
    except Exception:
        pass


class TicketCloseModal(Modal, title="Ticket schließen"):
    reason = TextInput(label="Schließgrund", placeholder="Warum wird das Ticket geschlossen?", required=True, max_length=500, style=discord.TextStyle.paragraph)

    def __init__(self, ticket_id: str, actor: discord.Member):
        super().__init__(timeout=300)
        self.ticket_id = ticket_id
        self.actor = actor

    async def on_submit(self, interaction: discord.Interaction):
        ticket = get_ticket(ticket_id=self.ticket_id)
        if not ticket or ticket.get("status") == "closed":
            await interaction.response.send_message("Das Ticket ist bereits geschlossen.", ephemeral=True)
            return
        channel = interaction.channel
        await interaction.response.send_message("🔒 Ticket wird archiviert …", ephemeral=True)
        path = ""
        try:
            text = await transcript(channel)
            local_name = f"{ticket['id']}.txt"
            local_path = os.path.join("transcripts", local_name)
            with open(local_path, "w", encoding="utf-8") as f:
                f.write(text)
            path = local_name
            # Keep the transcript in the channel shortly so the team can verify it exists.
            try:
                await channel.send(file=discord.File(local_path, filename=local_name))
            except Exception:
                pass
        except Exception as exc:
            print(f"Transcript konnte nicht gespeichert werden: {exc}")
        reason = str(self.reason.value).strip()[:500]
        if not close_ticket(ticket["id"], reason, path, self.actor.id, self.actor.display_name):
            return
        record_event("ticket_closed", "ticket", ticket["id"], self.actor.id, self.actor.display_name, {"reason": reason})
        try:
            member = channel.guild.get_member(int(ticket["user_id"]))
            if member:
                dm = discord.Embed(title="🎫 Dein Ticket wurde geschlossen", color=discord.Color.blurple())
                dm.description = f"Ticket `{ticket['id']}` wurde von **{self.actor.display_name}** geschlossen.\n\n**Grund:** {reason}"
                await member.send(embed=dm)
        except Exception:
            pass
        await asyncio.sleep(1)
        try:
            await channel.delete(reason=f"Pulse Ticket geschlossen von {self.actor}")
        except Exception:
            pass


class TicketSelect(Select):
    def __init__(self):
        options = [discord.SelectOption(label=v[0], description=v[1], emoji=v[2], value=k) for k, v in CATEGORY_META.items()]
        super().__init__(placeholder="Wähle eine Kategorie…", min_values=1, max_values=1, options=options, custom_id="pulse_ticket_category")

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("Nur auf dem Server verfügbar.", ephemeral=True)
            return
        category = discord.utils.get(guild.categories, name="Tickets")
        if not category:
            category = await guild.create_category("Tickets", reason="Pulse Ticket-System")
        existing = []
        for c in category.text_channels:
            if c.topic and f"user:{interaction.user.id}" in c.topic:
                t = get_ticket(channel_id=c.id)
                if t and t.get("status") != "closed":
                    existing.append(c)
        if existing:
            await interaction.response.send_message(f"Du hast bereits ein offenes Ticket: {existing[0].mention}", ephemeral=True)
            return
        roles = team_roles(guild)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True, manage_messages=True),
        }
        for role in roles:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_messages=True, attach_files=True)
        key = self.values[0]
        label = CATEGORY_META[key][0]
        channel = await guild.create_text_channel(f"ticket-{key}-{safe_channel_name(interaction.user.display_name)}", category=category, overwrites=overwrites, topic=f"Pulse Ticket | user:{interaction.user.id} | category:{key}")
        tid = create_ticket(channel.id, guild.id, interaction.user.id, interaction.user.display_name, key)
        embed = discord.Embed(title=f"{CATEGORY_META[key][2]} {label} – Ticket", description=f"Willkommen {interaction.user.mention}!\n\nEin Teammitglied kann das Ticket übernehmen.\n**Ticket:** `{tid}`", color=discord.Color.blurple())
        embed.add_field(name="Status", value="🟢 Offen", inline=True)
        embed.add_field(name="Priorität", value="🟢 Normal", inline=True)
        embed.add_field(name="Kategorie", value=label, inline=True)
        embed.set_footer(text="Pulse Ticket-System")
        await channel.send(embed=embed, view=TicketControlView())
        for role in roles:
            try:
                await channel.send(f"{role.mention} Neues {label}-Ticket von {interaction.user.mention}.", allowed_mentions=discord.AllowedMentions(roles=True, users=True))
                break
            except Exception:
                continue
        await interaction.response.send_message(f"✅ Ticket erstellt: {channel.mention}", ephemeral=True)


class TicketView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketSelect())


class TicketControlView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Übernehmen", style=discord.ButtonStyle.success, emoji="📥", custom_id="pulse_ticket_claim")
    async def claim(self, interaction: discord.Interaction, button: Button):
        t = get_ticket(channel_id=interaction.channel.id)
        if not t:
            await interaction.response.send_message("Ticket nicht gefunden.", ephemeral=True)
            return
        if not is_team(interaction.user):
            await interaction.response.send_message("❌ Nur Teammitglieder dürfen Tickets übernehmen.", ephemeral=True)
            return
        ok = claim_ticket(t["id"], interaction.user.id, interaction.user.display_name)
        if not ok:
            latest = get_ticket(ticket_id=t["id"])
            who = latest.get("claimed_by_name") if latest else None
            await interaction.response.send_message(f"Dieses Ticket wurde bereits von **{who or 'jemand anderem'}** übernommen.", ephemeral=True)
            return
        latest = get_ticket(ticket_id=t["id"])
        button.disabled = True
        button.label = f"Übernommen · {interaction.user.display_name}"[:80]
        try:
            if latest:
                await refresh_ticket_embed(interaction.channel, latest, self)
        except Exception:
            pass
        await interaction.response.send_message(f"✅ Du bearbeitest Ticket `{t['id']}` jetzt.", ephemeral=True)

    @discord.ui.button(label="Freigeben", style=discord.ButtonStyle.secondary, emoji="↩️", custom_id="pulse_ticket_unclaim")
    async def unclaim(self, interaction: discord.Interaction, button: Button):
        t = get_ticket(channel_id=interaction.channel.id)
        if not t:
            await interaction.response.send_message("Ticket nicht gefunden.", ephemeral=True)
            return
        if not is_team(interaction.user):
            await interaction.response.send_message("❌ Nur Teammitglieder dürfen Tickets freigeben.", ephemeral=True)
            return
        if not t.get("claimed_by_id"):
            await interaction.response.send_message("Das Ticket ist bereits frei.", ephemeral=True)
            return
        if str(t.get("claimed_by_id")) != str(interaction.user.id) and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Du kannst nur dein eigenes Ticket freigeben (Admins dürfen übernehmen).", ephemeral=True)
            return
        if unclaim_ticket(t["id"], interaction.user.id, interaction.user.display_name):
            await refresh_ticket_embed(interaction.channel, get_ticket(ticket_id=t["id"]), TicketControlView())
            await interaction.response.send_message("↩️ Ticket wurde freigegeben.", ephemeral=True)
        else:
            await interaction.response.send_message("Ticket konnte nicht freigegeben werden.", ephemeral=True)

    @discord.ui.button(label="Priorität", style=discord.ButtonStyle.secondary, emoji="🚦", custom_id="pulse_ticket_priority")
    async def priority(self, interaction: discord.Interaction, button: Button):
        t = get_ticket(channel_id=interaction.channel.id)
        if not t:
            await interaction.response.send_message("Ticket nicht gefunden.", ephemeral=True)
            return
        if not is_team(interaction.user):
            await interaction.response.send_message("❌ Dafür fehlt dir die Berechtigung.", ephemeral=True)
            return
        current = t.get("priority", "normal")
        next_priority = PRIORITIES[(PRIORITIES.index(current) + 1) % len(PRIORITIES)] if current in PRIORITIES else "normal"
        update_ticket_priority(t["id"], next_priority, interaction.user.id, interaction.user.display_name)
        await refresh_ticket_embed(interaction.channel, get_ticket(ticket_id=t["id"]), TicketControlView())
        await interaction.response.send_message(f"🚦 Priorität: {priority_embed_value(next_priority)}", ephemeral=True)

    @discord.ui.button(label="Schließen", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="pulse_ticket_close")
    async def close(self, interaction: discord.Interaction, button: Button):
        t = get_ticket(channel_id=interaction.channel.id)
        if not t:
            await interaction.response.send_message("Ticket nicht gefunden.", ephemeral=True)
            return
        if not is_team(interaction.user) and str(t.get("user_id")) != str(interaction.user.id):
            await interaction.response.send_message("❌ Dafür fehlt dir die Berechtigung.", ephemeral=True)
            return
        await interaction.response.send_modal(TicketCloseModal(t["id"], interaction.user))


class Tickets(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(TicketView())
        self.bot.add_view(TicketControlView())

    @commands.command(name="ticketsetup")
    @commands.has_permissions(administrator=True)
    async def ticketsetup(self, ctx):
        embed = discord.Embed(title="🎫 Pulse Support-Tickets", description="Wähle eine Kategorie. Tickets sind privat und können vom Team übernommen werden.", color=discord.Color.blurple())
        embed.add_field(name="🛠️ Support", value="Fragen und Probleme", inline=True)
        embed.add_field(name="📝 Bewerbung", value="Bewerbungsfragen", inline=True)
        embed.add_field(name="🚨 Leitung", value="Wichtige Angelegenheiten", inline=True)
        embed.set_footer(text="Pulse Pro · Ticket-System")
        await ctx.send(embed=embed, view=TicketView())

    @commands.command(name="ticketstats")
    @commands.has_permissions(administrator=True)
    async def ticketstats(self, ctx):
        items = list_tickets(limit=2000)
        closed = [t for t in items if t["status"] == "closed"]
        open_items = [t for t in items if t["status"] != "closed"]
        rated = [int(t["rating"]) for t in closed if t.get("rating")]
        avg = sum(rated) / len(rated) if rated else 0
        urgent = sum(1 for t in open_items if t.get("priority") == "urgent")
        e = discord.Embed(title="🎫 Pulse Ticket-Statistik", color=discord.Color.blurple())
        e.add_field(name="Gesamt", value=str(len(items)), inline=True)
        e.add_field(name="Offen", value=str(len(open_items)), inline=True)
        e.add_field(name="Dringend", value=str(urgent), inline=True)
        e.add_field(name="Geschlossen", value=str(len(closed)), inline=True)
        e.add_field(name="Bewertungen", value=f"{avg:.1f} ⭐" if rated else "Keine", inline=True)
        e.add_field(name="Bearbeitungsquote", value=f"{len(closed)/len(items)*100:.0f}%" if items else "0%", inline=True)
        await ctx.send(embed=e)


async def setup(bot):
    await bot.add_cog(Tickets(bot))
