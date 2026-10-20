# Hintergrund-Jobs und Rechte-Schlüssel

Automatisch erzeugt von `python tools/jobs_perms.py` (statische Analyse).

## `@tasks.loop` (13)

| Datei:Zeile | Funktion | Intervall | Fehlerbehandlung |
|---|---|---|---|
| cogs/abmeldung.py:429 | `check_expired_abmeldungen` | `minutes=15` | 1 except |
| cogs/activity_check.py:367 | `daily_activity_check` | `time=datetime_time(hour=6, minute=0, tzinfo=BERLIN_TZ)` | keine (Fehler beenden die Schleife bzw. werden von discord.ext.tasks geloggt) |
| cogs/activity_check.py:373 | `activity_check_recovery` | `minutes=5` | keine (Fehler beenden die Schleife bzw. werden von discord.ext.tasks geloggt) |
| cogs/activity_check.py:409 | `activity_warning_check` | `minutes=1` | 1 except, davon 1 breit |
| cogs/pulse_next.py:39 | `housekeeping` | `minutes=5` | keine (Fehler beenden die Schleife bzw. werden von discord.ext.tasks geloggt) |
| cogs/pulse_pro.py:59 | `housekeeping_loop` | `minutes=5` | 10 except, davon 10 breit, **4 still (`pass`)** |
| cogs/pulse_suite.py:705 | `auto` | `minutes=5` | 1 except, davon 1 breit |
| cogs/social_promo.py:663 | `poll_sources` | `minutes=POLL_INTERVAL_MINUTES` | 2 except, davon 1 breit |
| cogs/teamliste.py:358 | `update_list_loop` | `minutes=5` | keine (Fehler beenden die Schleife bzw. werden von discord.ext.tasks geloggt) |
| cogs/tickets.py:1228 | `panel_refresh` | `minutes=5` | 2 except, davon 2 breit |
| cogs/verification.py:216 | `reminder_loop` | `minutes=5` | 1 except, davon 1 breit |
| cogs/voice_support.py:1573 | `duty_status_loop` | `minutes=1` | keine (Fehler beenden die Schleife bzw. werden von discord.ext.tasks geloggt) |
| cogs/voice_support.py:1591 | `music_loop` | `seconds=8` | 1 except, **1 still (`pass`)** |

## Eigene Endlosschleifen (4)

| Datei:Zeile | Funktion | Pause (`asyncio.sleep`) | Fehlerbehandlung |
|---|---|---|---|
| cogs/ai_moderation.py:338 | `_wait_and_publish_outage_notice` | `min(max(remaining + 0.25, 1.0` | keine (Fehler beenden die Schleife bzw. werden von discord.ext.tasks geloggt) |
| pulse_features.py:322 | `housekeeping` | `300` | 5 except, davon 4 breit, **3 still (`pass`)** |
| pulse_ultimate.py:2274 | `automation_loop` | `60, 60` | 4 except, davon 3 breit |
| webserver.py:8079 | `auto_backup_loop` | `300` | 3 except, davon 1 breit |

## `create_task` / `ensure_future` (11)

Ohne gehaltene Referenz kann der Task vom Garbage Collector eingesammelt werden, Fehler gehen verloren (P-07).

| Datei:Zeile | Aufruf |
|---|---|
| cogs/ai_moderation.py:219 | `asyncio.create_task(self._worker(), name="pulse-ai-moderation-worker")` |
| cogs/ai_moderation.py:330 | `asyncio.create_task(
                self._wait_and_publish_outage_notice(),
             ` |
| cogs/pulse_suite.py:227 | `asyncio.get_running_loop().create_task(
                    web._notify_team_safety_incide` |
| cogs/role_nickname_prefixes.py:414 | `asyncio.create_task(
            self._run_sync_existing_members(), name="pulse-role-nickn` |
| cogs/server_stats.py:105 | `asyncio.create_task(
            wait_then_refresh(), name="pulse-server-stats-deferred-re` |
| cogs/server_stats.py:78 | `asyncio.create_task(
                self._refresh_loop(), name="pulse-server-stats-refres` |
| cogs/teamliste.py:386 | `asyncio.create_task(
                self._debounced_update_teamlist(after.guild),
       ` |
| cogs/voice_support.py:1112 | `loop.create_task(
                            self._continue_waiting_audio(guild_id, voice` |
