from __future__ import annotations

import asyncio
import os
import re

import discord
from discord import app_commands
from discord.ext import commands, tasks
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
    get_setting,
    set_setting,
)

os.makedirs("transcripts", exist_ok=True)

TICKET_PARENT_NAME = "🎫・Tickets"
TEAM_UPDATE_CHANNEL_ID = 1531132354272170115

CATEGORY_META = {
    "support": ("Allgemeiner Support", "Fragen, Hilfe und allgemeine Anliegen", "🎫", 10, "team"),
    "bug": ("Bug melden", "Technische Fehler oder Server-Bugs melden", "⚙️", 5, "team"),
    "unban": ("Entbannungsantrag", "Antrag auf Überprüfung einer Sanktion", "📩", 5, "team"),
    "highteam": ("Highteam Ticket", "Vertrauliche Angelegenheiten für das Highteam", "💢", 3, "highteam"),
    "leadership": ("Führungsebene Ticket", "Vertrauliche Angelegenheiten für die Führungsebene", "🐬", 3, "leadership"),
}

PRIORITIES = ["low", "normal", "high", "urgent"]
PRIORITY_LABELS = {"low": "⚪ Niedrig", "normal": "🟢 Normal", "high": "🟠 Hoch", "urgent": "🚨 Dringend"}


def web_cfg():
    import webserver
    return webserver.load_config()


def team_roles(guild):
    ids = set(web_cfg().get("team_role_ids", []))
    return [r for r in guild.roles if r.id in ids]


def ticket_config() -> dict:
    return get_setting("ticket_system_config", {}) or {}


def save_ticket_config(config: dict):
    set_setting("ticket_system_config", config)


def configured_role_ids(guild, group: str):
    config = ticket_config()
    key = f"{group}_role_ids"
    values = config.get(key, [])
    roles = []
    for raw in values if isinstance(values, list) else []:
        try:
            role = guild.get_role(int(raw))
        except (TypeError, ValueError):
            role = None
        if role and not role.managed:
            roles.append(role)
    if roles:
        return roles
    return team_roles(guild)


def category_open_count(key: str) -> int:
    return sum(
        1 for ticket in list_tickets(limit=2000)
        if ticket.get("category") == key and ticket.get("status") != "closed"
    )


def category_meta(key: str):
    meta = CATEGORY_META.get(key)
    if not meta:
        return ("Ticket", "Allgemeines Anliegen", "🎫", 10, "team")
    return meta


def category_roles(guild, key: str):
    return configured_role_ids(guild, category_meta(key)[4])


def can_manage_ticket(member, ticket) -> bool:
    if member.guild_permissions.administrator:
        return True
    key = str(ticket.get("category", "support"))
    return any(role.id in {r.id for r in category_roles(member.guild, key)} for role in member.roles)


