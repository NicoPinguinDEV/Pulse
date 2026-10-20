"""Configurable Pulse voice support, synced to the dashboard shift system."""
from __future__ import annotations

import asyncio
import base64
import binascii
import re
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import discord
from pulse_embed_design import color as _pulse_color, is_modern
from pulse_discord_style import card_info, edit_styled, is_v2, layout_from_embed, respond_edit_styled, same_content, send_styled
from discord.ext import commands, tasks
import pulse_db as db
from pulse_voice_support_utils import DEFAULT_HOURS, DAYS, DAY_NAMES, shift_is_online, support_open_now

CONFIG_KEY = "voice_support_config"
CASES_KEY = "voice_support_cases"
RATINGS_KEY = "voice_support_ratings"
PENDING_KEY = "voice_support_pending_ratings"
SUPPORT_REPORT_CHANNEL_ID = 1531443370172485703
DUTY_STATUS_FOOTER = "Pulse Voice-Support · Automatisch aktualisiert"
SUPPORT_STATUS_FOOTER = "Pulse Voice-Support · Live-Status"
def get_config() -> dict:
    cfg = db.get_setting(CONFIG_KEY, {})
    cfg = cfg if isinstance(cfg, dict) else {}
    old = cfg.get("hours", {})
    cfg["hours"] = {
        day: {**defaults, **(old.get(day, {}) if isinstance(old, dict) and isinstance(old.get(day), dict) else {})}
        for day, defaults in DEFAULT_HOURS.items()
    }
    return cfg


def channel_id(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def get_cases() -> dict:
    value = db.get_setting(CASES_KEY, {})
    return value if isinstance(value, dict) else {}



def private_overwrites(
    guild: discord.Guild,
    member: discord.Member,
    staff_roles: list[discord.Role],
    *,
    grant_member: bool = True,
) -> dict:
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False, connect=False),
    }
    if grant_member:
        overwrites[member] = discord.PermissionOverwrite(view_channel=True, connect=True, speak=True, stream=True)
    if guild.me:
        overwrites[guild.me] = discord.PermissionOverwrite(
            view_channel=True, connect=True, speak=True, manage_channels=True, move_members=True
        )
    for role in staff_roles:
        if role and not role.managed and not role.is_default():
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, connect=True, speak=True)
    return overwrites


def serialize_overwrites(channel: discord.VoiceChannel) -> list[dict]:
    output = []
    for target, overwrite in channel.overwrites.items():
        allow, deny = overwrite.pair()
        output.append({
            "kind": "role" if isinstance(target, discord.Role) else "member",
            "id": target.id, "allow": allow.value, "deny": deny.value,
        })
    return output


def deserialize_overwrites(guild: discord.Guild, values: list[dict]) -> dict:
    output = {}
    for item in values if isinstance(values, list) else []:
        try:
            ident = int(item["id"])
            target = guild.get_role(ident) if item.get("kind") == "role" else guild.get_member(ident)
            if target:
                output[target] = discord.PermissionOverwrite.from_pair(
                    discord.Permissions(int(item["allow"])), discord.Permissions(int(item["deny"]))
                )
        except (KeyError, TypeError, ValueError):
            continue
    return output


class RatingButton(discord.ui.Button):
    def __init__(self, case_id: str, target_id: int, rating: int):
        super().__init__(
            label=f"{rating} ⭐",
            style=discord.ButtonStyle.primary if rating >= 4 else discord.ButtonStyle.secondary,
            custom_id=f"pulse_voice_rating:{case_id}:{rating}",
        )
        self.case_id, self.target_id, self.rating = case_id, target_id, rating

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.target_id:
            await interaction.response.send_message("Diese Bewertung ist für einen anderen Supportfall.", ephemeral=True)
            return
        pending = db.get_setting(PENDING_KEY, [])
        pending = pending if isinstance(pending, list) else []
        if not any(isinstance(x, dict) and str(x.get("case_id")) == self.case_id for x in pending):
            await interaction.response.send_message("Dieser Supportfall wurde bereits bewertet.", ephemeral=True)
            return
        ratings = db.get_setting(RATINGS_KEY, [])
        ratings = ratings if isinstance(ratings, list) else []
        ratings.append({
            "case_id": self.case_id, "user_id": str(self.target_id), "rating": self.rating,
            "created_at": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(timespec="seconds"),
        })
        db.set_setting(RATINGS_KEY, ratings[-1000:])
        db.set_setting(PENDING_KEY, [x for x in pending if not (isinstance(x, dict) and str(x.get("case_id")) == self.case_id)])
        await respond_edit_styled(interaction, text=f"✅ Danke! Du hast **{self.rating}/5 Sterne** vergeben.", view=None)


class RatingView(discord.ui.View):
    def __init__(self, case_id: str, user_id: int):
        super().__init__(timeout=None)
        for value in range(1, 6):
            self.add_item(RatingButton(case_id, user_id, value))




class SupportCaseButton(discord.ui.Button):
    def __init__(self, cog: "VoiceSupport", case_id: str, action: str, label: str, style: discord.ButtonStyle, emoji: str):
        super().__init__(label=label, style=style, emoji=emoji, custom_id=f"pulse_voice_case:{action}:{case_id}")
        self.cog = cog
        self.case_id = case_id
        self.action = action

    async def callback(self, interaction: discord.Interaction):
        await self.cog.handle_case_action(interaction, self.case_id, self.action)


class SupportCaseView(discord.ui.View):
    """Persistent controls for accepting, transferring and closing a support case."""

    def __init__(self, cog: "VoiceSupport", case_id: str):
        super().__init__(timeout=None)
        self.add_item(SupportCaseButton(cog, case_id, "claim", "Übernehmen", discord.ButtonStyle.success, "✅"))
        self.add_item(SupportCaseButton(cog, case_id, "transfer", "Übertragen", discord.ButtonStyle.primary, "🔁"))
        self.add_item(SupportCaseButton(cog, case_id, "finish", "Beenden", discord.ButtonStyle.danger, "🏁"))


class TransferUserSelect(discord.ui.UserSelect):
    def __init__(self, cog: "VoiceSupport", case_id: str):
        super().__init__(
            placeholder="Supporter für die Übergabe auswählen",
            min_values=1,
            max_values=1,
            custom_id=f"pulse_voice_transfer_select:{case_id}",
        )
        self.cog = cog
        self.case_id = case_id

    async def callback(self, interaction: discord.Interaction):
        selected = self.values[0]
        await self.cog.handle_transfer_selection(interaction, self.case_id, int(selected.id))


class TransferUserView(discord.ui.View):
    def __init__(self, cog: "VoiceSupport", case_id: str):
        super().__init__(timeout=90)
        self.add_item(TransferUserSelect(cog, case_id))


