"""Configurable Pulse voice support, synced to the dashboard shift system."""
from __future__ import annotations

import re
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands, tasks
import pulse_db as db
from pulse_voice_support_utils import DEFAULT_HOURS, DAYS, DAY_NAMES, shift_is_online, support_open_now

CONFIG_KEY = "voice_support_config"
CASES_KEY = "voice_support_cases"
RATINGS_KEY = "voice_support_ratings"
PENDING_KEY = "voice_support_pending_ratings"
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



def private_overwrites(guild: discord.Guild, member: discord.Member, staff_roles: list[discord.Role]) -> dict:
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False, connect=False),
        member: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True, stream=True),
    }
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
        await interaction.response.edit_message(
            content=f"✅ Danke! Du hast **{self.rating}/5 Sterne** vergeben.", view=None
        )


class RatingView(discord.ui.View):
    def __init__(self, case_id: str, user_id: int):
        super().__init__(timeout=None)
        for value in range(1, 6):
            self.add_item(RatingButton(case_id, user_id, value))


class VoiceSupport(commands.Cog):
    """Opens private temporary voice rooms from a configured waiting room."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._opening: set[int] = set()
        self._music_warning_logged = False
        self.duty_status_loop.start()
        self.music_loop.start()

    def cog_unload(self):
        self.duty_status_loop.cancel()
        self.music_loop.cancel()

    def _guild(self):
        gid = channel_id(get_config().get("guild_id"))
        return self.bot.get_guild(gid) if gid else None

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
            color=discord.Color.green() if staff else discord.Color.orange(),
        )
        embed.add_field(name="Dienstquelle", value="Automatisch mit den Pulse-Dashboard-Schichten synchronisiert", inline=False)
        embed.set_footer(text="Pulse Voice-Support · Automatisch aktualisiert")
        msg = None
        saved_id = channel_id(cfg.get("duty_status_message_id"))
        if saved_id:
            try:
                msg = await channel.fetch_message(saved_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                msg = None
        try:
            if msg and msg.embeds and msg.embeds[0].to_dict() == embed.to_dict():
                return
            if msg:
                await msg.edit(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            else:
                msg = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
                cfg["duty_status_message_id"] = msg.id
                db.set_setting(CONFIG_KEY, cfg)
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"⚠️ Voice-Support: Bereitschaftsnachricht konnte nicht aktualisiert werden: {exc}")

    @commands.Cog.listener()
    async def on_ready(self):
        cfg = get_config()
        gid = channel_id(cfg.get("guild_id"))
        guild = self.bot.get_guild(gid) if gid else None
        if guild:
            await self._reconcile_duty_roles(guild, cfg)
            await self._refresh_duty_status(guild)
        pending = db.get_setting(PENDING_KEY, [])
        for item in pending if isinstance(pending, list) else []:
            try:
                self.bot.add_view(RatingView(str(item["case_id"]), int(item["user_id"])))
            except (KeyError, TypeError, ValueError):
                pass
        print("🎧 Pulse Voice-Support geladen · Bereitschaft wird aus der Dashboard-Schicht übernommen.")

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
            if isinstance(case, dict) and member.id != channel_id(case.get("user_id")) and staff_roles.intersection(r.id for r in member.roles):
                case["staff_joined"] = True
                db.set_setting(CASES_KEY, cases)
                await self._disconnect_music(after.channel.id)

        wait_id = channel_id(cfg.get("waiting_room_channel_id"))
        if cfg.get("active") and wait_id and after.channel and after.channel.id == wait_id and (not before.channel or before.channel.id != wait_id):
            if member.id in self._opening:
                return
            if not support_open_now(cfg):
                await self._closed_hours(member, before.channel, cfg)
                return
            self._opening.add(member.id)
            try:
                cases = get_cases()
                old = next((x for x in cases.values() if isinstance(x, dict) and str(x.get("guild_id")) == str(member.guild.id) and str(x.get("user_id")) == str(member.id)), None)
                if old:
                    channel = member.guild.get_channel(channel_id(old.get("channel_id")))
                    if isinstance(channel, discord.VoiceChannel):
                        await member.move_to(channel, reason="Pulse Voice-Support: vorhandenen Fall fortsetzen")
                        return
                await self._open_case(member, cfg)
            except (discord.Forbidden, discord.HTTPException, RuntimeError) as exc:
                print(f"❌ Voice-Support: Fall für {member.id} konnte nicht eröffnet werden: {exc}")
                await self._failure_dm(member)
            finally:
                self._opening.discard(member.id)

        if before.channel and before.channel.id != getattr(after.channel, "id", None):
            case = get_cases().get(str(before.channel.id))
            if isinstance(case, dict) and str(case.get("guild_id")) == str(member.guild.id) and not before.channel.members:
                await self._close_case(before.channel, case, cfg)

    async def _closed_hours(self, member: discord.Member, previous_channel, cfg: dict):
        try:
            await member.send("⏰ Der Voice-Support ist aktuell geschlossen. Bitte beachte die im Pulse-Dashboard eingestellten Öffnungszeiten.")
        except (discord.Forbidden, discord.HTTPException):
            pass
        try:
            await member.move_to(previous_channel, reason="Pulse Voice-Support: außerhalb der Öffnungszeiten")
        except (discord.Forbidden, discord.HTTPException):
            pass
        destination = member.guild.get_channel(channel_id(cfg.get("notification_channel_id")))
        if isinstance(destination, discord.TextChannel) and cfg.get("notification_enabled", True):
            try:
                await destination.send(
                    embed=discord.Embed(
                        title="⏰ Support außerhalb der Öffnungszeiten",
                        description=f"{member.mention} hat den Warteraum betreten, aber es ist kein Supportfenster geöffnet.",
                        color=discord.Color.orange(),
                    ),
                    allowed_mentions=discord.AllowedMentions(users=True, roles=False),
                )
            except (discord.Forbidden, discord.HTTPException):
                pass

    async def _failure_dm(self, member: discord.Member):
        try:
            await member.send("Dein Supportraum konnte nicht erstellt werden. Bitte versuche es erneut oder öffne ein Ticket.")
        except (discord.Forbidden, discord.HTTPException):
            pass

    async def _open_case(self, member: discord.Member, cfg: dict):
        guild = member.guild
        category = guild.get_channel(channel_id(cfg.get("category_id")))
        if not isinstance(category, discord.CategoryChannel):
            raise RuntimeError("Bitte eine gültige Support-Kategorie einstellen.")
        support_role = guild.get_role(channel_id(cfg.get("support_role_id")))
        duty_role = guild.get_role(channel_id(cfg.get("duty_role_id")))
        roles = [duty_role] if cfg.get("duty_mode") and duty_role else [support_role] if support_role else []
        prefix = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(cfg.get("channel_prefix") or "support").casefold()).strip("-_")[:15] or "support"
        cases = get_cases()
        used = {channel_id(x.get("channel_id", key)) for key, x in cases.items() if isinstance(x, dict)}
        existing = None
        if cfg.get("use_existing_channels"):
            existing = next(
                (v for v in category.voice_channels if v.id not in used and v.id != channel_id(cfg.get("waiting_room_channel_id")) and not v.members and v.name.casefold().startswith(prefix.casefold())),
                None,
            )
        created = not isinstance(existing, discord.VoiceChannel)
        snapshot = []
        if existing:
            snapshot = serialize_overwrites(existing)
            # Replace existing permissions temporarily; the original overwrites are saved and restored on close.
            overwrites = private_overwrites(guild, member, roles)
            channel = await existing.edit(overwrites=overwrites, reason=f"Pulse Voice-Support: temporär für {member} geschützt")
        else:
            channel = await guild.create_voice_channel(
                name=f"{prefix}-{re.sub(r'[^a-zA-Z0-9_-]+', '-', member.display_name).strip('-')[:70] or str(member.id)}"[:95],
                category=category,
                overwrites=private_overwrites(guild, member, roles),
                reason=f"Pulse Voice-Support: Supportfall für {member}",
            )
        case_id = uuid.uuid4().hex[:12]
        case = {
            "case_id": case_id, "guild_id": str(guild.id), "channel_id": channel.id,
            "user_id": str(member.id), "user_name": member.display_name,
            "room_name": str(cfg.get("room_name") or "Support")[:45],
            "created_at": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(timespec="seconds"),
            "created_by_pulse": created, "original_overwrites": snapshot, "staff_joined": False,
            "notification_message_id": 0, "note_thread_id": 0,
        }
        cases[str(channel.id)] = case
        db.set_setting(CASES_KEY, cases)
        try:
            await member.move_to(channel, reason="Pulse Voice-Support: privater Raum bereit")
        except Exception:
            cases.pop(str(channel.id), None)
            db.set_setting(CASES_KEY, cases)
            if created:
                try:
                    await channel.delete(reason="Pulse Voice-Support: Start fehlgeschlagen")
                except (discord.Forbidden, discord.HTTPException):
                    pass
            else:
                try:
                    await channel.edit(overwrites=deserialize_overwrites(guild, snapshot), reason="Pulse Voice-Support: ursprüngliche Rechte wiederhergestellt")
                except (discord.Forbidden, discord.HTTPException):
                    pass
            raise

        staff = await self._on_duty(guild, cfg) if cfg.get("duty_mode") else []
        ping_role = duty_role if cfg.get("duty_mode") else support_role
        ping = ping_role.mention if ping_role and cfg.get("notification_enabled", True) and (not cfg.get("duty_mode") or staff) else ""
        destination = guild.get_channel(channel_id(cfg.get("notification_channel_id")))
        if isinstance(destination, discord.TextChannel) and cfg.get("notification_enabled", True):
            embed = discord.Embed(
                title=f"🎧 Neuer {case['room_name']}",
                description=f"**Nutzer:** {member.mention} ({member.id})\n**Raum:** {channel.mention}\n**Status:** {'🟢 Team im Dienst' if staff else '🟠 Warte auf Team' if cfg.get('duty_mode') else '🟢 Anfrage eingegangen'}",
                color=discord.Color.green() if staff or not cfg.get("duty_mode") else discord.Color.orange(),
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(name="Bereitschaft", value="Mit der Pulse-Dashboard-Schicht synchronisiert" if cfg.get("duty_mode") else "Benachrichtigung an Support-Rolle", inline=False)
            embed.set_footer(text=f"Pulse Voice-Support · Fall {case_id}")
            try:
                message = await destination.send(content=ping, embed=embed, allowed_mentions=discord.AllowedMentions(roles=True, users=False))
                case["notification_message_id"] = message.id
                if cfg.get("notes_enabled"):
                    await self._create_note_thread(destination, member, staff, case)
                cases[str(channel.id)] = case
                db.set_setting(CASES_KEY, cases)
            except (discord.Forbidden, discord.HTTPException) as exc:
                print(f"⚠️ Voice-Support: Supportmeldung/Notizthread fehlgeschlagen: {exc}")

        if cfg.get("waiting_music_enabled") and cfg.get("waiting_music_path"):
            path = Path(__file__).resolve().parents[1] / str(cfg["waiting_music_path"])
            if not path.is_file() and not self._music_warning_logged:
                print("⚠️ Voice-Support: Wartemusik aktiviert, aber Audiodatei fehlt.")
                self._music_warning_logged = True
        print(f"🎧 Voice-Support-Fall {case_id}: {member} → {channel.id}")

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
            await thread.send(
                embed=discord.Embed(
                    title="📝 Support-Notizen",
                    description=f"Fall {case['case_id']} · {member.display_name} · Sprachkanal {case['channel_id']}\nHier Gesprächsverlauf, Maßnahmen und Übergabe dokumentieren.",
                    color=discord.Color.blurple(),
                ),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            case["note_thread_id"] = thread.id
            cases = get_cases()
            cases[str(case["channel_id"])] = case
            db.set_setting(CASES_KEY, cases)
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"⚠️ Voice-Support: Notiz-Thread konnte nicht erstellt werden: {exc}")

    async def _close_case(self, channel: discord.VoiceChannel, case: dict, cfg: dict):
        cases = get_cases()
        latest = cases.pop(str(channel.id), case)
        db.set_setting(CASES_KEY, cases)
        await self._disconnect_music(channel.id)
        guild = channel.guild
        if latest.get("created_by_pulse"):
            try:
                await channel.delete(reason="Pulse Voice-Support: Raum leer, Fall geschlossen")
            except (discord.Forbidden, discord.HTTPException) as exc:
                print(f"⚠️ Voice-Support: Kanal konnte nicht gelöscht werden: {exc}")
        else:
            try:
                await channel.edit(overwrites=deserialize_overwrites(guild, latest.get("original_overwrites", [])), reason="Pulse Voice-Support: Rechte wiederhergestellt")
            except (discord.Forbidden, discord.HTTPException) as exc:
                print(f"⚠️ Voice-Support: Kanalrechte konnten nicht wiederhergestellt werden: {exc}")

        destination = guild.get_channel(channel_id(cfg.get("notification_channel_id")))
        if isinstance(destination, discord.TextChannel) and cfg.get("notification_enabled", True):
            embed = discord.Embed(
                title="✅ Voice-Support geschlossen",
                description=f"**Nutzer:** {latest.get('user_name', 'Unbekannt')}\n**Fall:** {latest.get('case_id')}\n**Dauer:** seit {latest.get('created_at', 'unbekannt')}",
                color=discord.Color.dark_grey(), timestamp=discord.utils.utcnow(),
            )
            msg_id = channel_id(latest.get("notification_message_id"))
            try:
                msg = await destination.fetch_message(msg_id) if msg_id else None
                if msg:
                    await msg.edit(content=None, embed=embed, allowed_mentions=discord.AllowedMentions.none())
                else:
                    await destination.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            except (discord.Forbidden, discord.HTTPException):
                pass

        member = guild.get_member(channel_id(latest.get("user_id")))
        if member and cfg.get("ratings_enabled"):
            await self._ask_rating(member, latest)
        thread = guild.get_thread(channel_id(latest.get("note_thread_id"))) if channel_id(latest.get("note_thread_id")) else None
        if thread:
            try:
                await thread.send("✅ Supportfall geschlossen.")
                await thread.edit(archived=True, locked=True)
            except (discord.Forbidden, discord.HTTPException):
                pass

    async def _ask_rating(self, member: discord.Member, case: dict):
        case_id = str(case.get("case_id") or uuid.uuid4().hex[:12])
        pending = db.get_setting(PENDING_KEY, [])
        pending = pending if isinstance(pending, list) else []
        pending = [x for x in pending if not (isinstance(x, dict) and str(x.get("case_id")) == case_id)]
        pending.append({"case_id": case_id, "user_id": str(member.id)})
        db.set_setting(PENDING_KEY, pending[-500:])
        try:
            await member.send(
                embed=discord.Embed(title="⭐ Wie war dein Support?", description=f"Bewerte Fall **{case_id}** bitte mit 1 bis 5 Sternen.", color=discord.Color.blurple()),
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
        if cfg.get("duty_mode") and cfg.get("duty_message_enabled"):
            await self._refresh_duty_status()

    @duty_status_loop.before_loop
    async def before_duty_status_loop(self):
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=8)
    async def music_loop(self):
        cfg = get_config()
        audio_value = str(cfg.get("waiting_music_path") or "")
        if not cfg.get("active") or not cfg.get("waiting_music_enabled") or not audio_value:
            return
        path = Path(__file__).resolve().parents[1] / audio_value
        if not path.is_file():
            return
        for case in get_cases().values():
            if not isinstance(case, dict) or case.get("staff_joined"):
                continue
            guild = self.bot.get_guild(channel_id(case.get("guild_id")))
            channel = guild.get_channel(channel_id(case.get("channel_id"))) if guild else None
            if not isinstance(channel, discord.VoiceChannel) or not any(m.id == channel_id(case.get("user_id")) for m in channel.members):
                continue
            if any(vc.guild.id == guild.id and vc.channel and vc.channel.id != channel.id for vc in self.bot.voice_clients):
                continue
            vc = next((v for v in self.bot.voice_clients if v.guild.id == guild.id), None)
            try:
                if vc is None:
                    vc = await channel.connect(self_deaf=True)
                if vc.channel and vc.channel.id == channel.id and not vc.is_playing():
                    vc.play(discord.FFmpegPCMAudio(str(path), options="-vn"))
            except (discord.ClientException, discord.Forbidden, discord.HTTPException, RuntimeError) as exc:
                if not self._music_warning_logged:
                    print(f"⚠️ Voice-Support: Wartemusik nicht verfügbar: {exc}")
                    self._music_warning_logged = True

    @music_loop.before_loop
    async def before_music_loop(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(VoiceSupport(bot))
