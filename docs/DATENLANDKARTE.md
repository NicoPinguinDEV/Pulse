# Datenlandkarte

Automatisch erzeugt von `python tools/data_map.py` (statische Analyse, liest keine Laufzeitdaten).
*Schreibstellen* sind Zeilen mit typischen Schreibaufrufen (`save_json`, `INSERT/UPDATE/DELETE`, `write_text`, `os.replace` …) – als Einstieg, nicht vollständig.

## Dateien und Ordner

| Ablage | Module (lesen/schreiben) | Schreibstellen (Auszug) | JSON-Backup `/backups` | ZIP-Backup `/ultimate/backup` |
|---|---|---|---|---|
| `pulse.db` | `cogs/advanced_logs.py`, `cogs/pulse_suite.py`, `pulse_backup.py`, `pulse_command_sync.py`, `pulse_db.py`, `pulse_factions.py`, `pulse_features.py`, `pulse_flags.py`, `pulse_migrations.py`, `pulse_mod_cases.py`, `pulse_next.py`, `pulse_panels.py`, `pulse_pro.py`, `pulse_server_control.py`, `pulse_ticket_extras.py`, `pulse_ultimate.py`, `pulse_voice_queue.py`, `webserver.py` | cogs/pulse_suite.py:66; pulse_db.py:23; pulse_next.py:651 | ⚠️ nur 22 Tabellen über `export_state` | ✅ ganze Datei |
| `abmeldungen.db` | `cogs/abmeldung.py`, `cogs/activity_check.py`, `cogs/buero_system.py`, `cogs/flaggenquiz.py`, `cogs/role_nickname_prefixes.py`, `cogs/rp_verwaltung.py`, `cogs/teamliste.py`, `pulse_backup.py`, `pulse_features.py`, `pulse_panels.py`, `webserver.py` | cogs/abmeldung.py:139,197,209,219,415,453; cogs/activity_check.py:33,81,89,97,112,165; cogs/buero_system.py:28,51,59,92,103,113; cogs/flaggenquiz.py:173,203,216,228,241,257; cogs/role_nickname_prefixes.py:199; cogs/rp_verwaltung.py:21,34,42; cogs/teamliste.py:43,70,81,92,104,113; pulse_features.py:387; webserver.py:306,946,8762,9812,11389,11410 | ❌ | ❌ |
| `activity_check.db` | `cogs/activity_check.py`, `cogs/pulse_suite.py`, `pulse_backup.py`, `pulse_panels.py`, `webserver.py` | cogs/pulse_suite.py:363 | ❌ | ❌ |
| `pulse_suite.db` | `cogs/pulse_suite.py`, `pulse_backup.py` | — | ❌ | ❌ |
| `teamliste.db` | `cogs/teamliste.py`, `pulse_backup.py`, `pulse_panels.py` | — | ❌ | ❌ |
| `buero_system.db` | `cogs/buero_system.py`, `pulse_backup.py`, `pulse_panels.py` | — | ❌ | ❌ |
| `rp_verwaltung.db` | `cogs/rp_verwaltung.py`, `pulse_backup.py`, `pulse_panels.py` | — | ❌ | ❌ |
| `flaggenquiz.db` | `cogs/flaggenquiz.py`, `pulse_backup.py`, `pulse_features.py`, `pulse_panels.py`, `pulse_pro.py` | pulse_features.py:248; pulse_pro.py:2304 | ❌ | ❌ |
| `config.json` | `cogs/activity_check.py`, `cogs/ai_moderation.py`, `cogs/antinuke.py`, `cogs/automod.py`, `cogs/backups.py`, `cogs/pulse_next.py`, `pulse_antinuke_admin.py`, `pulse_backup.py`, `pulse_features.py`, `pulse_guard_admin.py`, `pulse_next.py`, `pulse_phase7.py`, `pulse_pro.py`, `pulse_settings_history.py`, `webserver.py` | cogs/backups.py:48; pulse_antinuke_admin.py:151; pulse_guard_admin.py:239; pulse_phase7.py:276; pulse_pro.py:526,562,2353,2602,2622,2651; webserver.py:8540,10276,11764,11777,11792,11800 | ❌ | ✅ |
| `team_data.json` | `cogs/activity_check.py`, `cogs/ai_moderation.py`, `cogs/pulse_next.py`, `cogs/pulse_suite.py`, `cogs/social_promo.py`, `pulse_backup.py`, `pulse_cockpit.py`, `pulse_consistency.py`, `pulse_features.py`, `pulse_home.py`, `pulse_next.py`, `pulse_phase7.py`, `pulse_pro.py`, `pulse_retention.py`, `pulse_store.py`, `pulse_ultimate.py`, `webserver.py` | cogs/ai_moderation.py:1080; cogs/pulse_next.py:116; webserver.py:1989,1995,2012,2086,2434,2516 | ❌ | ✅ |
| `applications.json` | `cogs/pulse_pro.py`, `cogs/pulse_suite.py`, `pulse_backup.py`, `pulse_cockpit.py`, `pulse_consistency.py`, `pulse_features.py`, `pulse_home.py`, `pulse_next.py`, `pulse_phase7.py`, `pulse_pro.py`, `pulse_retention.py`, `pulse_ultimate.py`, `webserver.py` | cogs/pulse_suite.py:446,467; pulse_retention.py:119; webserver.py:8381,8417,8467,11034,11426,11495 | ❌ | ✅ |
| `shifts.json` | `cogs/pulse_suite.py`, `cogs/teamliste.py`, `pulse_backup.py`, `pulse_consistency.py`, `pulse_features.py`, `pulse_next.py`, `pulse_pro.py`, `pulse_retention.py`, `webserver.py` | cogs/pulse_suite.py:1503; pulse_pro.py:840,880; webserver.py:1342,4713,10549 | ❌ | ✅ |
| `logs.json` | `cogs/pulse_suite.py`, `pulse_backup.py`, `pulse_consistency.py`, `pulse_features.py`, `pulse_next.py`, `pulse_pro.py`, `pulse_retention.py`, `webserver.py` | webserver.py:4805,4855,4880 | ❌ | ✅ |
| `audit_logs.json` | `cogs/pulse_suite.py`, `pulse_antinuke_admin.py`, `pulse_backup.py`, `pulse_consistency.py`, `pulse_next.py`, `pulse_pro.py`, `pulse_retention.py`, `webserver.py` | pulse_retention.py:125; webserver.py:531 | ❌ | ✅ |
| `meetings.json` | `pulse_backup.py`, `pulse_features.py`, `pulse_next.py`, `webserver.py` | webserver.py:517,11655,11665,11676,11697 | ❌ | ✅ |
| `pulse_announcement_archive.json` | `cogs/announcement_archive.py`, `pulse_backup.py` | — | ❌ | ❌ |
| `role_nickname_state.json` | `cogs/role_nickname_prefixes.py`, `pulse_backup.py` | — | ❌ | ❌ |
| `social_promo_sources.json` | `cogs/social_promo.py`, `pulse_backup.py` | — | ❌ | ❌ |
| `ai_moderation_cases.json` | `cogs/ai_moderation.py`, `pulse_backup.py`, `pulse_retention.py` | — | ❌ | ❌ |
| `uploads` | `cogs/social_promo.py`, `cogs/tickets.py`, `cogs/verification.py`, `pulse_admin.py`, `pulse_backup.py`, `pulse_safe_path.py`, `pulse_ticket_design.py`, `pulse_ultimate.py`, `pulse_uploads.py`, `pulse_verification.py`, `pulse_welcome.py`, `webserver.py` | — | ❌ | ❌ |
| `attachments` | `cogs/advanced_logs.py`, `cogs/announcement_archive.py`, `cogs/pulse_suite.py`, `cogs/tickets.py`, `cogs/verification.py`, `pulse_backup.py`, `pulse_next.py`, `pulse_transcripts.py`, `pulse_ultimate.py`, `webserver.py` | cogs/pulse_suite.py:554 | ❌ | ✅ |
| `transcripts` | `cogs/tickets.py`, `pulse_backup.py`, `pulse_discord_style.py`, `pulse_features.py`, `pulse_retention.py`, `pulse_transcripts.py`, `webserver.py` | — | ❌ | ❌ |
| `backups` | `cogs/backups.py`, `pulse_admin.py`, `pulse_alerts.py`, `pulse_backup.py`, `pulse_features.py`, `pulse_flags.py`, `pulse_intelligence.py`, `pulse_migrations.py`, `pulse_next.py`, `pulse_retention.py`, `pulse_server_control.py`, `webserver.py` | — | ❌ | ✅ |
| `.session_secret` | `pulse_backup.py`, `pulse_system_checks.py`, `webserver.py` | — | ❌ | ❌ |