class VoiceSupport(commands.Cog):
    """Manages a shared support waiting room, support cases and duty synchronization."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._opening: set[int] = set()
        self._closing_cases: set[str] = set()
        self._registered_case_views: set[str] = set()
        self._duty_status_lock = asyncio.Lock()
        self._case_action_locks: dict[str, asyncio.Lock] = {}
        self._music_warning_logged = False
        self._after_hours_audio_warning_logged = False
        self.duty_status_loop.start()
        self.music_loop.start()

    def cog_unload(self):
        self.duty_status_loop.cancel()
        self.music_loop.cancel()

    def _guild(self):
        gid = channel_id(get_config().get("guild_id"))
        return self.bot.get_guild(gid) if gid else None


    def _case_action_lock(self, case_id: str) -> asyncio.Lock:
        return self._case_action_locks.setdefault(str(case_id), asyncio.Lock())

    def _store_case(self, case: dict, key: str) -> None:
        """K-04: write one case into the *current* blob (no await in between) instead of a stale copy
        of all cases – otherwise cases opened by others during Discord calls would vanish."""
        fresh = get_cases()
        case_id = str(case.get("case_id", ""))
        for old_key in [k for k, v in fresh.items() if isinstance(v, dict) and str(v.get("case_id", "")) == case_id]:
            fresh.pop(old_key, None)
        fresh[str(key)] = case
        db.set_setting(CASES_KEY, fresh)

    def _find_case_by_id(self, case_id: str):
        cases = get_cases()
        for key, value in cases.items():
            if isinstance(value, dict) and str(value.get("case_id", "")) == str(case_id):
                return str(key), value, cases
        return None, None, cases

    def _is_support_staff(self, member: discord.Member, cfg: dict) -> bool:
        """Accept support staff whether or not they are currently on duty.

        The support role represents membership/eligibility; the duty role is a
        live presence indicator. Treating the duty role as the only valid role
        in duty mode incorrectly prevented off-duty support staff from claiming
        queued cases. Claiming still requires joining a valid support voice room.
        """
        if not isinstance(member, discord.Member) or member.bot:
            return False
        eligible_role_ids = {
            role_id
            for role_id in (
                channel_id(cfg.get("support_role_id")),
                channel_id(cfg.get("duty_role_id")),
            )
            if role_id
        }
        return bool(eligible_role_ids and any(role.id in eligible_role_ids for role in member.roles))

    def _register_case_view(self, case_id: str, message_id: int = 0):
        case_id = str(case_id or "")
        if not case_id or case_id in self._registered_case_views:
            return
        try:
            self.bot.add_view(SupportCaseView(self, case_id), message_id=message_id or None)
            self._registered_case_views.add(case_id)
        except (ValueError, RuntimeError) as exc:
            print(f"⚠️ Voice-Support: Buttons für Fall {case_id} konnten nicht registriert werden: {exc}")

    def _case_notification_embed(self, guild: discord.Guild, case: dict) -> discord.Embed:
        user_id = channel_id(case.get("user_id"))
        staff_id = channel_id(case.get("assigned_staff_id"))
        waiting_in_shared_room = bool(case.get("waiting_in_shared_room") and not staff_id)
        active_channel_id = (
            channel_id(case.get("waiting_room_id"))
            if waiting_in_shared_room
            else channel_id(case.get("support_channel_id") or case.get("channel_id"))
        )
        active_channel = guild.get_channel(active_channel_id)
        active_channel_label = "Warteraum" if waiting_in_shared_room else "Sprachkanal"
        if staff_id:
            status = f"✅ **Übernommen von:** <@{staff_id}>"
            color = _pulse_color("success")
        else:
            support_label = (
                "einem berechtigten Supporter (auch außer Dienst)"

            )
            status = f"🟠 **Status:** Wartet auf Übernahme durch {support_label}"
            color = _pulse_color("warning")
        after_hours_note = (
            "\n⏰ **Außerhalb der Öffnungszeiten:** Der Fall wurde trotzdem regulär aufgenommen."
            if case.get("outside_opening_hours") else ""
        )
        description = (
            f"**Nutzer:** <@{user_id}> ({user_id})\n"
            f"**{active_channel_label}:** {active_channel.mention if active_channel else ('Warteraum (bis zur Übernahme)' if waiting_in_shared_room else 'Wird beim Übernehmen zugewiesen')}\n"
            f"{status}{after_hours_note}"
        )
        embed = discord.Embed(
            title=f"🎧 Supportfall · {case.get('room_name') or 'Support'}",
            description=description,
            color=color,
            timestamp=discord.utils.utcnow(),
        )
        case_id = str(case.get("case_id") or "UNBEKANNT")
        embed.add_field(name="CaseID", value=f"#S-{case_id[:9].upper()}", inline=True)
        embed.add_field(name="Erstellt am", value=str(case.get("created_at") or "Unbekannt"), inline=True)
        if staff_id:
            embed.add_field(name="Supporter", value=f"<@{staff_id}>", inline=True)
        embed.set_footer(text=f"Pulse Voice-Support · Fall {case_id}")
        return embed

    async def _update_case_notification(self, case: dict):
        cfg = get_config()
        guild = self.bot.get_guild(channel_id(case.get("guild_id")))
        destination = guild.get_channel(channel_id(cfg.get("notification_channel_id"))) if guild else None
        if not isinstance(destination, discord.TextChannel):
            return
        message_id = channel_id(case.get("notification_message_id"))
        if not message_id:
            return
        try:
            message = await destination.fetch_message(message_id)
            await edit_styled(
                message,
                embed=self._case_notification_embed(guild, case),
                view=SupportCaseView(self, str(case.get("case_id", ""))),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            self._register_case_view(str(case.get("case_id", "")), message.id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
            print(f"⚠️ Voice-Support: Fallmeldung {case.get('case_id')} konnte nicht aktualisiert werden: {exc}")

    async def handle_case_action(self, interaction: discord.Interaction, case_id: str, action: str):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Diese Aktion funktioniert nur auf dem Support-Server.", ephemeral=True)
            return
        cfg = get_config()
        _, case, _ = self._find_case_by_id(case_id)
        if not isinstance(case, dict):
            await interaction.response.send_message("Dieser Supportfall ist bereits geschlossen.", ephemeral=True)
            return
        actor = interaction.user
        if not self._is_support_staff(actor, cfg):
            await interaction.response.send_message("Du benötigst die konfigurierte Supportrolle oder Bereitschaftsrolle, um Supportfälle zu verwalten. Eine aktive Dienstschicht ist nicht erforderlich.", ephemeral=True)
            return

        if action == "claim":
            # Claiming may perform several Discord API operations and exceed
            # Discord's 3-second initial-interaction deadline. Acknowledge the
            # button first; the interaction token remains valid for the follow-up.
            await interaction.response.defer(ephemeral=True, thinking=True)
            ok, message = await self._assign_case(case_id, actor, transfer=False)
            await interaction.followup.send(message, ephemeral=True)
            if ok:
                _, updated, _ = self._find_case_by_id(case_id)
                if updated:
                    await self._update_case_notification(updated)
            return

        assigned_id = channel_id(case.get("assigned_staff_id"))
        can_manage = actor.guild_permissions.manage_channels or actor.guild_permissions.administrator
        if assigned_id and assigned_id != actor.id and not can_manage:
            await interaction.response.send_message("Nur der zugewiesene Supporter oder ein Server-Manager darf diesen Fall übertragen oder beenden.", ephemeral=True)
            return

        if action == "transfer":
            await interaction.response.send_message(
                "Wähle den Supporter aus, an den du den Fall übertragen möchtest. Er muss in einem Sprachkanal sein.",
                view=TransferUserView(self, case_id),
                ephemeral=True,
            )
            return

        if action == "finish":
            await interaction.response.defer(ephemeral=True, thinking=True)
            guild = interaction.guild
            channel = guild.get_channel(channel_id(case.get("support_channel_id") or case.get("channel_id")))
            await self._close_case(
                channel,
                case,
                cfg,
                reason="Supportfall vom Team beendet",
                kick_user=True,
                closed_by=actor,
            )
            await interaction.followup.send("✅ Supportfall beendet. Die Falldaten und temporären Rechte wurden bereinigt. Wartende Bürger bleiben selbstständig im gemeinsamen Warteraum.", ephemeral=True)
            return

        await interaction.response.send_message("Unbekannte Support-Aktion.", ephemeral=True)

    async def handle_transfer_selection(self, interaction: discord.Interaction, case_id: str, target_id: int):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Diese Aktion funktioniert nur auf dem Support-Server.", ephemeral=True)
            return
        cfg = get_config()
        _, case, _ = self._find_case_by_id(case_id)
        if not isinstance(case, dict):
            await interaction.response.send_message("Dieser Supportfall ist bereits geschlossen.", ephemeral=True)
            return
        actor = interaction.user
        assigned_id = channel_id(case.get("assigned_staff_id"))
        can_manage = actor.guild_permissions.manage_channels or actor.guild_permissions.administrator
        if not self._is_support_staff(actor, cfg) or (assigned_id and assigned_id != actor.id and not can_manage):
            await interaction.response.send_message("Du darfst diesen Supportfall nicht übertragen.", ephemeral=True)
            return
        target = interaction.guild.get_member(int(target_id))
        if not target or not self._is_support_staff(target, cfg):
            await interaction.response.send_message("Der ausgewählte Nutzer hat nicht die erforderliche Support-/Bereitschaftsrolle.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        ok, message = await self._assign_case(case_id, target, transfer=True)
        await interaction.followup.send(message, ephemeral=True)
        if ok:
            _, updated, _ = self._find_case_by_id(case_id)
            if updated:
                await self._update_case_notification(updated)

    async def _restore_member_overwrite(self, channel, member: discord.Member | None, snapshot: dict | None, reason: str):
        if channel is None or member is None or not isinstance(snapshot, dict):
            return
        try:
            if snapshot.get("had_overwrite"):
                overwrite = discord.PermissionOverwrite.from_pair(
                    discord.Permissions(int(snapshot.get("allow", 0))),
                    discord.Permissions(int(snapshot.get("deny", 0))),
                )
                await channel.set_permissions(member, overwrite=overwrite, reason=reason)
            else:
                await channel.set_permissions(member, overwrite=None, reason=reason)
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"⚠️ Voice-Support: Temporäre Rechte für {member.id} in Kanal {channel.id} konnten nicht zurückgesetzt werden: {exc}")

    async def _cleanup_private_case_room(self, guild: discord.Guild, case: dict):
        private_id = channel_id(case.get("private_channel_id"))
        if not private_id or private_id == channel_id(case.get("support_channel_id")) or case.get("private_room_cleaned"):
            return
        private_channel = guild.get_channel(private_id)
        if private_channel:
            try:
                if case.get("created_by_pulse"):
                    await private_channel.delete(reason=f"Pulse Voice-Support: Fall {case.get('case_id')} wurde übernommen")
                else:
                    await private_channel.edit(
                        overwrites=deserialize_overwrites(guild, case.get("original_overwrites", [])),
                        reason=f"Pulse Voice-Support: ursprüngliche Rechte von Fall {case.get('case_id')} wiederhergestellt",
                    )
            except (discord.Forbidden, discord.HTTPException) as exc:
                print(f"⚠️ Voice-Support: Warteraum-Kanal {private_id} konnte nach Übernahme nicht bereinigt werden: {exc}")
                return
        case["private_room_cleaned"] = True
        case["private_channel_id"] = 0
        latest_key, latest, cases = self._find_case_by_id(str(case.get("case_id", "")))
        if latest_key is not None:
            latest["private_room_cleaned"] = True
            latest["private_channel_id"] = 0
            cases[latest_key] = latest
            db.set_setting(CASES_KEY, cases)

    async def _assign_case(self, case_id: str, staff: discord.Member, transfer: bool) -> tuple[bool, str]:
        async with self._case_action_lock(case_id):
            return await self._assign_case_locked(case_id, staff, transfer)

    async def _assign_case_locked(self, case_id: str, staff: discord.Member, transfer: bool) -> tuple[bool, str]:
        cfg = get_config()
        key, case, cases = self._find_case_by_id(case_id)
        if key is None or not isinstance(case, dict):
            return False, "Dieser Supportfall ist bereits geschlossen."
        if not self._is_support_staff(staff, cfg):
            return False, "Nur Teamler mit Support- oder Bereitschaftsrolle können Fälle übernehmen. Du musst dafür nicht im Dienst sein."
        current_staff_id = channel_id(case.get("assigned_staff_id"))
        if current_staff_id and not transfer:
            current = staff.guild.get_member(current_staff_id)
            return False, f"Dieser Fall wurde bereits von {current.mention if current else 'einem anderen Supporter'} übernommen. Nutze für eine Übergabe die Schaltfläche „Übertragen“."
        if transfer and current_staff_id == staff.id:
            return False, "Du bist bereits für diesen Fall zuständig."
        target = staff.voice.channel if staff.voice else None
        if not isinstance(target, discord.VoiceChannel):
            return False, "Tritt zuerst einem normalen Sprachkanal bei. Der Bürger wird bei der Übernahme dorthin verschoben."
        if target.id == channel_id(cfg.get("waiting_room_channel_id")):
            return False, "Du kannst den Fall nicht in den allgemeinen Warteraum übernehmen. Tritt zuerst deinem Support-Sprachkanal bei."
        if not current_staff_id and target.id == channel_id(case.get("channel_id")):
            return False, "Bitte übernimm den Fall aus deinem eigenen Support-Sprachkanal, nicht im privaten Warteraum des Bürgers."

        for other in cases.values():
            if not isinstance(other, dict) or str(other.get("case_id")) == str(case_id):
                continue
            if channel_id(other.get("support_channel_id")) == target.id:
                return False, "In diesem Sprachkanal läuft bereits ein anderer Supportfall. Nutze für diesen Fall einen freien Support-Sprachkanal."

        guild = staff.guild
        user_id = channel_id(case.get("user_id"))
        citizen = guild.get_member(user_id)
        if citizen is None:
            try:
                citizen = await guild.fetch_member(user_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return False, "Der Bürger ist nicht mehr auf dem Server. Der Supportfall kann nicht übernommen werden."

        previous_channel_id = channel_id(case.get("support_channel_id"))
        previous_overwrite = case.get("support_original_overwrite", {})
        before_overwrite = target.overwrites_for(citizen)
        allow, deny = before_overwrite.pair()
        snapshot = {
            "had_overwrite": citizen in target.overwrites,
            "allow": allow.value,
            "deny": deny.value,
        }
        # If the transfer stays in the same voice channel, retain the true
        # pre-support overwrite rather than snapshotting the temporary grant.
        if current_staff_id and previous_channel_id == target.id and isinstance(previous_overwrite, dict):
            snapshot = dict(previous_overwrite)
        case["claiming_staff_id"] = staff.id
        cases[key] = case
        db.set_setting(CASES_KEY, cases)

        try:
            await target.set_permissions(
                citizen,
                view_channel=True,
                connect=True,
                speak=True,
                stream=True,
                use_voice_activation=True,
                reason=f"Pulse Voice-Support: Fall {case_id} an {staff} übergeben",
            )
            await citizen.move_to(target, reason=f"Pulse Voice-Support: Fall {case_id} von {staff} übernommen")
        except (discord.Forbidden, discord.HTTPException) as exc:
            await self._restore_member_overwrite(
                target, citizen, snapshot,
                f"Pulse Voice-Support: fehlgeschlagene Übernahme von Fall {case_id} zurückgerollt",
            )
            case.pop("claiming_staff_id", None)
            self._store_case(case, key)
            return False, f"Übernahme fehlgeschlagen. Prüfe „Mitglieder verschieben“ und die Voice-Berechtigungen des Bots. ({type(exc).__name__})"

        if previous_channel_id and previous_channel_id != target.id:
            previous_channel = guild.get_channel(previous_channel_id)
            old_member_snapshot = previous_overwrite if isinstance(previous_overwrite, dict) else {}
            await self._restore_member_overwrite(
                previous_channel, citizen, old_member_snapshot,
                f"Pulse Voice-Support: Fall {case_id} an {staff} übertragen; alte Rechte zurückgesetzt",
            )

        private_channel_id = channel_id(case.get("private_channel_id"))
        # Pending cases live in the shared waiting room; never treat that room as
        # a private case channel or clean up its permissions when the case is claimed.
        if not current_staff_id and not private_channel_id and not case.get("waiting_in_shared_room"):
            private_channel_id = channel_id(case.get("channel_id"))
        cases.pop(key, None)
        case.pop("claiming_staff_id", None)
        case["channel_id"] = target.id
        case["support_channel_id"] = target.id
        case["assigned_staff_id"] = staff.id
        case["assigned_staff_name"] = staff.display_name
        case["waiting_in_shared_room"] = False
        case["support_original_overwrite"] = snapshot
        case["support_started_at"] = datetime.now(ZoneInfo("Europe/Berlin")).isoformat(timespec="seconds")
        case["staff_joined"] = True
        if private_channel_id and private_channel_id != target.id:
            case["private_channel_id"] = private_channel_id
        self._store_case(case, str(target.id))

        await self._cleanup_private_case_room(guild, case)
        await self._disconnect_music(target.id)
        await self._refresh_support_status(guild)
        verb = "übertragen" if transfer else "übernommen"
        print(f"🎧 Voice-Support-Fall {case_id} von {staff} {verb} · Sprachkanal {target.id}")
        return True, f"✅ Supportfall erfolgreich {verb}. {citizen.mention} wurde in deinen Sprachkanal verschoben; Sprechen, Sprachaktivierung ohne Push-to-Talk und Streamen sind freigeschaltet."


    async def _on_duty(self, guild: discord.Guild, cfg: dict) -> list[discord.Member]:
        try:
            import webserver
            shifts = webserver.load_shifts()
            team_ids = {int(x) for x in webserver.load_config().get("team_role_ids", []) if str(x).isdigit()}
        except Exception:
            shifts, team_ids = {"active_shifts": {}}, set()
        support_id = channel_id(cfg.get("support_role_id"))
        result = []
        for uid, item in shifts.get("active_shifts", {}).items():
            if not str(uid).isdigit() or not isinstance(item, dict) or item.get("status") != "online":
                continue
            member = guild.get_member(int(uid))
            if member and not member.bot and (
                not support_id or support_id in {r.id for r in member.roles}
                or any(r.id in team_ids for r in member.roles)
            ):
                result.append(member)
        return result

    async def sync_member_duty_role(self, guild: discord.Guild, user_id: int | str):
        cfg = get_config()
        if not cfg.get("duty_mode") or channel_id(cfg.get("guild_id")) not in (0, guild.id):
            return
        role = guild.get_role(channel_id(cfg.get("duty_role_id")))
        member = guild.get_member(int(user_id)) if str(user_id).isdigit() else None
        if not role or not member or member.bot:
            return
        try:
            import webserver
            team_ids = {int(x) for x in webserver.load_config().get("team_role_ids", []) if str(x).isdigit()}
            online = shift_is_online(webserver.load_shifts(), member.id)
        except Exception:
            team_ids, online = set(), False
        is_team = any(r.id in team_ids for r in member.roles)
        has_role = any(r.id == role.id for r in member.roles)
        try:
            if online and is_team and not has_role:
                await member.add_roles(role, reason="Pulse Voice-Support: Bereitschaft aus Dashboard-Schicht")
            elif (not online or not is_team) and has_role:
                await member.remove_roles(role, reason="Pulse Voice-Support: Pause/Schichtende/Teamwechsel im Dashboard")
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"⚠️ Voice-Support: Bereitschaftsrolle für {member.id} konnte nicht synchronisiert werden: {exc}")

    async def _reconcile_duty_roles(self, guild: discord.Guild, cfg: dict):
        if not cfg.get("duty_mode"):
            return
        role = guild.get_role(channel_id(cfg.get("duty_role_id")))
        if not role:
            return
        try:
            import webserver
            shifts = webserver.load_shifts()
            team_ids = {int(x) for x in webserver.load_config().get("team_role_ids", []) if str(x).isdigit()}
        except Exception:
            shifts, team_ids = {"active_shifts": {}}, set()
        active = shifts.get("active_shifts", {})
        candidates = {
            int(uid) for uid, state in active.items()
            if str(uid).isdigit() and isinstance(state, dict) and state.get("status") == "online"
        }
        candidates.update(m.id for m in guild.members if role in m.roles and any(r.id in team_ids for r in m.roles))
        for user_id in candidates:
            await self.sync_member_duty_role(guild, user_id)

    async def _refresh_duty_status(self, guild=None):
        async with self._duty_status_lock:
            await self._refresh_duty_status_locked(guild)

    async def _refresh_duty_status_locked(self, guild=None):
        cfg = get_config()
        if not cfg.get("duty_mode") or not cfg.get("duty_message_enabled"):
            return
        guild = guild or self._guild()
        if not guild:
            return
        channel = guild.get_channel(channel_id(cfg.get("checkin_channel_id")))
        if not isinstance(channel, discord.TextChannel):
            return
        staff = await self._on_duty(guild, cfg)
        names = [f"🟢 {m.mention} · **{m.display_name}**" for m in sorted(staff, key=lambda x: x.display_name.casefold())[:20]]
        embed = discord.Embed(
            title="🧠 Support-Bereitschaft",
            description="\n".join(names) if names else "🔴 Aktuell ist niemand im Support-Dienst.",
            color=_pulse_color("success") if staff else _pulse_color("warning"),
        )
        embed.add_field(name="Dienstquelle", value="Automatisch mit den Pulse-Dashboard-Schichten synchronisiert", inline=False)
        embed.set_footer(text="Pulse Voice-Support · Automatisch aktualisiert")
        saved_id = channel_id(cfg.get("duty_status_message_id"))
        matches = []
        saved_message = None
        if saved_id:
            try:
                saved_message = await channel.fetch_message(saved_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                saved_message = None
        try:
            async for candidate in channel.history(limit=100):
                if candidate.author.id != getattr(self.bot.user, "id", 0):
                    continue
                info = card_info(candidate)
                if info and info["title"] == "🧠 Support-Bereitschaft" and info["footer"] == DUTY_STATUS_FOOTER:
                    matches.append(candidate)
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"⚠️ Voice-Support: Verlauf zur Duplikatprüfung nicht lesbar: {exc}")

        msg = None
        saved_info = card_info(saved_message) if saved_message else None
        if (
            saved_message
            and saved_message.author.id == getattr(self.bot.user, "id", 0)
            and saved_info
            and saved_info["title"] == "🧠 Support-Bereitschaft"
            and saved_info["footer"] == DUTY_STATUS_FOOTER
        ):
            msg = saved_message
        elif matches:
            msg = matches[0]

        # Older releases could create duplicates when the saved message ID was missing.
        for duplicate in matches:
            if msg and duplicate.id == msg.id:
                continue
            try:
                await duplicate.delete()
                print(f"🧹 Voice-Support: doppelte Bereitschaftsnachricht {duplicate.id} entfernt.")
            except (discord.Forbidden, discord.HTTPException):
                pass

        try:
            if msg and is_v2(msg) != is_modern():
                # Style changed: embed and container cannot be converted, replace the board once.
                try:
                    await msg.delete()
                except discord.NotFound:
                    pass
                msg = None
            if msg and same_content(msg, embed):
                if channel_id(cfg.get("duty_status_message_id")) != msg.id:
                    cfg["duty_status_message_id"] = msg.id
                    db.set_setting(CONFIG_KEY, cfg)
                return
            if msg:
                await edit_styled(msg, embed=embed, allowed_mentions=discord.AllowedMentions.none())
            else:
                msg = await send_styled(channel, embed=embed, allowed_mentions=discord.AllowedMentions.none())
            cfg["duty_status_message_id"] = msg.id
            db.set_setting(CONFIG_KEY, cfg)
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"⚠️ Voice-Support: Bereitschaftsnachricht konnte nicht aktualisiert werden: {exc}")

    async def _refresh_support_status(self, guild=None):
        """Keep one live open/closed status message in the configured notification channel."""
        cfg = get_config()
        guild = guild or self._guild()
        if guild is None:
            return
        channel = guild.get_channel(channel_id(cfg.get("notification_channel_id")))
        if not isinstance(channel, discord.TextChannel):
            return

        saved_id = channel_id(cfg.get("support_status_message_id"))
        saved_message = None
        saved_partial = None
        if saved_id:
            try:
                saved_message = await channel.fetch_message(saved_id)
            except discord.NotFound:
                saved_message = None
            except (discord.Forbidden, discord.HTTPException):
                # Editing a saved partial message avoids duplicate status posts when
                # the bot can send/edit but cannot read channel history.
                saved_partial = channel.get_partial_message(saved_id)

        if not cfg.get("notification_enabled", True):
            target_message = saved_message or saved_partial
            if target_message:
                try:
                    await target_message.delete()
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass
            if cfg.pop("support_status_message_id", None):
                db.set_setting(CONFIG_KEY, cfg)
            return

        now_open = bool(cfg.get("active") and support_open_now(cfg))
        cases = [
            item for item in get_cases().values()
            if isinstance(item, dict) and channel_id(item.get("guild_id")) == guild.id
        ]
        waiting = sum(
            1 for item in cases
            if item.get("waiting_in_shared_room") and not channel_id(item.get("assigned_staff_id"))
        )
        active = sum(1 for item in cases if channel_id(item.get("assigned_staff_id")))

        if not cfg.get("active"):
            state = "🔴 Deaktiviert"
            detail = "Der Voice-Support ist im Dashboard deaktiviert."
            color = _pulse_color("neutral")
        elif now_open:
            state = "🟢 Geöffnet"
            detail = "Der Voice-Support ist innerhalb der eingestellten Öffnungszeiten."
            color = _pulse_color("success")
        else:
            state = "🌙 Außerhalb der Öffnungszeiten"
            detail = "Supportanfragen werden weiterhin aufgenommen und bleiben im gemeinsamen Warteraum."
            color = _pulse_color("warning")

        embed = discord.Embed(
            title="🎧 Voice-Support · Aktueller Status",
            description=f"**Status:** {state}\n{detail}",
            color=color,
        )
        embed.add_field(name="⏳ Wartende Fälle", value=str(waiting), inline=True)
        embed.add_field(name="🎧 Laufende Gespräche", value=str(active), inline=True)
        if cfg.get("duty_mode"):
            staff = await self._on_duty(guild, cfg)
            availability = f"{len(staff)} Teammitglied(er) im Dienst"
        else:
            role = guild.get_role(channel_id(cfg.get("support_role_id")))
            availability = (
                f"{sum(1 for member in role.members if not member.bot)} mit Support-Rolle"
                if role else "Keine Support-Rolle konfiguriert"
            )
        embed.add_field(name="👥 Teamstatus", value=availability, inline=False)
        embed.set_footer(text=SUPPORT_STATUS_FOOTER)

        if saved_partial is not None:
            try:
                # Without history access the saved message's format is unknown; edit in the
                # configured style. Discord rejects a format change, then the board is replaced once.
                if is_modern():
                    await saved_partial.edit(content=None, embeds=[], view=layout_from_embed(embed),
                                             allowed_mentions=discord.AllowedMentions.none())
                else:
                    await saved_partial.edit(embed=embed, allowed_mentions=discord.AllowedMentions.none())
                return
            except discord.NotFound:
                saved_partial = None
            except (discord.Forbidden, discord.HTTPException) as exc:
                if getattr(exc, "status", 0) != 400:
                    print(f"⚠️ Voice-Support: gespeicherter Live-Status konnte nicht editiert werden: {exc}")
                    return
                try:
                    await saved_partial.delete()
                    replacement = await send_styled(channel, embed=embed, allowed_mentions=discord.AllowedMentions.none())
                    cfg["support_status_message_id"] = replacement.id
                    db.set_setting(CONFIG_KEY, cfg)
                except (discord.Forbidden, discord.HTTPException) as replace_exc:
                    print(f"⚠️ Voice-Support: Live-Status konnte nicht ersetzt werden: {replace_exc}")
                return

        saved_info = card_info(saved_message) if saved_message else None
        valid_saved = (
            saved_message
            and saved_message.author.id == getattr(self.bot.user, "id", 0)
            and saved_info
            and saved_info["title"] == "🎧 Voice-Support · Aktueller Status"
            and saved_info["footer"] == SUPPORT_STATUS_FOOTER
        )
        matches = []
        # After the first successful write, the saved message ID is authoritative.
        # Only scan history when that ID is missing/stale to avoid needless API calls.
        if not valid_saved:
            try:
                async for candidate in channel.history(limit=100):
                    if candidate.author.id != getattr(self.bot.user, "id", 0):
                        continue
                    info = card_info(candidate)
                    if info and info["title"] == "🎧 Voice-Support · Aktueller Status" and info["footer"] == SUPPORT_STATUS_FOOTER:
                        matches.append(candidate)
            except (discord.Forbidden, discord.HTTPException) as exc:
                print(f"⚠️ Voice-Support: Status-Verlauf konnte nicht geprüft werden: {exc}")

        message = saved_message if valid_saved else (matches[0] if matches else None)
        for duplicate in matches:
            if message and duplicate.id == message.id:
                continue
            try:
                await duplicate.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

        try:
            if message and is_v2(message) != is_modern():
                try:
                    await message.delete()
                except discord.NotFound:
                    pass
                message = None
            if message and same_content(message, embed):
                if channel_id(cfg.get("support_status_message_id")) != message.id:
                    cfg["support_status_message_id"] = message.id
                    db.set_setting(CONFIG_KEY, cfg)
                return
            if message:
                await edit_styled(message, embed=embed, allowed_mentions=discord.AllowedMentions.none())
            else:
                message = await send_styled(channel, embed=embed, allowed_mentions=discord.AllowedMentions.none())
            if channel_id(cfg.get("support_status_message_id")) != message.id:
                cfg["support_status_message_id"] = message.id
                db.set_setting(CONFIG_KEY, cfg)
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"⚠️ Voice-Support: Live-Status konnte nicht aktualisiert werden: {exc}")

    @commands.Cog.listener()
    async def on_ready(self):
        from pulse_ready import first_ready
        # P-08: reconcile once, and again only after a longer outage (> 10 min since the last run).
        if not first_ready(self.bot, "voice-support-ready", min_interval=600):
            return
        cfg = get_config()
        gid = channel_id(cfg.get("guild_id"))
        guild = self.bot.get_guild(gid) if gid else None
        if guild:
            await self._reconcile_duty_roles(guild, cfg)
            await self._refresh_duty_status(guild)
            await self._refresh_support_status(guild)
        pending = db.get_setting(PENDING_KEY, [])
        for item in pending if isinstance(pending, list) else []:
            try:
                self.bot.add_view(RatingView(str(item["case_id"]), int(item["user_id"])))
            except (KeyError, TypeError, ValueError):
                pass
        for case in get_cases().values():
            if isinstance(case, dict) and channel_id(case.get("notification_message_id")):
                self._register_case_view(str(case.get("case_id", "")), channel_id(case.get("notification_message_id")))
        print("🎧 Pulse Voice-Support geladen · Bereitschaft und Supportfall-Buttons sind synchronisiert.")
        try:
            from pulse_audio_check import audio_status
            audio = audio_status()
            if not audio["ok"]:
                print(f"⚠️ Voice-Support: {audio['message']}.")
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if member.bot:
            return
        cfg = get_config()
        gid = channel_id(cfg.get("guild_id"))
        if gid and member.guild.id != gid:
            return
        cases = get_cases()

        # When support staff join, finish the waiting/music stage.
        if after.channel:
            case = cases.get(str(after.channel.id))
            staff_roles = {channel_id(cfg.get("support_role_id")), channel_id(cfg.get("duty_role_id"))} - {0}
            if isinstance(case, dict) and not case.get("waiting_in_shared_room") and member.id != channel_id(case.get("user_id")) and staff_roles.intersection(r.id for r in member.roles):
                case["staff_joined"] = True
                db.set_setting(CASES_KEY, cases)
                await self._disconnect_music(after.channel.id)

        wait_id = channel_id(cfg.get("waiting_room_channel_id"))
        if cfg.get("active") and wait_id and after.channel and after.channel.id == wait_id and (not before.channel or before.channel.id != wait_id):
            if member.id in self._opening:
                return
            self._opening.add(member.id)
            try:
                outside_opening_hours = not support_open_now(cfg)
                cases = get_cases()
                old = next((x for x in cases.values() if isinstance(x, dict) and str(x.get("guild_id")) == str(member.guild.id) and str(x.get("user_id")) == str(member.id)), None)
                if old:
                    old_channel = member.guild.get_channel(channel_id(old.get("support_channel_id") or old.get("channel_id")))
                    # Re-entering the wait room means the citizen left the prior call.
                    # Close and clean that case first, then treat the new wait-room entry
                    # as a new support request instead of moving the citizen back.
                    await self._close_case(
                        old_channel,
                        old,
                        cfg,
                        reason="Bürger hat den Supportkanal verlassen und erneut den Warteraum betreten",
                        kick_user=False,
                    )
                await self._open_case(member, cfg, outside_opening_hours=outside_opening_hours)
            except (discord.Forbidden, discord.HTTPException, RuntimeError) as exc:
                print(f"❌ Voice-Support: Fall für {member.id} konnte nicht eröffnet werden: {exc}")
                await self._failure_dm(member)
            finally:
                self._opening.discard(member.id)

        if before.channel and before.channel.id != getattr(after.channel, "id", None):
            cases = get_cases()
            case = cases.get(str(before.channel.id))

            # Pending cases are indexed per user because everyone shares one room.
            # Find the departing user's case and ignore the voice move used for pickup.
            if not (isinstance(case, dict) and str(case.get("guild_id")) == str(member.guild.id)):
                case = next(
                    (
                        item for item in cases.values()
                        if isinstance(item, dict)
                        and str(item.get("guild_id")) == str(member.guild.id)
                        and channel_id(item.get("user_id")) == member.id
                        and item.get("waiting_in_shared_room")
                        and channel_id(item.get("waiting_room_id")) == before.channel.id
                    ),
                    None,
                )
                if isinstance(case, dict):
                    if channel_id(case.get("claiming_staff_id")):
                        # Staff pickup moves the citizen from the queue into the supporter's call.
                        return
                    private_channel = member.guild.get_channel(channel_id(case.get("channel_id")))
                    await self._close_case(
                        private_channel if isinstance(private_channel, discord.VoiceChannel) else None,
                        case,
                        cfg,
                        reason="Bürger hat den Support-Warteraum verlassen",
                        kick_user=False,
                    )
            else:
                if channel_id(case.get("claiming_staff_id")):
                    # Claim/transfer moves the citizen between channels; don't mistake this for abandonment.
                    return
                if member.id == channel_id(case.get("user_id")):
                    await self._close_case(
                        before.channel, case, cfg,
                        reason="Bürger hat den Support-Sprachkanal verlassen",
                        kick_user=False,
                    )
                elif not before.channel.members:
                    await self._close_case(
                        before.channel, case, cfg,
                        reason="Support-Sprachkanal ist leer",
                        kick_user=False,
                    )

    async def _closed_hours(self, member: discord.Member, previous_channel, cfg: dict):
        try:
            await member.send("⏰ Der Voice-Support ist aktuell geschlossen. Bitte beachte die im Pulse-Dashboard eingestellten Öffnungszeiten.")
        except (discord.Forbidden, discord.HTTPException):
            pass
        # Intentionally never move a citizen out of the waiting room here.
        # After-hours requests are accepted as normal cases and remain queued
        # until a supporter claims the case or the citizen leaves voluntarily.
        destination = member.guild.get_channel(channel_id(cfg.get("notification_channel_id")))
        if isinstance(destination, discord.TextChannel) and cfg.get("notification_enabled", True):
            try:
                await send_styled(
                    destination,
                    embed=discord.Embed(
                        title="⏰ Support außerhalb der Öffnungszeiten",
                        description=f"{member.mention} hat den Warteraum betreten, aber es ist kein Supportfenster geöffnet.",
                        color=_pulse_color("warning"),
                    ),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except (discord.Forbidden, discord.HTTPException):
                pass

    def _configured_audio_path(self, cfg: dict, key: str) -> Path | None:
        """Resolve a configured audio file without relying on the browser-supplied filename."""
        raw = str(cfg.get(key) or "").strip()
        if not raw:
            return None
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = Path(__file__).resolve().parents[1] / candidate
        try:
            candidate = candidate.resolve()
            return candidate if candidate.is_file() and candidate.stat().st_size > 0 else None
        except (OSError, RuntimeError):
            return None

    def _after_hours_audio_path(self) -> Path | None:
        """Decode the bundled spoken notice once, keeping runtime audio outside the repo."""
        cached = Path(tempfile.gettempdir()) / "pulse_voice_support_after_hours_v1.mp3"
        try:
            if cached.is_file() and cached.stat().st_size > 1024:
                return cached
            source = Path(__file__).resolve().parents[1] / "assets" / "voice_support_after_hours.mp3.b64"
            encoded = source.read_text(encoding="ascii").strip()
            audio_bytes = base64.b64decode(encoded, validate=True)
            if len(audio_bytes) <= 1024:
                raise ValueError("Audiodatei ist leer oder unvollständig")
            cached.write_bytes(audio_bytes)
            return cached
        except (OSError, ValueError, binascii.Error) as exc:
            if not self._after_hours_audio_warning_logged:
                print(f"⚠️ Voice-Support: Außerhalb-Öffnungszeiten-Ansage nicht verfügbar: {exc}")
                self._after_hours_audio_warning_logged = True
            return None

    async def _failure_dm(self, member: discord.Member):
        try:
            await member.send("Deine Supportanfrage konnte nicht aufgenommen werden. Bitte versuche es erneut oder öffne ein Ticket.")
        except (discord.Forbidden, discord.HTTPException):
            pass

    async def _start_waiting_audio(self, case: dict, cfg: dict) -> bool:
        """Play an opening announcement once, then keep the selected music looping."""
        if not isinstance(case, dict) or case.get("staff_joined"):
            return False
        guild = self.bot.get_guild(channel_id(case.get("guild_id")))
        shared_wait = bool(case.get("waiting_in_shared_room"))
        room_id = (
            channel_id(case.get("waiting_room_id") or cfg.get("waiting_room_channel_id"))
            if shared_wait else channel_id(case.get("channel_id"))
        )
        channel = guild.get_channel(room_id) if guild else None
        if not isinstance(channel, discord.VoiceChannel):
            return False
        user_id = channel_id(case.get("user_id"))
        if not any(member.id == user_id for member in channel.members):
            return False

        is_after_hours = bool(case.get("outside_opening_hours"))
        mark_after_hours_notice = is_after_hours and not case.get("after_hours_notice_played")
        mark_opening_audio = False
        if is_after_hours:
            if not mark_after_hours_notice:
                # Outside opening hours, play only the one-time spoken notice.
                return False
            audio_path = (
                self._configured_audio_path(cfg, "custom_after_hours_audio_path")
                or self._after_hours_audio_path()
            )
        else:
            # The configured opening clip is an intro, never the repeating music.
            opening_path = self._configured_audio_path(cfg, "custom_opening_audio_path")
            if opening_path and not case.get("opening_audio_played"):
                audio_path = opening_path
                mark_opening_audio = True
            else:
                audio_path = (
                    self._configured_audio_path(cfg, "waiting_music_path")
                    if cfg.get("waiting_music_enabled") else None
                )

        if audio_path is None:
            if not is_after_hours and (
                cfg.get("waiting_music_enabled") or cfg.get("custom_opening_audio_path")
            ):
                if not self._music_warning_logged:
                    print("⚠️ Voice-Support: Eine konfigurierte Wartemusik-/Eröffnungs-Audiodatei fehlt oder ist leer.")
                    self._music_warning_logged = True
            return False

        voice_client = next((client for client in self.bot.voice_clients if client.guild.id == guild.id), None)
        try:
            if voice_client and (not voice_client.channel or voice_client.channel.id != channel.id):
                # A stale, idle connection in another channel must not block the queue.
                if voice_client.is_playing():
                    return False
                await voice_client.disconnect(force=True)
                voice_client = None
            if voice_client is None:
                voice_client = await channel.connect(self_deaf=True)
            if not voice_client.channel or voice_client.channel.id != channel.id:
                return False
            if voice_client.is_playing():
                return False

            after_playback = None
            if not is_after_hours:
                loop = self.bot.loop
                guild_id = guild.id
                voice_room_id = channel.id
                case_id = str(case.get("case_id", ""))

                def after_playback(error):
                    # Discord calls this callback from its audio-player thread.
                    # Only chain audio after clean completion; the 8-second loop
                    # remains responsible for retrying playback errors.
                    if error:
                        print(f"⚠️ Voice-Support: Audio-Wiedergabe beendet mit Fehler: {type(error).__name__}: {error}")
                        return

                    def schedule_next():
                        from pulse_tasks import spawn  # P-07: keep a reference, log failures
                        spawn(self._continue_waiting_audio(guild_id, voice_room_id, case_id),
                              name="voice-support-audio", loop=loop)

                    try:
                        loop.call_soon_threadsafe(schedule_next)
                    except RuntimeError:
                        pass

            voice_client.play(
                discord.FFmpegPCMAudio(str(audio_path), options="-vn"),
                after=after_playback,
            )
            if mark_after_hours_notice or mark_opening_audio:
                case_key, latest, latest_cases = self._find_case_by_id(str(case.get("case_id", "")))
                if case_key is not None and isinstance(latest, dict):
                    if mark_after_hours_notice:
                        latest["after_hours_notice_played"] = True
                        latest["after_hours_notice_played_at"] = datetime.now(ZoneInfo("Europe/Berlin")).isoformat(timespec="seconds")
                    if mark_opening_audio:
                        latest["opening_audio_played"] = True
                        latest["opening_audio_played_at"] = datetime.now(ZoneInfo("Europe/Berlin")).isoformat(timespec="seconds")
                    latest_cases[case_key] = latest
                    db.set_setting(CASES_KEY, latest_cases)
            audio_label = (
                "Ansage außerhalb der Öffnungszeiten" if mark_after_hours_notice
                else "Eröffnungsansage" if mark_opening_audio
                else "Wartemusik"
            )
            print(f"🎧 Voice-Support: {audio_label} gestartet · Kanal {channel.id} · Fall {case.get('case_id')}")
            return True
        except (discord.ClientException, discord.Forbidden, discord.HTTPException, RuntimeError, OSError) as exc:
            warning_flag = "_after_hours_audio_warning_logged" if is_after_hours else "_music_warning_logged"
            if not getattr(self, warning_flag):
                print(f"⚠️ Voice-Support: Audio konnte in Warteraum {channel.id} nicht gestartet werden: {type(exc).__name__}: {exc}")
                setattr(self, warning_flag, True)
            return False

    async def _continue_waiting_audio(self, guild_id: int, room_id: int, previous_case_id: str):
        """Advance from intro to looping music, including when a queue member leaves."""
        cfg = get_config()
        guild = self.bot.get_guild(channel_id(guild_id))
        channel = guild.get_channel(channel_id(room_id)) if guild else None
        if not cfg.get("active") or not isinstance(channel, discord.VoiceChannel):
            await self._disconnect_music(room_id)
            return

        present_user_ids = {member.id for member in channel.members}
        candidates = []
        for item in get_cases().values():
            if not isinstance(item, dict) or item.get("staff_joined"):
                continue
            if channel_id(item.get("guild_id")) != guild_id:
                continue
            target_room_id = (
                channel_id(item.get("waiting_room_id") or cfg.get("waiting_room_channel_id"))
                if item.get("waiting_in_shared_room")
                else channel_id(item.get("channel_id"))
            )
            if target_room_id != room_id or channel_id(item.get("user_id")) not in present_user_ids:
                continue
            candidates.append(item)

        # Play any newly-arrived user's intro once before returning to the music.
        opening_path = self._configured_audio_path(cfg, "custom_opening_audio_path")
        pending_notices = [
            item for item in candidates
            if item.get("outside_opening_hours") and not item.get("after_hours_notice_played")
        ]
        pending_openings = [
            item for item in candidates
            if not item.get("outside_opening_hours")
            and opening_path
            and not item.get("opening_audio_played")
        ]
        previous = next(
            (item for item in candidates if str(item.get("case_id", "")) == str(previous_case_id)),
            None,
        )
        ordered = []
        seen_ids = set()
        for item in [*pending_notices, *pending_openings, previous, *candidates]:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("case_id", ""))
            if not item_id or item_id in seen_ids:
                continue
            seen_ids.add(item_id)
            ordered.append(item)

        for item in ordered:
            if await self._start_waiting_audio(item, cfg):
                return

        # Nothing else is queued for this room (or all audio has been disabled).
        # Disconnect only the idle player in this room, never another active call.
        voice_client = next(
            (
                client for client in self.bot.voice_clients
                if client.guild.id == guild_id and client.channel and client.channel.id == room_id
            ),
            None,
        )
        if voice_client and not voice_client.is_playing():
            try:
                await voice_client.disconnect(force=True)
            except (discord.ClientException, discord.HTTPException):
                pass

    async def _open_case(self, member: discord.Member, cfg: dict, *, outside_opening_hours: bool | None = None):
        if outside_opening_hours is None:
            outside_opening_hours = not support_open_now(cfg)
        guild = member.guild
        waiting_room_id = channel_id(cfg.get("waiting_room_channel_id"))
        waiting_room = guild.get_channel(waiting_room_id)
        if not isinstance(waiting_room, discord.VoiceChannel):
            raise RuntimeError("Bitte einen gültigen gemeinsamen Support-Warteraum einstellen.")
        if not member.voice or not member.voice.channel or member.voice.channel.id != waiting_room.id:
            raise RuntimeError("Der Nutzer befindet sich nicht mehr im Support-Warteraum.")

        support_role = guild.get_role(channel_id(cfg.get("support_role_id")))
        duty_role = guild.get_role(channel_id(cfg.get("duty_role_id")))
        case_id = uuid.uuid4().hex[:12]
        case = {
            "case_id": case_id,
            "guild_id": str(guild.id),
            # Until claimed, the case points at the one shared waiting room.
            "channel_id": waiting_room.id,
            "user_id": str(member.id),
            "user_name": member.display_name,
            "room_name": str(cfg.get("room_name") or "Support")[:45],
            "created_at": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(timespec="seconds"),
            "created_by_pulse": False,
            "original_overwrites": [],
            "staff_joined": False,
            "outside_opening_hours": bool(outside_opening_hours),
            "after_hours_notice_played": False,
            "waiting_room_id": waiting_room.id,
            "waiting_in_shared_room": True,
            "private_channel_id": 0,
            "support_channel_id": 0,
            "support_original_overwrite": {},
            "assigned_staff_id": 0,
            "assigned_staff_name": "",
            "claiming_staff_id": 0,
            "private_room_cleaned": False,
            "notification_message_id": 0,
            "note_thread_id": 0,
        }
        # Several people can wait in the same channel, so store each pending case
        # under a unique user key instead of using the shared channel ID as the key.
        case_key = f"waiting:{member.id}"
        cases = get_cases()
        cases[case_key] = case
        db.set_setting(CASES_KEY, cases)

        # Do not create or move the citizen into a new voice channel here.
        # The citizen remains in the configured shared waiting room until a
        # supporter explicitly claims the case from the notification message.
        staff = await self._on_duty(guild, cfg) if cfg.get("duty_mode") else []
        ping_role = duty_role if cfg.get("duty_mode") else support_role
        ping = ping_role.mention if ping_role and cfg.get("notification_enabled", True) and (not cfg.get("duty_mode") or staff) else ""
        destination = guild.get_channel(channel_id(cfg.get("notification_channel_id")))
        if isinstance(destination, discord.TextChannel) and cfg.get("notification_enabled", True):
            embed = self._case_notification_embed(guild, case)
            try:
                message = await send_styled(
                    destination,
                    content=ping,
                    embed=embed,
                    view=SupportCaseView(self, case_id),
                    allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False),
                )
                case["notification_message_id"] = message.id
                self._register_case_view(case_id, message.id)
                if cfg.get("notes_enabled"):
                    await self._create_note_thread(destination, member, staff, case)
                latest_cases = get_cases()
                latest_cases[case_key] = case
                db.set_setting(CASES_KEY, latest_cases)
            except (discord.Forbidden, discord.HTTPException) as exc:
                print(f"⚠️ Voice-Support: Supportmeldung/Notizthread fehlgeschlagen: {exc}")

        await self._refresh_support_status(guild)
        # Join/play immediately on the voice-state event; music_loop remains the
        # retry mechanism if Discord is finishing an older voice connection.
        _, saved_case, _ = self._find_case_by_id(case_id)
        if isinstance(saved_case, dict):
            await self._start_waiting_audio(saved_case, cfg)
        print(f"🎧 Voice-Support-Fall {case_id}: {member} → gemeinsamer Warteraum {waiting_room.id}")

    async def _create_note_thread(self, parent: discord.TextChannel, member: discord.Member, staff: list[discord.Member], case: dict):
        try:
            thread = await parent.create_thread(
                name=f"Notizen-{re.sub(r'[^a-zA-Z0-9_-]+', '-', member.display_name)[:70]}"[:100],
                type=discord.ChannelType.private_thread, invitable=False, auto_archive_duration=1440,
                reason="Pulse Voice-Support: private Notizen",
            )
            for person in [member, *staff]:
                try:
                    await thread.add_user(person)
                except (discord.Forbidden, discord.HTTPException):
                    pass
            await send_styled(
                thread,
                embed=discord.Embed(
                    title="📝 Support-Notizen",
                    description=f"Fall {case['case_id']} · {member.display_name} · Sprachkanal {case['channel_id']}\nHier Gesprächsverlauf, Maßnahmen und Übergabe dokumentieren.",
                    color=_pulse_color("info"),
                ),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            case["note_thread_id"] = thread.id
            _, _, cases = self._find_case_by_id(str(case.get("case_id", "")))
            case_key = (
                f"waiting:{channel_id(case.get('user_id'))}"
                if case.get("waiting_in_shared_room")
                else str(channel_id(case.get("channel_id")))
            )
            cases[case_key] = case
            db.set_setting(CASES_KEY, cases)
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"⚠️ Voice-Support: Notiz-Thread konnte nicht erstellt werden: {exc}")

    async def _close_case(
        self,
        channel: discord.VoiceChannel | None,
        case: dict,
        cfg: dict,
        *,
        reason: str = "Supportfall geschlossen",
        kick_user: bool = False,
        closed_by: discord.Member | None = None,
    ):
        case_id = str(case.get("case_id") or "")
        if not case_id:
            return
        async with self._case_action_lock(case_id):
            await self._close_case_locked(channel, case, cfg, reason=reason, kick_user=kick_user, closed_by=closed_by)
        await self._refresh_support_status(self._guild() or (channel.guild if channel else None))

    async def _close_case_locked(
        self,
        channel: discord.VoiceChannel | None,
        case: dict,
        cfg: dict,
        *,
        reason: str = "Supportfall geschlossen",
        kick_user: bool = False,
        closed_by: discord.Member | None = None,
    ):
        case_id = str(case.get("case_id") or "")
        if not case_id or case_id in self._closing_cases:
            return
        key, current, cases = self._find_case_by_id(case_id)
        if key is None or not isinstance(current, dict):
            return

        self._closing_cases.add(case_id)
        latest = dict(current)
        cases.pop(key, None)
        db.set_setting(CASES_KEY, cases)

        try:
            guild = self.bot.get_guild(channel_id(latest.get("guild_id"))) or (channel.guild if channel else None)
            if guild is None:
                print(f"⚠️ Voice-Support: Server für Fall {case_id} nicht mehr verfügbar.")
                return

            user_id = channel_id(latest.get("user_id"))
            member = guild.get_member(user_id)
            if member is None and user_id:
                try:
                    member = await guild.fetch_member(user_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    member = None

            support_channel_id = channel_id(latest.get("support_channel_id"))
            support_channel = guild.get_channel(support_channel_id) if support_channel_id else None
            active_channel = support_channel if isinstance(support_channel, discord.VoiceChannel) else guild.get_channel(channel_id(latest.get("channel_id")))
            if latest.get("waiting_in_shared_room"):
                # Ending a queued case must never disconnect a citizen from the
                # shared room while they are waiting for a supporter. They leave manually.
                active_channel = None
            if kick_user and member and isinstance(active_channel, discord.VoiceChannel):
                if member.voice and member.voice.channel and member.voice.channel.id == active_channel.id:
                    try:
                        await member.move_to(None, reason=f"Pulse Voice-Support: Supportfall {case_id} beendet")
                    except (discord.Forbidden, discord.HTTPException) as exc:
                        print(f"⚠️ Voice-Support: Bürger {member.id} konnte nach Fallende nicht aus dem Sprachkanal entfernt werden: {exc}")

            if support_channel_id and isinstance(support_channel, discord.VoiceChannel) and member:
                await self._restore_member_overwrite(
                    support_channel,
                    member,
                    latest.get("support_original_overwrite", {}),
                    f"Pulse Voice-Support: temporäre Rechte für Fall {case_id} zurückgesetzt",
                )

            private_id = channel_id(latest.get("private_channel_id"))
            if not private_id and not support_channel_id and not latest.get("waiting_in_shared_room"):
                private_id = channel_id(latest.get("channel_id"))
            private_channel = guild.get_channel(private_id) if private_id and private_id != support_channel_id else None
            music_channel_id = private_id or (channel.id if channel else 0)
            waiting_room_id = channel_id(cfg.get("waiting_room_channel_id"))
            other_shared_waiters = any(
                isinstance(item, dict)
                and item.get("waiting_in_shared_room")
                and channel_id(item.get("guild_id")) == guild.id
                and channel_id(item.get("user_id")) != user_id
                for item in get_cases().values()
            )
            if music_channel_id and (music_channel_id != waiting_room_id or not other_shared_waiters):
                await self._disconnect_music(music_channel_id)
            if isinstance(private_channel, discord.VoiceChannel) and not latest.get("private_room_cleaned"):
                try:
                    if latest.get("created_by_pulse"):
                        await private_channel.delete(reason=f"Pulse Voice-Support: Fall {case_id} geschlossen")
                    else:
                        await private_channel.edit(
                            overwrites=deserialize_overwrites(guild, latest.get("original_overwrites", [])),
                            reason=f"Pulse Voice-Support: ursprüngliche Rechte für Fall {case_id} wiederhergestellt",
                        )
                except (discord.Forbidden, discord.HTTPException) as exc:
                    print(f"⚠️ Voice-Support: temporärer Kanal {private_id} konnte nicht bereinigt werden: {exc}")

            created_raw = str(latest.get("created_at") or "")
            start_raw = str(latest.get("support_started_at") or created_raw)
            now = datetime.now(ZoneInfo("Europe/Berlin"))
            try:
                created_dt = datetime.fromisoformat(created_raw)
            except (TypeError, ValueError):
                created_dt = now
            try:
                start_dt = datetime.fromisoformat(start_raw)
            except (TypeError, ValueError):
                start_dt = created_dt
            elapsed = max(0, int((now - start_dt).total_seconds()))
            duration = f"{elapsed // 60}:{elapsed % 60:02d}m"
            supporter_id = channel_id(latest.get("assigned_staff_id"))
            try:
                db.record_voice_support_case(
                    case_id=case_id,
                    guild_id=guild.id,
                    user_id=user_id,
                    user_name=latest.get("user_name") or (member.display_name if member else "Unbekannt"),
                    assigned_staff_id=supporter_id or None,
                    assigned_staff_name=latest.get("assigned_staff_name") or "",
                    created_at=created_raw,
                    support_started_at=start_raw,
                    duration_seconds=elapsed,
                    close_reason=reason,
                    closed_by_id=closed_by.id if closed_by else None,
                    closed_by_name=closed_by.display_name if closed_by else "",
                    outside_opening_hours=bool(latest.get("outside_opening_hours")),
                )
            except Exception as exc:
                print(f"⚠️ Voice-Support: Fall {case_id} konnte nicht für Teamstatistiken gespeichert werden: {exc}")
            case_tag = f"#S-{case_id[:9].upper()}"

            user_label = member.mention if member else str(latest.get("user_name") or "Unbekannter Bürger")
            if supporter_id and reason == "Supportfall vom Team beendet":
                result_description = f"{user_label} wurde von <@{supporter_id}> erfolgreich supportet!"
            elif supporter_id:
                result_description = f"Der Supportfall für {user_label} mit <@{supporter_id}> wurde geschlossen."
            else:
                result_description = f"Der Supportfall für {user_label} wurde automatisch geschlossen."
            result_embed = discord.Embed(
                title="✅ Support Fall beendet",
                description=result_description,
                color=_pulse_color("success") if supporter_id and reason == "Supportfall vom Team beendet" else _pulse_color("neutral"),
                timestamp=discord.utils.utcnow(),
            )
            result_embed.add_field(name="CaseID", value=case_tag, inline=True)
            result_embed.add_field(name="Erstellt am", value=created_dt.strftime("%d.%m.%Y %H:%M"), inline=True)
            result_embed.add_field(name="Nutzer", value=member.mention if member else str(latest.get("user_name") or "Unbekannt"), inline=False)
            result_embed.add_field(name="Supporter", value=f"<@{supporter_id}>" if supporter_id else "Nicht übernommen", inline=True)
            result_embed.add_field(name="Dauer", value=duration, inline=True)
            result_embed.add_field(name="Abschlussgrund", value=reason[:1024], inline=False)
            if closed_by:
                result_embed.add_field(name="Beendet von", value=closed_by.mention, inline=True)
            result_embed.set_footer(text="Pulse TeamOS · Support-Protokoll")

            notification = guild.get_channel(channel_id(cfg.get("notification_channel_id")))
            notification_message = None
            if isinstance(notification, discord.TextChannel):
                message_id = channel_id(latest.get("notification_message_id"))
                if message_id:
                    try:
                        notification_message = await notification.fetch_message(message_id)
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        notification_message = None
                try:
                    if notification_message:
                        await edit_styled(
                            notification_message,
                            content=None,
                            embed=result_embed,
                            view=None,
                            allowed_mentions=discord.AllowedMentions.none(),
                        )
                    elif notification.id != SUPPORT_REPORT_CHANNEL_ID:
                        await send_styled(notification, embed=result_embed, allowed_mentions=discord.AllowedMentions.none())
                except (discord.Forbidden, discord.HTTPException) as exc:
                    print(f"⚠️ Voice-Support: Abschlussmeldung im Benachrichtigungskanal fehlgeschlagen: {exc}")

            report = guild.get_channel(SUPPORT_REPORT_CHANNEL_ID)
            if isinstance(report, discord.TextChannel):
                already_posted_in_report = isinstance(notification, discord.TextChannel) and notification.id == report.id and notification_message is not None
                if not already_posted_in_report:
                    try:
                        await send_styled(report, embed=result_embed, allowed_mentions=discord.AllowedMentions.none())
                    except (discord.Forbidden, discord.HTTPException) as exc:
                        print(f"⚠️ Voice-Support: Abschlussprotokoll in Kanal {SUPPORT_REPORT_CHANNEL_ID} fehlgeschlagen: {exc}")
            else:
                print(f"⚠️ Voice-Support: Abschlusskanal {SUPPORT_REPORT_CHANNEL_ID} nicht gefunden oder kein Textkanal.")

            if member and cfg.get("ratings_enabled"):
                await self._ask_rating(member, latest)

            thread = guild.get_thread(channel_id(latest.get("note_thread_id"))) if channel_id(latest.get("note_thread_id")) else None
            if thread:
                try:
                    await thread.send(f"✅ Supportfall geschlossen. Grund: {reason}")
                    await thread.edit(archived=True, locked=True)
                except (discord.Forbidden, discord.HTTPException):
                    pass
            print(f"✅ Voice-Support-Fall {case_id} geschlossen · Grund: {reason}")
        finally:
            self._closing_cases.discard(case_id)

    async def _ask_rating(self, member: discord.Member, case: dict):
        case_id = str(case.get("case_id") or uuid.uuid4().hex[:12])
        pending = db.get_setting(PENDING_KEY, [])
        pending = pending if isinstance(pending, list) else []
        pending = [x for x in pending if not (isinstance(x, dict) and str(x.get("case_id")) == case_id)]
        pending.append({"case_id": case_id, "user_id": str(member.id)})
        db.set_setting(PENDING_KEY, pending[-500:])
        try:
            await send_styled(
                member,
                embed=discord.Embed(title="⭐ Wie war dein Support?", description=f"Bewerte Fall **{case_id}** bitte mit 1 bis 5 Sternen.", color=_pulse_color("info")),
                view=RatingView(case_id, member.id),
            )
        except (discord.Forbidden, discord.HTTPException):
            db.set_setting(PENDING_KEY, [x for x in pending if not (isinstance(x, dict) and str(x.get("case_id")) == case_id)])

    async def _closed_music(self, channel_id_: int):
        await self._disconnect_music(channel_id_)

    async def _disconnect_music(self, channel_id_: int):
        for client in list(self.bot.voice_clients):
            if client.channel and client.channel.id == channel_id_:
                try:
                    if client.is_playing():
                        client.stop()
                    await client.disconnect(force=True)
                except (discord.ClientException, discord.HTTPException):
                    pass

    @tasks.loop(minutes=1)
    async def duty_status_loop(self):
        cfg = get_config()
        guild = self._guild()
        if guild is None:
            return
        if cfg.get("duty_mode"):
            # The dashboard may automatically close an overlong shift. Reconcile on
            # every cycle so the Discord duty role cannot remain stale after that.
            await self._reconcile_duty_roles(guild, cfg)
            if cfg.get("duty_message_enabled"):
                await self._refresh_duty_status(guild)
        await self._refresh_support_status(guild)

    @duty_status_loop.before_loop
    async def before_duty_status_loop(self):
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=8)
    async def music_loop(self):
        cfg = get_config()
        if not cfg.get("active"):
            return

        cases = list(get_cases().values())

        # Pending after-hours notices have priority over optional waiting music.
        for case in cases:
            if (
                not isinstance(case, dict)
                or case.get("staff_joined")
                or not case.get("outside_opening_hours")
                or case.get("after_hours_notice_played")
            ):
                continue
            if await self._start_waiting_audio(case, cfg):
                return

        # Once every citizen has either left or been picked up, disconnect from
        # the shared waiting room so the bot does not remain there indefinitely.
        waiting_room_id = channel_id(cfg.get("waiting_room_channel_id"))
        support_guild_id = channel_id(cfg.get("guild_id"))
        active_shared_wait = any(
            isinstance(item, dict)
            and item.get("waiting_in_shared_room")
            and not item.get("staff_joined")
            and channel_id(item.get("guild_id")) == support_guild_id
            for item in cases
        )
        if waiting_room_id and support_guild_id and not active_shared_wait:
            for voice_client in list(self.bot.voice_clients):
                if (
                    voice_client.guild.id == support_guild_id
                    and voice_client.channel
                    and voice_client.channel.id == waiting_room_id
                    and not voice_client.is_playing()
                ):
                    try:
                        await voice_client.disconnect(force=True)
                    except (discord.ClientException, discord.HTTPException):
                        pass

        # During opening hours the configured waiting-music file loops in the
        # shared waiting room; custom opening audio is not used as queue music.
        for case in cases:
            if not isinstance(case, dict) or case.get("staff_joined") or case.get("outside_opening_hours"):
                continue
            if await self._start_waiting_audio(case, cfg):
                return

    @music_loop.before_loop
    async def before_music_loop(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(VoiceSupport(bot))
