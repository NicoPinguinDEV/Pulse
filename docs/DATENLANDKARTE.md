# Datenlandkarte

Automatisch erzeugt von `python tools/data_map.py` (statische Analyse, liest keine Laufzeitdaten).
*Schreibstellen* sind Zeilen mit typischen Schreibaufrufen (`save_json`, `INSERT/UPDATE/DELETE`, `write_text`, `os.replace` …) – als Einstieg, nicht vollständig.

## Dateien und Ordner

| Ablage | Module (lesen/schreiben) | Schreibstellen (Auszug) | JSON-Backup `/backups` | ZIP-Backup `/ultimate/backup` |
|---|---|---|---|---|
| `pulse.db` | `cogs/pulse_suite.py`, `pulse_db.py`, `pulse_features.py`, `pulse_next.py`, `pulse_pro.py`, `pulse_ultimate.py` | cogs/pulse_suite.py:61; pulse_db.py:21; pulse_next.py:63,590,594; pulse_ultimate.py:289 | ⚠️ nur 22 Tabellen über `export_state` | ✅ ganze Datei |
| `abmeldungen.db` | `cogs/abmeldung.py`, `cogs/activity_check.py`, `cogs/buero_system.py`, `cogs/flaggenquiz.py`, `cogs/rp_verwaltung.py`, `cogs/teamliste.py`, `pulse_features.py`, `webserver.py` | cogs/abmeldung.py:135,193,205,215,406,437; cogs/activity_check.py:30,78,86,94,109,162; cogs/buero_system.py:38,61,69,102,113,123; cogs/flaggenquiz.py:172,202,215,227,240,256; cogs/rp_verwaltung.py:11,24,32; cogs/teamliste.py:42,69,80,91,103,112; pulse_features.py:377; webserver.py:232,689,7342,8382,9419,9440 | ✅ | ❌ |
| `activity_check.db` | `cogs/activity_check.py`, `cogs/pulse_suite.py`, `webserver.py` | cogs/pulse_suite.py:336 | ✅ | ❌ |
| `pulse_suite.db` | `cogs/pulse_suite.py` | — | ❌ | ❌ |
| `teamliste.db` | `cogs/teamliste.py` | — | ❌ | ❌ |
| `buero_system.db` | `cogs/buero_system.py` | — | ❌ | ❌ |
| `rp_verwaltung.db` | `cogs/rp_verwaltung.py` | — | ❌ | ❌ |
| `flaggenquiz.db` | `cogs/flaggenquiz.py`, `pulse_features.py`, `pulse_pro.py` | pulse_features.py:240; pulse_pro.py:2046 | ❌ | ❌ |
| `config.json` | `cogs/activity_check.py`, `cogs/ai_moderation.py`, `cogs/backups.py`, `cogs/pulse_next.py`, `pulse_features.py`, `pulse_next.py`, `pulse_pro.py`, `webserver.py` | cogs/backups.py:48; pulse_pro.py:463,499,2095,2363,2383,2412; webserver.py:7130,9798,9811,9826,9834,9842 | ✅ | ✅ |
| `team_data.json` | `cogs/activity_check.py`, `cogs/ai_moderation.py`, `cogs/pulse_next.py`, `cogs/pulse_suite.py`, `cogs/social_promo.py`, `pulse_cockpit.py`, `pulse_features.py`, `pulse_home.py`, `pulse_next.py`, `pulse_pro.py`, `pulse_ultimate.py`, `webserver.py` | cogs/activity_check.py:589,607,633,646,656; cogs/ai_moderation.py:1069; cogs/pulse_next.py:116; cogs/pulse_suite.py:1033,1037,1092; webserver.py:995,1049,1054,1522,1528,1545 | ✅ | ✅ |
| `applications.json` | `cogs/pulse_pro.py`, `cogs/pulse_suite.py`, `pulse_cockpit.py`, `pulse_features.py`, `pulse_home.py`, `pulse_next.py`, `pulse_pro.py`, `pulse_ultimate.py`, `webserver.py` | cogs/pulse_suite.py:419,440; webserver.py:7058,8831,9456,9525,9619,9663 | ✅ | ✅ |
| `shifts.json` | `cogs/pulse_suite.py`, `cogs/teamliste.py`, `pulse_features.py`, `pulse_next.py`, `pulse_pro.py`, `webserver.py` | cogs/pulse_suite.py:1410; pulse_pro.py:763; webserver.py:907,948,965,974,982,999 | ✅ | ✅ |
| `logs.json` | `cogs/pulse_suite.py`, `pulse_features.py`, `pulse_next.py`, `pulse_pro.py`, `webserver.py` | webserver.py:3844,3894,3919 | ✅ | ✅ |
| `audit_logs.json` | `cogs/pulse_suite.py`, `pulse_next.py`, `pulse_pro.py`, `webserver.py` | webserver.py:332 | ❌ | ✅ |
| `meetings.json` | `pulse_features.py`, `pulse_next.py`, `webserver.py` | webserver.py:318,9689,9699,9710,9731 | ✅ | ✅ |
| `pulse_announcement_archive.json` | `cogs/announcement_archive.py` | — | ❌ | ❌ |
| `role_nickname_state.json` | `cogs/role_nickname_prefixes.py` | — | ❌ | ❌ |
| `social_promo_sources.json` | `cogs/social_promo.py` | — | ❌ | ❌ |
| `ai_moderation_cases.json` | `cogs/ai_moderation.py` | — | ❌ | ❌ |
| `uploads` | `cogs/social_promo.py`, `cogs/tickets.py`, `cogs/verification.py`, `pulse_admin.py`, `pulse_ticket_design.py`, `pulse_ultimate.py`, `pulse_uploads.py`, `pulse_verification.py`, `pulse_welcome.py`, `webserver.py` | — | ❌ | ❌ |
| `attachments` | `cogs/announcement_archive.py`, `cogs/pulse_suite.py`, `cogs/tickets.py`, `cogs/verification.py`, `pulse_next.py`, `pulse_ultimate.py`, `webserver.py` | cogs/pulse_suite.py:523 | ❌ | ✅ |
| `transcripts` | `cogs/tickets.py`, `pulse_discord_style.py`, `pulse_features.py`, `webserver.py` | — | ✅ | ❌ |
| `backups` | `cogs/backups.py`, `pulse_admin.py`, `pulse_features.py`, `pulse_intelligence.py`, `pulse_next.py`, `webserver.py` | — | ❌ | ✅ |
| `.session_secret` | `webserver.py` | — | ❌ | ❌ |

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
| `events` | `pulse_db.py` | ✅ |
| `handover_notes` | `pulse_db.py` | ✅ |
| `meetings_history` | `pulse_db.py` | ✅ |
| `messages` | `cogs/teamliste.py` | ❌ |
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
| `rejoin_oauth_tokens` | `pulse_rejoin.py` (verschlüsselte OAuth-Tokens, siehe docs/VERIFIZIERUNG_AUTO_REJOIN.md) | ❌ |
| `responses` | `cogs/activity_check.py` | ❌ |
| `roles` | `cogs/teamliste.py` | ❌ |
| `settings` | `pulse_db.py` | ✅ |
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
| `team_safety_actor_state` | `pulse_db.py` | ✅ |
| `team_safety_warn_events` | `pulse_db.py` | ✅ |
| `team_status` | `pulse_db.py` | ✅ |
| `ticket_events` | `pulse_db.py` | ✅ |
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

