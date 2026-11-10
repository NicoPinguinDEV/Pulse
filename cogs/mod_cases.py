"""BOT-09 (E-8): /mod warn|timeout|kick|ban|akte – citizen moderation with case file, proof, expiry,
DM with the appeal route and audit. Kick and ban need an explicit confirmation. Nothing here runs on
its own: every action is a person's decision (no automatic sanction).
"""
from __future__ import annotations

from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

import pulse_mod_cases as mc
from pulse_interactions import PulseView


def pulse_perms(guild, user_id):
    import webserver
    perms, _ = webserver.compute_perms(guild, user_id, webserver.load_config())
    return perms

MODERATION_ACTION_CHOICES = [
    app_commands.Choice(name="Verwarnung", value="warn"),
    app_commands.Choice(name="Timeout", value="timeout"),
    app_commands.Choice(name="Kick", value="kick"),
    app_commands.Choice(name="Ban", value="ban"),
]


class ConfirmAction(PulseView):
    def __init__(self, owner_id: int, run):
        super().__init__(timeout=60)
        self.owner_id, self.run, self.used = owner_id, run, False

    @discord.ui.button(label="Bestätigen", style=discord.ButtonStyle.danger, emoji="✅", custom_id="pulse_mod:confirm")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id or self.used:
            await interaction.response.send_message("Nur wer die Aktion gestartet hat, kann sie bestätigen.", ephemeral=True)
            return
        self.used = True
        self.stop()
        await interaction.response.edit_message(content="⏳ Wird ausgeführt …", view=None)
        await self.run(interaction)

    @discord.ui.button(label="Abbrechen", style=discord.ButtonStyle.secondary, emoji="❌", custom_id="pulse_mod:cancel")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Nur wer die Aktion gestartet hat, kann sie abbrechen.", ephemeral=True)
            return
        self.used = True
        self.stop()
        await interaction.response.edit_message(content="❌ Abgebrochen – nichts wurde ausgeführt.", view=None)


