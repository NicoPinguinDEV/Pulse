"""BOT-09 (E-8): /mod warn|timeout|kick|ban|akte – citizen moderation with case file, proof, expiry,
DM with the appeal route and audit. Kick and ban need an explicit confirmation. Nothing here runs on
its own: every action is a person's decision (no automatic sanction).
"""
from __future__ import annotations

from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

import pulse_db
import pulse_mod_cases as mc
import pulse_mod_reason_templates as mrt
from pulse_interactions import PulseView


def _reason_template_choices(interaction: discord.Interaction, action: str, current: str):
    if interaction.guild is None:
        return []
    perms = pulse_perms(interaction.guild, interaction.user.id)
    if not mc.may_moderate(action, perms, getattr(interaction.user, "guild_permissions", None)):
        return []
    needle = str(current or "").casefold().strip()
    templates = mrt.list_templates(interaction.guild.id, action, limit=100)
    return [
        app_commands.Choice(
            name=f"{item['title'][:76]} · v{item['current_version']}",
            value=mrt.selection_token(item["id"], item["current_version"]),
        )
        for item in templates
        if not needle or needle in str(item["title"]).casefold()
    ][:25]




def pulse_perms(guild, user_id):
    import webserver
    perms, _ = webserver.compute_perms(guild, user_id, webserver.load_config())
    return perms