## Tabellen (CREATE TABLE im Code) und JSON-Backup-Abdeckung von `pulse.db`

`pulse_db.export_state()` exportiert 22 Tabellen (`TABLES`). Tabellen anderer Datenbanken stehen nur informativ dabei.

| Tabelle | angelegt in | im JSON-Backup (`export_state`) |
|---|---|---|
| `abmeldungen` | `cogs/abmeldung.py`, `webserver.py` | ❌ |
| `achievement_progress` | `pulse_db.py` | ✅ |
| `achievements` | `pulse_db.py` | ✅ |
| `activity_warn_actions` | `cogs/activity_check.py` | ❌ |
| `announcements` | `pulse_db.py` | ✅ |
| `calendar_events` | `pulse_db.py` | ✅ |
| `check_members` | `cogs/activity_check.py` | ❌ |
| `checkins` | `pulse_db.py` | ✅ |
| `checks` | `cogs/activity_check.py` | ❌ |
| `config` | `cogs/abmeldung.py`, `cogs/activity_check.py`, `cogs/buero_system.py`, `cogs/rp_verwaltung.py`, `cogs/teamliste.py` | ❌ |
| `dashboard_login_ip_audit` | `pulse_ip_audit.py` | ❌ |
| `dashboard_sessions` | `pulse_sessions.py` | ❌ |
| `events` | `pulse_db.py` | ✅ |
| `faction_categories` | `pulse_factions.py` | ❌ |
| `faction_warnings` | `pulse_factions.py` | ❌ |
| `factions` | `pulse_factions.py` | ❌ |
| `handover_notes` | `pulse_db.py` | ✅ |
| `meetings_history` | `pulse_db.py` | ✅ |
| `messages` | `cogs/teamliste.py` | ❌ |
| `mod_cases` | `pulse_mod_cases.py` | ❌ |
| `next_achievements` | `pulse_next.py` | ❌ |
| `next_activity` | `pulse_next.py` | ❌ |
| `next_announcement_reads` | `pulse_next.py` | ❌ |
| `next_announcements` | `pulse_next.py` | ❌ |
| `next_approvals` | `pulse_next.py` | ❌ |
| `next_goal_updates` | `pulse_next.py` | ❌ |
| `next_goals` | `pulse_next.py` | ❌ |
| `next_idea_votes` | `pulse_next.py` | ❌ |
| `next_ideas` | `pulse_next.py` | ❌ |
| `next_member_achievements` | `pulse_next.py` | ❌ |
| `next_permission_overrides` | `pulse_next.py` | ❌ |
| `next_promotions` | `pulse_next.py` | ❌ |
| `next_score_snapshots` | `pulse_next.py` | ❌ |
| `next_team_events` | `pulse_next.py` | ❌ |
| `next_workflow_runs` | `pulse_next.py` | ❌ |
| `next_workflows` | `pulse_next.py` | ❌ |
| `notifications` | `pulse_db.py` | ✅ |
| `pending_requests` | `cogs/buero_system.py` | ❌ |
| `promotion_requests` | `pulse_db.py` | ✅ |
| `quiz_config` | `cogs/flaggenquiz.py` | ❌ |
| `quiz_scores` | `cogs/flaggenquiz.py` | ❌ |
| `recovery_oauth_tokens` | `pulse_recovery.py` | ❌ |
| `responses` | `cogs/activity_check.py` | ❌ |
| `roles` | `cogs/teamliste.py` | ❌ |
| `schema_migrations` | `pulse_migrations.py` | ❌ |
| `settings` | `pulse_db.py` | ✅ |
| `settings_history` | `pulse_settings_history.py` | ❌ |
| `suite_achievements` | `cogs/pulse_suite.py` | ❌ |
| `suite_actions` | `cogs/pulse_suite.py` | ❌ |
| `suite_audit` | `cogs/pulse_suite.py` | ❌ |
| `suite_cases` | `cogs/pulse_suite.py` | ❌ |
| `suite_events` | `cogs/pulse_suite.py` | ❌ |
| `suite_interviews` | `cogs/pulse_suite.py` | ❌ |
| `suite_members` | `cogs/pulse_suite.py` | ❌ |
| `suite_promotions` | `cogs/pulse_suite.py` | ❌ |
| `suite_schedule` | `cogs/pulse_suite.py` | ❌ |
| `suite_settings` | `cogs/pulse_suite.py` | ❌ |
| `suite_task_meta` | `cogs/pulse_suite.py` | ❌ |
| `suite_training_assignments` | `cogs/pulse_suite.py` | ❌ |
| `task_history` | `pulse_db.py` | ✅ |
| `tasks` | `pulse_db.py` | ✅ |
| `team_checklists` | `pulse_checklists.py` | ❌ |
| `team_safety_actor_state` | `pulse_db.py` | ✅ |
| `team_safety_warn_events` | `pulse_db.py` | ✅ |
| `team_status` | `pulse_db.py` | ✅ |
| `ticket_events` | `pulse_db.py` | ✅ |
| `ticket_tags` | `pulse_ticket_extras.py` | ❌ |
| `tickets` | `pulse_db.py` | ✅ |
| `training_attempts` | `pulse_db.py` | ✅ |
| `training_questions` | `pulse_db.py` | ✅ |
| `trainings` | `pulse_db.py` | ✅ |
| `ultimate_api_keys` | `pulse_ultimate.py` | ❌ |
| `ultimate_attachments` | `pulse_ultimate.py` | ❌ |
| `ultimate_automation_runs` | `pulse_ultimate.py` | ❌ |
| `ultimate_automations` | `pulse_ultimate.py` | ❌ |
| `ultimate_awards` | `pulse_ultimate.py` | ❌ |
| `ultimate_calendar` | `pulse_ultimate.py` | ❌ |
| `ultimate_case_events` | `pulse_ultimate.py` | ❌ |
| `ultimate_case_players` | `pulse_ultimate.py` | ❌ |
| `ultimate_cases` | `pulse_ultimate.py` | ❌ |
| `ultimate_certificates` | `pulse_ultimate.py` | ❌ |
| `ultimate_department_members` | `pulse_ultimate.py` | ❌ |
| `ultimate_departments` | `pulse_ultimate.py` | ❌ |
| `ultimate_feedback` | `pulse_ultimate.py` | ❌ |
| `ultimate_handovers` | `pulse_ultimate.py` | ❌ |
| `ultimate_poll_votes` | `pulse_ultimate.py` | ❌ |
| `ultimate_polls` | `pulse_ultimate.py` | ❌ |
| `ultimate_profiles` | `pulse_ultimate.py` | ❌ |
| `ultimate_roblox_profiles` | `pulse_ultimate.py` | ❌ |
| `ultimate_settings` | `pulse_ultimate.py` | ❌ |
| `voice_support_case_history` | `pulse_db.py` | ❌ |
| `wiki_pages` | `pulse_db.py` | ✅ |