def build_panel_embed(guild) -> discord.Embed:
    lines = [
        "<:ticket:1360768000235409559> **Ticket-Support**",
        "",
        "Brauchst du Hilfe oder möchtest etwas melden? Unser Team hilft dir schnell und zuverlässig weiter.",
        "",
        "## 📂 Verfügbare Tickets",
        "",
        "🎫 **Allgemeiner Support**",
        "⚙️ **Bug melden**",
        "📩 **Entbannungsantrag**",
        "💢 **Highteam Ticket**",
        "🐬 **Führungsebene Ticket**",
        "",
        "«Wähle unten einfach die passende Kategorie aus und erstelle dein Ticket.»",
        "",
        "💙 Dein ©𝑩𝑶𝑪𝑯𝑼𝑴 𝑹𝑷 | 𝑽𝟏 Team",
        "",
        "### <:chartsimple:1492541035497128132> Ticket Auslastung",
        "",
        "Hier siehst du die aktuelle Auslastung unserer Tickets. Bei einer höheren Auslastung kann es zu längeren Bearbeitungszeiten kommen. Ein Ticket kannst du jedoch jederzeit eröffnen, unabhängig von der Auslastung.",
        "",
    ]
    for key, meta in CATEGORY_META.items():
        current = category_open_count(key)
        maximum = meta[3]
        percent = int((current / maximum) * 100) if maximum else 0
        lines.append(f"- <:ut_low:1360768000239769621> **{meta[0]}:** Verfügbar `{current}/{maximum}` ({percent}%)")
    embed = discord.Embed(title="<:ticket:1360768000235409559> Ticket-Support", description="\n".join(lines), color=discord.Color.blurple())
    embed.add_field(name="🔐 Privat & sicher", value="Tickets sind nur für den Ersteller und die zuständigen Bearbeiter sichtbar.", inline=True)
    embed.add_field(name="⚡ Schnelle Bearbeitung", value="Tickets können übernommen, priorisiert und strukturiert abgeschlossen werden.", inline=True)
    embed.set_footer(text="Pulse Ticket-System • Bochum RP")
    embed.timestamp = discord.utils.utcnow()
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    return embed


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
    label, _, emoji, _, _ = category_meta(ticket.get("category"))
    embed = discord.Embed(
        title=f"{emoji} {label} – Ticket",
        description=f"Ticket-ID: `{ticket['id']}`\nAnliegen: **{ticket['user_name']}**",
        color=discord.Color.red() if ticket.get("priority") == "urgent" else (
            CATEGORY_META.get(ticket.get("category"), CATEGORY_META["support"])[3] and discord.Color.blurple()
        ),
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
        options = [
            discord.SelectOption(
                label=meta[0],
                description=f"{meta[1]} · {category_open_count(key)}/{meta[3]} offen",
                emoji=meta[2],
                value=key,
            )
            for key, meta in CATEGORY_META.items()
        ]
        super().__init__(
            placeholder="Wähle die passende Ticket-Kategorie …",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="pulse_ticket_category_v3",
        )

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("❌ Nur auf einem Server verfügbar.", ephemeral=True)
            return

        key = self.values[0]
        label, desc, emoji, maximum, group = category_meta(key)

        existing = None
        for ticket in list_tickets(limit=2000):
            if (
                str(ticket.get("guild_id")) == str(guild.id)
                and str(ticket.get("user_id")) == str(interaction.user.id)
                and ticket.get("status") != "closed"
            ):
                existing = ticket
                break

        if existing:
            channel = guild.get_channel(int(existing["channel_id"])) if existing.get("channel_id") else None
            if channel:
                await interaction.response.send_message(
                    f"🎫 Du hast bereits ein offenes Ticket: {channel.mention}",
                    ephemeral=True,
                )
                return
            close_ticket(existing["id"], "Verwaistes Ticket automatisch archiviert.", "", interaction.user.id, interaction.user.display_name)

        parent = discord.utils.get(guild.categories, name=TICKET_PARENT_NAME)
        if not parent:
            parent = await guild.create_category(TICKET_PARENT_NAME, reason="Pulse Ticket-System")

        roles = category_roles(guild, key)
        if key in ("highteam", "leadership") and not ticket_config().get(f"{group}_role_ids"):
            await interaction.response.send_message(
                f"⚠️ **{label}** ist noch nicht vollständig eingerichtet. Eine zuständige Rolle muss zuerst durch einen Administrator konfiguriert werden.",
                ephemeral=True,
            )
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True,
                manage_messages=True,
                manage_permissions=True,
                attach_files=True,
                embed_links=True,
            ),
        }

        for role in roles:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_messages=True,
                attach_files=True,
                embed_links=True,
            )

        channel = await guild.create_text_channel(
            f"ticket-{key}-{safe_channel_name(interaction.user.display_name)}",
            category=parent,
            overwrites=overwrites,
            topic=f"Pulse Ticket | user:{interaction.user.id} | category:{key}",
            reason=f"Pulse Ticket erstellt von {interaction.user}",
        )
        tid = create_ticket(channel.id, guild.id, interaction.user.id, interaction.user.display_name, key)

        embed = discord.Embed(
            title=f"{emoji} {label}",
            description=(
                f"Willkommen {interaction.user.mention}!\n\n"
                f"Beschreibe dein Anliegen bitte möglichst vollständig.\n"
                f"Ein zuständiges Teammitglied wird sich um dein Anliegen kümmern.\n\n"
                f"**Ticket-ID:** `{tid}`\n"
                f"**Kategorie:** {label}\n"
                f"**Status:** 🟢 Offen\n"
                f"**Priorität:** 🟢 Normal\n"
                f"**Bearbeiter:** Noch nicht übernommen"
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="📋 Vorgehen",
            value="1. Anliegen erklären\n2. Rückfragen beantworten\n3. Lösung bestätigen\n4. Ticket über **Schließen** beenden",
            inline=False,
        )
        embed.add_field(
            name="🛡️ Hinweise",
            value="Keine unnötigen Pings. Sensible Daten nur an zuständige Teammitglieder weitergeben.",
            inline=False,
        )
        embed.set_footer(text="Pulse Ticket-System • Ticket-Steuerung")
        embed.timestamp = discord.utils.utcnow()
        await channel.send(embed=embed, view=TicketControlView())

        mentions = " ".join(role.mention for role in roles[:10])
        if mentions:
            await channel.send(
                f"📣 {mentions}\nNeues **{label}** von {interaction.user.mention}.",
                allowed_mentions=discord.AllowedMentions(roles=True, users=True),
            )

        record_event(
            "ticket_created",
            "ticket",
            tid,
            interaction.user.id,
            interaction.user.display_name,
            {"category": key, "channel_id": channel.id},
        )
        await interaction.response.send_message(f"✅ Dein Ticket wurde erstellt: {channel.mention}", ephemeral=True)


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
        self.panel_refresh.start()

    def cog_unload(self):
        self.panel_refresh.cancel()

    async def cog_load(self):
        self.bot.add_view(TicketView())
        self.bot.add_view(TicketControlView())

    @tasks.loop(minutes=5)
    async def panel_refresh(self):
        await self.bot.wait_until_ready()
        settings = ticket_config()
        channel_id = settings.get("panel_channel_id")
        message_id = settings.get("panel_message_id")
        if not channel_id or not message_id:
            return
        for guild in self.bot.guilds:
            try:
                channel = guild.get_channel(int(channel_id))
                if not channel:
                    continue
                message = await channel.fetch_message(int(message_id))
                await message.edit(embed=build_panel_embed(guild), view=TicketView())
            except Exception as exc:
                print(f"Ticket-Panel konnte nicht aktualisiert werden: {exc}")

    @panel_refresh.before_loop
    async def before_panel_refresh(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="ticketpanel", description="Richtet das professionelle Ticket-Panel ein.")
    @app_commands.checks.has_permissions(administrator=True)
    async def ticketpanel(self, interaction: discord.Interaction):
        if not interaction.guild or not interaction.channel:
            await interaction.response.send_message("❌ Nur auf einem Server verfügbar.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        message = await interaction.channel.send(embed=build_panel_embed(interaction.guild), view=TicketView())
        settings = ticket_config()
        settings.update({"panel_channel_id": interaction.channel.id, "panel_message_id": message.id})
        save_ticket_config(settings)
        await interaction.followup.send("✅ Professionelles Ticket-Panel eingerichtet. Die Auslastung wird automatisch aktualisiert.", ephemeral=True)

    @app_commands.command(name="ticketrole", description="Setzt die zuständige Rolle für Highteam oder Führungsebene.")
    @app_commands.describe(bereich="Geschützter Ticketbereich", rolle="Rolle, die Zugriff erhält")
    @app_commands.choices(bereich=[
        app_commands.Choice(name="Highteam Ticket", value="highteam"),
        app_commands.Choice(name="Führungsebene Ticket", value="leadership"),
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def ticketrole(self, interaction: discord.Interaction, bereich: app_commands.Choice[str], rolle: discord.Role):
        settings = ticket_config()
        settings[f"{bereich.value}_role_ids"] = [rolle.id]
        save_ticket_config(settings)
        await interaction.response.send_message(
            f"✅ {rolle.mention} ist jetzt für **{CATEGORY_META[bereich.value][0]}** zuständig.",
            ephemeral=True,
        )

    @app_commands.command(name="ticketlog", description="Setzt den Kanal für Ticket-Protokolle.")
    @app_commands.checks.has_permissions(administrator=True)
    async def ticketlog(self, interaction: discord.Interaction, kanal: discord.TextChannel):
        settings = ticket_config()
        settings["log_channel_id"] = kanal.id
        save_ticket_config(settings)
        await interaction.response.send_message(f"✅ Ticket-Protokolle gehen ab jetzt in {kanal.mention}.", ephemeral=True)

    @app_commands.command(name="ticketstats", description="Zeigt Ticket-Auslastung und Bearbeitungsstatistik.")
    @app_commands.checks.has_permissions(administrator=True)
    async def ticketstats_slash(self, interaction: discord.Interaction):
        items = list_tickets(limit=2000)
        open_items = [ticket for ticket in items if ticket.get("status") != "closed"]
        closed_items = [ticket for ticket in items if ticket.get("status") == "closed"]
        ratings = [int(ticket["rating"]) for ticket in closed_items if ticket.get("rating")]
        average = sum(ratings) / len(ratings) if ratings else 0

        lines = []
        for key, meta in CATEGORY_META.items():
            current = sum(1 for ticket in open_items if ticket.get("category") == key)
            percent = int(current / meta[3] * 100) if meta[3] else 0
            lines.append(f"{meta[2]} **{meta[0]}** · `{current}/{meta[3]}` · {percent}%")

        embed = discord.Embed(
            title="📊 Pulse Ticket-Auslastung",
            description="\n".join(lines),
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="🎫 Gesamt", value=str(len(items)), inline=True)
        embed.add_field(name="🟢 Offen", value=str(len(open_items)), inline=True)
        embed.add_field(name="✅ Geschlossen", value=str(len(closed_items)), inline=True)
        embed.add_field(name="⭐ Durchschnitt", value=f"{average:.1f}/5" if ratings else "Noch keine Bewertungen", inline=True)
        embed.set_footer(text="Pulse Ticket-System • Statistik")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="ticketsetup", description="Legacy-Setup: erstellt das Ticket-Panel im aktuellen Kanal.")
    @app_commands.checks.has_permissions(administrator=True)
    async def ticketsetup_slash(self, interaction: discord.Interaction):
        if not interaction.guild or not interaction.channel:
            await interaction.response.send_message("❌ Nur auf einem Server verfügbar.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        message = await interaction.channel.send(embed=build_panel_embed(interaction.guild), view=TicketView())
        settings = ticket_config()
        settings.update({"panel_channel_id": interaction.channel.id, "panel_message_id": message.id})
        save_ticket_config(settings)
        await interaction.followup.send("✅ Ticket-Panel eingerichtet und mit dem Auto-Refresh verbunden.", ephemeral=True)

    @commands.command(name="ticketsetup")
    @commands.has_permissions(administrator=True)
    async def legacy_ticketsetup(self, ctx):
        message = await ctx.send(embed=build_panel_embed(ctx.guild), view=TicketView())
        settings = ticket_config()
        settings.update({"panel_channel_id": ctx.channel.id, "panel_message_id": message.id})
        save_ticket_config(settings)

    @commands.command(name="ticketstats")
    @commands.has_permissions(administrator=True)
    async def legacy_ticketstats(self, ctx):
        items = list_tickets(limit=2000)
        closed = sum(1 for ticket in items if ticket.get("status") == "closed")
        open_count = len(items) - closed
        await ctx.send(
            f"📊 **Pulse Ticket-System** · Gesamt `{len(items)}` · Offen `{open_count}` · Geschlossen `{closed}`"
        )


async def setup(bot):
    await bot.add_cog(Tickets(bot))
