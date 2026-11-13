# Hintergrund-Jobs und Rechte-Schlüssel

Automatisch erzeugt von `python tools/jobs_perms.py` (statische Analyse).

## `@tasks.loop` (14)

| Datei:Zeile | Funktion | Intervall | Fehlerbehandlung |
|---|---|---|---|
| cogs/abmeldung.py:441 | `check_expired_abmeldungen` | `minutes=15` | 1 except, davon 1 breit; `@tracked`: Fehler erfasst, Schleife läuft weiter |
| cogs/activity_check.py:373 | `daily_activity_check` | `time=datetime_time(hour=6, minute=0, tzinfo=BERLIN_TZ)` | keine eigene; `@tracked`: Fehler erfasst, Schleife läuft weiter |
| cogs/activity_check.py:380 | `activity_check_recovery` | `minutes=5` | keine eigene; `@tracked`: Fehler erfasst, Schleife läuft weiter |
| cogs/activity_check.py:418 | `activity_warning_check` | `minutes=1` | 1 except, davon 1 breit; `@tracked`: Fehler erfasst, Schleife läuft weiter |
| cogs/privacy_requests.py:170 | `overdue_loop` | `hours=6` | 1 except; `@tracked`: Fehler erfasst, Schleife läuft weiter |
| cogs/pulse_next.py:42 | `housekeeping` | `minutes=5` | keine eigene; `@tracked`: Fehler erfasst, Schleife läuft weiter |
| cogs/pulse_pro.py:163 | `housekeeping_loop` | `minutes=5` | 13 except, davon 13 breit; `@tracked`: Fehler erfasst, Schleife läuft weiter |
| cogs/pulse_suite.py:1606 | `auto` | `minutes=5` | 1 except, davon 1 breit; `@tracked`: Fehler erfasst, Schleife läuft weiter |
| cogs/scheduled_announcements.py:272 | `delivery_loop` | `seconds=30` | 4 except, davon 4 breit, **1 still (`pass`)**; `@tracked`: Fehler erfasst, Schleife läuft weiter |
| cogs/social_promo.py:712 | `poll_sources` | `minutes=POLL_INTERVAL_MINUTES` | 2 except, davon 1 breit; `@tracked`: Fehler erfasst, Schleife läuft weiter |
| cogs/teamliste.py:489 | `update_list_loop` | `minutes=5` | 1 except, davon 1 breit; `@tracked`: Fehler erfasst, Schleife läuft weiter |
| cogs/tickets.py:1469 | `panel_refresh` | `minutes=5` | 2 except, davon 2 breit; `@tracked`: Fehler erfasst, Schleife läuft weiter |
| cogs/voice_support.py:1645 | `duty_status_loop` | `minutes=1` | keine eigene; `@tracked`: Fehler erfasst, Schleife läuft weiter |
| cogs/voice_support.py:1664 | `music_loop` | `seconds=8` | 1 except, **1 still (`pass`)**; `@tracked`: Fehler erfasst, Schleife läuft weiter |

## Eigene Endlosschleifen (4)

| Datei:Zeile | Funktion | Pause (`asyncio.sleep`) | Fehlerbehandlung |
|---|---|---|---|
| cogs/ai_moderation.py:399 | `_wait_and_publish_outage_notice` | `min(max(remaining + 0.25, 1.0` | keine (Fehler beenden die Schleife bzw. werden von discord.ext.tasks geloggt) |
| cogs/recovery.py:219 | `_run_all_sync` | `1.0, 0.2` | keine (Fehler beenden die Schleife bzw. werden von discord.ext.tasks geloggt) |
| pulse_ultimate.py:3499 | `automation_loop` | `60, 60` | 4 except, davon 3 breit |
| webserver.py:10112 | `auto_backup_loop` | `300` | 3 except, davon 1 breit |

## `create_task` / `ensure_future` / `pulse_tasks.spawn` (15)

