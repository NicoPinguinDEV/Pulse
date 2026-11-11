"""BOT-06: /ticket antwort|uebergeben|tag|sperre|entsperren – used inside a ticket channel by the team.

Rights are checked on every use (can_manage_tickets or admin); the data logic is pulse_ticket_extras.
"""
from __future__ import annotations

import sqlite3

import discord
from discord import app_commands
from discord.ext import commands

import pulse_db
import pulse_summaries as ps
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



def _locale(interaction: discord.Interaction) -> str:
    import pulse_i18n
    return pulse_i18n.get_locale(
        getattr(getattr(interaction, "user", None), "id", None),
        getattr(interaction, "locale", None),
    )


def _t(interaction: discord.Interaction, key: str, **values) -> str:
    import pulse_i18n
    return pulse_i18n.translate(_locale(interaction), key, **values)


def _ticket_text(interaction: discord.Interaction, message: str) -> str:
    import pulse_i18n
    return pulse_i18n.translate_ticket_message(_locale(interaction), message)


async def deny(interaction: discord.Interaction, text: str) -> None:
    await interaction.response.send_message("❌ " + _ticket_text(interaction, text), ephemeral=True)


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
            _t(interaction, "ticket_note_saved"),
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
                _t(interaction, "ticket_notes_empty"),
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
            title=_t(interaction, "ticket_notes_title", ticket_id=ticket["id"]),
            description="\n\n".join(blocks)[:4096],
            color=0x64748B,
        )
        embed.set_footer(text=_t(interaction, "ticket_notes_footer", page=seite))
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
                _t(interaction, "ticket_link_already", ticket_id=ticket_id.strip()),
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        await interaction.response.send_message(
            _t(interaction, "ticket_link_success", ticket_id=ticket["id"], related_id=ticket_id.strip()),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @verknuepfen.autocomplete("ticket_id")
    async def verknuepfen_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Suggest tickets from this guild only; never offer the currently open ticket itself."""
        ticket = current_ticket(interaction)
        if ticket is None or interaction.guild is None or not may_manage(interaction):
            return []
        needle = str(current or "").strip().casefold()
        try:
            candidates = pulse_db.list_tickets(limit=2000)
        except (sqlite3.Error, OSError):
            return []
        choices = []
        for candidate in candidates:
            ticket_id = str(candidate.get("id") or "")
            if not ticket_id or ticket_id == str(ticket.get("id")):
                continue
            if str(candidate.get("guild_id") or "") != str(interaction.guild.id):
                continue
            parts = [
                ticket_id,
                str(candidate.get("category") or ""),
                str(candidate.get("status") or ""),
                str(candidate.get("user_name") or ""),
            ]
            if needle and needle not in " ".join(parts).casefold():
                continue
            label = " · ".join(part for part in parts if part)[:100]
            choices.append(app_commands.Choice(name=label, value=ticket_id[:100]))
            if len(choices) >= 25:
                break
        return choices

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
                _t(interaction, "ticket_links_empty"),
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
            destination = f"<#{channel_id}>" if channel_id.isdigit() else _t(interaction, "ticket_channel_unavailable")
            note = discord.utils.escape_mentions(
                discord.utils.escape_markdown(str(link.get("link_note") or ""))
            )
            line = f"**{related_id}** · {category} · {status} · {priority} · {destination}"
            if note:
                line += f"\n_{note[:300]}_"
            blocks.append(line)
        embed = discord.Embed(
            title=_t(interaction, "ticket_links_title", ticket_id=ticket["id"]),
            description="\n\n".join(blocks)[:4096],
            color=0x64748B,
        )
        embed.set_footer(text=_t(interaction, "ticket_links_footer", page=seite, more=_t(interaction, "ticket_links_more") if has_more else ""))
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
            _t(interaction, "ticket_unlink_success") if removed else _t(interaction, "ticket_unlink_missing"),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @trennen.autocomplete("ticket_id")
    async def trennen_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Suggest only tickets already linked to this ticket."""
        ticket = current_ticket(interaction)
        if ticket is None or interaction.guild is None or not may_manage(interaction):
            return []
        needle = str(current or "").strip().casefold()
        try:
            linked = tx.linked_tickets(ticket["id"], limit=11)
        except Exception:
            return []
        choices = []
        for candidate in linked:
            ticket_id = str(candidate.get("related_ticket_id") or "")
            parts = [
                ticket_id,
                str(candidate.get("category") or ""),
                str(candidate.get("status") or ""),
                str(candidate.get("user_name") or ""),
            ]
            if needle and needle not in " ".join(parts).casefold():
                continue
            label = " · ".join(part for part in parts if part)[:100]
            if ticket_id:
                choices.append(app_commands.Choice(name=label, value=ticket_id[:100]))
            if len(choices) >= 25:
                break
        return choices

    @app_commands.command(name="zusammenfassung", description="Eine lokale Übergabe-Zusammenfassung des Ticketverlaufs erstellen.")
    @app_commands.describe(speichern="Die Zusammenfassung zusätzlich als interne Ticketnotiz ablegen")
    async def zusammenfassung(self, interaction: discord.Interaction, speichern: bool = False):
        ticket = await self._guard(interaction)
        if ticket is None:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            messages = await ps.channel_messages(interaction.channel)
        except (discord.Forbidden, discord.HTTPException):
            await interaction.followup.send(
                _t(interaction, "ticket_summary_read_error"),
                ephemeral=True,
            )
            return

        # One shared, local summary for Discord and the dashboard (pulse_summaries) – no external AI.
        summary = ps.summarize_ticket(messages, ticket=ticket, events=pulse_db.ticket_events(ticket["id"], 50))
        if not summary["message_count"]:
            await interaction.followup.send(
                _t(interaction, "ticket_summary_empty"),
                ephemeral=True,
            )
            return

        def clean(text: str) -> str:
            return discord.utils.escape_mentions(discord.utils.escape_markdown(str(text)))

        sections = ps.ticket_sections(summary)
        embed = discord.Embed(
            title=_t(interaction, "ticket_summary_title", ticket_id=ticket["id"]),
            description=("**" + sections[0][0] + "**\n" + "\n".join("• " + clean(line) for line in sections[0][1]))[:3500],
            color=0x64748B,
        )
        for title, lines in sections[1:]:
            embed.add_field(name=title, value="\n".join("• " + clean(line) for line in lines)[:1000] or "–", inline=False)
        embed.add_field(
            name="Umfang",
            value=f"{summary['message_count']} Nachrichten · {summary['attachment_count']} Anhänge",
            inline=True,
        )
        embed.set_footer(text=ps.TICKET_NOTICE)
        saved_text = ""
        if speichern:
            note_text = ("Automatische Übergabe-Zusammenfassung (bitte vor Weitergabe prüfen)\n"
                         + ps.ticket_summary_text(summary))[:tx.MAX_INTERNAL_NOTE_LENGTH]
            try:
                tx.add_internal_note(
                    ticket["id"],
                    interaction.user.id,
                    interaction.user.display_name,
                    note_text,
                )
                saved_text = _t(interaction, "ticket_summary_saved")
            except ValueError as exc:
                saved_text = _t(interaction, "ticket_summary_save_error", error=str(exc))
        await interaction.followup.send(
            embed=embed,
            content=saved_text or None,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="aehnliche", description="Ähnliche, bereits archivierte Tickets anhand ihrer Verläufe finden.")
    async def aehnliche(self, interaction: discord.Interaction):
        ticket = await self._guard(interaction)
        if ticket is None:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        query_parts = []
        total_chars = 0
        try:
            async for message in interaction.channel.history(limit=50, oldest_first=False):
                if getattr(message.author, "bot", False):
                    continue
                text = str(getattr(message, "content", "") or "").strip()
                if not text:
                    continue
                remaining = tx.SIMILAR_MAX_QUERY_CHARS - total_chars
                if remaining <= 0:
                    break
                text = text[:min(1200, remaining)]
                query_parts.append(text)
                total_chars += len(text)
        except (discord.Forbidden, discord.HTTPException):
            await interaction.followup.send(
                "Der Ticketverlauf konnte nicht gelesen werden. Bitte die Kanal-Berechtigungen prüfen.",
                ephemeral=True,
            )
            return

        matches = tx.similar_tickets(
            ticket["id"],
            interaction.guild.id,
            "\n".join(query_parts),
            limit=5,
        )
        if not matches:
            await interaction.followup.send(
                _t(interaction, "ticket_similar_empty"),
                ephemeral=True,
            )
            return

        lines = []
        for item in matches:
            ticket_id = discord.utils.escape_markdown(item["ticket_id"])
            category = discord.utils.escape_markdown(item["category"] or "–")
            status = discord.utils.escape_markdown(item["status"] or "geschlossen")
            customer = discord.utils.escape_markdown(item["user_name"] or "Unbekannt")[:80]
            channel_id = str(item.get("channel_id") or "")
            channel_ref = f"<#{channel_id}>" if channel_id.isdigit() else "Kanal nicht verfügbar"
            percent = round(float(item["score"]) * 100)
            lines.append(
                f"**{ticket_id}** · {category} · {status} · {percent}% Übereinstimmung\n"
                f"{customer} · {channel_ref} · {item['shared_term_count']} gemeinsame Begriffe"
            )
        embed = discord.Embed(
            title=_t(interaction, "ticket_similar_title", ticket_id=ticket["id"]),
            description="\n\n".join(lines)[:4096],
            color=0x64748B,
        )
        embed.set_footer(text=_t(interaction, "ticket_similar_footer"))
        await interaction.followup.send(
            embed=embed,
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
            await deny(interaction, _t(interaction, "ticket_quick_reply_missing"))
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
            await deny(interaction, _t(interaction, "ticket_handover_permission"))
            return
        ok, message = tx.handover(ticket["id"], mitglied.id, mitglied.display_name, interaction.user.id, interaction.user.display_name, notiz)
        if not ok:
            await deny(interaction, message)
            return
        await interaction.response.send_message(
            _t(interaction, "ticket_handover_title", assignee=mitglied.mention, actor=interaction.user.mention)
            + "\n"
            + _t(interaction, "ticket_handover_note", note=notiz.strip()[:1500]),
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
            await deny(interaction, _t(interaction, "ticket_ban_team_only"))
            return
        import webserver
        team_ids = {int(x) for x in webserver.load_config().get("team_role_ids", []) if str(x).isdigit()}
        if mitglied.bot or mitglied.id == interaction.user.id or any(r.id in team_ids for r in getattr(mitglied, "roles", [])):
            # review fix: ticket bans are for citizens – not for the team, bots or oneself
            await deny(interaction, _t(interaction, "ticket_ban_citizen_only"))
            return
        try:
            entry = tx.block(mitglied.id, tage, grund, interaction.user.display_name)
        except ValueError as exc:
            await deny(interaction, str(exc))
            return
        webserver.log_audit(interaction.user.display_name, str(interaction.user.id), "Ticket-Sperre",
                            f"{mitglied.display_name} ({mitglied.id}) bis {entry['until'][:10]}: {entry['reason']}")
        await interaction.response.send_message(_t(interaction, "ticket_ban_until", member=mitglied.display_name, date=entry["until"][:10]),
                                                ephemeral=True)

    @app_commands.command(name="entsperren", description="Ticket-Sperre einer Person aufheben.")
    async def entsperren(self, interaction: discord.Interaction, mitglied: discord.Member):
        if not may_manage(interaction):
            await deny(interaction, _t(interaction, "ticket_unban_team_only"))
            return
        done = tx.unblock(mitglied.id)
        if done:
            import webserver
            webserver.log_audit(interaction.user.display_name, str(interaction.user.id), "Ticket-Sperre aufgehoben", str(mitglied.id))
        await interaction.response.send_message(_t(interaction, "ticket_unban_success") if done else _t(interaction, "ticket_unban_missing"), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(TicketTools(bot))