**Nicht im JSON-Backup:** 70 von 92 Tabellen.

## Komplett-Backup `pulse_backup` (aktueller Stand, maßgeblich)

- Enthält alle Datenbanken aus `pulse_backup.SQLITE_FILES`, alle JSON-Dateien aus `JSON_FILES`, die Ordner `uploads/`, `attachments/`, `transcripts/` (im Rahmen von `PULSE_BACKUP_MAX_MB`, Übersprungenes steht im Manifest) und die hochgeladene Wartemusik `assets/support_wait.{mp3,ogg,wav}`; bewusst nicht: `backups/`, `.env`, `.session_secret`.
- `tests/test_full_backup.py` (CoverageTests) schlägt fehl, wenn eine neue `*.db`/`*.json`-Ablage im Code auftaucht, die weder gesichert noch bewusst ausgenommen ist.
- Der Restore-Test (`pulse_backup.restore_test`, wöchentlich und im Systemstatus) stellt das ganze Archiv probeweise in ein Temp-Verzeichnis wieder her: Prüfsummen, Integrität jeder Datenbank, Tabellen/Zeilen, JSON-Gültigkeit und für jeden Eintrag ein Wiederherstellungsziel.
- Die Abschnitte unten beschreiben den Stand **vor** dem Komplett-Backup und die älteren Backup-Wege (JSON-Backup, ZIP-Backup).