**Nicht im JSON-Backup:** 59 von 81 Tabellen.

## Manuell geprüfte Ergänzungen (Stand P0-3, 20.10.2026)

- **ZIP-Backup und `backups/`:** Der Ordner ist nur das *Ziel* der ZIP-Datei (`pulse_next.create_backup`), kein Inhalt – die Spalte zeigt dort fälschlich ✅.
- **Transkripte im JSON-Backup:** Dateien über 2 MB oder über 10 MB gesamt werden **still übersprungen** (`webserver.build_backup`).
- **ZIP-Backup:** ganze `pulse.db` + 7 JSON-Dateien + `attachments/` bis zu einem Größenlimit; ohne `abmeldungen.db`, `activity_check.db`, die fünf weiteren Datenbanken, `uploads/` und `transcripts/`.
- **In keinem Backup:** `pulse_suite.db`, `teamliste.db`, `buero_system.db`, `rp_verwaltung.db`, `flaggenquiz.db`, `pulse_announcement_archive.json`, `role_nickname_state.json`, `social_promo_sources.json`, `ai_moderation_cases.json`, `uploads/` (Panel-/Banner-Bilder, Voice-Audio), `.session_secret`.
- **Wachstum ohne Grenze:** `shifts.json` (`history` wird nie gekürzt), `logs.json` (kein Kürzen beim Speichern), `applications.json`. Begrenzt: `audit_logs.json` (letzte 5.000), Bewertungen Voice (1.000), Pending-Bewertungen (500), Verifikations-Fehler (10).
- **Pfadabhängigkeit:** `PULSE_DB_PATH` gilt nur in `pulse_db.py`; `pulse_ultimate.py` nutzt fest `BASE/"pulse.db"` (D-05).

