import json
import sqlite3
from datetime import datetime
from pathlib import Path
import zoneinfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

from pulse_activity_utils import (
    activity_action_is_terminal,
    activity_warning_due,
    activity_warning_id,
    confirmed_by_deadline,
    leave_covers_date,
)

BASE_DIR = Path(__file__).resolve().parents[1]
DB_NAME = str(BASE_DIR / "activity_check.db")
ABMELDUNGEN_DB = str(BASE_DIR / "abmeldungen.db")
BERLIN_TZ = zoneinfo.ZoneInfo("Europe/Berlin")
SYSTEM_ACTOR = "Pulse – automatische Activity-Check-Kontrolle"
WARNING_CHANNEL_ACTION = "activity-check-warning"


def init_db():
    with sqlite3.connect(DB_NAME, timeout=5.0) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            message_id INTEGER,
            check_date TEXT NOT NULL,
            created_at TEXT NOT NULL,
            snapshot_version INTEGER NOT NULL DEFAULT 0,
            UNIQUE(guild_id, check_date)
        );
        CREATE TABLE IF NOT EXISTS check_members (
            check_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            user_name TEXT NOT NULL,
            role_name TEXT DEFAULT "",
            added_at TEXT NOT NULL,
            PRIMARY KEY(check_id,user_id)
        );
        CREATE INDEX IF NOT EXISTS idx_check_members_check ON check_members(check_id);
        CREATE TABLE IF NOT EXISTS responses (
            check_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            reacted_at TEXT NOT NULL,
            PRIMARY KEY(check_id,user_id)
        );
        CREATE TABLE IF NOT EXISTS activity_warn_actions (
            check_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            warning_id TEXT,
            updated_at TEXT NOT NULL,
            error TEXT DEFAULT "",
            PRIMARY KEY(check_id,user_id)
        );
        """)
        # Migrate databases created by older Pulse releases. A zero version means
        # that the old row may predate immutable snapshots; new checks set version 1.
        columns = {row[1] for row in conn.execute("PRAGMA table_info(checks)").fetchall()}
        if "snapshot_version" not in columns:
            conn.execute(
                "ALTER TABLE checks ADD COLUMN snapshot_version INTEGER NOT NULL DEFAULT 0"
            )


def set_config(key, value):
    with sqlite3.connect(DB_NAME, timeout=5.0) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO config(key,value) VALUES(?,?)",
            (key, str(value)),
        )


def get_config(key):
    with sqlite3.connect(DB_NAME, timeout=5.0) as conn:
        row = conn.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
        return row[0] if row else None


def set_warn_action(check_id: int, user_id: int, status: str, warning_id: str | None = None, error: str = ""):
    """Persist each member's warning outcome so retries never create duplicate warnings."""
    now = datetime.now(BERLIN_TZ).isoformat(timespec="seconds")
    with sqlite3.connect(DB_NAME, timeout=5.0) as conn:
        conn.execute(
            """INSERT INTO activity_warn_actions
               (check_id,user_id,status,warning_id,updated_at,error)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(check_id,user_id) DO UPDATE SET
                 status=excluded.status,
                 warning_id=COALESCE(excluded.warning_id, activity_warn_actions.warning_id),
                 updated_at=excluded.updated_at,
                 error=excluded.error""",
            (int(check_id), int(user_id), status, warning_id, now, str(error or "")[:500]),
        )


def get_warn_action(check_id: int, user_id: int):
    with sqlite3.connect(DB_NAME, timeout=5.0) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT status,warning_id,error FROM activity_warn_actions WHERE check_id=? AND user_id=?",
            (int(check_id), int(user_id)),
        ).fetchone()
        return dict(row) if row else None