## Manuell geprüfte Ergänzungen (Stand P0-3, 20.10.2026)

- **ZIP-Backup und `backups/`:** Der Ordner ist nur das *Ziel* der ZIP-Datei (`pulse_next.create_backup`), kein Inhalt – die Spalte zeigt dort fälschlich ✅.
- **Transkripte im JSON-Backup:** Dateien über 2 MB oder über 10 MB gesamt werden **still übersprungen** (`webserver.build_backup`).
- **ZIP-Backup:** ganze `pulse.db` + 7 JSON-Dateien + `attachments/` bis zu einem Größenlimit; ohne `abmeldungen.db`, `activity_check.db`, die fünf weiteren Datenbanken, `uploads/` und `transcripts/`.
- **In keinem Backup:** `pulse_suite.db`, `teamliste.db`, `buero_system.db`, `rp_verwaltung.db`, `flaggenquiz.db`, `pulse_announcement_archive.json`, `role_nickname_state.json`, `social_promo_sources.json`, `ai_moderation_cases.json`, `uploads/` (Panel-/Banner-Bilder, Voice-Audio), `.session_secret`.
- **Wachstum ohne Grenze:** `shifts.json` (`history` wird nie gekürzt), `logs.json` (kein Kürzen beim Speichern), `applications.json`. Begrenzt: `audit_logs.json` (letzte 5.000), Bewertungen Voice (1.000), Pending-Bewertungen (500), Verifikations-Fehler (10).
- **Pfadabhängigkeit:** `PULSE_DB_PATH` gilt nur in `pulse_db.py`; `pulse_ultimate.py` nutzt fest `BASE/"pulse.db"` (D-05).