`pulse_tasks.spawn` hält Tasks bis zum Abschluss, protokolliert unbehandelte Fehler und begrenzt normale Hintergrundarbeit; kritische Kernaufgaben können die weiche Obergrenze übergehen.

| Datei:Zeile | Aufruf |
|---|---|
| cogs/advanced_logs.py:95 | `spawn_task( self._flush(channel), name=f"pulse-advanced-logs-flush-{channel.id}", loop=loo` |
| cogs/ai_moderation.py:273 | `spawn_task(self._worker(), name="pulse-ai-moderation-worker", critical=True)` |
| cogs/ai_moderation.py:390 | `spawn_task( self._wait_and_publish_outage_notice(), name="pulse-ai-moderation-outage-notic` |
| cogs/pulse_suite.py:528 | `spawn( web._notify_team_safety_incident( i.guild, i.user, i.user, web.load_config(), decis` |
| cogs/recovery.py:141 | `spawn_task( self._run_all_sync(dict(settings), target, role, snapshot_at), name="pulse-rec` |
| cogs/role_nickname_prefixes.py:530 | `spawn_task( self._run_sync_existing_members(), name="pulse-role-nickname-prefix-sync", cri` |
| cogs/server_stats.py:107 | `spawn_task( wait_then_refresh(), name="pulse-server-stats-deferred-refresh", critical=True` |
| cogs/server_stats.py:80 | `spawn_task( self._refresh_loop(), name="pulse-server-stats-refresh", critical=True )` |
| cogs/teamliste.py:528 | `spawn_task( self._debounced_update_teamlist(after.guild), name="pulse-teamlist-debounced-u` |
| cogs/voice_support.py:1165 | `spawn(self._continue_waiting_audio(guild_id, voice_room_id, case_id), name="voice-support-` |
| main.py:171 | `spawn_task(server.serve(), name="pulse-webserver", critical=True)` |
| main.py:99 | `spawn_task(self.close(), name="pulse-sigterm-close", loop=loop, critical=True)` |
| pulse_tasks.py:44 | `target.create_task(coro, name=name)` |
| pulse_ultimate.py:3532 | `spawn_task(automation_loop(bot), name="pulse-automation", critical=True)` |
| webserver.py:10189 | `spawn_task(auto_backup_loop(), name="pulse-auto-backup", critical=True)` |

## Rechte-Schlüssel

- **Basis (`webserver.PERM_KEYS`, je Discord-Teamrolle in `config.permissions`):** `can_view_dashboard`, `can_warn`, `can_promote`, `can_add_notes`, `can_manage_tickets`, `can_manage_applications`, `can_manage_tasks`, `can_manage_training`, `can_manage_wiki`, `can_view_analytics`
- **Erweitert (`pulse_pro.EXTRA_PERMS`, zusätzlich über Dashboard-Profile `dashboard_roles`):** `can_manage_announcements`, `can_manage_handover`, `can_manage_promotions`, `can_view_team_status`, `can_manage_checkins`, `can_moderate_kicks`, `can_moderate_bans`, `can_moderate_bolo`, `can_manage_server`
- **Weitere Ebenen:** Nutzer-Overrides `next_permission_overrides` (`pulse_next.has_perm`), Slash-Befehlsrollen `command_roles` (`cogs/pulse_suite.py`).