def is_abgemeldet(user_id: int, check_date: str) -> bool:
    """Query the shared Abmeldungsmodul database for the exact check date."""
    try:
        with sqlite3.connect(ABMELDUNGEN_DB, timeout=3.0) as conn:
            rows = conn.execute(
                "SELECT von,bis FROM abmeldungen WHERE user_id=?",
                (int(user_id),),
            ).fetchall()
    except (sqlite3.Error, OSError, TypeError, ValueError):
        # Do not issue automated warnings if the leave system cannot be checked.
        return True
    return any(leave_covers_date(von, bis, check_date) for von, bis in rows)


class ActivityView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Aktivität bestätigen",
        style=discord.ButtonStyle.success,
        emoji="✅",
        custom_id="pulse_activity_confirm",
    )
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
        except discord.NotFound:
            print("⚠️ Activity Check: Unknown interaction – Bestätigung kam bereits abgelaufen beim Bot an.")
            return
        except discord.HTTPException as exc:
            print(f"⚠️ Activity Check: Interaktion konnte nicht bestätigt werden ({type(exc).__name__}).")
            return

        response_text = "⚠️ Deine Bestätigung konnte nicht verarbeitet werden."
        try:
            message_id = interaction.message.id if interaction.message else 0
            check_id = self.cog.current_check_id(message_id)
            if not check_id:
                response_text = "⚠️ Dieser Activity Check ist nicht mehr aktiv."
            else:
                with sqlite3.connect(DB_NAME, timeout=5.0) as conn:
                    check_row = conn.execute(
                        "SELECT snapshot_version FROM checks WHERE id=?",
                        (int(check_id),),
                    ).fetchone()
                    snapshot_version = int(check_row[0] or 0) if check_row else 0
                    has_snapshot = conn.execute(
                        "SELECT 1 FROM check_members WHERE check_id=? LIMIT 1",
                        (check_id,),
                    ).fetchone()
                    member_row = conn.execute(
                        "SELECT 1 FROM check_members WHERE check_id=? AND user_id=?",
                        (check_id, interaction.user.id),
                    ).fetchone()
                    if (has_snapshot or snapshot_version >= 1) and not member_row:
                        response_text = "⚠️ Du warst bei diesem Activity Check nicht als Teammitglied erfasst."
                    else:
                        conn.execute(
                            "INSERT OR REPLACE INTO responses(check_id,user_id,reacted_at) VALUES(?,?,?)",
                            (check_id, interaction.user.id, datetime.now(BERLIN_TZ).isoformat()),
                        )
                        response_text = "✅ Aktivität für diesen Activity Check bestätigt."
        except sqlite3.Error as exc:
            print(f"⚠️ Activity Check: Datenbankfehler beim Speichern einer Bestätigung ({type(exc).__name__}).")
            response_text = "⚠️ Deine Bestätigung konnte nicht gespeichert werden. Bitte versuche es später erneut."

        try:
            await interaction.followup.send(response_text, ephemeral=True)
        except discord.NotFound:
            print("⚠️ Activity Check: Follow-up abgelaufen, obwohl die Interaktion bestätigt wurde.")
        except discord.HTTPException as exc:
            print(f"⚠️ Activity Check: Antwort konnte nicht gesendet werden ({type(exc).__name__}).")