| main.py:90 | `asyncio.create_task(server.serve(), name="pulse-webserver")` |
| pulse_ultimate.py:2324 | `asyncio.create_task(automation_loop(bot))` |
| webserver.py:8154 | `asyncio.create_task(auto_backup_loop())` |

## Rechte-Schlüssel

- **Basis (`webserver.PERM_KEYS`, je Discord-Teamrolle in `config.permissions`):** `can_view_dashboard`, `can_warn`, `can_promote`, `can_add_notes`, `can_manage_tickets`, `can_manage_applications`, `can_manage_tasks`, `can_manage_training`, `can_manage_wiki`, `can_view_analytics`
- **Erweitert (`pulse_pro.EXTRA_PERMS`, zusätzlich über Dashboard-Profile `dashboard_roles`):** `can_manage_announcements`, `can_manage_handover`, `can_manage_promotions`, `can_view_team_status`, `can_manage_checkins`, `can_moderate_kicks`, `can_moderate_bans`, `can_moderate_bolo`
- **Weitere Ebenen:** Nutzer-Overrides `next_permission_overrides` (`pulse_next.has_perm`), Slash-Befehlsrollen `command_roles` (`cogs/pulse_suite.py`).

| Schlüssel | Herkunft | gelesen in (Anzahl) | implizit auf True gesetzt in |
|---|---|---|---|
| `can_add_notes` | PERM_KEYS | pulse_pro.py (2), webserver.py (3) | — |
| `can_manage_announcements` | EXTRA_PERMS | cogs/pulse_pro.py (1), pulse_admin.py (1), pulse_next.py (1), pulse_pro.py (2), pulse_social_dashboard.py (1) | pulse_pro.py |
| `can_manage_applications` | PERM_KEYS | pulse_features.py (1), pulse_home.py (1), pulse_pro.py (1), webserver.py (5) | webserver.py |
| `can_manage_checkins` | EXTRA_PERMS | pulse_pro.py (1) | — |
| `can_manage_goals` | nur im Code | pulse_next.py (1) | — |
| `can_manage_handover` | EXTRA_PERMS | pulse_pro.py (2) | — |
| `can_manage_promotions` | EXTRA_PERMS | pulse_pro.py (3) | pulse_pro.py |
| `can_manage_tasks` | PERM_KEYS | cogs/pulse_pro.py (2), pulse_features.py (3), pulse_pro.py (2) | — |
| `can_manage_tickets` | PERM_KEYS | pulse_features.py (3), pulse_home.py (1), pulse_pro.py (3), pulse_ticket_center.py (1), webserver.py (1) | webserver.py |
| `can_manage_training` | PERM_KEYS | pulse_features.py (2) | — |
| `can_manage_wiki` | PERM_KEYS | pulse_features.py (3) | — |
| `can_moderate_bans` | EXTRA_PERMS | webserver.py (2) | — |
| `can_moderate_bolo` | EXTRA_PERMS | webserver.py (2) | — |
| `can_moderate_kicks` | EXTRA_PERMS | webserver.py (2) | — |
| `can_promote` | PERM_KEYS | cogs/ai_moderation.py (1), cogs/pulse_pro.py (4), cogs/pulse_suite.py (2), pulse_cockpit.py (1), pulse_features.py (12), pulse_home.py (4), pulse_next.py (2), pulse_pro.py (20), pulse_ticket_center.py (1), pulse_ultimate.py (8), webserver.py (14) | — |
| `can_view_analytics` | PERM_KEYS | pulse_pro.py (1) | — |
| `can_view_dashboard` | PERM_KEYS | cogs/pulse_pro.py (4), pulse_pro.py (2), webserver.py (1) | — |
| `can_view_team_status` | EXTRA_PERMS | pulse_pro.py (3) | — |
| `can_warn` | PERM_KEYS | cogs/ai_moderation.py (1), cogs/pulse_pro.py (1), pulse_pro.py (6), webserver.py (4) | — |
| `is_admin` | nur im Code | cogs/ai_moderation.py (1), cogs/pulse_pro.py (12), cogs/pulse_suite.py (2), pulse_admin.py (1), pulse_cockpit.py (1), pulse_features.py (12), pulse_home.py (4), pulse_next.py (2), pulse_pro.py (33), pulse_role_manager.py (1), pulse_social_dashboard.py (1), pulse_ticket_center.py (3), pulse_ultimate.py (8), webserver.py (32) | — |