class AppealModal(discord.ui.Modal):
    """Private DM form for the affected member; usable even after a kick or ban."""

    def __init__(self, case_id: str, guild_id: int, target_id: int, bot):
        super().__init__(title="Moderationsentscheidung anfechten", timeout=300)
        self.case_id = case_id
        self.guild_id = int(guild_id)
        self.target_id = int(target_id)
        self.bot = bot
        self.reason = discord.ui.TextInput(
            label="Warum möchtest du Einspruch einlegen?",
            placeholder="Beschreibe sachlich, warum die Entscheidung geprüft werden sollte …",
            style=discord.TextStyle.paragraph,
            min_length=mc.APPEAL_REASON_MIN_LENGTH,
            max_length=mc.APPEAL_REASON_MAX_LENGTH,
            required=True,
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        if int(interaction.user.id) != self.target_id:
            await interaction.response.send_message(
                "❌ Dieser Einspruch gehört zu einem anderen Discord-Konto.", ephemeral=True
            )
            return
        try:
            appeal = mc.submit_appeal(
                self.guild_id, interaction.user.id, self.case_id, self.reason.value
            )
        except ValueError as exc:
            await interaction.response.send_message("❌ " + str(exc), ephemeral=True)
            return

        safe_reason = discord.utils.escape_mentions(self.reason.value[:1000])
        try:
            import webserver
            webserver.log_audit(
                interaction.user.display_name,
                str(interaction.user.id),
                "Moderationseinspruch eingereicht",
                f"{self.case_id} · {safe_reason}",
            )
            guild = self.bot.get_guild(self.guild_id)
            if guild is not None:
                config = webserver.load_config()
                for member in list(getattr(guild, "members", []) or [])[:5000]:
                    if getattr(member, "bot", False):
                        continue
                    perms, _ = webserver.compute_perms(guild, member.id, config)
                    if perms.get("is_admin") or perms.get("can_moderate_kicks") or perms.get("can_moderate_bans"):
                        pulse_db.notify(
                            member.id,
                            "⚖ Neuer Moderationseinspruch",
                            f"Fall {self.case_id}: {safe_reason}",
                            "warning",
                            "/mod-cases",
                            f"mod-appeal:{self.case_id}",
                            7 * 86400,
                        )
        except Exception as exc:
            print(f"⚠️ Moderationseinspruch {self.case_id}: Benachrichtigung fehlgeschlagen ({type(exc).__name__}).")

        deadline = appeal["deadline_at"][:16].replace("T", " ")
        await interaction.response.send_message(
            f"✅ Dein Einspruch für Fall **{self.case_id}** wurde eingereicht. "
            f"Die Leitung prüft ihn. Einreichungsfrist: {deadline} UTC. "
            "Die ursprüngliche Maßnahme bleibt bis zu einer gesonderten Entscheidung unverändert.",
            ephemeral=True,
        )


class AppealButton(discord.ui.Button):
    def __init__(self, case_id: str, guild_id: int, target_id: int, bot):
        super().__init__(
            label="Einspruch einreichen",
            style=discord.ButtonStyle.primary,
            emoji="📝",
            custom_id=f"pulse_mod:appeal:{case_id}",
        )
        self.case_id = case_id
        self.guild_id = int(guild_id)
        self.target_id = int(target_id)
        self.bot = bot

    async def callback(self, interaction: discord.Interaction):
        if int(interaction.user.id) != self.target_id:
            await interaction.response.send_message(
                "❌ Nur die betroffene Person kann zu diesem Fall Einspruch einlegen.", ephemeral=True
            )
            return
        try:
            mc.can_submit_appeal(self.guild_id, interaction.user.id, self.case_id)
        except ValueError as exc:
            await interaction.response.send_message("❌ " + str(exc), ephemeral=True)
            return
        await interaction.response.send_modal(
            AppealModal(self.case_id, self.guild_id, self.target_id, self.bot)
        )


class AppealView(discord.ui.View):
    """Persistent view so the appeal button in older DMs survives bot restarts."""

    def __init__(self, case_id: str, guild_id: int, target_id: int, bot):
        super().__init__(timeout=None)
        self.add_item(AppealButton(case_id, guild_id, target_id, bot))


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

    async def _execute(
        self,
        interaction: discord.Interaction,
        action: str,
        target: discord.Member,
        grund: str,
        beweis: str,
        minutes: int | None,
        respond,
        reason_template_id: str | None = None,
        reason_template_version: int | None = None,
    ) -> None:
        """Creates the case, sends the DM first (kick/ban remove the person), then acts on Discord."""
        import webserver
        try:
            case = mc.create_case(interaction.guild.id, target, action, grund, beweis, minutes, interaction.user,
                                 reason_template_id=reason_template_id, reason_template_version=reason_template_version)
        except ValueError as exc:
            await respond("❌ " + str(exc))
            return
        appeal = mc.appeal_text(interaction.guild.id, case["id"])
        dm_ok = False

        async def send_dm() -> bool:
            try:
                await target.send(
                    mc.dm_text(case, interaction.guild.name, appeal),
                    view=AppealView(case["id"], interaction.guild.id, target.id, self.bot),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
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
                            + (f" · bis {case['expires_at']}" if case.get("expires_at") else ""))
        await respond(f"✅ {mc.ACTIONS[action]} für **{target.display_name}** – Fall `{case['id']}`"
                      + (f", bis {case['expires_at'][:16].replace('T', ' ')} UTC" if case.get("expires_at") else "")
                      + ("" if dm_ok else " · ⚠️ DM nicht zustellbar (DMs geschlossen)"))

    @app_commands.command(name="warn", description="Bürger verwarnen (Fallakte, DM mit Einspruchsweg).")
    @app_commands.describe(mitglied="Wer", grund="Pflichtgrund", beweis="Link zum Beweis (optional)")
    async def warn(self, interaction: discord.Interaction, mitglied: discord.Member, grund: str, beweis: str = ""):
        if not await self._check(interaction, "warn", mitglied):
            return
        try:
            grund, template_id, template_version = mrt.resolve_reason(interaction.guild.id, "warn", grund)
        except ValueError as exc:
            await interaction.response.send_message("❌ " + str(exc), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self._execute(
            interaction, "warn", mitglied, grund, beweis, None,
            lambda text: interaction.followup.send(text, ephemeral=True),
            template_id, template_version,
        )

    @warn.autocomplete("grund")
    async def warn_reason_autocomplete(self, interaction: discord.Interaction, current: str):
        return _reason_template_choices(interaction, "warn", current)

    @app_commands.command(name="timeout", description="Bürger stummschalten (Timeout mit Ablauf).")
    @app_commands.describe(mitglied="Wer", minuten="Dauer in Minuten (1 bis 40320 = 28 Tage)", grund="Pflichtgrund", beweis="Link zum Beweis (optional)")
    async def timeout(self, interaction: discord.Interaction, mitglied: discord.Member,
                      minuten: app_commands.Range[int, 1, mc.MAX_TIMEOUT_MINUTES], grund: str, beweis: str = ""):
        if not await self._check(interaction, "timeout", mitglied):
            return
        try:
            grund, template_id, template_version = mrt.resolve_reason(interaction.guild.id, "timeout", grund)
        except ValueError as exc:
            await interaction.response.send_message("❌ " + str(exc), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self._execute(
            interaction, "timeout", mitglied, grund, beweis, minuten,
            lambda text: interaction.followup.send(text, ephemeral=True),
            template_id, template_version,
        )

    @timeout.autocomplete("grund")
    async def timeout_reason_autocomplete(self, interaction: discord.Interaction, current: str):
        return _reason_template_choices(interaction, "timeout", current)

    async def _confirm_then(self, interaction, action, mitglied, grund, beweis, minutes, template_id=None, template_version=None):
        async def run(button_interaction):
            import webserver
            # review fix: up to 60 s may have passed – check rights and rank again before acting
            problem = ("" if mc.may_moderate(action, pulse_perms(interaction.guild, interaction.user.id), interaction.user.guild_permissions)
                       else "Dir fehlt inzwischen das nötige Recht.") or mc.target_problem(
                interaction.user, mitglied, interaction.guild, webserver.load_config().get("team_role_ids", []))
            if problem:
                await button_interaction.edit_original_response(content="❌ " + problem + " Es wurde nichts ausgeführt.")
                return
            await self._execute(
                interaction, action, mitglied, grund, beweis, minutes,
                lambda text: button_interaction.edit_original_response(content=text),
                template_id, template_version,
            )
        label = mc.ACTIONS[action]
        await interaction.response.send_message(
            f"⚠️ **{label}** für {mitglied.mention} wirklich ausführen?\nGrund: {grund.strip()[:500]}"
            + (f"\nDauer: {minutes // 1440} Tage" if minutes else "") + "\nDie Person bekommt vorher eine DM mit der Fall-ID.",
            view=ConfirmAction(interaction.user.id, run), ephemeral=True)

    @app_commands.command(name="kick", description="Bürger vom Server kicken (mit Bestätigung und Fallakte).")
    async def kick(self, interaction: discord.Interaction, mitglied: discord.Member, grund: str, beweis: str = ""):
        if not await self._check(interaction, "kick", mitglied):
            return
        try:
            grund, template_id, template_version = mrt.resolve_reason(interaction.guild.id, "kick", grund)
        except ValueError as exc:
            await interaction.response.send_message("❌ " + str(exc), ephemeral=True)
            return
        await self._confirm_then(interaction, "kick", mitglied, grund, beweis, None, template_id, template_version)

    @kick.autocomplete("grund")
    async def kick_reason_autocomplete(self, interaction: discord.Interaction, current: str):
        return _reason_template_choices(interaction, "kick", current)

    @app_commands.command(name="ban", description="Bürger bannen (mit Bestätigung, optional befristet).")
    @app_commands.describe(tage="0 = unbefristet, sonst Tage bis zur automatischen Aufhebung")
    async def ban(self, interaction: discord.Interaction, mitglied: discord.Member, grund: str,
                  tage: app_commands.Range[int, 0, 365] = 0, beweis: str = ""):
        if not await self._check(interaction, "ban", mitglied):
            return
        try:
            grund, template_id, template_version = mrt.resolve_reason(interaction.guild.id, "ban", grund)
        except ValueError as exc:
            await interaction.response.send_message("❌ " + str(exc), ephemeral=True)
            return
        await self._confirm_then(
            interaction, "ban", mitglied, grund, beweis, tage * 1440 if tage else None,
            template_id, template_version,
        )

    @ban.autocomplete("grund")
    async def ban_reason_autocomplete(self, interaction: discord.Interaction, current: str):
        return _reason_template_choices(interaction, "ban", current)

    def _template_admin_allowed(self, interaction: discord.Interaction) -> bool:
        return bool(interaction.guild and pulse_perms(interaction.guild, interaction.user.id).get("is_admin"))

    async def _require_template_admin(self, interaction: discord.Interaction) -> bool:
        if self._template_admin_allowed(interaction):
            return True
        await interaction.response.send_message(
            "❌ Nur Pulse-Administratoren dürfen Begründungsvorlagen erstellen, ändern oder archivieren.",
            ephemeral=True,
        )
        return False

    @app_commands.command(name="vorlagen", description="Aktive, versionierte Moderations-Begründungsvorlagen anzeigen.")
    @app_commands.describe(aktion="Optional nach Verwarnung, Timeout, Kick oder Ban filtern")
    @app_commands.choices(aktion=[
        app_commands.Choice(name="Alle Aktionen", value="all"),
        app_commands.Choice(name="Verwarnung", value="warn"),
        app_commands.Choice(name="Timeout", value="timeout"),
        app_commands.Choice(name="Kick", value="kick"),
        app_commands.Choice(name="Ban", value="ban"),
    ])
    async def vorlagen(self, interaction: discord.Interaction, aktion: app_commands.Choice[str] | None = None):
        if interaction.guild is None:
            await interaction.response.send_message("❌ Nur auf dem Server.", ephemeral=True)
            return
        perms = pulse_perms(interaction.guild, interaction.user.id)
        allowed_actions = [
            action for action in mrt.ACTIONS
            if mc.may_moderate(action, perms, interaction.user.guild_permissions)
        ]
        selected_action = aktion.value if aktion and aktion.value != "all" else None
        if selected_action and selected_action not in allowed_actions:
            await interaction.response.send_message("❌ Für diese Moderationsaktion fehlen dir die nötigen Rechte.", ephemeral=True)
            return
        if not allowed_actions:
            await interaction.response.send_message("❌ Kein Zugriff auf Moderationsvorlagen.", ephemeral=True)
            return
        actions_to_show = [selected_action] if selected_action else allowed_actions
        templates = [
            item
            for action in actions_to_show
            for item in mrt.list_templates(interaction.guild.id, action, limit=50)
        ][:50]
        lines = [
            f"`{item['id']}` · **{item['title']}** · {mc.ACTIONS[item['action']]} · v{item['current_version']}"
            for item in templates
        ]
        body = "\n".join(lines) if lines else "Für diese Auswahl sind noch keine aktiven Vorlagen vorhanden."
        await interaction.response.send_message(
            "📚 **Moderations-Begründungsvorlagen**\n" + body,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="vorlage_erstellen", description="Neue versionierte Begründungsvorlage anlegen (Admin).")
    @app_commands.describe(aktion="Für welche Moderationsaktion", titel="Interner Vorlagenname", text="Begründungstext (5–900 Zeichen)")
    @app_commands.choices(aktion=[
        app_commands.Choice(name="Verwarnung", value="warn"),
        app_commands.Choice(name="Timeout", value="timeout"),
        app_commands.Choice(name="Kick", value="kick"),
        app_commands.Choice(name="Ban", value="ban"),
    ])
    async def vorlage_erstellen(
        self,
        interaction: discord.Interaction,
        aktion: app_commands.Choice[str],
        titel: str,
        text: str,
    ):
        if not await self._require_template_admin(interaction):
            return
        try:
            item = mrt.create_template(
                interaction.guild.id, aktion.value, titel, text,
                interaction.user.id, interaction.user.display_name,
            )
        except ValueError as exc:
            await interaction.response.send_message("❌ " + str(exc), ephemeral=True)
            return
        import webserver
        webserver.log_audit(
            interaction.user.display_name,
            str(interaction.user.id),
            "Moderationsvorlage erstellt",
            f"{item['id']} · {item['action']} · v{item['version']} · {item['title']}",
        )
        await interaction.response.send_message(
            f"✅ Vorlage `{item['id']}` erstellt · {mc.ACTIONS[item['action']]} · Version 1. "
            "Sie steht ab sofort bei der Begründung per Autovervollständigung zur Auswahl.",
            ephemeral=True,
        )

    @app_commands.command(name="vorlage_aktualisieren", description="Neue unveränderliche Version einer Begründungsvorlage speichern (Admin).")
    @app_commands.describe(vorlage_id="ID aus /mod vorlagen", titel="Titel der neuen Version", text="Begründungstext der neuen Version")
    async def vorlage_aktualisieren(self, interaction: discord.Interaction, vorlage_id: str, titel: str, text: str):
        if not await self._require_template_admin(interaction):
            return
        try:
            item = mrt.update_template(
                interaction.guild.id, vorlage_id, titel, text,
                interaction.user.id, interaction.user.display_name,
            )
        except ValueError as exc:
            await interaction.response.send_message("❌ " + str(exc), ephemeral=True)
            return
        import webserver
        webserver.log_audit(
            interaction.user.display_name,
            str(interaction.user.id),
            "Moderationsvorlage aktualisiert",
            f"{item['id']} · {item['action']} · v{item['version']} · {item['title']}",
        )
        await interaction.response.send_message(
            f"✅ `{item['id']}` ist jetzt Version **{item['version']}**. "
            "Alle vorherigen Versionen bleiben erhalten.",
            ephemeral=True,
        )

    @app_commands.command(name="vorlage_archivieren", description="Eine Vorlage aus der Auswahl entfernen, ohne Versionen zu löschen (Admin).")
    @app_commands.describe(vorlage_id="ID aus /mod vorlagen")
    async def vorlage_archivieren(self, interaction: discord.Interaction, vorlage_id: str):
        if not await self._require_template_admin(interaction):
            return
        try:
            archived = mrt.archive_template(
                interaction.guild.id, vorlage_id,
                interaction.user.id, interaction.user.display_name,
            )
        except ValueError as exc:
            await interaction.response.send_message("❌ " + str(exc), ephemeral=True)
            return
        if not archived:
            await interaction.response.send_message("Diese aktive Vorlage wurde nicht gefunden.", ephemeral=True)
            return
        import webserver
        webserver.log_audit(
            interaction.user.display_name,
            str(interaction.user.id),
            "Moderationsvorlage archiviert",
            str(vorlage_id).strip().upper(),
        )
        await interaction.response.send_message(
            "✅ Vorlage archiviert. Bereits verwendete Versionen bleiben in den Fallakten erhalten.",
            ephemeral=True,
        )

    @app_commands.command(name="vorlage_versionen", description="Unveränderliche Versionshistorie einer Begründungsvorlage anzeigen.")
    @app_commands.describe(vorlage_id="ID aus /mod vorlagen")
    async def vorlage_versionen(self, interaction: discord.Interaction, vorlage_id: str):
        if interaction.guild is None:
            await interaction.response.send_message("❌ Nur auf dem Server.", ephemeral=True)
            return
        perms = pulse_perms(interaction.guild, interaction.user.id)
        if not perms.get("is_admin"):
            await interaction.response.send_message("❌ Nur Administratoren dürfen die Versionshistorie einsehen.", ephemeral=True)
            return
        versions = mrt.list_versions(interaction.guild.id, vorlage_id, limit=20)
        if not versions:
            await interaction.response.send_message("Diese Vorlage oder Versionshistorie wurde nicht gefunden.", ephemeral=True)
            return
        lines = [
            f"**v{row['version']} · {row['title']}** · {row['created_at'][:19].replace('T', ' ')} · {row['created_by_name']}"
            for row in versions
        ]
        await interaction.response.send_message(
            f"📚 **Versionshistorie `{vorlage_id.upper()}`**\n" + "\n".join(lines),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    def _appeal_reviewer_allowed(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            return False
        perms = pulse_perms(interaction.guild, interaction.user.id)
        return bool(
            perms.get("is_admin")
            or perms.get("can_moderate_kicks")
            or perms.get("can_moderate_bans")
        )

    @app_commands.command(name="einsprueche", description="Offene Moderationseinsprüche des Servers anzeigen.")
    async def einsprueche(self, interaction: discord.Interaction):
        if not self._appeal_reviewer_allowed(interaction):
            await interaction.response.send_message("❌ Dafür fehlt dir ein Moderationsrecht.", ephemeral=True)
            return
        rows = mc.list_appeals(interaction.guild.id, status="pending", limit=10)
        if not rows:
            await interaction.response.send_message("✅ Es liegen aktuell keine offenen Einsprüche vor.", ephemeral=True)
            return
        lines = [
            f"**{row['case_id']}** · <@{row['target_id']}> · "
            f"{row['submitted_at'][:16].replace('T', ' ')} UTC\n"
            f"{discord.utils.escape_markdown(row['reason'][:350])}"
            for row in rows
        ]
        await interaction.response.send_message(
            "⚖ **Offene Moderationseinsprüche**\n" + "\n\n".join(lines)
            + "\n\nEntscheide mit /mod einspruch-entscheiden.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="einspruch-entscheiden", description="Einen offenen Einspruch als Leitung entscheiden.")
    @app_commands.describe(
        fall_id="MOD-Fall-ID aus der Moderationsakte",
        entscheidung="Einspruch annehmen oder ablehnen",
        notiz="Interne Begründung für die Entscheidung (5 bis 500 Zeichen)",
    )
    @app_commands.choices(entscheidung=[
        app_commands.Choice(name="Annehmen", value="accepted"),
        app_commands.Choice(name="Ablehnen", value="rejected"),
    ])
    async def einspruch_entscheiden(
        self,
        interaction: discord.Interaction,
        fall_id: str,
        entscheidung: app_commands.Choice[str],
        notiz: str,
    ):
        if not self._appeal_reviewer_allowed(interaction):
            await interaction.response.send_message("❌ Dafür fehlt dir ein Moderationsrecht.", ephemeral=True)
            return
        try:
            appeal = mc.decide_appeal(
                interaction.guild.id,
                fall_id,
                entscheidung.value,
                notiz,
                interaction.user.id,
                interaction.user.display_name,
            )
        except ValueError as exc:
            await interaction.response.send_message("❌ " + str(exc), ephemeral=True)
            return
        import webserver
        label = "angenommen" if appeal["status"] == "accepted" else "abgelehnt"
        webserver.log_audit(
            interaction.user.display_name,
            str(interaction.user.id),
            "Moderationseinspruch entschieden",
            f"{appeal['case_id']} · {label} · {appeal['decision_note']}",
        )
        await interaction.response.send_message(
            f"✅ Einspruch **{appeal['case_id']}** wurde {label}. "
            "Die ursprüngliche Sanktion wurde nicht automatisch verändert. "
            "Eine nötige Moderationsänderung muss separat und mit den üblichen Rechten erfolgen.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

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
    # Re-register the buttons in already-delivered moderation DMs after a restart.
    for case in mc.list_appealable_cases(limit=1000):
        try:
            bot.add_view(AppealView(case["id"], int(case["guild_id"]), int(case["target_id"]), bot))
        except (TypeError, ValueError):
            continue