class ActivityCheckCog(commands.Cog):
    async def eligible_members(self, guild, role=None):
        """Return a de-duplicated member snapshot after trying to fill the guild cache."""
        try:
            if not getattr(guild, "chunked", True):
                await guild.chunk(cache=True)
        except (discord.Forbidden, discord.HTTPException):
            pass
        if role:
            eligible_role_ids = {int(role.id)}
        else:
            try:
                with open(BASE_DIR / "config.json", "r", encoding="utf-8") as file:
                    cfg = json.load(file)
                eligible_role_ids = {
                    int(value) for value in cfg.get("team_role_ids", [])
                    if str(value).isdigit()
                }
            except Exception:
                eligible_role_ids = set()
        members = {}
        for member in list(getattr(guild, "members", []) or []):
            if member.bot:
                continue
            if eligible_role_ids and any(r.id in eligible_role_ids for r in member.roles):
                members[member.id] = member
        return sorted(members.values(), key=lambda member: member.display_name.casefold())

    def __init__(self, bot):
        self.bot = bot
        init_db()
        self.daily_activity_check.start()
        self.activity_warning_check.start()
        self.bot.add_view(ActivityView(self))

    def cog_unload(self):
        self.daily_activity_check.cancel()
        self.activity_warning_check.cancel()

    def current_check_id(self, message_id):
        with sqlite3.connect(DB_NAME, timeout=5.0) as conn:
            row = conn.execute(
                "SELECT id FROM checks WHERE message_id=?",
                (int(message_id),),
            ).fetchone()
            return row[0] if row else None

    async def send_check_message(self, channel, role=None):
        if channel is None or not getattr(channel, "guild", None):
            return None
        today = datetime.now(BERLIN_TZ).date().isoformat()
        with sqlite3.connect(DB_NAME, timeout=5.0) as conn:
            existing = conn.execute(
                "SELECT id FROM checks WHERE guild_id=? AND check_date=?",
                (channel.guild.id, today),
            ).fetchone()
            if existing:
                return None

        eligible = await self.eligible_members(channel.guild, role)
        embed = discord.Embed(
            title="⟡ Activity Check",
            description=(
                "Bitte bestätige deine Aktivität über den Button.\n\n"
                "🕐 Tagescheck · ✅ Bestätigung · 📊 Auswertung im Bot\n\n"
                "Nicht reagiert = wird im Bericht als offen geführt. "
                "Offene Bestätigungen werden um 20:00 Uhr geprüft."
            ),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"{channel.guild.name} • {today}")
        role_ping = role.mention if role else ""
        message = await channel.send(
            content=role_ping,
            embed=embed,
            view=ActivityView(self),
            allowed_mentions=discord.AllowedMentions(roles=bool(role)),
        )
        now = datetime.now(BERLIN_TZ).isoformat(timespec="seconds")
        try:
            with sqlite3.connect(DB_NAME, timeout=5.0) as conn:
                cursor = conn.execute(
                    """INSERT INTO checks
                       (guild_id,channel_id,message_id,check_date,created_at,snapshot_version)
                       VALUES(?,?,?,?,?,1)""",
                    (channel.guild.id, channel.id, message.id, today, now),
                )
                check_id = int(cursor.lastrowid)
                for member in eligible:
                    conn.execute(
                        """INSERT OR REPLACE INTO check_members
                           (check_id,user_id,user_name,role_name,added_at)
                           VALUES(?,?,?,?,?)""",
                        (
                            check_id,
                            member.id,
                            member.display_name,
                            member.top_role.name if member.top_role else "",
                            now,
                        ),
                    )
        except Exception:
            try:
                await message.delete()
            except Exception:
                pass
            raise
        if not eligible:
            print(
                f"⚠️ Activity Check {today}: Snapshot ist leer für Guild {channel.guild.id}. "
                "Bitte Teamrollen, Bot-Mitglieder-Intent und Activity-Check-Rolle prüfen; um 20:00 wird niemand pauschal verwarnt."
            )
        else:
            print(
                f"✅ Activity Check erstellt: Datum={today}, Guild={channel.guild.id}, "
                f"Snapshot={len(eligible)}, Nachricht={message.id}"
            )
        return message

    @tasks.loop(minutes=1)
    async def daily_activity_check(self):
        await self.bot.wait_until_ready()
        # Besides the regular 06:00 post, recover a missing daily check after a
        # restart later in the day. Never create a "late" check at/after 20:00.
        now = datetime.now(BERLIN_TZ)
        if (now.hour, now.minute) < (6, 0) or now.hour >= 20:
            return
        channel_id = get_config("activity_channel_id")
        role_id = get_config("activity_role_id")
        if not channel_id:
            print("⚠️ Activity Check: Kein Kanal konfiguriert. Bitte /setup_activitycheck ausführen.")
            return
        channel = self.bot.get_channel(int(channel_id))
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(int(channel_id))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
                print(f"⚠️ Activity Check: Kanal {channel_id} konnte nicht geladen werden ({type(exc).__name__}).")
                return
        if not isinstance(channel, discord.TextChannel):
            print(f"⚠️ Activity Check: Kanal {channel_id} ist kein Textkanal.")
            return
        role = channel.guild.get_role(int(role_id)) if role_id and str(role_id).isdigit() and int(role_id) else None
        if role_id and str(role_id).isdigit() and int(role_id) and role is None:
            print(f"⚠️ Activity Check: Konfigurierte Ping-/Snapshot-Rolle {role_id} nicht gefunden; verwende die konfigurierten Teamrollen.")
        try:
            await self.send_check_message(channel, role)
        except Exception as exc:
            print(f"⚠️ Activity Check: Tagescheck konnte nicht erstellt werden ({type(exc).__name__}: {exc}).")

    @tasks.loop(minutes=5)
    async def activity_warning_check(self):
        await self.bot.wait_until_ready()
        now = datetime.now(BERLIN_TZ)
        if not activity_warning_due(now):
            return
        for guild in list(getattr(self.bot, "guilds", []) or []):
            try:
                report = await self.process_daily_warnings(guild, now)
                if report.get("warned") or report.get("failed") or report.get("max_warns") or report.get("status") in {"empty_snapshot", "config_missing"}:
                    print(f"⚠️ Automatischer Activity-Check-Warnlauf: Guild={guild.id} Bericht={report}")
            except Exception as exc:
                print(f"⚠️ Activity-Check-Warnlauf fehlgeschlagen für Guild {guild.id}: {type(exc).__name__}: {exc}")

    async def process_daily_warnings(self, guild, now=None):
        """Warn unconfirmed snapshot members after 20:00, excluding active leaves."""
        now = now or datetime.now(BERLIN_TZ)
        if not activity_warning_due(now):
            return {"status": "not_due", "warned": 0, "failed": 0}
        check_date = now.date().isoformat()
        with sqlite3.connect(DB_NAME, timeout=5.0) as conn:
            conn.row_factory = sqlite3.Row
            check = conn.execute(
                "SELECT id,snapshot_version FROM checks WHERE guild_id=? AND check_date=?",
                (guild.id, check_date),
            ).fetchone()
            if not check:
                return {"status": "no_check", "warned": 0, "failed": 0}
            check_id = int(check["id"])
            snapshot = [
                dict(row) for row in conn.execute(
                    "SELECT user_id,user_name,role_name FROM check_members WHERE check_id=? ORDER BY user_id",
                    (check_id,),
                ).fetchall()
            ]
            response_rows = conn.execute(
                "SELECT user_id,reacted_at FROM responses WHERE check_id=?",
                (check_id,),
            ).fetchall()
        if not snapshot:
            # A broken or intentionally empty snapshot must not mass-warn the whole server.
            return {"status": "empty_snapshot", "warned": 0, "failed": 0}

        import webserver as ws
        cfg = ws.load_config()
        team_role_ids = {
            int(value) for value in cfg.get("team_role_ids", [])
            if str(value).isdigit()
        }
        if not team_role_ids:
            return {"status": "config_missing", "warned": 0, "failed": len(snapshot)}
        responses = {int(row["user_id"]): row["reacted_at"] for row in response_rows}
        report = {
            "status": "processed",
            "check_id": check_id,
            "snapshot": len(snapshot),
            "warned": 0,
            "confirmed": 0,
            "on_leave": 0,
            "not_team_member": 0,
            "member_left_server": 0,
            "max_warns": 0,
            "failed": 0,
        }
        team_db = ws.load_json(ws.DATA_FILE, {})
        if not isinstance(team_db, dict):
            team_db = {}

        for person in snapshot:
            uid = int(person["user_id"])
            action = get_warn_action(check_id, uid)
            status = action.get("status") if action else None
            if activity_action_is_terminal(status):
                continue

            # Look up an existing activity warning without creating empty team-data
            # records for every confirmed/abgemeldeten participant.
            existing_entry = team_db.get(str(uid), {})
            existing_warnings = existing_entry.get("warns_list", []) if isinstance(existing_entry, dict) else []
            if not isinstance(existing_warnings, list):
                existing_warnings = []
            warning_id = activity_warning_id(check_id, uid)
            warn = next(
                (
                    item for item in existing_warnings
                    if isinstance(item, dict)
                    and (
                        str(item.get("source_activity_check_id", "")) == str(check_id)
                        and str(item.get("source_activity_user_id", "")) == str(uid)
                    )
                ),
                None,
            )
            if warn is None and confirmed_by_deadline(responses.get(uid), check_date):
                set_warn_action(check_id, uid, "confirmed")
                report["confirmed"] += 1
                continue
            if warn is None and is_abgemeldet(uid, check_date):
                set_warn_action(check_id, uid, "on_leave")
                report["on_leave"] += 1
                continue

            member = guild.get_member(uid)
            if member is None:
                try:
                    member = await guild.fetch_member(uid)
                except discord.NotFound:
                    set_warn_action(check_id, uid, "member_left_server")
                    report["member_left_server"] += 1
                    continue
                except Exception as exc:
                    set_warn_action(check_id, uid, "failed", warning_id, str(exc))
                    report["failed"] += 1
                    continue
            if member.bot:
                set_warn_action(check_id, uid, "not_team_member")
                report["not_team_member"] += 1
                continue
            if not any(role.id in team_role_ids for role in member.roles):
                set_warn_action(check_id, uid, "not_team_member")
                report["not_team_member"] += 1
                continue

            try:
                entry = ws.user_entry(team_db, str(uid))
                warnings = entry.setdefault("warns_list", [])
                if warn is None:
                    count_before = len(ws.active_warns(entry))
                    if count_before >= 5:
                        set_warn_action(check_id, uid, "max_warns", warning_id)
                        report["max_warns"] += 1
                        ws.log_audit(
                            SYSTEM_ACTOR, "system", "Activity-Check-Warn nicht möglich",
                            f"{member.display_name} ({uid}) hat bereits 5 aktive Warnungen; Check {check_id} am {check_date}.",
                        )
                        for manager in list(getattr(guild, "members", []) or []):
                            if manager.bot or manager.id == member.id:
                                continue
                            permissions, _ = ws.compute_perms(guild, manager.id, cfg)
                            if permissions.get("can_promote") or permissions.get("is_admin"):
                                try:
                                    ws.pulse_db.notify(
                                        manager.id,
                                        "🚨 Activity Check: maximale Warnstufe",
                                        f"{member.display_name} hat den Activity Check am {check_date} nicht bis 20:00 bestätigt, ist nicht abgemeldet und hat bereits 5 aktive Warnungen.",
                                        "warning",
                                        "/warns",
                                        f"activity-warn-max:{check_id}:{uid}",
                                        86400,
                                    )
                                except Exception:
                                    pass
                        continue

                    warn = {
                        "id": warning_id,
                        "reason": f"Activity Check am {check_date} bis 20:00 Uhr nicht bestätigt; keine Abmeldung im Abmeldungsmodul vorhanden.",
                        "proof": "",
                        "by": SYSTEM_ACTOR,
                        "date": now.strftime("%d.%m.%Y %H:%M"),
                        "active": True,
                        "revoked_at": None,
                        "revoked_by": None,
                        "revoked_reason": "",
                        "source": "auto_activity_check",
                        "source_activity_check_id": check_id,
                        "source_activity_user_id": str(uid),
                        "source_activity_date": check_date,
                        "activity_consequences_applied": False,
                    }
                    warnings.append(warn)
                    ws.save_json(ws.DATA_FILE, team_db)
                    set_warn_action(check_id, uid, "processing", warning_id)

                count = len(ws.active_warns(entry))
                role_report = await ws.sync_warn_roles(guild, member, count, cfg)
                if not role_report.get("ok", False):
                    set_warn_action(check_id, uid, "failed", warning_id, role_report.get("message", "Warn-Rolle konnte nicht synchronisiert werden"))
                    report["failed"] += 1
                    continue
                if count >= 2 and not warn.get("activity_consequences_applied"):
                    consequence_report = await ws.apply_warn_consequences(guild, member, warn, count, cfg)
                    if consequence_report.get("ok", False):
                        warn["activity_consequences_applied"] = True
                        warn["activity_consequences"] = consequence_report.get("actions", [])
                        ws.save_json(ws.DATA_FILE, team_db)

                if not warn.get("activity_team_update_sent"):
                    update = await ws.send_team_update_embed(
                        guild,
                        "⚠️ Team-Warn: Activity Check nicht bestätigt",
                        f"{member.mention} hat den Activity Check nicht bis 20:00 bestätigt und war nicht abgemeldet.",
                        discord.Color.orange(),
                        target=f"{member.mention}\nWarn-Stufe: {count}/5",
                        action="Automatische Activity-Check-Verwarnung",
                        actor=SYSTEM_ACTOR,
                        fields=[
                            ("Datum", check_date, True),
                            ("Grund", warn.get("reason") or "Activity Check nicht bestätigt", False),
                            ("Warnrolle", role_report.get("message", "Nicht verfügbar"), False),
                        ],
                        thumbnail=member.display_avatar.url,
                    )
                    if update is not None:
                        warn["activity_team_update_sent"] = True
                        ws.save_json(ws.DATA_FILE, team_db)

                if not warn.get("activity_dm_attempted"):
                    consequence_text = "; ".join(
                        str(value) for value in warn.get("activity_consequences", []) if value
                    )
                    suffix = f"\n**Automatische Folge:** {consequence_text}" if consequence_text else ""
                    await ws.send_dm_notification(
                        member,
                        f"⚠️ Du hast eine Team-Warnung erhalten ({count}/5).\n"
                        f"**Grund:** {warn.get('reason')}\n**Von:** {SYSTEM_ACTOR}{suffix}",
                    )
                    warn["activity_dm_attempted"] = True
                    ws.save_json(ws.DATA_FILE, team_db)

                if not warn.get("activity_audit_logged"):
                    ws.log_audit(
                        SYSTEM_ACTOR,
                        "system",
                        "Automatische Activity-Check-Verwarnung",
                        f"{member.display_name} ({uid}) · {count}/5 · Check {check_id} vom {check_date}: {warn.get('reason')}",
                    )
                    warn["activity_audit_logged"] = True
                    ws.save_json(ws.DATA_FILE, team_db)

                set_warn_action(check_id, uid, "issued", warning_id)
                report["warned"] += 1
            except Exception as exc:
                set_warn_action(check_id, uid, "failed", warning_id, f"{type(exc).__name__}: {exc}")
                report["failed"] += 1
                print(f"⚠️ Activity-Check-Warn fehlgeschlagen für {uid} / Check {check_id}: {type(exc).__name__}: {exc}")

        return report

    @app_commands.command(name="setup_activitycheck", description="[Admin] Activity Check ab 06:00 einrichten (fehlende Checks werden nachgeholt)")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_activitycheck(self, interaction, kanal: discord.TextChannel, team_rolle: discord.Role = None):
        set_config("activity_channel_id", kanal.id)
        set_config("activity_role_id", team_rolle.id if team_rolle else 0)
        await interaction.response.send_message(
            f"✅ Activity Check: {kanal.mention} täglich 06:00"
            + (f" · Rolle {team_rolle.mention}" if team_rolle else " · Snapshot über konfigurierte Teamrollen"),
            ephemeral=True,
        )

    @app_commands.command(name="test_activitycheck", description="[Admin] Activity Check sofort senden")
    @app_commands.checks.has_permissions(administrator=True)
    async def test_activitycheck(self, interaction):
        channel_id = get_config("activity_channel_id")
        role_id = get_config("activity_role_id")
        channel = self.bot.get_channel(int(channel_id)) if channel_id else interaction.channel
        if channel is None and channel_id:
            try:
                channel = await self.bot.fetch_channel(int(channel_id))
            except Exception:
                channel = None
        role = interaction.guild.get_role(int(role_id)) if role_id and str(role_id).isdigit() and int(role_id) else None
        if channel is None:
            await interaction.response.send_message("⚠️ Activity-Check-Kanal nicht gefunden. Bitte /setup_activitycheck prüfen.", ephemeral=True)
            return
        try:
            message = await self.send_check_message(channel, role)
        except Exception as exc:
            await interaction.response.send_message(
                f"⚠️ Activity Check konnte nicht erstellt werden ({type(exc).__name__}).",
                ephemeral=True,
            )
            return
        if message is None:
            await interaction.response.send_message(
                "ℹ️ Für heute existiert bereits ein Activity Check. Nutze das Dashboard zur Auswertung.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"✅ Activity Check erstellt: {len(await self.eligible_members(channel.guild, role))} Teilnehmer im Snapshot.",
            ephemeral=True,
        )

    @app_commands.command(name="activity_status", description="Zeigt den heutigen Activity Check inklusive offener Teamler")
    @app_commands.checks.has_permissions(administrator=True)
    async def activity_status(self, interaction):
        today = datetime.now(BERLIN_TZ).date().isoformat()
        with sqlite3.connect(DB_NAME, timeout=5.0) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT id,snapshot_version FROM checks WHERE guild_id=? AND check_date=?",
                (interaction.guild.id, today),
            ).fetchone()
            if not row:
                await interaction.response.send_message(
                    "Heute wurde noch kein Activity Check gesendet. Im Pulse-Dashboard findest du die Auswertung unter **Activity Check**.",
                    ephemeral=True,
                )
                return
            check_id = int(row["id"])
            snapshot_rows = conn.execute(
                "SELECT user_id,user_name FROM check_members WHERE check_id=?",
                (check_id,),
            ).fetchall()
            snapshot = {int(r["user_id"]) for r in snapshot_rows}
            if not snapshot and not int(row["snapshot_version"] or 0):
                role_id = get_config("activity_role_id")
                role = interaction.guild.get_role(int(role_id)) if role_id and str(role_id).isdigit() else None
                members = [m for m in (role.members if role else interaction.guild.members) if not m.bot]
            else:
                members = [interaction.guild.get_member(uid) for uid in snapshot]
                members = [m for m in members if m and not m.bot]
            responded = {
                int(r["user_id"]) for r in conn.execute(
                    "SELECT user_id FROM responses WHERE check_id=?", (check_id,)
                ).fetchall()
            }

        confirmed = sorted((m for m in members if m.id in responded), key=lambda m: m.display_name.casefold())
        open_members = sorted((m for m in members if m.id not in responded), key=lambda m: m.display_name.casefold())

        def names(items):
            value = ", ".join(m.display_name for m in items) or "—"
            return value if len(value) <= 1000 else value[:997] + "…"

        embed = discord.Embed(title=f"📊 Activity Check · {today}", color=discord.Color.blurple())
        embed.add_field(name=f"✅ Bestätigt · {len(confirmed)}", value=names(confirmed), inline=False)
        embed.add_field(name=f"⏳ Noch offen · {len(open_members)}", value=names(open_members), inline=False)
        embed.set_footer(text="Für die vollständige Liste: Pulse Dashboard → Activity Check")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(ActivityCheckCog(bot))
