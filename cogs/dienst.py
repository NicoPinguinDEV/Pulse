"""BOT-03: start, pause, resume and end a shift directly in Discord.

Same logic and data as the dashboard (webserver.apply_shift_action): the same warning lock, the same
LOA hint, the same Voice-Support-Bereitschaft sync, the same end-of-shift summary. Two ways in:
* `/dienst start|pause|weiter|ende|status` for the person themself,
* a persistent panel (`/dienst panel`, admins) with four buttons – survives restarts.
Rights are checked on every click: only people who may see the dashboard (team members) can use it.
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

import pulse_db
from pulse_interactions import PulseView

PANEL_SETTING = "duty_panel"  # {"channel_id": …, "message_id": …} – also shown in the panel overview (BOT-14)
BUTTONS = (("start", "Dienst starten", "🟢", discord.ButtonStyle.success),
           ("break", "Pause", "⏸️", discord.ButtonStyle.secondary),
           ("resume", "Weiter", "▶️", discord.ButtonStyle.primary),
           ("end", "Dienst beenden", "🔴", discord.ButtonStyle.danger))


def may_use_duty(guild, user_id) -> bool:
    import webserver
    perms, _ = webserver.compute_perms(guild, user_id, webserver.load_config())
    return bool(perms.get("can_view_dashboard") or perms.get("is_admin"))


async def run_duty_action(interaction: discord.Interaction, action: str) -> None:
    """Shared by the slash commands and the panel buttons."""
    import webserver
    if interaction.guild is None or not may_use_duty(interaction.guild, interaction.user.id):
        await interaction.response.send_message("❌ Den Dienst können nur Teammitglieder starten und beenden.", ephemeral=True)
        return
    if action == "status":
        await interaction.response.send_message("⏱ " + webserver.shift_status_text(interaction.user.id), ephemeral=True)
        return
    ok, message = webserver.apply_shift_action(interaction.user.id, action)
    await interaction.response.send_message(("✅ " if ok else "❌ ") + message, ephemeral=True)
    if ok:
        await webserver.sync_duty_after_shift(interaction.client, interaction.guild, interaction.user.id)
        try:
            import pulse_presence
            await pulse_presence.update(interaction.client)
        except Exception:
            pass


async def run_shift_note(interaction: discord.Interaction, note: str) -> None:
    """Add a private, immutable note to the invoking member's currently active shift."""
    import webserver
    if interaction.guild is None or not may_use_duty(interaction.guild, interaction.user.id):
        await interaction.response.send_message(
            "❌ Schichtnotizen können nur Teammitglieder verwenden.", ephemeral=True
        )
        return
    text = str(note or "").strip()
    if not text:
        await interaction.response.send_message("❌ Bitte gib eine Notiz ein.", ephemeral=True)
        return
    if len(text) > 1000:
        await interaction.response.send_message(
            "❌ Eine Schichtnotiz darf höchstens 1.000 Zeichen enthalten.", ephemeral=True
        )
        return

    uid = str(interaction.user.id)
    try:
        import pulse_shift_history
        import pulse_shift_utils
        member_state = pulse_shift_history.load_member_view(webserver, uid)
        active = member_state.get("active_shifts", {}) if isinstance(member_state, dict) else {}
        shift = active.get(uid) if isinstance(active, dict) else None
    except Exception as exc:
        await interaction.response.send_message(
            f"❌ Der aktuelle Dienststatus konnte nicht gelesen werden ({type(exc).__name__}).",
            ephemeral=True,
        )
        return
    if not isinstance(shift, dict) or not shift.get("started_at_iso"):
        await interaction.response.send_message(
            "❌ Du bist aktuell nicht im Dienst. Starte zuerst mit /dienst start eine Schicht.",
            ephemeral=True,
        )
        return

    event_id = str(
        shift.get("shift_event_id")
        or pulse_shift_utils.shift_event_id(uid, shift.get("started_at_iso"))
    )
    try:
        note_id = pulse_db.create_shift_note(
            event_id, interaction.guild.id, uid,
            getattr(interaction.user, "display_name", None) or getattr(interaction.user, "name", "Teammitglied"),
            text,
        )
    except ValueError as exc:
        await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
        return

    try:
        webserver.log_audit(
            getattr(interaction.user, "display_name", None) or getattr(interaction.user, "name", "Teammitglied"),
            uid, "Schichtnotiz erstellt", f"Notiz-ID {note_id} · Schicht {event_id}",
        )
    except Exception:
        pass
    await interaction.response.send_message(
        "✅ Schichtnotiz wurde privat gespeichert und deiner laufenden Schicht zugeordnet.",
        ephemeral=True,
    )


