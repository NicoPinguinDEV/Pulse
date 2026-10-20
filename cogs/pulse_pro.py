"""Pulse Pro Discord layer: commands, presence and housekeeping automation."""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

import discord
from pulse_discord_style import send_styled
from pulse_embed_design import color as _pulse_color
from discord import app_commands
from discord.ext import commands, tasks

import pulse_db as db
from pulse_time import parse_timestamp


GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", "0") or 0)
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")


def panel_url(path: str = "/dashboard") -> str:
    return f"{PUBLIC_BASE_URL}{path}" if PUBLIC_BASE_URL else path


class PulsePro(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.housekeeping_loop.start()

    def cog_unload(self):
        self.housekeeping_loop.cancel()

    def guild(self):
        return self.bot.get_guild(GUILD_ID) if GUILD_ID else None

    def team_members(self, guild):
        try:
            import webserver
            cfg = webserver.load_config()
            role_ids = set(cfg.get("team_role_ids", []))
            return [m for m in guild.members if not m.bot and any(r.id in role_ids for r in m.roles)]
        except Exception:
            return []

    def manager_members(self, guild):
        try:
            import webserver
            cfg = webserver.load_config()
            role_ids = cfg.get("team_role_ids", [])
            out = []
            for m in self.team_members(guild):
                perms, _ = webserver.compute_perms(guild, m.id, cfg)
                if perms.get("can_promote") or perms.get("is_admin"):
                    out.append(m)
            return out
        except Exception:
            return []

    @tasks.loop(minutes=5)
    async def housekeeping_loop(self):
        guild = self.guild()
        if not guild:
            return
        try:
            # Keep important work visible without repeatedly notifying the same person.
            now = datetime.now(timezone.utc)
            import webserver
            # Resolve permissions/team managers once per pass instead of for each ticket/application.
            manager_cache = self.manager_members(guild)
            housekeeping_cfg = webserver.load_config()
            # Warning/probation expiries and their Discord role consequences must be
            # enforced repeatedly, including after a bot restart.
            try:
                warn_expiry = await webserver.cleanup_expired_team_warnings(guild, housekeeping_cfg)
                penalty_expiry = await webserver.cleanup_expired_warn_restrictions(guild, housekeeping_cfg)
                probation_expiry = await webserver.process_expired_probations(guild)
                if warn_expiry.get("expired") or warn_expiry.get("failed") or penalty_expiry.get("removed") or penalty_expiry.get("failed") or probation_expiry.get("completed") or probation_expiry.get("failed"):
                    print(
                        f"🧾 Ablaufkontrolle: Warnungen {warn_expiry}; "
                        f"Sperrrollen {penalty_expiry}; Probezeiten {probation_expiry}"
                    )
            except Exception as exc:
                print(f"⚠️ Warn-/Probezeit-Ablaufkontrolle fehlgeschlagen: {exc}")
            try:
                shift_report = await webserver.process_overdue_shifts(guild, housekeeping_cfg)
                if shift_report.get("closed") or shift_report.get("warned") or shift_report.get("failed") or shift_report.get("max_warns"):
                    print(f"⏱ Automatische Schichtkontrolle: {shift_report}")
            except Exception as exc:
                print(f"⚠️ Automatische Schichtkontrolle fehlgeschlagen: {exc}")
            if now.hour == 3 and now.minute < 5:
                try: db.purge_old_notifications(90)
                except Exception: pass
            try:
                # D-08: retention periods (off by default) – at most once a day, in a worker thread.
                import pulse_retention
                if pulse_retention.due_today(now):
                    report = await asyncio.to_thread(pulse_retention.run, webserver, now)
                    if report.get("ran"):
                        print(f"🔒 Datenbereinigung: {report['applications']} Bewerbungen, {report['audit']} Audit-Einträge entfernt.")
            except Exception as exc:
                print(f"⚠️ Datenbereinigung fehlgeschlagen: {type(exc).__name__}")
            overdue = [t for t in db.list_tasks(limit=1000) if t.get("due_at") and t.get("status") not in ("done", "archived")]
            for task in overdue:
                try:
                    due = datetime.fromisoformat(str(task["due_at"]).replace("Z", "+00:00"))
                    if due.tzinfo is None:
                        due = due.replace(tzinfo=timezone.utc)
                except Exception:
                    continue
                if due <= now and task.get("assignee_id"):
                    db.notify(
                        task["assignee_id"],
                        "⏰ Aufgabe überfällig",
                        f"{task['title']} ist überfällig.",
                        "warning",
                        "/tasks",
                        f"overdue:{task['id']}",
                        21600,
                    )
            # Bewerbungen, die länger als 48h offen sind, an berechtigte Entscheider melden.
            try:
                apps = webserver.load_json(webserver.APPS_FILE, {})
                for aid, app_data in apps.items():
                    if app_data.get("status") != "pending":
                        continue
                    # D-09: tolerant parser (Berlin time, any stored format); unreadable dates are skipped.
                    created = parse_timestamp(app_data.get("created_at"))
                    if created is None or (now - created).total_seconds() < 172800:
                        continue
                    for manager in manager_cache:
                        db.notify(manager.id, "📝 Bewerbung wartet", f"Bewerbung {aid} von {app_data.get('name','Unbekannt')} wartet seit über 48 Stunden.", "warning", "/applications", f"stale-application:{aid}", 86400)
            except Exception:
                pass

            for ticket in db.list_tickets(limit=1000):
                if ticket.get("status") == "closed":
                    continue
                try:
                    opened = datetime.fromisoformat(str(ticket["opened_at"]).replace("Z", "+00:00"))
                    if opened.tzinfo is None:
                        opened = opened.replace(tzinfo=timezone.utc)
                except Exception:
                    continue
                age = (now - opened).total_seconds()
                if age >= 1800:
                    for member in manager_cache:
                        db.notify(
                            member.id,
                            "🎫 Ticket wartet",
                            f"{ticket['category']} · {ticket['user_name']} wartet seit mehr als 30 Minuten.",
                            "warning",
                            "/tickets",
                            f"stale-ticket:{ticket['id']}",
                            21600,
                        )
            # LOA end reminder when the existing web data contains ISO dates.
            try:
                import webserver
                loas = webserver.get_loas()
                end_window = now.astimezone(webserver.TZ) if getattr(webserver, "TZ", None) else now
                for item in loas.values():
                    raw = str(item.get("bis", ""))
                    try:
                        end = datetime.fromisoformat(raw)
                        if end.tzinfo is None:
                            end = end.replace(tzinfo=end_window.tzinfo)
                        hours = (end - end_window).total_seconds() / 3600
                        if 0 <= hours <= 24:
                            uid = item.get("user_id") or item.get("discord_id")
                            if uid:
                                db.notify(uid, "🌴 LOA endet bald", "Deine Abmeldung endet innerhalb der nächsten 24 Stunden.", "info", "/loa", f"loa-ending:{uid}:{raw}", 86400)
                    except Exception:
                        continue
            except Exception:
                pass
            try:
                active_count=sum(1 for x in webserver.load_shifts().get("active_shifts",{}).values() if x.get("status") in ("online","break")) if 'webserver' in locals() else 0
                await self.bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=f"Pulse · {active_count} im Dienst"))
            except Exception:
                pass
        except Exception as exc:
            print(f"⚠️ Pulse Pro Housekeeping: {exc}")

    @housekeeping_loop.before_loop
    async def before_housekeeping(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        try:
            guild = self.guild()
            active = 0
            if guild:
                import webserver
                active = len(webserver.load_shifts().get("active_shifts", {}))
            await self.bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=f"Pulse · {active} im Dienst"))
        except Exception:
            pass
        await self.send_startup_report()

    async def send_startup_report(self):
        """Post one compact start report per process to the optional system alert channel."""
        if getattr(self.bot, "_pulse_startup_report_sent", False):
            return  # on_ready also fires after gateway reconnects
        self.bot._pulse_startup_report_sent = True
        raw_channel = os.getenv("PULSE_SYSTEM_ALERT_CHANNEL_ID", "").strip()
        guild = self.guild()
        if not raw_channel.isdigit() or not guild:
            return
        channel = guild.get_channel(int(raw_channel))
        if channel is None:
            print(f"⚠️ Start-Diagnose: Alert-Kanal {raw_channel} nicht gefunden.")
            return
        try:
            import webserver
            import pulse_ultimate
            loaded = getattr(self.bot, "pulse_loaded_cogs", [])
            failed = getattr(self.bot, "pulse_failed_cogs", [])
            synced = getattr(self.bot, "pulse_synced_commands", None)
            rss = pulse_ultimate.process_rss_mb()
            ok = not failed and synced is not None
            embed = discord.Embed(
                title=("✅" if ok else "⚠️") + " Pulse gestartet",
                description=f"Version **{getattr(webserver, 'PULSE_VERSION', '?')}** ist online.",
                color=_pulse_color("success") if ok else _pulse_color("warning"),
                timestamp=datetime.now(timezone.utc),
            )
            embed.add_field(name="Module", value=f"{len(loaded)} geladen" + (f", {len(failed)} Fehler" if failed else ""), inline=True)
            embed.add_field(name="Slash Commands", value=str(synced) if synced is not None else "Sync fehlgeschlagen", inline=True)
            embed.add_field(name="RAM", value=f"{rss:.0f} MB" if rss else "nicht messbar", inline=True)
            if failed:
                embed.add_field(name="Nicht geladen", value="\n".join(f"• {x}" for x in failed[:10])[:1000], inline=False)
            embed.add_field(name="Dashboard", value=f"[Öffnen]({panel_url('/me')})", inline=False)
            await send_styled(channel, embed=embed)
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"⚠️ Start-Diagnose konnte nicht gesendet werden: {type(exc).__name__}")
        except Exception as exc:
            print(f"⚠️ Start-Diagnose fehlgeschlagen: {type(exc).__name__}: {exc}")

    @app_commands.command(name="pulse", description="Öffnet Pulse und zeigt die wichtigsten Teamzahlen.")
    async def pulse(self, interaction: discord.Interaction):
        try:
            if not interaction.guild:
                await interaction.response.send_message("Dieser Befehl ist nur auf dem Server verfügbar.", ephemeral=True)
                return
            import webserver
            cfg = webserver.load_config()
            perms, member = webserver.compute_perms(interaction.guild, interaction.user.id, cfg)
            if not member or not (perms.get("can_view_dashboard") or perms.get("is_admin")):
                await interaction.response.send_message("❌ Du hast keinen Zugriff auf Pulse.", ephemeral=True)
                return
            shifts = webserver.load_shifts()
            active = len(shifts.get("active_shifts", {}))
            tickets = len([x for x in db.list_tickets(limit=1000) if x.get("status") != "closed"])
            tasks_open = len([x for x in db.list_tasks(limit=1000) if x.get("status") in ("open", "in_progress")])
            promos = len(db.promotion_requests("pending", 100))
            embed = discord.Embed(title="⚡ Pulse TeamOS", description="Zentrale Teamverwaltung", color=_pulse_color("info"))
            embed.add_field(name="🟢 Im Dienst", value=str(active), inline=True)
            embed.add_field(name="🎫 Offene Tickets", value=str(tickets), inline=True)
            embed.add_field(name="📋 Aktive Aufgaben", value=str(tasks_open), inline=True)
            embed.add_field(name="✓ Freigaben", value=str(promos), inline=True)
            # Personal block: the most common question is "how am I doing this week?".
            uid = str(interaction.user.id)
            own = shifts.get("active_shifts", {}).get(uid)
            weekly = webserver.calculate_weekly_seconds(uid, shifts.get("history", []), shifts.get("active_shifts", {}))
            goal = float(cfg.get("weekly_goal_hours", 3.0) or 0)
            pct = min(100, int(weekly * 100 / (goal * 3600))) if goal else 100
            state = {"online": "🟢 Im Dienst", "break": "☕ Pause"}.get(own.get("status") if own else "", "⚪ Nicht im Dienst")
            my_tasks = len([x for x in db.list_tasks(assignee_id=interaction.user.id, limit=100) if x.get("status") not in ("done", "archived")])
            embed.add_field(
                name="👤 Deine Woche",
                value=f"{state}\n⏱ {webserver.fmt_duration(weekly)} von {goal:g} h ({pct} %)\n📋 {my_tasks} offene Aufgaben",
                inline=False,
            )
            embed.add_field(name="🌐 Dashboard", value=f"[Mein Pulse]({panel_url('/me')}) · [Übersicht]({panel_url()})", inline=False)
            embed.set_footer(text="Pulse Pro")
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as exc:
            print(f"Pulse slash command error: {exc}")
            if not interaction.response.is_done():
                await interaction.response.send_message("Pulse konnte gerade nicht geladen werden.", ephemeral=True)

    @app_commands.command(name="pulse-hilfe", description="Zeigt alle Pulse-Befehle, passend zu deinen Rechten.")
    async def pulse_help(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("Nur auf dem Server verfügbar.", ephemeral=True)
            return
        import webserver
        cfg = webserver.load_config()
        perms, member = webserver.compute_perms(interaction.guild, interaction.user.id, cfg)
        if not member or not (perms.get("can_view_dashboard") or perms.get("is_admin")):
            await interaction.response.send_message("❌ Du hast keinen Zugriff auf Pulse.", ephemeral=True)
            return
        leader = bool(perms.get("can_promote") or perms.get("is_admin"))
        sections = [
            ("👤 Für dich", [
                ("/pulse", "Teamzahlen und deine Woche"),
                ("/mytasks", "Deine offenen Aufgaben"),
                ("/taskdone", "Aufgabe als erledigt markieren"),
                ("/teamstatus", "Wer ist gerade online/im Dienst"),
                ("/anmeldung", "Abmeldung (LOA) verwalten"),
                ("/melden", "Vorfall an die Führung melden"),
            ]),
            ("🧭 Team", [
                ("/memberinfo", "Kurzinfo zu einem Teammitglied"),
                ("/pulse-ticket", "Ticket-Funktionen"),
                ("/activity-check", "Activity Check"),
            ]),
        ]
        if perms.get("can_warn") or leader:
            sections.append(("⚠ Moderation", [
                ("/warn", "Team-Warnung mit Beleg"),
                ("/warns", "Warnungen ansehen"),
                ("/warn-remove", "Warnung zurücknehmen (mit Grund)"),
            ]))
        if leader:
            sections.append(("🛡 Führung", [
                ("/teamtask", "Aufgabe zuweisen"),
                ("/teamannounce", "Team-Ankündigung senden"),
                ("/promote · /demote", "Rangwechsel mit Grund"),
                ("/team-kick · /team-exit", "Nur Teamrollen entfernen – kein Server-Kick"),
                ("/pulse-approve", "Zweitfreigabe erteilen"),
            ]))
        if perms.get("is_admin"):
            sections.append(("⚙ Administration", [
                ("/pulsehealth", "Systemstatus"),
                ("/pulse-diagnose", "RAM, Datenbanken, Rollen und Kanäle prüfen"),
                ("/pulse-lock · /pulse-unlock", "Notfall-Sperre"),
            ]))
        embed = discord.Embed(
            title="📖 Pulse-Hilfe",
            description=f"Alles Wichtige findest du auch im Dashboard: [Mein Pulse]({panel_url('/me')})",
            color=_pulse_color("info"),
        )
        for title, rows in sections:
            embed.add_field(name=title, value="\n".join(f"`{cmd}` – {text}" for cmd, text in rows), inline=False)
        embed.set_footer(text="Es werden nur Befehle angezeigt, die zu deinen Rechten passen.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="teamstatus", description="Zeigt den aktuellen Teamstatus.")
    async def teamstatus(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("Nur auf dem Server verfügbar.", ephemeral=True)
            return
        import webserver
        cfg = webserver.load_config()
        perms, member = webserver.compute_perms(interaction.guild, interaction.user.id, cfg)
        if not member or not (perms.get("can_view_dashboard") or perms.get("is_admin")):
            await interaction.response.send_message("❌ Kein Zugriff.", ephemeral=True)
            return
        members = self.team_members(interaction.guild)
        active = webserver.load_shifts().get("active_shifts", {})
        lines = []
        for m in sorted(members, key=lambda x: x.display_name.lower()):
            sh = active.get(str(m.id))
            discord_status = str(getattr(m, "status", discord.Status.offline))
            presence = {"online":"🟢 Online","idle":"🟡 AFK","dnd":"🔴 Nicht stören","offline":"⚪ Offline"}.get(discord_status, "⚪ Offline")
            shift_state = " · 🟢 Im Dienst" if sh and sh.get("status") == "online" else (" · ☕ Pause" if sh and sh.get("status") == "break" else "")
            activity = ""
            for a in (getattr(m, "activities", []) or []):
                value = getattr(a, "name", None) or getattr(a, "state", None)
                if value:
                    activity = f" · {str(value)[:60]}"
                    break
            lines.append(f"{presence}{shift_state} · **{m.display_name}**{activity}")
        text = "\n".join(lines[:40]) or "Keine Teammitglieder gefunden."
        embed = discord.Embed(title="◉ Pulse Teamstatus", description=text, color=_pulse_color("info"))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="mytasks", description="Zeigt deine offenen Pulse-Aufgaben.")
    async def mytasks(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("Nur auf dem Server verfügbar.", ephemeral=True)
            return
        import webserver
        cfg = webserver.load_config()
        perms, member = webserver.compute_perms(interaction.guild, interaction.user.id, cfg)
        if not member:
            await interaction.response.send_message("❌ Kein Zugriff.", ephemeral=True)
            return
        rows = db.list_tasks(assignee_id=interaction.user.id, limit=12)
        rows = [x for x in rows if x.get("status") not in ("done", "archived")]
        if not rows:
            await interaction.response.send_message("✅ Du hast aktuell keine offenen Aufgaben.", ephemeral=True)
            return
        embed = discord.Embed(title="📋 Meine Aufgaben", color=_pulse_color("info"))
        for task in rows[:8]:
            pr = {"urgent":"🚨","high":"🟠","normal":"🟢","low":"⚪"}.get(task.get("priority"),"•")
            embed.add_field(name=f"{pr} {task['title']}", value=f"{task.get('description') or 'Keine Beschreibung'}\n`{task['id']}`", inline=False)
        embed.set_footer(text=f"Weitere Aufgaben: {panel_url('/tasks')}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="teamannounce", description="Sendet eine Team-Ankündigung und speichert sie in Pulse.")
    @app_commands.describe(title="Kurzer Titel", message="Inhalt der Ankündigung", importance="Priorität")
    @app_commands.choices(importance=[app_commands.Choice(name="Info", value="info"),app_commands.Choice(name="Wichtig", value="warning"),app_commands.Choice(name="Dringend", value="urgent")])
    async def teamannounce(self, interaction: discord.Interaction, title: str, message: str, importance: app_commands.Choice[str] = None):
        if not interaction.guild:
            await interaction.response.send_message("Nur auf dem Server verfügbar.", ephemeral=True); return
        import webserver
        cfg=webserver.load_config(); perms, member=webserver.compute_perms(interaction.guild, interaction.user.id, cfg)
        if not member or not (perms.get("can_manage_announcements") or perms.get("can_promote") or perms.get("is_admin")):
            await interaction.response.send_message("❌ Dafür fehlt dir die Berechtigung.", ephemeral=True); return
        kind=(importance.value if importance else "info")
        title=title.strip()[:160]; message=message.strip()[:4000]
        if not title or not message:
            await interaction.response.send_message("Titel und Nachricht dürfen nicht leer sein.", ephemeral=True); return
        aid=db.create_announcement(title,message,interaction.user.id,interaction.user.display_name,kind)
        color=_pulse_color("danger") if kind=="urgent" else _pulse_color("warning") if kind=="warning" else _pulse_color("info")
        sent=await webserver.send_team_update_embed(interaction.guild,title,message,color)
        for m in self.team_members(interaction.guild):
            if m.id != interaction.user.id:
                db.notify(m.id, f"📢 {title}", message, "warning" if kind in ("warning","urgent") else "info", "/announcements", f"announcement:{aid}", 86400)
        await interaction.response.send_message(f"✅ Ankündigung veröffentlicht" + (f" · {sent.jump_url}" if sent else ""), ephemeral=True)

    @app_commands.command(name="teamtask", description="Erstellt eine Pulse-Aufgabe für ein Teammitglied.")
    @app_commands.describe(member="Zuständiges Teammitglied", title="Aufgabentitel", description="Beschreibung", priority="Priorität", due="Deadline, optional: YYYY-MM-DD HH:MM")
    @app_commands.choices(priority=[app_commands.Choice(name="Normal",value="normal"),app_commands.Choice(name="Hoch",value="high"),app_commands.Choice(name="Dringend",value="urgent")])
    async def teamtask(self, interaction: discord.Interaction, member: discord.Member, title: str, description: str = "", priority: app_commands.Choice[str] = None, due: str = ""):
        if not interaction.guild:
            await interaction.response.send_message("Nur auf dem Server verfügbar.", ephemeral=True); return
        import webserver
        cfg=webserver.load_config(); perms, actor=webserver.compute_perms(interaction.guild, interaction.user.id, cfg)
        if not actor or not (perms.get("can_manage_tasks") or perms.get("can_promote") or perms.get("is_admin")):
            await interaction.response.send_message("❌ Dafür fehlt dir die Berechtigung.", ephemeral=True); return
        if member.bot:
            await interaction.response.send_message("Bots können keine Team-Aufgaben erhalten.", ephemeral=True); return
        if not any(r.id in cfg.get("team_role_ids",[]) for r in member.roles):
            await interaction.response.send_message("Das ausgewählte Mitglied ist kein Teammitglied.", ephemeral=True); return
        due_at=None
        if due.strip():
            try:
                dt=datetime.strptime(due.strip(), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc); due_at=dt.isoformat()
            except ValueError:
                await interaction.response.send_message("Ungültige Deadline. Nutze `YYYY-MM-DD HH:MM`.", ephemeral=True); return
        pr=(priority.value if priority else "normal")
        tid=db.create_task(title.strip()[:160],description.strip()[:2500],member.id,member.display_name,interaction.user.id,interaction.user.display_name,pr,due_at)
        db.record_event("task_created","task",tid,interaction.user.id,interaction.user.display_name,{"assignee_id":member.id,"title":title.strip()[:160],"priority":pr})
        db.notify(member.id,"📋 Neue Aufgabe",f"{title.strip()[:160]} wurde dir von {interaction.user.display_name} zugewiesen.","warning" if pr=="urgent" else "info","/tasks",f"task:{tid}",86400)
        await interaction.response.send_message(f"✅ Aufgabe `{tid}` wurde **{member.display_name}** zugewiesen.",ephemeral=True)

    @app_commands.command(name="taskdone", description="Markiert eine Pulse-Aufgabe als erledigt.")
    @app_commands.describe(task_id="Pulse Aufgaben-ID")
    async def taskdone(self, interaction: discord.Interaction, task_id: str):
        if not interaction.guild:
            await interaction.response.send_message("Nur auf dem Server verfügbar.", ephemeral=True); return
        import webserver
        cfg=webserver.load_config(); perms, member=webserver.compute_perms(interaction.guild, interaction.user.id, cfg)
        if not member:
            await interaction.response.send_message("❌ Kein Teamzugriff.", ephemeral=True); return
        task=db.get_task(task_id.strip())
        if not task:
            await interaction.response.send_message("❌ Aufgabe nicht gefunden.", ephemeral=True); return
        if str(task.get("assignee_id"))!=str(interaction.user.id) and not (perms.get("can_manage_tasks") or perms.get("is_admin")):
            await interaction.response.send_message("❌ Du bist nicht für diese Aufgabe zuständig.", ephemeral=True); return
        if task.get("status") in ("done","archived"):
            await interaction.response.send_message("Die Aufgabe ist bereits erledigt.", ephemeral=True); return
        db.update_task(task["id"],status="done",actor_id=interaction.user.id,actor_name=interaction.user.display_name,note="Per Discord erledigt")
        db.record_event("task_completed","task",task["id"],interaction.user.id,interaction.user.display_name,{"title":task.get("title")})
        await interaction.response.send_message(f"✅ `{task['title']}` als erledigt markiert.",ephemeral=True)

    @app_commands.command(name="memberinfo", description="Zeigt kompakte Pulse-Infos zu einem Teammitglied.")
    @app_commands.describe(member="Teammitglied")
    async def memberinfo(self, interaction: discord.Interaction, member: discord.Member):
        if not interaction.guild:
            await interaction.response.send_message("Nur auf dem Server verfügbar.", ephemeral=True); return
        import webserver
        cfg=webserver.load_config(); perms, actor=webserver.compute_perms(interaction.guild, interaction.user.id, cfg)
        if not actor or not (perms.get("can_view_dashboard") or perms.get("is_admin")):
            await interaction.response.send_message("❌ Kein Zugriff.", ephemeral=True); return
        shifts=webserver.load_shifts(); hist=[h for h in shifts.get("history",[]) if str(h.get("mod_id"))==str(member.id)]
        sec=webserver.calculate_weekly_seconds(str(member.id),shifts.get("history",[]),shifts.get("active_shifts",{}))
        tickets=len([t for t in db.list_tickets(limit=2000) if str(t.get("claimed_by_id"))==str(member.id)])
        tasks=len([t for t in db.list_tasks(assignee_id=member.id,limit=200,include_archived=True)])
        embed=discord.Embed(title=f"👤 {member.display_name}",description=f"[Teamakte öffnen]({panel_url(f'/member/{member.id}')})",color=_pulse_color("info"))
        embed.add_field(name="Rolle",value=webserver.member_role(member,cfg.get("team_role_ids",[])),inline=True); embed.add_field(name="Wochenzeit",value=webserver.fmt_duration(sec),inline=True); embed.add_field(name="Schichten",value=str(len(hist)),inline=True)
        embed.add_field(name="Tickets bearbeitet",value=str(tickets),inline=True); embed.add_field(name="Aufgaben",value=str(tasks),inline=True); embed.add_field(name="Status",value="🟢 Im Dienst" if str(member.id) in shifts.get("active_shifts",{}) else "⚪ Offline",inline=True)
        await interaction.response.send_message(embed=embed,ephemeral=True)

    # K-08: slash-command errors are handled by pulse_command_errors.PulseCommandTree (main.py).
    # The former `on_app_command_error` listener here was never dispatched by discord.py.

    @app_commands.command(name="pulsehealth", description="Zeigt den Systemstatus von Pulse.")
    async def pulsehealth(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("Nur auf dem Server verfügbar.", ephemeral=True)
            return
        import webserver
        cfg = webserver.load_config()
        perms, _ = webserver.compute_perms(interaction.guild, interaction.user.id, cfg)
        if not perms.get("is_admin"):
            await interaction.response.send_message("❌ Nur Administratoren können den Systemstatus anzeigen.", ephemeral=True)
            return
        bot_ready = self.bot.is_ready()
        db_ok = True
        try:
            with db.connect() as cx:
                cx.execute("SELECT 1")
        except Exception:
            db_ok = False
        embed = discord.Embed(title="♥ Pulse Systemstatus", color=_pulse_color("success") if bot_ready and db_ok else _pulse_color("warning"))
        embed.add_field(name="Bot", value="🟢 Online" if bot_ready else "🔴 Offline", inline=True)
        embed.add_field(name="Datenbank", value="🟢 OK" if db_ok else "🔴 Fehler", inline=True)
        embed.add_field(name="Cogs", value=str(len(self.bot.extensions)), inline=True)
        version = getattr(webserver, "PULSE_VERSION", "unknown")
        embed.add_field(name="Version", value=f"Pulse {version}", inline=True)
        try:
            role_rows = webserver.warning_role_health(interaction.guild, cfg)
            ok_roles = sum(1 for row in role_rows if row.get("ok"))
            embed.add_field(name="Warnrollen", value=f"{ok_roles}/5 bereit", inline=True)
        except Exception:
            embed.add_field(name="Warnrollen", value="⚠️ Nicht prüfbar", inline=True)
        embed.add_field(name="Health-Endpunkt", value="/healthz", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="pulse-diagnose", description="[Admin] Prüft RAM, Datenbanken, Warnrollen und wichtige Kanäle.")
    async def pulse_diagnose(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("Nur auf dem Server verfügbar.", ephemeral=True)
            return

        import webserver
        import pulse_ultimate
        cfg = webserver.load_config()
        perms, _ = webserver.compute_perms(interaction.guild, interaction.user.id, cfg)
        if not perms.get("is_admin"):
            await interaction.response.send_message(
                "❌ Nur Administratoren können die vollständige Systemdiagnose ausführen.",
                ephemeral=True,
            )
            return

        rss = pulse_ultimate.process_rss_mb()
        peak = pulse_ultimate.process_peak_rss_mb()
        db_ok = True
        ultimate_db_ok = True
        try:
            with db.connect() as cx:
                cx.execute("SELECT 1")
        except Exception:
            db_ok = False
        try:
            with pulse_ultimate.cx() as cx:
                cx.execute("SELECT 1")
        except Exception:
            ultimate_db_ok = False

        # Diagnose the persisted Büro setting rather than assuming the default ID.
        try:
            import cogs.buero_system as office_system
            office_ping_id = int(
                office_system.get_config("ping_channel_id")
                or office_system.DEFAULT_PING_CHANNEL_ID
            )
            office_waiting_id = int(
                office_system.get_config("warteraum_channel_id")
                or office_system.DEFAULT_WAITING_ROOM_ID
            )
        except Exception:
            office_ping_id = 1560211874602160218
            office_waiting_id = 1528821871057965067

        channel_checks = []
        for label, channel_id in (
            ("Büro-Pings", office_ping_id),
            ("Team-Updates", webserver.TEAM_UPDATE_CHANNEL_ID),
            ("Social-Promo", 1530972281893949521),
        ):
            channel = self.bot.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await interaction.guild.fetch_channel(channel_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    channel = None
            if not isinstance(channel, discord.TextChannel):
                channel_checks.append(f"🔴 {label}: Kanal fehlt / nicht erreichbar")
                continue
            me = interaction.guild.me
            can_send = bool(me and channel.permissions_for(me).send_messages)
            can_embed = bool(me and channel.permissions_for(me).embed_links)
            status = "🟢 erreichbar"
            if not can_send:
                status = "🔴 keine Sendeberechtigung"
            elif not can_embed:
                status = "🟡 Nachrichten möglich, Embeds nicht erlaubt"
            channel_checks.append(f"{status} · {label} · {channel.mention}")

        if getattr(webserver, "SESSION_SECRET_PROBLEM", ""):
            channel_checks.insert(0, f"🔴 Sicherheit · {webserver.SESSION_SECRET_PROBLEM}")
        try:
            from pulse_audio_check import audio_status
            audio = audio_status()
            channel_checks.append(f"{'🟢' if audio['ok'] else '🟡'} Voice-Audio · {audio['message']}")
        except Exception:
            pass
        waiting_channel = interaction.guild.get_channel(office_waiting_id)
        if waiting_channel is None:
            try:
                waiting_channel = await interaction.guild.fetch_channel(office_waiting_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                waiting_channel = None
        if isinstance(waiting_channel, discord.VoiceChannel):
            channel_checks.append(f"🟢 erreichbar · Büro-Warteraum · {waiting_channel.mention}")
        else:
            channel_checks.append("🔴 Büro-Warteraum: Sprachkanal fehlt / nicht erreichbar")

        try:
            role_rows = webserver.warning_role_health(interaction.guild, cfg)
            role_ok = sum(1 for row in role_rows if row.get("ok"))
            role_text = f"{role_ok}/5 Warnrollen bereit"
        except Exception:
            role_ok = 0
            role_text = "⚠️ nicht prüfbar"

        ai_mod = self.bot.get_cog("AIModeration")
        queue = getattr(ai_mod, "queue", None)
        ai_queue_text = (
            f"{queue.qsize()}/{getattr(ai_mod, 'max_queue_size', 0)}"
            if queue is not None else "KI-Moderation nicht geladen"
        )
        cogs_text = f"{len(self.bot.cogs)} Cogs / {len(self.bot.extensions)} Erweiterungen"
        ram_level = pulse_ultimate.memory_guard_level(rss)
        ram_labels = {0: "🟢 Normal", 1: "🟡 Warnung", 2: "🟠 Kritisch", 3: "🔴 Notfallreserve"}
        ram_text = "Nicht verfügbar" if rss is None else (
            f"{ram_labels[ram_level]} · {rss:.1f} MB / 250 MB "
            f"(Spitze: {peak:.1f} MB)" if peak is not None else
            f"{ram_labels[ram_level]} · {rss:.1f} MB / 250 MB"
        )
        trend = pulse_ultimate.ram_trend_snapshot()
        if trend.get("growth_mb") is None:
            trend_text = f"Messwerte werden gesammelt ({trend.get('samples', 0)} Samples)"
        else:
            trend_text = (
                f"{float(trend['growth_mb']):+.1f} MB in "
                f"{float(trend.get('window_minutes') or 0):.0f} Min. · "
                f"{float(trend.get('growth_mb_per_hour') or 0):+.1f} MB/Stunde"
            )
        bot_member = interaction.guild.me
        if bot_member:
            bot_permissions_ready = all((
                bot_member.guild_permissions.manage_roles,
                bot_member.guild_permissions.kick_members,
                bot_member.guild_permissions.move_members,
            ))
            bot_permissions_text = "\n".join((
                ("🟢" if bot_member.guild_permissions.manage_roles else "🔴") + " Rollen verwalten",
                ("🟢" if bot_member.guild_permissions.kick_members else "🔴") + " Mitglieder kicken",
                ("🟢" if bot_member.guild_permissions.move_members else "🔴") + " Mitglieder verschieben",
            ))
        else:
            bot_permissions_ready = False
            bot_permissions_text = "🔴 Bot-Mitglied nicht verfügbar"

        critical = (not self.bot.is_ready()) or (not db_ok) or (not ultimate_db_ok) or ram_level >= 2
        warnings = ram_level == 1 or role_ok < 5 or not bot_permissions_ready or any(
            row.startswith(("🔴", "🟡")) for row in channel_checks
        )
        color = _pulse_color("danger") if critical else (
            _pulse_color("warning") if warnings else _pulse_color("success")
        )
        embed = discord.Embed(
            title="🩺 Pulse – Systemdiagnose",
            description="Prüfung der wichtigsten Laufzeitkomponenten. Die Diagnose nimmt keine Teamänderungen vor.",
            color=color,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="RAM-Schutz", value=ram_text, inline=False)
        embed.add_field(name="RAM-Langzeittrend", value=trend_text, inline=False)
        embed.add_field(name="Bot-Berechtigungen", value=bot_permissions_text, inline=False)
        embed.add_field(name="Discord", value="🟢 verbunden" if self.bot.is_ready() else "🔴 nicht bereit", inline=True)
        embed.add_field(name="Pulse-Datenbank", value="🟢 OK" if db_ok else "🔴 Fehler", inline=True)
        embed.add_field(name="TeamOS-Datenbank", value="🟢 OK" if ultimate_db_ok else "🔴 Fehler", inline=True)
        embed.add_field(name="Warnrollen", value=role_text, inline=True)
        embed.add_field(name="KI-Moderationswarteschlange", value=ai_queue_text, inline=True)
        embed.add_field(name="Geladene Module", value=cogs_text, inline=True)
        embed.add_field(name="Wichtige Kanalprüfungen", value="\n".join(channel_checks), inline=False)
        embed.set_footer(text="RAM-Schwellen: 180 MB Warnung · 210 MB kritisch · 230 MB Notfallreserve")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(PulsePro(bot))