| Schlüssel | Herkunft | gelesen in (Anzahl) | implizit auf True gesetzt in |
|---|---|---|---|
| `can_add_notes` | PERM_KEYS | cogs/dienst.py (1), pulse_home.py (1), pulse_pro.py (4), webserver.py (4) | — |
| `can_manage_announcements` | EXTRA_PERMS | cogs/pulse_pro.py (1), pulse_admin.py (1), pulse_next.py (1), pulse_pro.py (2), pulse_social_dashboard.py (1) | — |
| `can_manage_applications` | PERM_KEYS | pulse_home.py (1), pulse_phase7.py (3), pulse_pro.py (1), webserver.py (7) | — |
| `can_manage_checkins` | EXTRA_PERMS | pulse_pro.py (1) | — |
| `can_manage_factions` | nur im Code | pulse_pro.py (1), webserver.py (1) | — |
| `can_manage_goals` | nur im Code | pulse_next.py (1) | — |
| `can_manage_handover` | EXTRA_PERMS | pulse_pro.py (2) | — |
| `can_manage_promotions` | EXTRA_PERMS | pulse_pro.py (3) | — |
| `can_manage_server` | EXTRA_PERMS | pulse_admin.py (1) | — |
| `can_manage_tasks` | PERM_KEYS | cogs/pulse_pro.py (2), pulse_features.py (2), pulse_pro.py (2) | — |
| `can_manage_team_warnings` | nur im Code | cogs/ai_moderation.py (1), pulse_pro.py (6), webserver.py (1) | — |
| `can_manage_tickets` | PERM_KEYS | cogs/ticket_tools.py (2), cogs/tickets.py (2), pulse_features.py (1), pulse_home.py (1), pulse_pro.py (3), pulse_ticket_center.py (2), webserver.py (1) | — |
| `can_manage_training` | PERM_KEYS | pulse_features.py (5) | — |
| `can_manage_wiki` | PERM_KEYS | pulse_features.py (4) | — |
| `can_moderate_bans` | EXTRA_PERMS | cogs/mod_cases.py (3), pulse_admin.py (2), webserver.py (3) | — |
| `can_moderate_bolo` | EXTRA_PERMS | webserver.py (2) | — |
| `can_moderate_kicks` | EXTRA_PERMS | cogs/automod.py (1), cogs/mod_cases.py (3), pulse_admin.py (2), webserver.py (3) | — |
| `can_promote` | PERM_KEYS | cogs/ai_moderation.py (1), cogs/dienst.py (1), cogs/pulse_pro.py (3), cogs/pulse_suite.py (2), cogs/raid_watch.py (1), cogs/rp_verwaltung.py (1), pulse_admin.py (1), pulse_cockpit.py (1), pulse_command_help.py (1), pulse_features.py (11), pulse_home.py (4), pulse_next.py (4), pulse_phase7.py (6), pulse_pro.py (22), pulse_ticket_center.py (1), pulse_ultimate.py (11), pulse_workflows.py (2), webserver.py (17) | — |
| `can_view_analytics` | PERM_KEYS | pulse_phase7.py (2), pulse_pro.py (1) | — |
| `can_view_dashboard` | PERM_KEYS | cogs/dienst.py (1), cogs/pulse_pro.py (9), pulse_pro.py (2), webserver.py (2) | — |
| `can_view_team_status` | EXTRA_PERMS | pulse_pro.py (3) | — |
| `can_warn` | PERM_KEYS | cogs/ai_moderation.py (1), pulse_phase7.py (1), pulse_pro.py (1), webserver.py (5) | — |
| `is_admin` | nur im Code | cogs/ai_moderation.py (3), cogs/automod.py (1), cogs/dienst.py (4), cogs/mod_cases.py (4), cogs/pulse_pro.py (15), cogs/pulse_suite.py (4), cogs/raid_watch.py (1), cogs/rp_verwaltung.py (1), cogs/ticket_tools.py (2), cogs/tickets.py (2), pulse_admin.py (1), pulse_cockpit.py (1), pulse_command_help.py (2), pulse_features.py (12), pulse_home.py (5), pulse_mod_cases.py (1), pulse_next.py (7), pulse_phase7.py (7), pulse_pro.py (36), pulse_role_manager.py (1), pulse_server_control.py (2), pulse_social_dashboard.py (1), pulse_ticket_center.py (5), pulse_ultimate.py (12), pulse_workflows.py (1), webserver.py (46) | cogs/ai_moderation.py |
