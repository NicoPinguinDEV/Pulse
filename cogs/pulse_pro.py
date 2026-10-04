"""Pulse Pro Discord layer: commands, presence and housekeeping automation."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

import pulse_db as db


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
            if now.hour == 3 and now.minute < 5:
                try: db.purge_old_notifications(90)
                except Exception: pass
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
                    created_raw = str(app_data.get("created_at", ""))
                    created = datetime.strptime(created_raw, "%d.%m.%Y %H:%M").replace(tzinfo=timezone.utc)
                    if (now - created).total_seconds() < 172800:
                        continue
                    for manager in self.manager_members(guild):
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
                    for member in self.manager_members(guild):
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
            embed = discord.Embed(title="⚡ Pulse TeamOS", description="Zentrale Teamverwaltung", color=discord.Color.blurple())
            embed.add_field(name="🟢 Im Dienst", value=str(active), inline=True)
            embed.add_field(name="🎫 Offene Tickets", value=str(tickets), inline=True)
            embed.add_field(name="📋 Aktive Aufgaben", value=str(tasks_open), inline=True)
            embed.add_field(name="✓ Freigaben", value=str(promos), inline=True)
            embed.add_field(name="🌐 Dashboard", value=f"[Pulse öffnen]({panel_url()})", inline=False)
            embed.set_footer(text="Pulse Pro")
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as exc:
            print(f"Pulse slash command error: {exc}")
            if not interaction.response.is_done():
                await interaction.response.send_message("Pulse konnte gerade nicht geladen werden.", ephemeral=True)

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
        embed = discord.Embed(title="◉ Pulse Teamstatus", description=text, color=discord.Color.blurple())
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
        embed = discord.Embed(title="📋 Meine Aufgaben", color=discord.Color.blurple())
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
        color=discord.Color.red() if kind=="urgent" else discord.Color.orange() if kind=="warning" else discord.Color.blurple()
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
        embed=discord.Embed(title=f"👤 {member.display_name}",description=f"[Teamakte öffnen]({panel_url(f'/member/{member.id}')})",color=discord.Color.blurple())
        embed.add_field(name="Rolle",value=webserver.member_role(member,cfg.get("team_role_ids",[])),inline=True); embed.add_field(name="Wochenzeit",value=webserver.fmt_duration(sec),inline=True); embed.add_field(name="Schichten",value=str(len(hist)),inline=True)
        embed.add_field(name="Tickets bearbeitet",value=str(tickets),inline=True); embed.add_field(name="Aufgaben",value=str(tasks),inline=True); embed.add_field(name="Status",value="🟢 Im Dienst" if str(member.id) in shifts.get("active_shifts",{}) else "⚪ Offline",inline=True)
        await interaction.response.send_message(embed=embed,ephemeral=True)

    @commands.Cog.listener()
    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        print(f"⚠️ Pulse App-Command Fehler: {error!r}")
        try:
            message="❌ Dabei ist ein unerwarteter Fehler aufgetreten. Bitte prüfe die Pulse-Konfiguration oder das Systemlog."
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except Exception:
            pass

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
        embed = discord.Embed(title="♥ Pulse Systemstatus", color=discord.Color.green() if bot_ready and db_ok else discord.Color.orange())
        embed.add_field(name="Bot", value="🟢 Online" if bot_ready else "🔴 Offline", inline=True)
        embed.add_field(name="Datenbank", value="🟢 OK" if db_ok else "🔴 Fehler", inline=True)
        embed.add_field(name="Cogs", value=str(len(self.bot.extensions)), inline=True)
        embed.add_field(name="Version", value="Pulse Pro 5.1", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(PulsePro(bot))
