"""BOT-06: /ticket antwort|uebergeben|tag|sperre|entsperren – used inside a ticket channel by the team.

Rights are checked on every use (can_manage_tickets or admin); the data logic is pulse_ticket_extras.
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

import pulse_db
import pulse_ticket_extras as tx


def may_manage(interaction: discord.Interaction) -> bool:
    import webserver
    if interaction.guild is None:
        return False
    perms, _ = webserver.compute_perms(interaction.guild, interaction.user.id, webserver.load_config())
    return bool(perms.get("can_manage_tickets") or perms.get("is_admin"))


def current_ticket(interaction: discord.Interaction):
    ticket = pulse_db.get_ticket(channel_id=str(getattr(interaction.channel, "id", "")))
    return ticket if ticket and ticket.get("status") != "closed" else None


async def deny(interaction: discord.Interaction, text: str) -> None:
    await interaction.response.send_message("❌ " + text, ephemeral=True)


class InternalTicketNoteModal(discord.ui.Modal, title="Interne Ticketnotiz"):
    """Collect note text in a modal so sensitive content is never exposed as a slash-command option."""

    def __init__(self, ticket_id: str):
        super().__init__(timeout=300)
        self.ticket_id = str(ticket_id)
        self.note_input = discord.ui.TextInput(
            label="Notiz (nur für berechtigtes Team)",
            style=discord.TextStyle.paragraph,
            placeholder="Interne Information für die weitere Ticketbearbeitung …",
            min_length=5,
            max_length=tx.MAX_INTERNAL_NOTE_LENGTH,
            required=True,
        )
        self.add_item(self.note_input)

    async def on_submit(self, interaction: discord.Interaction):
        # Re-check permissions and ticket/channel association when the modal is submitted.
        if not may_manage(interaction):
            await deny(interaction, "Nur das Ticket-Team kann interne Notizen speichern.")
            return
        ticket = current_ticket(interaction)
        if ticket is None or str(ticket.get("id")) != self.ticket_id:
            await deny(interaction, "Das Ticket ist nicht mehr offen oder der Kontext stimmt nicht.")
            return
        try:
            tx.add_internal_note(
                ticket["id"],
                interaction.user.id,
                interaction.user.display_name,
                self.note_input.value,
            )
        except ValueError as exc:
            await deny(interaction, str(exc))
            return
        await interaction.response.send_message(
            "🔒 Interne Notiz gespeichert. Sie wurde nicht im Ticket-Kanal veröffentlicht.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class TicketTools(commands.GroupCog, group_name="ticket", group_description="Werkzeuge im Ticket-Kanal (Team)"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        super().__init__()

    async def _guard(self, interaction) -> dict | None:
        if not may_manage(interaction):
            await deny(interaction, "Nur das Ticket-Team kann das nutzen.")
            return None
        ticket = current_ticket(interaction)
        if ticket is None:
            await deny(interaction, "Bitte in einem offenen Ticket-Kanal verwenden.")
            return None
        return ticket

    @app_commands.command(name="notiz", description="Interne, nur für das Team sichtbare Notiz hinzufügen.")
    async def notiz(self, interaction: discord.Interaction):
        ticket = await self._guard(interaction)
        if ticket is None:
            return
        await interaction.response.send_modal(InternalTicketNoteModal(ticket["id"]))

    @app_commands.command(name="notizen", description="Interne Teamnotizen dieses Tickets privat anzeigen.")
    @app_commands.describe(seite="Seite mit jeweils zwei Notizen (neueste zuerst)")
    async def notizen(self, interaction: discord.Interaction, seite: app_commands.Range[int, 1, 100] = 1):
        ticket = await self._guard(interaction)
        if ticket is None:
            return
        notes = tx.internal_notes_for(ticket["id"], limit=2, offset=(seite - 1) * 2)
        if not notes:
            await interaction.response.send_message(
                "Für dieses Ticket gibt es auf dieser Seite keine internen Notizen.",
                ephemeral=True,
            )
            return
        blocks = []
        for item in notes:
            created = str(item.get("created_at") or "")[:19].replace("T", " ")
            author = discord.utils.escape_markdown(str(item.get("author_name") or "Teammitglied"))[:100]
            note_text = discord.utils.escape_mentions(
                discord.utils.escape_markdown(str(item.get("note") or ""))
            )
            blocks.append(f"**{author} · {created}**\n{note_text}")
        embed = discord.Embed(
            title=f"🔒 Interne Notizen · Ticket {ticket['id']}",
            description="\n\n".join(blocks)[:4096],
            color=0x64748B,
        )
        embed.set_footer(text=f"Seite {seite} · jeweils zwei Notizen")
        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="verknuepfen", description="Dieses Ticket mit einem anderen Ticket verbinden.")
    @app_commands.describe(ticket_id="ID des anderen Tickets", notiz="Optionaler interner Grund für die Verknüpfung")
    async def verknuepfen(
        self,
        interaction: discord.Interaction,
        ticket_id: str,
        notiz: str = "",
    ):
        ticket = await self._guard(interaction)
        if ticket is None:
            return
        try:
            created = tx.link_tickets(
                ticket["id"],
                ticket_id,
                interaction.user.id,
                interaction.user.display_name,
                notiz,
            )
        except ValueError as exc:
            await deny(interaction, str(exc))
            return
        if not created:
            await interaction.response.send_message(
                f"🔗 Ticket **{ticket_id.strip()}** ist bereits mit diesem Ticket verknüpft.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        await interaction.response.send_message(
            f"🔗 Ticket **{ticket['id']}** und **{ticket_id.strip()}** wurden intern verknüpft.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="verknuepfungen", description="Verknüpfte Tickets dieses Vorgangs anzeigen.")
    @app_commands.describe(seite="Seite mit jeweils zehn verknüpften Tickets")
    async def verknuepfungen(
        self,
        interaction: discord.Interaction,
        seite: app_commands.Range[int, 1, 1000] = 1,
    ):
        ticket = await self._guard(interaction)
        if ticket is None:
            return
        links = tx.linked_tickets(ticket["id"], limit=11, offset=(seite - 1) * 10)
        if not links:
            await interaction.response.send_message(
                "Für dieses Ticket gibt es keine Verknüpfungen auf dieser Seite.",
                ephemeral=True,
            )
            return
        has_more = len(links) > 10
        blocks = []
        for link in links[:10]:
            related_id = discord.utils.escape_markdown(str(link.get("related_ticket_id") or ""))
            status = discord.utils.escape_markdown(str(link.get("status") or "unbekannt"))
            category = discord.utils.escape_markdown(str(link.get("category") or "–"))
            priority = discord.utils.escape_markdown(str(link.get("priority") or "normal"))
            channel_id = str(link.get("channel_id") or "")
            destination = f"<#{channel_id}>" if channel_id.isdigit() else "Kanal nicht verfügbar"
            note = discord.utils.escape_mentions(
                discord.utils.escape_markdown(str(link.get("link_note") or ""))
            )
            line = f"**{related_id}** · {category} · {status} · {priority} · {destination}"
            if note:
                line += f"\\n_{note[:300]}_"
            blocks.append(line)
        embed = discord.Embed(
            title=f"🔗 Verknüpfte Tickets · {ticket['id']}",
            description="\\n\\n".join(blocks)[:4096],
            color=0x64748B,
        )
        embed.set_footer(text=f"Seite {seite}" + (" · weitere Einträge vorhanden" if has_more else ""))
        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="trennen", description="Eine Ticket-Verknüpfung entfernen.")
    @app_commands.describe(ticket_id="ID des zu trennenden Tickets")
    async def trennen(self, interaction: discord.Interaction, ticket_id: str):
        ticket = await self._guard(interaction)
        if ticket is None:
            return
        try:
            removed = tx.unlink_tickets(ticket["id"], ticket_id)
        except ValueError as exc:
            await deny(interaction, str(exc))
            return
        await interaction.response.send_message(
            "🔓 Ticket-Verknüpfung entfernt." if removed else "Diese Ticket-Verknüpfung bestand nicht.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="antwort", description="Schnellantwort für diese Ticket-Kategorie senden.")
    @app_commands.describe(vorlage="Titel der Schnellantwort")
    async def antwort(self, interaction: discord.Interaction, vorlage: str):
        ticket = await self._guard(interaction)
        if ticket is None:
            return
        reply = next((r for r in tx.replies_for(ticket.get("category", "")) if r["title"] == vorlage), None)
        if reply is None:
            await deny(interaction, "Diese Schnellantwort gibt es für die Kategorie nicht (Dashboard → Ticket-Werkzeuge).")
            return
        await interaction.response.send_message(reply["text"], allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False))
        pulse_db.touch_ticket_activity(str(interaction.channel.id), "staff")

    @antwort.autocomplete("vorlage")
    async def antwort_autocomplete(self, interaction: discord.Interaction, current: str):
        ticket = current_ticket(interaction)
        if ticket is None or not may_manage(interaction):
            return []
        needle = (current or "").lower()
        return [app_commands.Choice(name=r["title"][:100], value=r["title"][:100])
                for r in tx.replies_for(ticket.get("category", "")) if needle in r["title"].lower()][:25]

    @app_commands.command(name="uebergeben", description="Ticket an ein anderes Teammitglied übergeben (Pflicht-Notiz).")
    @app_commands.describe(mitglied="Wer übernimmt", notiz="Was die Person wissen muss (Pflicht)")
    async def uebergeben(self, interaction: discord.Interaction, mitglied: discord.Member, notiz: str):
        ticket = await self._guard(interaction)
        if ticket is None:
            return
        import webserver
        perms, _ = webserver.compute_perms(interaction.guild, mitglied.id, webserver.load_config())
        if mitglied.bot or not (perms.get("can_manage_tickets") or perms.get("is_admin")):
            await deny(interaction, "Übergeben geht nur an Teammitglieder mit Ticket-Recht.")
            return
        ok, message = tx.handover(ticket["id"], mitglied.id, mitglied.display_name, interaction.user.id, interaction.user.display_name, notiz)
        if not ok:
            await deny(interaction, message)
            return
        await interaction.response.send_message(
            f"🔁 **Übergabe** an {mitglied.mention} durch {interaction.user.mention}\n📝 {notiz.strip()[:1500]}",
            allowed_mentions=discord.AllowedMentions(users=[mitglied], roles=False, everyone=False))

    @app_commands.command(name="tag", description="Tag an diesem Ticket setzen oder entfernen.")
    @app_commands.describe(tag="z. B. rueckerstattung, bug, eskaliert", entfernen="Tag wieder entfernen")
    async def tag(self, interaction: discord.Interaction, tag: str, entfernen: bool = False):
        ticket = await self._guard(interaction)
        if ticket is None:
            return
        try:
            if entfernen:
                done = tx.remove_tag(ticket["id"], tag)
                text = f"🏷️ Tag **{tx.clean_tag(tag)}** entfernt." if done else "Dieser Tag war nicht gesetzt."
            else:
                text = f"🏷️ Tag **{tx.add_tag(ticket['id'], tag, interaction.user.display_name)}** gesetzt."
        except ValueError as exc:
            await deny(interaction, str(exc))
            return
        await interaction.response.send_message(text + " Alle Tags: " + (", ".join(tx.tags_for([ticket["id"]]).get(ticket["id"], [])) or "keine"),
                                                ephemeral=True)

    @app_commands.command(name="sperre", description="Person für neue Tickets sperren (mit Ablauf und Grund).")
    @app_commands.describe(mitglied="Wer gesperrt wird", tage="Dauer in Tagen (1–365)", grund="Pflichtgrund")
    async def sperre(self, interaction: discord.Interaction, mitglied: discord.Member,
                     tage: app_commands.Range[int, 1, 365], grund: str):
        if not may_manage(interaction):
            await deny(interaction, "Nur das Ticket-Team kann Ticket-Sperren setzen.")
            return
        import webserver
        team_ids = {int(x) for x in webserver.load_config().get("team_role_ids", []) if str(x).isdigit()}
        if mitglied.bot or mitglied.id == interaction.user.id or any(r.id in team_ids for r in getattr(mitglied, "roles", [])):
            # review fix: ticket bans are for citizens – not for the team, bots or oneself
            await deny(interaction, "Ticket-Sperren gelten nur für Bürger – nicht für Teammitglieder, Bots oder dich selbst.")
            return
        try:
            entry = tx.block(mitglied.id, tage, grund, interaction.user.display_name)
        except ValueError as exc:
            await deny(interaction, str(exc))
            return
        webserver.log_audit(interaction.user.display_name, str(interaction.user.id), "Ticket-Sperre",
                            f"{mitglied.display_name} ({mitglied.id}) bis {entry['until'][:10]}: {entry['reason']}")
        await interaction.response.send_message(f"🚫 {mitglied.display_name} kann bis {entry['until'][:10]} keine neuen Tickets öffnen.",
                                                ephemeral=True)

    @app_commands.command(name="entsperren", description="Ticket-Sperre einer Person aufheben.")
    async def entsperren(self, interaction: discord.Interaction, mitglied: discord.Member):
        if not may_manage(interaction):
            await deny(interaction, "Nur das Ticket-Team kann Ticket-Sperren aufheben.")
            return
        done = tx.unblock(mitglied.id)
        if done:
            import webserver
            webserver.log_audit(interaction.user.display_name, str(interaction.user.id), "Ticket-Sperre aufgehoben", str(mitglied.id))
        await interaction.response.send_message("✅ Sperre aufgehoben." if done else "Für diese Person gab es keine Sperre.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(TicketTools(bot))