class ModCases(commands.GroupCog, group_name="mod", group_description="Bürger-Moderation mit Fallakte"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        super().__init__()

    async def _check(self, interaction: discord.Interaction, action: str, target: discord.Member) -> bool:
        import webserver
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("❌ Nur auf dem Server.", ephemeral=True)
            return False
        if not mc.may_moderate(action, pulse_perms(interaction.guild, interaction.user.id), interaction.user.guild_permissions):
            await interaction.response.send_message(
                f"❌ Für **{mc.ACTIONS[action]}** brauchst du das Pulse-Recht und das passende Discord-Recht.", ephemeral=True)
            return False
        problem = mc.target_problem(interaction.user, target, interaction.guild, webserver.load_config().get("team_role_ids", []))
        if problem:
            await interaction.response.send_message("❌ " + problem, ephemeral=True)
            return False
        return True

    async def _execute(self, interaction: discord.Interaction, action: str, target: discord.Member, grund: str,
                       beweis: str, minutes: int | None, respond, vorlage: str = "") -> None:
        """Creates the case, snapshots a selected template revision, sends the DM and performs the action."""
        import webserver
        template = None
        reason = grund
        if str(vorlage or "").strip():
            template = mc.get_reason_template(interaction.guild.id, action, vorlage)
            if template is None:
                await respond("❌ Aktive Moderationsvorlage nicht gefunden. Bitte /mod vorlagen prüfen.")
                return
            try:
                reason = mc.render_reason_template(template, grund)
            except ValueError as exc:
                await respond("❌ " + str(exc))
                return
        try:
            case = mc.create_case(interaction.guild.id, target, action, reason, beweis, minutes, interaction.user, template=template)
        except ValueError as exc:
            await respond("❌ " + str(exc))
            return
        appeal = (mc.appeal_text(interaction.guild.id, case["id"]) if action != "ban"
                  else f"Schreibe dem Serverteam per Direktnachricht und nenne die Fall-ID {case['id']}.")
        dm_ok = False

        async def send_dm() -> bool:
            try:
                await target.send(mc.dm_text(case, interaction.guild.name, appeal))
                mc.mark(case["id"], dm_sent=1)
                return True
            except (discord.Forbidden, discord.HTTPException):
                return False
        if action in ("kick", "ban"):  # afterwards the person can no longer be reached
            dm_ok = await send_dm()
        reason = f"{case['id']} · {interaction.user.display_name}: {case['reason']}"[:512]
        try:
            if action == "timeout":
                await target.timeout(timedelta(minutes=minutes), reason=reason)
            elif action == "kick":
                await target.kick(reason=reason)
            elif action == "ban":
                await interaction.guild.ban(target, reason=reason, delete_message_seconds=0)
        except (discord.Forbidden, discord.HTTPException) as exc:
            mc.mark(case["id"], status="failed", close_note=type(exc).__name__)
            await respond(f"❌ Discord hat die Aktion verweigert ({type(exc).__name__}). Fall {case['id']} ist als fehlgeschlagen vermerkt.")
            return
        if action in ("warn", "timeout"):  # review fix: no "Timeout"-DM when Discord refused the timeout
            dm_ok = await send_dm()
        webserver.log_audit(interaction.user.display_name, str(interaction.user.id), f"Bürger-Moderation: {mc.ACTIONS[action]}",
                            f"{case['id']} · {target.display_name} ({target.id}) · {case['reason']}"
                            + (f" · bis {case['expires_at']}" if case.get("expires_at") else "")
                            + (f" · Vorlage {template['name']} v{template['active_version']}" if template else ""))
        await respond(f"✅ {mc.ACTIONS[action]} für **{target.display_name}** – Fall `{case['id']}`"
                      + (f", bis {case['expires_at'][:16].replace('T', ' ')} UTC" if case.get("expires_at") else "")
                      + (f" · Vorlage {template['name']} v{template['active_version']}" if template else "")
                      + ("" if dm_ok else " · ⚠️ DM nicht zustellbar (DMs geschlossen)"))

    @app_commands.command(name="warn", description="Bürger verwarnen (Fallakte, DM mit Einspruchsweg).")
    @app_commands.describe(mitglied="Wer", grund="Pflichtgrund oder Zusatzdetails", beweis="Link zum Beweis (optional)", vorlage="Aktive Begründungsvorlage (optional)")
    async def warn(self, interaction: discord.Interaction, mitglied: discord.Member, grund: str, beweis: str = "", vorlage: str = ""):
        if not await self._check(interaction, "warn", mitglied):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self._execute(interaction, "warn", mitglied, grund, beweis, None,
                            lambda text: interaction.followup.send(text, ephemeral=True), vorlage)

    @app_commands.command(name="timeout", description="Bürger stummschalten (Timeout mit Ablauf).")
    @app_commands.describe(mitglied="Wer", minuten="Dauer in Minuten (1 bis 40320 = 28 Tage)", grund="Pflichtgrund oder Zusatzdetails", beweis="Link zum Beweis (optional)", vorlage="Aktive Begründungsvorlage (optional)")
    async def timeout(self, interaction: discord.Interaction, mitglied: discord.Member,
                      minuten: app_commands.Range[int, 1, mc.MAX_TIMEOUT_MINUTES], grund: str, beweis: str = "", vorlage: str = ""):
        if not await self._check(interaction, "timeout", mitglied):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self._execute(interaction, "timeout", mitglied, grund, beweis, minuten,
                            lambda text: interaction.followup.send(text, ephemeral=True), vorlage)

    async def _confirm_then(self, interaction, action, mitglied, grund, beweis, minutes, vorlage: str = ""):
        async def run(button_interaction):
            import webserver
            # review fix: up to 60 s may have passed – check rights and rank again before acting
            problem = ("" if mc.may_moderate(action, pulse_perms(interaction.guild, interaction.user.id), interaction.user.guild_permissions)
                       else "Dir fehlt inzwischen das nötige Recht.") or mc.target_problem(
                interaction.user, mitglied, interaction.guild, webserver.load_config().get("team_role_ids", []))
            if problem:
                await button_interaction.edit_original_response(content="❌ " + problem + " Es wurde nichts ausgeführt.")
                return
            await self._execute(interaction, action, mitglied, grund, beweis, minutes,
                                lambda text: button_interaction.edit_original_response(content=text), vorlage)
        label = mc.ACTIONS[action]
        await interaction.response.send_message(
            f"⚠️ **{label}** für {mitglied.mention} wirklich ausführen?\nGrund: {grund.strip()[:500]}"
            + (f"\nDauer: {minutes // 1440} Tage" if minutes else "")
            + (f"\nVorlage: {vorlage}" if vorlage else "") + "\nDie Person bekommt vorher eine DM mit der Fall-ID.",
            view=ConfirmAction(interaction.user.id, run), ephemeral=True)

    @app_commands.command(name="kick", description="Bürger vom Server kicken (mit Bestätigung und Fallakte).")
    @app_commands.describe(grund="Pflichtgrund oder Zusatzdetails", beweis="Link zum Beweis (optional)", vorlage="Aktive Begründungsvorlage (optional)")
    async def kick(self, interaction: discord.Interaction, mitglied: discord.Member, grund: str, beweis: str = "", vorlage: str = ""):
        if not await self._check(interaction, "kick", mitglied):
            return
        await self._confirm_then(interaction, "kick", mitglied, grund, beweis, None, vorlage)

    @app_commands.command(name="ban", description="Bürger bannen (mit Bestätigung, optional befristet).")
    @app_commands.describe(tage="0 = unbefristet, sonst Tage bis zur automatischen Aufhebung", grund="Pflichtgrund oder Zusatzdetails", beweis="Link zum Beweis (optional)", vorlage="Aktive Begründungsvorlage (optional)")
    async def ban(self, interaction: discord.Interaction, mitglied: discord.Member, grund: str,
                  tage: app_commands.Range[int, 0, 365] = 0, beweis: str = "", vorlage: str = ""):
        if not await self._check(interaction, "ban", mitglied):
            return
        await self._confirm_then(interaction, "ban", mitglied, grund, beweis, tage * 1440 if tage else None, vorlage)


    @app_commands.command(name="vorlagen", description="Aktive Begründungsvorlagen für deine Moderationsrechte anzeigen.")
    async def vorlagen(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("❌ Nur auf dem Server verfügbar.", ephemeral=True)
            return
        perms = pulse_perms(interaction.guild, interaction.user.id)
        available = [
            action for action in mc.ACTIONS
            if mc.may_moderate(action, perms, interaction.user.guild_permissions)
        ]
        if not available:
            await interaction.response.send_message("❌ Du hast keine Moderationsrechte für Vorlagen.", ephemeral=True)
            return
        templates = mc.list_reason_templates(interaction.guild.id, actions=available)
        if not templates:
            await interaction.response.send_message("Für deine Moderationsrechte sind keine aktiven Vorlagen vorhanden.", ephemeral=True)
            return
        lines = []
        for item in templates[:25]:
            name = discord.utils.escape_markdown(str(item["name"]))
            body = discord.utils.escape_markdown(str(item["body"]))
            label = mc.ACTIONS.get(item["action"], item["action"])
            lines.append(f"**{label} · {name}** · v{item['active_version']}\n{body[:180]}")
        embed = discord.Embed(title="📚 Moderationsvorlagen", description="\n\n".join(lines)[:3900], color=0x64748B)
        embed.set_footer(text="Beim Moderieren den Vorlagennamen im optionalen Feld 'vorlage' angeben.")
        await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @app_commands.choices(aktion=MODERATION_ACTION_CHOICES)
    @app_commands.command(name="vorlage_erstellen", description="Neue versionierte Moderationsvorlage anlegen (Admin).")
    @app_commands.describe(aktion="Moderationsaktion", name="Eindeutiger Vorlagenname", text="Vorlagentext; optionaler Platzhalter: {details}")
    async def vorlage_erstellen(self, interaction: discord.Interaction, aktion: str, name: str, text: str):
        if interaction.guild is None or not mc.may_manage_templates(pulse_perms(interaction.guild, interaction.user.id)):
            await interaction.response.send_message("❌ Nur Administratoren dürfen Moderationsvorlagen verwalten.", ephemeral=True)
            return
        try:
            template = mc.create_reason_template(interaction.guild.id, aktion, name, text, interaction.user)
        except ValueError as exc:
            await interaction.response.send_message("❌ " + str(exc), ephemeral=True)
            return
        await interaction.response.send_message("✅ Vorlage **" + discord.utils.escape_markdown(template["name"]) + "** v1 wurde erstellt.", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @app_commands.choices(aktion=MODERATION_ACTION_CHOICES)
    @app_commands.command(name="vorlage_bearbeiten", description="Neue unveränderliche Version einer Moderationsvorlage speichern (Admin).")
    @app_commands.describe(aktion="Moderationsaktion", name="Name der bestehenden Vorlage", text="Neuer Vorlagentext; optionaler Platzhalter: {details}")
    async def vorlage_bearbeiten(self, interaction: discord.Interaction, aktion: str, name: str, text: str):
        if interaction.guild is None or not mc.may_manage_templates(pulse_perms(interaction.guild, interaction.user.id)):
            await interaction.response.send_message("❌ Nur Administratoren dürfen Moderationsvorlagen verwalten.", ephemeral=True)
            return
        try:
            template = mc.revise_reason_template(interaction.guild.id, aktion, name, text, interaction.user)
        except ValueError as exc:
            await interaction.response.send_message("❌ " + str(exc), ephemeral=True)
            return
        await interaction.response.send_message("✅ Neue Version v" + str(template["active_version"]) + " von **" + discord.utils.escape_markdown(template["name"]) + "** gespeichert.", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @app_commands.choices(aktion=MODERATION_ACTION_CHOICES)
    @app_commands.command(name="vorlage_archivieren", description="Moderationsvorlage archivieren, ohne Historie zu löschen (Admin).")
    @app_commands.describe(aktion="Moderationsaktion", name="Name der Vorlage")
    async def vorlage_archivieren(self, interaction: discord.Interaction, aktion: str, name: str):
        if interaction.guild is None or not mc.may_manage_templates(pulse_perms(interaction.guild, interaction.user.id)):
            await interaction.response.send_message("❌ Nur Administratoren dürfen Moderationsvorlagen verwalten.", ephemeral=True)
            return
        archived = mc.archive_reason_template(interaction.guild.id, aktion, name, interaction.user)
        await interaction.response.send_message("✅ Vorlage archiviert; alle bisherigen Versionen bleiben erhalten." if archived else "❌ Aktive Vorlage nicht gefunden.", ephemeral=True)

    @app_commands.choices(aktion=MODERATION_ACTION_CHOICES)
    @app_commands.command(name="vorlage_historie", description="Frühere Versionen einer Moderationsvorlage anzeigen (Admin).")
    @app_commands.describe(aktion="Moderationsaktion", name="Name der Vorlage")
    async def vorlage_historie(self, interaction: discord.Interaction, aktion: str, name: str):
        if interaction.guild is None or not mc.may_manage_templates(pulse_perms(interaction.guild, interaction.user.id)):
            await interaction.response.send_message("❌ Nur Administratoren dürfen die Vorlagenhistorie einsehen.", ephemeral=True)
            return
        history = mc.list_reason_template_versions(interaction.guild.id, aktion, name, limit=10)
        if not history:
            await interaction.response.send_message("❌ Für diese Vorlage wurde keine Historie gefunden.", ephemeral=True)
            return
        lines = [
            f"**v{row['version']}** · {row['created_at'][:16].replace('T', ' ')} · {row['created_by']}\n{discord.utils.escape_markdown(row['body'])[:350]}"
            for row in history
        ]
        embed = discord.Embed(title="🕘 Vorlagenhistorie · " + discord.utils.escape_markdown(name), description="\n\n".join(lines)[:3900], color=0x64748B)
        await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="akte", description="Moderationsfälle einer Person anzeigen.")
    async def akte(self, interaction: discord.Interaction, mitglied: discord.User):
        perms = pulse_perms(interaction.guild, interaction.user.id) if interaction.guild else {}
        if not (perms.get("is_admin") or perms.get("can_moderate_kicks") or perms.get("can_moderate_bans")):
            await interaction.response.send_message("❌ Kein Zugriff auf die Fallakte.", ephemeral=True)
            return
        cases = mc.list_cases(mitglied.id, limit=15)
        lines = [f"`{c['id']}` · {mc.ACTIONS.get(c['action'], c['action'])} · {c['created_at'][:10]} · {c['status']} · {c['reason'][:80]}"
                 for c in cases] or ["Keine Fälle."]
        await interaction.response.send_message(f"📁 **Fallakte {mitglied}**\n" + "\n".join(lines), ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())


async def lift_expired_bans(bot) -> int:
    """Called by the existing automation loop: lifts bans whose expiry has passed (no new loop)."""
    lifted = 0
    for case in mc.expired_bans():
        guild = bot.get_guild(int(case["guild_id"]))
        if guild is None:
            continue
        now = mc._now().isoformat(timespec="seconds")
        target = discord.Object(id=int(case["target_id"]))
        try:
            ban = await guild.fetch_ban(target)
            # Review fix: if the person was unbanned and banned again outside this case, that newer ban is not ours.
            if not str(getattr(ban, "reason", "") or "").startswith(case["id"]):
                mc.mark(case["id"], status="expired", closed_at=now, closed_by="Pulse (Ban wurde zwischenzeitlich ersetzt)")
                continue
            await guild.unban(target, reason=f"{case['id']}: befristeter Ban abgelaufen")
            mc.mark(case["id"], status="expired", closed_at=now, closed_by="Pulse (Ablauf)")
            lifted += 1
        except discord.NotFound:
            mc.mark(case["id"], status="expired", closed_at=now, closed_by="Pulse (bereits entbannt)")
        except discord.Forbidden:
            # no retry every minute: the case is marked and shows up in the case file
            mc.mark(case["id"], status="failed", close_note="Ablauf: Pulse darf nicht entbannen (Rechte prüfen)")
            print(f"⚠️ Bürger-Moderation: Ban {case['id']} konnte nicht aufgehoben werden (Forbidden) – Fall als fehlgeschlagen markiert.")
        except discord.HTTPException as exc:
            print(f"⚠️ Bürger-Moderation: Ban {case['id']} konnte nicht aufgehoben werden ({type(exc).__name__}).")
    return lifted


async def setup(bot: commands.Bot):
    await bot.add_cog(ModCases(bot))