async def run_shift_notes(
    interaction: discord.Interaction, member: discord.Member | None = None
) -> None:
    """Show own notes; notes about another member require leadership permissions."""
    import webserver
    if interaction.guild is None or not may_use_duty(interaction.guild, interaction.user.id):
        await interaction.response.send_message(
            "❌ Schichtnotizen können nur Teammitglieder einsehen.", ephemeral=True
        )
        return
    target = member or interaction.user
    target_id = str(target.id)
    actor_id = str(interaction.user.id)
    if target_id != actor_id:
        perms, _ = webserver.compute_perms(
            interaction.guild, interaction.user.id, webserver.load_config()
        )
        if not (perms.get("can_promote") or perms.get("is_admin")):
            await interaction.response.send_message(
                "❌ Schichtnotizen anderer Teammitglieder sind nur für die Leitung freigegeben.",
                ephemeral=True,
            )
            return
    rows = pulse_db.list_shift_notes(interaction.guild.id, target_id, limit=10)
    target_name = (
        getattr(target, "display_name", None)
        or getattr(target, "global_name", None)
        or getattr(target, "name", "Teammitglied")
    )
    if not rows:
        await interaction.response.send_message(
            f"📝 Für **{discord.utils.escape_markdown(str(target_name))}** liegen noch keine Schichtnotizen vor.",
            ephemeral=True,
        )
        return

    lines = [f"**Schichtnotizen für {discord.utils.escape_markdown(str(target_name))}**"]
    for row in rows:
        created = discord.utils.escape_markdown(str(row.get("created_at") or "Zeit unbekannt"))
        body = discord.utils.escape_mentions(
            discord.utils.escape_markdown(str(row.get("note") or ""))
        )
        lines.append(f"• {created}\n{body}")
    output = "\n\n".join(lines)
    if len(output) > 1900:
        output = output[:1880].rsplit("\n", 1)[0] + "\n… weitere Einträge ausgeblendet."
    await interaction.response.send_message(output, ephemeral=True)


class DutyPanelView(PulseView):
    def __init__(self):
        super().__init__(timeout=None)
        for action, label, emoji, style in BUTTONS:
            button = discord.ui.Button(label=label, emoji=emoji, style=style, custom_id=f"pulse_dienst:{action}")
            button.callback = self._make_callback(action)
            self.add_item(button)

    @staticmethod
    def _make_callback(action):
        async def callback(interaction: discord.Interaction):
            await run_duty_action(interaction, action)
        return callback


def panel_embed(guild_name: str = "") -> discord.Embed:
    embed = discord.Embed(
        title="⏱ Dienst",
        description=("Starte, pausiere und beende deine Schicht direkt hier – genau wie im Dashboard.\n"
                     "Die Zeit wird gleich gezählt, die Voice-Support-Bereitschaft folgt automatisch.\n\n"
                     "Deinen Stand siehst du mit `/dienst status`."),
        color=discord.Color.from_rgb(91, 92, 240),
    )
    embed.set_footer(text=f"Pulse TeamOS{' · ' + guild_name if guild_name else ''}")
    return embed


class Dienst(commands.GroupCog, group_name="dienst", group_description="Eigene Schicht starten, pausieren und beenden"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        super().__init__()

    @app_commands.command(name="start", description="Startet deine Schicht.")
    async def start(self, interaction: discord.Interaction):
        await run_duty_action(interaction, "start")

    @app_commands.command(name="pause", description="Pausiert deine laufende Schicht.")
    async def pause(self, interaction: discord.Interaction):
        await run_duty_action(interaction, "break")

    @app_commands.command(name="weiter", description="Setzt deine pausierte Schicht fort.")
    async def weiter(self, interaction: discord.Interaction):
        await run_duty_action(interaction, "resume")

    @app_commands.command(name="ende", description="Beendet deine Schicht und schreibt die Zeit gut.")
    async def ende(self, interaction: discord.Interaction):
        await run_duty_action(interaction, "end")

    @app_commands.command(name="status", description="Zeigt, ob du im Dienst bist und deine Zeit diese Woche.")
    async def status(self, interaction: discord.Interaction):
        await run_duty_action(interaction, "status")

    @app_commands.command(name="notiz", description="Speichert eine private Notiz zu deiner laufenden Schicht.")
    @app_commands.describe(text="Kurze Dokumentation eines Ereignisses (maximal 1.000 Zeichen).")
    async def notiz(self, interaction: discord.Interaction, text: app_commands.Range[str, 1, 1000]):
        await run_shift_note(interaction, text)

    @app_commands.command(name="notizen", description="Zeigt deine Schichtnotizen; Leitung kann andere einsehen.")
    @app_commands.describe(member="Optionales Teammitglied (nur Leitung).")
    async def notizen(self, interaction: discord.Interaction, member: discord.Member | None = None):
        await run_shift_notes(interaction, member)

    @app_commands.command(name="panel", description="Veröffentlicht das Dienst-Panel in diesem Kanal (nur Admins).")
    @app_commands.default_permissions(manage_guild=True)
    async def panel(self, interaction: discord.Interaction):
        import webserver
        perms, _ = webserver.compute_perms(interaction.guild, interaction.user.id, webserver.load_config()) if interaction.guild else ({}, None)
        if not perms.get("is_admin"):
            await interaction.response.send_message("❌ Nur Admins können das Dienst-Panel veröffentlichen.", ephemeral=True)
            return
        try:
            message = await interaction.channel.send(embed=panel_embed(interaction.guild.name), view=DutyPanelView())
        except discord.HTTPException as exc:
            await interaction.response.send_message(f"❌ Pulse kann hier nicht senden ({type(exc).__name__}). Kanalrechte prüfen.", ephemeral=True)
            return
        pulse_db.set_setting(PANEL_SETTING, {"channel_id": interaction.channel.id, "message_id": message.id})
        webserver.log_audit(interaction.user.display_name, str(interaction.user.id), "Dienst-Panel veröffentlicht",
                            f"Kanal {interaction.channel.id}")
        await interaction.response.send_message("✅ Dienst-Panel veröffentlicht.", ephemeral=True)


async def setup(bot: commands.Bot):
    bot.add_view(DutyPanelView())  # persistent: the buttons keep working after a restart
    await bot.add_cog(Dienst(bot))
