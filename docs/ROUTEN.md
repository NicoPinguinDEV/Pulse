# Routen-Landkarte (effektiv zur Laufzeit)

Automatisch erzeugt von `python tools/route_map.py` aus dem laufenden FastAPI-Objekt (nicht von Hand bearbeiten).
Spalte *Prüfung* ist eine Quelltext-Heuristik (`auth(...)`, `cctx`, `ctx_for`, `admin_ctx`, `perm=`) – Hinweis, kein Beweis.

- **Effektive Routen (Methode × Pfad):** 499
- **Doppelt registriert:** 1 → `GET /ultimate`
- **Im Quelltext registriert, zur Laufzeit verdrängt:** 5
- **Ohne erkennbare Prüfung:** 58 (Login/OAuth/Health/Bewerbungsportal/öffentliche Seiten – einzeln prüfen)

> Achtung `remove_and_add` (`pulse_pro.py`): ersetzt nur die angegebenen HTTP-Methoden; andere Methoden desselben Pfads bleiben erhalten.

## Doppelte Registrierungen

| Methode | Pfad | aktive Handler (Reihenfolge) |
|---|---|---|
| GET | `/ultimate` | `pulse_web.dashboard.ultimate_dashboard` (pulse_web/dashboard.py:786) → `pulse_ultimate.register.<locals>.ultimate_home` (pulse_ultimate.py:1077) (FastAPI nimmt den **ersten** Treffer) |

## Verdrängte Handler (toter Code)

| Methode | Pfad | verdrängte Funktion | Ort | aktiv stattdessen |
|---|---|---|---|---|
| GET | `/dashboard` | `dashboard_main` | pulse_web/dashboard.py:507 | `pulse_pro.register.<locals>.dashboard_v5` |
| GET | `/melonly` | `melonly_alias` | pulse_web/dashboard.py:992 | `pulse_pro.register.<locals>.melonly_page` |
| GET | `/member/{user_id}` | `member_detail` | pulse_web/team.py:669 | `pulse_pro.register.<locals>.member_v5` |
| GET | `/settings` | `settings_page` | pulse_web/administration.py:194 | `pulse_pro.register.<locals>.settings_v5` |
| GET | `/tickets` | `pulse_tickets_dashboard` | pulse_web/tickets.py:248 | `pulse_pro.register.<locals>.tickets_v5` |

## Effektive Routen nach Bereich

### `/` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/` | `pulse_web.dashboard.home` | pulse_web/dashboard.py:7 | — (öffentlich/keine erkennbare Prüfung) |

### `/academy` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/academy` | `pulse_academy.register.<locals>.academy_page` | pulse_academy.py:64 | can_view_dashboard |

### `/achievements` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/achievements` | `pulse_pro.register.<locals>.achievements_v5` | pulse_pro.py:3286 | can_view_dashboard |

### `/action` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| POST | `/action` | `pulse_web.administration.handle_action` | pulse_web/administration.py:871 | angemeldet (+ can_manage_applications, can_promote, is_admin) |

### `/activity-check` (2)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/activity-check` | `pulse_pro.register.<locals>.activity_check_page` | pulse_pro.py:1800 | can_manage_checkins |
| POST | `/activity-check/setup` | `pulse_pro.register.<locals>.activity_check_setup` | pulse_pro.py:1926 | can_manage_checkins |

### `/admin` (82)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/admin` | `pulse_admin.register.<locals>.admin_hub` | pulse_admin.py:195 | angemeldet |
| GET | `/admin/betrieb` | `pulse_ops_pages.register.<locals>.operations_console` | pulse_ops_pages.py:198 | Admin |
| POST | `/admin/betrieb/rechte-soll` | `pulse_ops_pages.register.<locals>.save_rights_target` | pulse_ops_pages.py:189 | Admin |
| POST | `/admin/commands/sync` | `pulse_admin.register.<locals>.admin_commands_sync` | pulse_admin.py:730 | Admin |
| GET | `/admin/console` | `pulse_admin.register.<locals>.admin_console` | pulse_admin.py:831 | Admin |
| POST | `/admin/console/command` | `pulse_admin.register.<locals>.admin_console_command` | pulse_admin.py:890 | Admin |
| GET | `/admin/console/logs` | `pulse_admin.register.<locals>.admin_console_logs` | pulse_admin.py:884 | Admin |
| GET | `/admin/discord` | `pulse_admin.register.<locals>.admin_discord` | pulse_admin.py:384 | Admin |
| GET | `/admin/discord/banner/{filename}` | `pulse_admin.register.<locals>.admin_discord_banner` | pulse_admin.py:818 | Admin |
| POST | `/admin/discord/branding` | `pulse_admin.register.<locals>.admin_discord_branding` | pulse_admin.py:459 | Admin |
| POST | `/admin/discord/log-routing` | `pulse_admin.register.<locals>.admin_discord_log_routing` | pulse_admin.py:766 | Admin |
| POST | `/admin/discord/logs` | `pulse_admin.register.<locals>.admin_discord_logs` | pulse_admin.py:745 | Admin |
| POST | `/admin/discord/test` | `pulse_admin.register.<locals>.admin_discord_test` | pulse_admin.py:796 | Admin |
| POST | `/admin/discord/welcome` | `pulse_admin.register.<locals>.admin_discord_welcome` | pulse_admin.py:407 | Admin |
| GET | `/admin/features` | `pulse_phase7.register.<locals>.features_page` | pulse_phase7.py:124 | Admin |
| POST | `/admin/features/maintenance` | `pulse_phase7.register.<locals>.features_maintenance` | pulse_phase7.py:166 | Admin |
| POST | `/admin/features/toggle` | `pulse_phase7.register.<locals>.features_toggle` | pulse_phase7.py:153 | Admin |
| GET | `/admin/live-console` | `pulse_web.ui.admin_live_console` | pulse_web/ui.py:517 | Admin |
| POST | `/admin/live-console/command` | `pulse_web.ui.admin_live_console_command` | pulse_web/ui.py:595 | Admin |
| GET | `/admin/live-console/logs` | `pulse_web.ui.admin_live_console_logs` | pulse_web/ui.py:589 | Admin |
| GET | `/admin/panels` | `pulse_admin.register.<locals>.admin_panels` | pulse_admin.py:685 | Admin |
| POST | `/admin/panels/republish` | `pulse_admin.register.<locals>.admin_panels_republish` | pulse_admin.py:711 | Admin |
| GET | `/admin/privacy` | `pulse_admin.register.<locals>.admin_privacy` | pulse_admin.py:478 | Admin |
| POST | `/admin/privacy` | `pulse_admin.register.<locals>.admin_privacy_save` | pulse_admin.py:573 | Admin |
| GET | `/admin/privacy/export` | `pulse_admin.register.<locals>.admin_privacy_export` | pulse_admin.py:668 | Admin |
| GET | `/admin/privacy/requests` | `pulse_privacy_requests_web.register.<locals>.privacy_requests_page` | pulse_privacy_requests_web.py:51 | Admin |
| POST | `/admin/privacy/requests/create` | `pulse_privacy_requests_web.register.<locals>.privacy_requests_create` | pulse_privacy_requests_web.py:96 | Admin |
| GET | `/admin/privacy/requests/{request_id}` | `pulse_privacy_requests_web.register.<locals>.privacy_request_detail` | pulse_privacy_requests_web.py:119 | Admin |
| POST | `/admin/privacy/requests/{request_id}/assign` | `pulse_privacy_requests_web.register.<locals>.privacy_request_assign` | pulse_privacy_requests_web.py:219 | — (öffentlich/keine erkennbare Prüfung) |
| POST | `/admin/privacy/requests/{request_id}/extend` | `pulse_privacy_requests_web.register.<locals>.privacy_request_extend` | pulse_privacy_requests_web.py:235 | — (öffentlich/keine erkennbare Prüfung) |
| POST | `/admin/privacy/requests/{request_id}/note` | `pulse_privacy_requests_web.register.<locals>.privacy_request_note` | pulse_privacy_requests_web.py:240 | — (öffentlich/keine erkennbare Prüfung) |
| POST | `/admin/privacy/requests/{request_id}/status` | `pulse_privacy_requests_web.register.<locals>.privacy_request_status` | pulse_privacy_requests_web.py:214 | — (öffentlich/keine erkennbare Prüfung) |
| POST | `/admin/recovery/save` | `pulse_admin.register.<locals>.admin_recovery_save` | pulse_admin.py:588 | Admin |
| GET | `/admin/rights` | `pulse_phase7.register.<locals>.rights_page` | pulse_phase7.py:177 | Admin |
| GET | `/admin/security` | `pulse_guard_admin.register.<locals>.admin_security` | pulse_guard_admin.py:689 | Admin |
| POST | `/admin/security/antinuke` | `pulse_guard_admin.register.<locals>.admin_security_antinuke` | pulse_guard_admin.py:807 | Admin |
| POST | `/admin/security/antinuke/rules/delete` | `pulse_antinuke_admin.register.<locals>.antinuke_rule_delete` | pulse_antinuke_admin.py:770 | — (öffentlich/keine erkennbare Prüfung) |
| POST | `/admin/security/antinuke/rules/preset` | `pulse_antinuke_admin.register.<locals>.antinuke_rule_preset` | pulse_antinuke_admin.py:774 | Admin |
| POST | `/admin/security/antinuke/rules/save` | `pulse_antinuke_admin.register.<locals>.antinuke_rule_save` | pulse_antinuke_admin.py:696 | Admin |
| POST | `/admin/security/antinuke/rules/simulate` | `pulse_antinuke_admin.register.<locals>.antinuke_rule_simulate` | pulse_antinuke_admin.py:799 | Admin |
| POST | `/admin/security/antinuke/rules/toggle` | `pulse_antinuke_admin.register.<locals>.antinuke_rule_toggle` | pulse_antinuke_admin.py:766 | — (öffentlich/keine erkennbare Prüfung) |
| POST | `/admin/security/antinuke/trusted` | `pulse_antinuke_admin.register.<locals>.antinuke_trusted_save` | pulse_antinuke_admin.py:821 | Admin |
| POST | `/admin/security/antinuke/trusted/check` | `pulse_antinuke_admin.register.<locals>.antinuke_trusted_check` | pulse_antinuke_admin.py:849 | Admin |
| POST | `/admin/security/automod` | `pulse_guard_admin.register.<locals>.admin_security_automod` | pulse_guard_admin.py:694 | Admin |
| POST | `/admin/security/drills/run` | `pulse_antinuke_admin.register.<locals>.security_drill_run` | pulse_antinuke_admin.py:970 | Admin |
| POST | `/admin/security/lockdown/approve` | `pulse_antinuke_admin.register.<locals>.antinuke_lockdown_approve` | pulse_antinuke_admin.py:930 | Admin |
| POST | `/admin/security/lockdown/cancel` | `pulse_antinuke_admin.register.<locals>.antinuke_lockdown_cancel` | pulse_antinuke_admin.py:945 | Admin |
| POST | `/admin/security/lockdown/lift` | `pulse_antinuke_admin.register.<locals>.antinuke_lockdown_lift` | pulse_antinuke_admin.py:926 | — (öffentlich/keine erkennbare Prüfung) |
| POST | `/admin/security/lockdown/policy` | `pulse_antinuke_admin.register.<locals>.antinuke_lockdown_policy` | pulse_antinuke_admin.py:957 | Admin |
| POST | `/admin/security/lockdown/start` | `pulse_antinuke_admin.register.<locals>.antinuke_lockdown_start` | pulse_antinuke_admin.py:922 | — (öffentlich/keine erkennbare Prüfung) |
| POST | `/admin/security/profanity` | `pulse_guard_admin.register.<locals>.admin_security_profanity` | pulse_guard_admin.py:767 | Admin |
| POST | `/admin/security/quarantine/dismiss` | `pulse_guard_admin.register.<locals>.admin_security_quarantine_dismiss` | pulse_guard_admin.py:946 | — (öffentlich/keine erkennbare Prüfung) |
| POST | `/admin/security/quarantine/restore` | `pulse_guard_admin.register.<locals>.admin_security_quarantine_restore` | pulse_guard_admin.py:942 | — (öffentlich/keine erkennbare Prüfung) |
| POST | `/admin/security/test` | `pulse_guard_admin.register.<locals>.admin_security_test` | pulse_guard_admin.py:873 | Admin |
| GET | `/admin/server` | `pulse_server_control.register.<locals>.server_control_page` | pulse_server_control.py:636 | angemeldet + eigene Prüfung (+ is_admin) |
| POST | `/admin/server/api/actions` | `pulse_server_control.register.<locals>.scc_action` | pulse_server_control.py:914 | angemeldet + eigene Prüfung |
| GET | `/admin/server/api/backups` | `pulse_server_control.register.<locals>.scc_backups` | pulse_server_control.py:675 | angemeldet + eigene Prüfung (+ is_admin) |
| POST | `/admin/server/api/backups/verify` | `pulse_server_control.register.<locals>.scc_backup_verify` | pulse_server_control.py:988 | angemeldet + eigene Prüfung |
| POST | `/admin/server/api/console` | `pulse_server_control.register.<locals>.scc_console` | pulse_server_control.py:825 | angemeldet + eigene Prüfung |
| GET | `/admin/server/api/container` | `pulse_server_control.register.<locals>.scc_container` | pulse_server_control.py:657 | angemeldet + eigene Prüfung |
| GET | `/admin/server/api/deploy` | `pulse_server_control.register.<locals>.scc_deploy_status` | pulse_server_control.py:666 | angemeldet + eigene Prüfung |
| POST | `/admin/server/api/deploy/fetch` | `pulse_server_control.register.<locals>.scc_fetch` | pulse_server_control.py:901 | angemeldet + eigene Prüfung |
| GET | `/admin/server/api/diagnose` | `pulse_server_control.register.<locals>.scc_diagnose` | pulse_server_control.py:694 | angemeldet + eigene Prüfung |
| GET | `/admin/server/api/health` | `pulse_server_control.register.<locals>.scc_health` | pulse_server_control.py:684 | angemeldet + eigene Prüfung |
| GET | `/admin/server/api/jobs/{job_id}` | `pulse_server_control.register.<locals>.scc_job` | pulse_server_control.py:708 | angemeldet + eigene Prüfung |
| POST | `/admin/server/api/jobs/{job_id}/cancel` | `pulse_server_control.register.<locals>.scc_cancel` | pulse_server_control.py:971 | angemeldet + eigene Prüfung |
| GET | `/admin/server/api/logs/stream` | `pulse_server_control.register.<locals>.scc_log_stream` | pulse_server_control.py:746 | angemeldet + eigene Prüfung |
| GET | `/admin/server/api/overview` | `pulse_server_control.register.<locals>.scc_overview` | pulse_server_control.py:644 | angemeldet + eigene Prüfung |
| GET | `/admin/settings-history` | `pulse_phase7.register.<locals>.history_page` | pulse_phase7.py:254 | Admin |
| POST | `/admin/settings-history/revert` | `pulse_phase7.register.<locals>.history_revert` | pulse_phase7.py:275 | Admin |
| GET | `/admin/testmodus` | `pulse_testmode.register.<locals>.testmode_page` | pulse_testmode.py:83 | Admin |
| POST | `/admin/testmodus/daten` | `pulse_testmode.register.<locals>.testmode_seed` | pulse_testmode.py:105 | Admin |
| GET | `/admin/visibility` | `pulse_phase7.register.<locals>.visibility_page` | pulse_phase7.py:214 | Admin |
| POST | `/admin/visibility/expected` | `pulse_phase7.register.<locals>.visibility_expected` | pulse_phase7.py:238 | Admin |
| GET | `/admin/website` | `pulse_public_site.register.<locals>.website_admin` | pulse_public_site.py:849 | Admin |
| POST | `/admin/website/news` | `pulse_public_site.register.<locals>.website_news_add` | pulse_public_site.py:971 | Admin |
| POST | `/admin/website/news/{news_id}/delete` | `pulse_public_site.register.<locals>.website_news_delete` | pulse_public_site.py:982 | Admin |
| GET | `/admin/website/preview` | `pulse_public_site.register.<locals>.website_preview` | pulse_public_site.py:891 | Admin |
| GET | `/admin/website/team` | `pulse_public_site.register.<locals>.website_team_admin` | pulse_public_site.py:899 | Admin |
| POST | `/admin/website/team` | `pulse_public_site.register.<locals>.website_team_add` | pulse_public_site.py:937 | Admin |
| POST | `/admin/website/team/{profile_id}/delete` | `pulse_public_site.register.<locals>.website_team_delete` | pulse_public_site.py:964 | Admin |
| POST | `/admin/website/team/{profile_id}/publish` | `pulse_public_site.register.<locals>.website_team_publish` | pulse_public_site.py:950 | Admin |

### `/announcements` (2)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/announcements` | `pulse_pro.register.<locals>.announcements_page` | pulse_pro.py:2111 | angemeldet (+ can_manage_announcements, can_promote, is_admin) |
| POST | `/announcements/create` | `pulse_pro.register.<locals>.create_announcement` | pulse_pro.py:2122 | angemeldet (+ can_manage_announcements, can_promote, is_admin) |

### `/api` (11)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/api/events/poll` | `pulse_live.register.<locals>.events_poll` | pulse_live.py:114 | can_view_dashboard |
| GET | `/api/events/stream` | `pulse_live.register.<locals>.events_stream` | pulse_live.py:123 | can_view_dashboard |
| GET | `/api/live/feed` | `pulse_live.register.<locals>.live_feed` | pulse_live.py:104 | angemeldet |
| GET | `/api/live/status` | `pulse_live.register.<locals>.live_status` | pulse_live.py:99 | can_view_dashboard |
| GET | `/api/melonly/player-file` | `pulse_pro.register.<locals>.melonly_player_file_api` | pulse_pro.py:1273 | can_view_dashboard |
| GET | `/api/pulse/live` | `pulse_pro.register.<locals>.api_live` | pulse_pro.py:3896 | can_view_dashboard |
| GET | `/api/roblox-search` | `pulse_web.ui.roblox_search` | pulse_web/ui.py:384 | can_view_dashboard |
| GET | `/api/roblox-user` | `pulse_web.ui.roblox_user_lookup` | pulse_web/ui.py:320 | can_view_dashboard |
| GET | `/api/team/activity` | `pulse_web.tickets.team_activity_api` | pulse_web/tickets.py:7 | can_view_dashboard |
| GET | `/api/team/discord-status` | `pulse_pro.register.<locals>.discord_status_api` | pulse_pro.py:2063 | angemeldet (+ can_promote, can_view_team_status, is_admin) |
| GET | `/api/team/warn-roles` | `pulse_web.tickets.warn_roles_api` | pulse_web/tickets.py:44 | can_warn |

### `/applications` (14)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/applications` | `pulse_web.applications.applications_page` | pulse_web/applications.py:406 | angemeldet (+ can_manage_applications, can_promote, can_view_dashboard, is_admin) |
| POST | `/applications/assign` | `pulse_web.applications.assign_application_reviewers` | pulse_web/applications.py:1060 | angemeldet (+ can_manage_applications, can_promote, is_admin) |
| GET | `/applications/board` | `pulse_phase7.register.<locals>.board_page` | pulse_phase7.py:445 | angemeldet (+ can_manage_applications, can_promote, is_admin) |
| POST | `/applications/board/deadlines` | `pulse_phase7.register.<locals>.board_deadlines` | pulse_phase7.py:514 | angemeldet (+ can_manage_applications, can_promote, is_admin) |
| GET | `/applications/config` | `pulse_web.applications.application_modules_config_page` | pulse_web/applications.py:1193 | angemeldet (+ can_manage_applications, can_promote, is_admin) |
| POST | `/applications/config` | `pulse_web.applications.save_application_modules_config` | pulse_web/applications.py:1240 | angemeldet (+ can_manage_applications, can_promote, is_admin) |
| POST | `/applications/interview-cancel` | `pulse_web.applications.application_interview_cancel` | pulse_web/applications.py:863 | angemeldet |
| POST | `/applications/interview-slot` | `pulse_web.applications.application_interview_slot` | pulse_web/applications.py:805 | angemeldet (+ can_promote, is_admin) |
| GET | `/applications/journal/{app_id}` | `pulse_web.applications.application_comm_journal_page` | pulse_web/applications.py:1165 | angemeldet |
| POST | `/applications/review` | `pulse_web.applications.save_application_review` | pulse_web/applications.py:998 | angemeldet (+ can_manage_applications, can_promote, is_admin) |
| POST | `/applications/review-policy` | `pulse_web.applications.save_application_review_policy` | pulse_web/applications.py:1107 | Admin |
| GET | `/applications/statistik` | `pulse_web.applications.application_stats_page` | pulse_web/applications.py:1123 | angemeldet |
| POST | `/applications/templates` | `pulse_web.applications.application_templates_save` | pulse_web/applications.py:894 | Admin |
| POST | `/applications/templates/restore` | `pulse_web.applications.application_templates_restore` | pulse_web/applications.py:909 | Admin |

### `/apply` (5)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/apply` | `pulse_web.applications.public_apply_page` | pulse_web/applications.py:110 | Session (eigene Prüfung) |
| GET | `/apply/callback` | `pulse_web.applications.apply_callback` | pulse_web/applications.py:374 | Session (eigene Prüfung) |
| POST | `/apply/keepalive` | `pulse_web.applications.apply_keepalive` | pulse_web/applications.py:390 | Session (eigene Prüfung) |
| GET | `/apply/login` | `pulse_web.applications.apply_login` | pulse_web/applications.py:356 | — (öffentlich/keine erkennbare Prüfung) |
| POST | `/apply/logout` | `pulse_web.dashboard.apply_logout` | pulse_web/dashboard.py:112 | Session (eigene Prüfung) |

### `/approvals` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/approvals` | `pulse_pro.register.<locals>.approvals_page` | pulse_pro.py:2238 | can_view_dashboard (+ can_manage_applications, can_promote, is_admin) |

### `/backup` (10)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| POST | `/backup/create` | `pulse_web.backups.create_backup` | pulse_web/backups.py:1146 | Admin |
| POST | `/backup/delete` | `pulse_web.backups.delete_backup` | pulse_web/backups.py:1350 | Admin |
| GET | `/backup/download/{filename}` | `pulse_web.backups.download_backup` | pulse_web/backups.py:1209 | Admin |
| POST | `/backup/full/create` | `pulse_web.backups.full_backup_create` | pulse_web/backups.py:1476 | Admin |
| POST | `/backup/full/delete` | `pulse_web.backups.full_backup_delete` | pulse_web/backups.py:1571 | Admin |
| GET | `/backup/full/download/{filename}` | `pulse_web.backups.full_backup_download` | pulse_web/backups.py:1489 | Admin |
| GET | `/backup/full/preview/{filename}` | `pulse_web.backups.full_backup_preview` | pulse_web/backups.py:1498 | Admin |
| POST | `/backup/full/restore` | `pulse_web.backups.full_backup_restore` | pulse_web/backups.py:1544 | Admin |
| POST | `/backup/restore` | `pulse_web.backups.restore_backup` | pulse_web/backups.py:1231 | Admin |
| POST | `/backup/upload` | `pulse_web.backups.upload_backup` | pulse_web/backups.py:1158 | Admin |

### `/backups` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/backups` | `pulse_web.backups.backups_page` | pulse_web/backups.py:1071 | Admin |

### `/besprechungen` (7)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/besprechungen` | `pulse_meetings_pages.register.<locals>.meetings_page` | pulse_meetings_pages.py:27 | can_view_dashboard |
| POST | `/besprechungen/neu` | `pulse_meetings_pages.register.<locals>.meeting_new` | pulse_meetings_pages.py:45 | angemeldet |
| GET | `/besprechungen/{meeting_id}` | `pulse_meetings_pages.register.<locals>.meeting_detail` | pulse_meetings_pages.py:69 | can_view_dashboard |
| POST | `/besprechungen/{meeting_id}/abschliessen` | `pulse_meetings_pages.register.<locals>.meeting_close` | pulse_meetings_pages.py:145 | angemeldet |
| POST | `/besprechungen/{meeting_id}/anwesenheit` | `pulse_meetings_pages.register.<locals>.meeting_attendance` | pulse_meetings_pages.py:115 | angemeldet |
| POST | `/besprechungen/{meeting_id}/beschluss` | `pulse_meetings_pages.register.<locals>.meeting_decision` | pulse_meetings_pages.py:125 | angemeldet |
| POST | `/besprechungen/{meeting_id}/start` | `pulse_meetings_pages.register.<locals>.meeting_start` | pulse_meetings_pages.py:104 | angemeldet |

### `/bestaetigungen` (3)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/bestaetigungen` | `pulse_ack_pages.register.<locals>.ack_page` | pulse_ack_pages.py:44 | can_view_dashboard |
| POST | `/bestaetigungen/neu` | `pulse_ack_pages.register.<locals>.ack_new` | pulse_ack_pages.py:81 | angemeldet |
| POST | `/bestaetigungen/{kind}/{subject_id}/bestaetigen` | `pulse_ack_pages.register.<locals>.ack_confirm` | pulse_ack_pages.py:72 | can_view_dashboard |

### `/calendar` (2)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/calendar` | `pulse_pro.register.<locals>.calendar_v5` | pulse_pro.py:2651 | can_view_dashboard (+ can_manage_applications, can_promote, is_admin) |
| POST | `/calendar/create` | `pulse_pro.register.<locals>.create_calendar_event_v5` | pulse_pro.py:2866 | can_view_dashboard |

### `/callback` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/callback` | `pulse_web.dashboard.callback` | pulse_web/dashboard.py:120 | Session (eigene Prüfung) |

### `/cockpit` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/cockpit` | `pulse_cockpit.register.<locals>.leadership_cockpit` | pulse_cockpit.py:46 | angemeldet (+ can_promote, is_admin) |

### `/cookies` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/cookies` | `pulse_public_site.register.<locals>.add_page.<locals>.handler` | pulse_public_site.py:780 | — (öffentlich/keine erkennbare Prüfung) |

### `/dashboard` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/dashboard` | `pulse_pro.register.<locals>.dashboard_v5` | pulse_pro.py:1707 | can_view_dashboard |

### `/datenschutz` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/datenschutz` | `pulse_public_site.register.<locals>.add_page.<locals>.handler` | pulse_public_site.py:780 | — (öffentlich/keine erkennbare Prüfung) |

### `/export` (2)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/export/logs` | `pulse_web.dashboard.export_logs` | pulse_web/dashboard.py:1473 | can_view_analytics |
| GET | `/export/team.csv` | `pulse_pro.register.<locals>.team_export` | pulse_pro.py:3879 | can_view_analytics |

### `/factions` (11)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/factions` | `pulse_factions_dashboard.register.<locals>.factions_page` | pulse_factions_dashboard.py:155 | angemeldet + eigene Prüfung |
| POST | `/factions/categories/create` | `pulse_factions_dashboard.register.<locals>.faction_category_create` | pulse_factions_dashboard.py:410 | angemeldet + eigene Prüfung |
| POST | `/factions/categories/{category_id:int}/delete` | `pulse_factions_dashboard.register.<locals>.faction_category_delete` | pulse_factions_dashboard.py:438 | angemeldet + eigene Prüfung |
| POST | `/factions/categories/{category_id:int}/update` | `pulse_factions_dashboard.register.<locals>.faction_category_update` | pulse_factions_dashboard.py:423 | angemeldet + eigene Prüfung |
| POST | `/factions/create` | `pulse_factions_dashboard.register.<locals>.faction_create` | pulse_factions_dashboard.py:304 | angemeldet + eigene Prüfung |
| GET | `/factions/{faction_id:int}` | `pulse_factions_dashboard.register.<locals>.faction_detail` | pulse_factions_dashboard.py:238 | angemeldet + eigene Prüfung |
| POST | `/factions/{faction_id:int}/delete` | `pulse_factions_dashboard.register.<locals>.faction_delete` | pulse_factions_dashboard.py:357 | angemeldet + eigene Prüfung |
| POST | `/factions/{faction_id:int}/official` | `pulse_factions_dashboard.register.<locals>.faction_official` | pulse_factions_dashboard.py:343 | angemeldet + eigene Prüfung |
| POST | `/factions/{faction_id:int}/update` | `pulse_factions_dashboard.register.<locals>.faction_update` | pulse_factions_dashboard.py:318 | angemeldet + eigene Prüfung |
| POST | `/factions/{faction_id:int}/warnings/add` | `pulse_factions_dashboard.register.<locals>.faction_warn` | pulse_factions_dashboard.py:374 | angemeldet + eigene Prüfung |
| POST | `/factions/{faction_id:int}/warnings/{warning_id:int}/revoke` | `pulse_factions_dashboard.register.<locals>.faction_warn_revoke` | pulse_factions_dashboard.py:392 | angemeldet + eigene Prüfung |

### `/faq` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/faq` | `pulse_public_site.register.<locals>.add_page.<locals>.handler` | pulse_public_site.py:780 | — (öffentlich/keine erkennbare Prüfung) |

### `/favicon.ico` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/favicon.ico` | `webserver.favicon` | webserver.py:271 | — (öffentlich/keine erkennbare Prüfung) |

### `/fraktionen` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/fraktionen` | `pulse_public_site.register.<locals>.add_page.<locals>.handler` | pulse_public_site.py:780 | — (öffentlich/keine erkennbare Prüfung) |

### `/handover` (2)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/handover` | `pulse_pro.register.<locals>.handover_page` | pulse_pro.py:2084 | can_view_dashboard (+ can_manage_handover, can_promote, can_view_dashboard, is_admin) |
| POST | `/handover/create` | `pulse_pro.register.<locals>.create_handover` | pulse_pro.py:2098 | angemeldet (+ can_manage_handover, can_view_dashboard) |

### `/health` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/health` | `pulse_pro.register.<locals>.api_health` | pulse_pro.py:3903 | — (öffentlich/keine erkennbare Prüfung) |

### `/healthz` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/healthz` | `pulse_health_api.healthz` | pulse_health_api.py:23 | — (öffentlich/keine erkennbare Prüfung) |

### `/impressum` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/impressum` | `pulse_public_site.register.<locals>.add_page.<locals>.handler` | pulse_public_site.py:780 | — (öffentlich/keine erkennbare Prüfung) |

### `/leitstelle` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/leitstelle` | `pulse_teamakte_pages.register.<locals>.control_room` | pulse_teamakte_pages.py:307 | — (öffentlich/keine erkennbare Prüfung) |

### `/loa` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/loa` | `pulse_web.team.loa_page` | pulse_web/team.py:967 | can_view_dashboard |

### `/log` (3)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| POST | `/log/create` | `pulse_web.dashboard.create_log` | pulse_web/dashboard.py:1258 | can_view_dashboard (+ can_add_notes, can_moderate_bans, can_moderate_bolo, can_moderate_kicks) |
| POST | `/log/delete` | `pulse_web.dashboard.delete_log` | pulse_web/dashboard.py:1353 | can_view_dashboard |
| POST | `/log/edit` | `pulse_web.dashboard.edit_log` | pulse_web/dashboard.py:1302 | can_view_dashboard |

### `/login` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/login` | `pulse_web.dashboard.login` | pulse_web/dashboard.py:70 | — (öffentlich/keine erkennbare Prüfung) |

### `/logout` (2)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/logout` | `pulse_web.dashboard.logout_confirm` | pulse_web/dashboard.py:84 | — (öffentlich/keine erkennbare Prüfung) |
| POST | `/logout` | `pulse_web.dashboard.logout` | pulse_web/dashboard.py:94 | Session (eigene Prüfung) |

### `/manifest.webmanifest` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/manifest.webmanifest` | `pulse_pro.register.<locals>.manifest` | pulse_pro.py:3911 | — (öffentlich/keine erkennbare Prüfung) |

### `/me` (17)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/me` | `pulse_home.register.<locals>.my_pulse` | pulse_home.py:123 | can_view_dashboard (+ can_add_notes, can_manage_applications, can_manage_tickets, can_promote) |
| POST | `/me/favorites/add` | `pulse_home.register.<locals>.my_favorite_add` | pulse_home.py:582 | can_view_dashboard |
| POST | `/me/favorites/remove` | `pulse_home.register.<locals>.my_favorite_remove` | pulse_home.py:600 | can_view_dashboard |
| GET | `/me/layouts` | `pulse_home.register.<locals>.my_home_layouts` | pulse_home.py:460 | can_view_dashboard |
| POST | `/me/layouts/activate` | `pulse_home.register.<locals>.my_home_layout_activate` | pulse_home.py:558 | can_view_dashboard |
| POST | `/me/layouts/default` | `pulse_home.register.<locals>.my_home_layout_default` | pulse_home.py:576 | can_view_dashboard |
| POST | `/me/layouts/delete` | `pulse_home.register.<locals>.my_home_layout_delete` | pulse_home.py:567 | can_view_dashboard |
| POST | `/me/layouts/save` | `pulse_home.register.<locals>.my_home_layout_save` | pulse_home.py:543 | can_view_dashboard |
| GET | `/me/month` | `pulse_phase7.register.<locals>.my_month_page` | pulse_phase7.py:581 | can_view_dashboard |
| POST | `/me/month/optin` | `pulse_phase7.register.<locals>.my_month_optin` | pulse_phase7.py:610 | can_view_dashboard |
| GET | `/me/sessions` | `pulse_home.register.<locals>.my_sessions` | pulse_home.py:613 | can_view_dashboard |
| POST | `/me/sessions/revoke` | `pulse_home.register.<locals>.my_session_revoke` | pulse_home.py:642 | can_view_dashboard |
| POST | `/me/sessions/revoke-all` | `pulse_home.register.<locals>.my_session_revoke_all` | pulse_home.py:661 | can_view_dashboard |
| POST | `/me/sessions/revoke-others` | `pulse_home.register.<locals>.my_session_revoke_others` | pulse_home.py:651 | can_view_dashboard |
| POST | `/me/start-page` | `pulse_home.register.<locals>.my_start_page` | pulse_home.py:445 | can_view_dashboard |
| POST | `/me/views/save` | `pulse_views.register.<locals>.views_save` | pulse_views.py:144 | can_view_dashboard |
| POST | `/me/views/{view_id}/delete` | `pulse_views.register.<locals>.views_delete` | pulse_views.py:158 | can_view_dashboard |

### `/meetings` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/meetings` | `pulse_web.team.meetings_page` | pulse_web/team.py:831 | can_view_dashboard |

### `/meetings-history` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/meetings-history` | `pulse_pro.register.<locals>.meetings_history_v5` | pulse_pro.py:3280 | can_view_dashboard |

### `/melonly` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/melonly` | `pulse_pro.register.<locals>.melonly_page` | pulse_pro.py:1293 | can_view_dashboard |

### `/member` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/member/{user_id}` | `pulse_pro.register.<locals>.member_v5` | pulse_pro.py:2944 | can_view_dashboard (+ can_add_notes, can_manage_team_warnings, can_promote, is_admin) |

### `/mod-cases` (7)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/mod-cases` | `pulse_web.tickets.mod_cases_page` | pulse_web/tickets.py:1386 | — (öffentlich/keine erkennbare Prüfung) |
| GET | `/mod-cases/beweise` | `pulse_web.tickets.mod_case_evidence_page` | pulse_web/tickets.py:1545 | angemeldet |
| POST | `/mod-cases/beweise/add` | `pulse_web.tickets.mod_case_evidence_add` | pulse_web/tickets.py:1605 | angemeldet |
| POST | `/mod-cases/beweise/remove` | `pulse_web.tickets.mod_case_evidence_remove` | pulse_web/tickets.py:1625 | angemeldet |
| GET | `/mod-cases/stats` | `pulse_web.tickets.mod_cases_stats_page` | pulse_web/tickets.py:1645 | — (öffentlich/keine erkennbare Prüfung) |
| GET | `/mod-cases/stats.csv` | `pulse_web.tickets.mod_cases_stats_csv` | pulse_web/tickets.py:1711 | — (öffentlich/keine erkennbare Prüfung) |
| GET | `/mod-cases/verlauf` | `pulse_web.tickets.mod_cases_history_page` | pulse_web/tickets.py:1455 | angemeldet |

### `/mod-cases.csv` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/mod-cases.csv` | `pulse_web.tickets.mod_cases_csv` | pulse_web/tickets.py:1441 | — (öffentlich/keine erkennbare Prüfung) |

### `/news` (2)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/news` | `pulse_public_site.register.<locals>.add_page.<locals>.handler` | pulse_public_site.py:780 | — (öffentlich/keine erkennbare Prüfung) |
| GET | `/news/{news_id}` | `pulse_public_site.register.<locals>.public_news_detail` | pulse_public_site.py:807 | — (öffentlich/keine erkennbare Prüfung) |

### `/nutzungsbedingungen` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/nutzungsbedingungen` | `pulse_public_site.register.<locals>.add_page.<locals>.handler` | pulse_public_site.py:780 | — (öffentlich/keine erkennbare Prüfung) |

### `/promotions` (3)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/promotions` | `pulse_pro.register.<locals>.promotions_page` | pulse_pro.py:2135 | angemeldet (+ can_manage_promotions, can_promote, is_admin) |
| POST | `/promotions/create` | `pulse_pro.register.<locals>.create_promotion` | pulse_pro.py:2172 | angemeldet (+ can_manage_promotions, can_promote, is_admin) |
| POST | `/promotions/decide` | `pulse_pro.register.<locals>.decide_promotion` | pulse_pro.py:2190 | angemeldet (+ can_manage_promotions, can_promote, is_admin) |

### `/pulse-inbox` (5)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/pulse-inbox` | `pulse_pro.register.<locals>.inbox_v5` | pulse_pro.py:1747 | can_view_dashboard (+ is_admin) |
| POST | `/pulse-inbox/read` | `pulse_pro.register.<locals>.inbox_read_all` | pulse_pro.py:1793 | can_view_dashboard |
| POST | `/pulse-inbox/read/{nid}` | `pulse_pro.register.<locals>.inbox_read_one` | pulse_pro.py:1796 | can_view_dashboard |
| POST | `/pulse-inbox/server-quiet` | `pulse_pro.register.<locals>.inbox_server_quiet` | pulse_pro.py:1779 | Admin |
| POST | `/pulse-inbox/settings` | `pulse_pro.register.<locals>.inbox_settings` | pulse_pro.py:1787 | can_view_dashboard |

### `/recovery` (2)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/recovery/authorize` | `pulse_admin.register.<locals>.recovery_authorize` | pulse_admin.py:621 | — (öffentlich/keine erkennbare Prüfung) |
| POST | `/recovery/authorize/start` | `pulse_admin.register.<locals>.recovery_authorize_start` | pulse_admin.py:641 | — (öffentlich/keine erkennbare Prüfung) |

### `/regelwerk` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/regelwerk` | `pulse_public_site.register.<locals>.add_page.<locals>.handler` | pulse_public_site.py:780 | — (öffentlich/keine erkennbare Prüfung) |

### `/regelwerk-suche` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/regelwerk-suche` | `pulse_rules_lookup.register.<locals>.rules_page` | pulse_rules_lookup.py:158 | can_view_dashboard |

### `/robots.txt` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/robots.txt` | `pulse_pro.register.<locals>.robots_txt` | pulse_pro.py:3956 | — (öffentlich/keine erkennbare Prüfung) |

### `/search` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/search` | `pulse_pro.register.<locals>.search_v5` | pulse_pro.py:2922 | can_view_dashboard (+ can_manage_tickets, can_promote, is_admin) |

### `/security` (2)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/security` | `pulse_web.administration.security_page` | pulse_web/administration.py:91 | Admin |
| POST | `/security` | `pulse_web.administration.save_security_settings` | pulse_web/administration.py:98 | Admin |

### `/settings` (14)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/settings` | `pulse_pro.register.<locals>.settings_v5` | pulse_pro.py:3311 | Admin |
| POST | `/settings/dashboard-profile/delete` | `pulse_pro.register.<locals>.settings_dashboard_profile_delete` | pulse_pro.py:3608 | Admin |
| POST | `/settings/dashboard-profile/save` | `pulse_pro.register.<locals>.settings_dashboard_profile_save` | pulse_pro.py:3553 | Admin |
| POST | `/settings/nickname-prefix/default` | `pulse_pro.register.<locals>.settings_nickname_prefix_default` | pulse_pro.py:3658 | Admin |
| POST | `/settings/nickname-prefix/delete` | `pulse_pro.register.<locals>.settings_nickname_prefix_delete` | pulse_pro.py:3682 | Admin |
| POST | `/settings/nickname-prefix/save` | `pulse_pro.register.<locals>.settings_nickname_prefix_save` | pulse_pro.py:3625 | Admin |
| POST | `/settings/nickname-prefix/sync` | `pulse_pro.register.<locals>.settings_nickname_prefix_sync` | pulse_pro.py:3706 | Admin |
| POST | `/settings/pro-clear-all` | `pulse_pro.register.<locals>.settings_clear_all_permissions` | pulse_pro.py:3762 | Admin |
| POST | `/settings/pro-goal` | `pulse_pro.register.<locals>.settings_goal` | pulse_pro.py:3793 | Admin |
| POST | `/settings/pro-presets` | `pulse_pro.register.<locals>.settings_profiles` | pulse_pro.py:3755 | Admin |
| POST | `/settings/pro-roles` | `pulse_pro.register.<locals>.settings_roles` | pulse_pro.py:3729 | Admin |
| POST | `/settings/pro-save` | `pulse_pro.register.<locals>.settings_save` | pulse_pro.py:3721 | Admin |
| POST | `/settings/pro-warn-roles` | `pulse_pro.register.<locals>.settings_warn_roles` | pulse_pro.py:3767 | Admin |
| POST | `/settings/pro-warn-sync` | `pulse_pro.register.<locals>.settings_warn_sync` | pulse_pro.py:3786 | Admin |

### `/setup` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/setup` | `pulse_web.administration.setup_wizard` | pulse_web/administration.py:7 | Admin |

### `/shift` (4)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| POST | `/shift/action` | `pulse_web.dashboard.handle_shift_action` | pulse_web/dashboard.py:1225 | can_view_dashboard |
| POST | `/shift/admin-end` | `pulse_pro.register.<locals>.admin_end_shift` | pulse_pro.py:1658 | can_view_dashboard (+ can_promote, is_admin) |
| POST | `/shift/history/confirm-duration` | `pulse_pro.register.<locals>.confirm_auto_closed_shift_duration` | pulse_pro.py:1604 | can_view_dashboard (+ can_promote, is_admin) |
| POST | `/shift/notes` | `pulse_pro.register.<locals>.add_shift_note_dashboard` | pulse_pro.py:1557 | angemeldet (+ can_add_notes, is_admin) |

### `/sicherheit` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/sicherheit` | `pulse_public_site.register.<locals>.add_page.<locals>.handler` | pulse_public_site.py:780 | — (öffentlich/keine erkennbare Prüfung) |

### `/sitemap.xml` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/sitemap.xml` | `pulse_public_site.register.<locals>.sitemap` | pulse_public_site.py:826 | — (öffentlich/keine erkennbare Prüfung) |

### `/social` (7)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/social` | `pulse_social_dashboard.register.<locals>.social_page` | pulse_social_dashboard.py:60 | angemeldet + eigene Prüfung |
| POST | `/social/add` | `pulse_social_dashboard.register.<locals>.social_add_link` | pulse_social_dashboard.py:185 | angemeldet + eigene Prüfung |
| POST | `/social/announce-latest` | `pulse_social_dashboard.register.<locals>.social_announce_latest` | pulse_social_dashboard.py:232 | angemeldet + eigene Prüfung |
| POST | `/social/remove` | `pulse_social_dashboard.register.<locals>.social_remove_web` | pulse_social_dashboard.py:222 | angemeldet + eigene Prüfung |
| POST | `/social/test` | `pulse_social_dashboard.register.<locals>.social_test_web` | pulse_social_dashboard.py:241 | angemeldet + eigene Prüfung |
| POST | `/social/toggle` | `pulse_social_dashboard.register.<locals>.social_toggle_web` | pulse_social_dashboard.py:213 | angemeldet + eigene Prüfung |
| POST | `/social/update` | `pulse_social_dashboard.register.<locals>.social_update` | pulse_social_dashboard.py:201 | angemeldet + eigene Prüfung |

### `/static` (3)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/static/public.css` | `pulse_public_site.register.<locals>.add_static.<locals>.handler` | pulse_public_site.py:836 | — (öffentlich/keine erkennbare Prüfung) |
| GET | `/static/public.js` | `pulse_public_site.register.<locals>.add_static.<locals>.handler` | pulse_public_site.py:836 | — (öffentlich/keine erkennbare Prüfung) |
| GET | `/static/pulse.css` | `pulse_web.ui.static_pulse_css` | pulse_web/ui.py:687 | — (öffentlich/keine erkennbare Prüfung) |

### `/stats` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/stats` | `pulse_pro.register.<locals>.stats_v5` | pulse_pro.py:2634 | can_view_analytics |

### `/status` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/status` | `pulse_phase7.register.<locals>.public_status_page` | pulse_phase7.py:550 | — (öffentlich/keine erkennbare Prüfung) |

### `/status.json` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/status.json` | `pulse_phase7.register.<locals>.public_status_json` | pulse_phase7.py:542 | — (öffentlich/keine erkennbare Prüfung) |

### `/suite` (44)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/suite` | `cogs.pulse_suite.register_routes.<locals>.leadership` | cogs/pulse_suite.py:610 | can_view_dashboard |
| GET | `/suite/achievements` | `cogs.pulse_suite.register_routes.<locals>.achievements_page` | cogs/pulse_suite.py:1430 | can_view_dashboard |
| GET | `/suite/api/search` | `cogs.pulse_suite.register_routes.<locals>.search_api` | cogs/pulse_suite.py:1360 | can_view_dashboard |
| GET | `/suite/applications` | `cogs.pulse_suite.register_routes.<locals>.applications_page` | cogs/pulse_suite.py:1094 | can_view_dashboard |
| GET | `/suite/applications/{app_id}` | `cogs.pulse_suite.register_routes.<locals>.application_page` | cogs/pulse_suite.py:1108 | can_view_dashboard |
| POST | `/suite/applications/{app_id}/update` | `cogs.pulse_suite.register_routes.<locals>.application_update` | cogs/pulse_suite.py:1120 | can_view_dashboard |
| GET | `/suite/audit` | `cogs.pulse_suite.register_routes.<locals>.audit_page` | cogs/pulse_suite.py:1303 | can_view_dashboard |
| GET | `/suite/audit.csv` | `cogs.pulse_suite.register_routes.<locals>.audit_csv` | cogs/pulse_suite.py:1343 | can_view_dashboard |
| GET | `/suite/calendar` | `cogs.pulse_suite.register_routes.<locals>.calendar_page` | cogs/pulse_suite.py:1075 | can_view_dashboard |
| POST | `/suite/calendar/create` | `cogs.pulse_suite.register_routes.<locals>.calendar_create` | cogs/pulse_suite.py:1088 | can_view_dashboard |
| GET | `/suite/cases` | `cogs.pulse_suite.register_routes.<locals>.cases_page` | cogs/pulse_suite.py:1364 | can_view_dashboard |
| POST | `/suite/cases/{case_id}/action` | `cogs.pulse_suite.register_routes.<locals>.case_action` | cogs/pulse_suite.py:1405 | can_view_dashboard |
| GET | `/suite/forecast` | `cogs.pulse_suite.register_routes.<locals>.staffing_forecast_page` | cogs/pulse_suite.py:867 | can_view_dashboard |
| POST | `/suite/forecast/settings` | `cogs.pulse_suite.register_routes.<locals>.staffing_forecast_settings` | cogs/pulse_suite.py:961 | can_view_dashboard |
| GET | `/suite/interview/create` | `cogs.pulse_suite.register_routes.<locals>.interview_form` | cogs/pulse_suite.py:1146 | can_view_dashboard |
| POST | `/suite/interview/create` | `cogs.pulse_suite.register_routes.<locals>.interview_create` | cogs/pulse_suite.py:1152 | can_view_dashboard |
| GET | `/suite/interviews` | `cogs.pulse_suite.register_routes.<locals>.interviews_page` | cogs/pulse_suite.py:1138 | can_view_dashboard |
| GET | `/suite/member/{member_id}` | `cogs.pulse_suite.register_routes.<locals>.member_page` | cogs/pulse_suite.py:652 | can_view_dashboard |
| POST | `/suite/member/{member_id}/note` | `cogs.pulse_suite.register_routes.<locals>.member_note` | cogs/pulse_suite.py:666 | can_view_dashboard |
| GET | `/suite/my-schedule` | `cogs.pulse_suite.register_routes.<locals>.my_schedule` | cogs/pulse_suite.py:683 | can_view_dashboard |
| GET | `/suite/news` | `cogs.pulse_suite.register_routes.<locals>.news_page` | cogs/pulse_suite.py:1295 | can_view_dashboard |
| POST | `/suite/news/create` | `cogs.pulse_suite.register_routes.<locals>.news_create` | cogs/pulse_suite.py:1299 | can_view_dashboard |
| GET | `/suite/promotions` | `cogs.pulse_suite.register_routes.<locals>.promotions_page` | cogs/pulse_suite.py:1169 | can_view_dashboard |
| POST | `/suite/promotions/create` | `cogs.pulse_suite.register_routes.<locals>.promotions_create` | cogs/pulse_suite.py:1176 | can_view_dashboard (+ is_admin) |
| POST | `/suite/promotions/{pid}/action` | `cogs.pulse_suite.register_routes.<locals>.promotions_action` | cogs/pulse_suite.py:1191 | can_view_dashboard (+ is_admin) |
| GET | `/suite/schedule` | `cogs.pulse_suite.register_routes.<locals>.schedule_page` | cogs/pulse_suite.py:761 | can_view_dashboard |
| POST | `/suite/schedule/create` | `cogs.pulse_suite.register_routes.<locals>.schedule_create` | cogs/pulse_suite.py:810 | can_view_dashboard |
| POST | `/suite/schedule/minimums/add` | `cogs.pulse_suite.register_routes.<locals>.schedule_minimum_add` | cogs/pulse_suite.py:844 | can_view_dashboard |
| POST | `/suite/schedule/minimums/{rule_id}/delete` | `cogs.pulse_suite.register_routes.<locals>.schedule_minimum_delete` | cogs/pulse_suite.py:858 | can_view_dashboard |
| POST | `/suite/schedule/reminder-lead` | `cogs.pulse_suite.register_routes.<locals>.schedule_reminder_lead` | cogs/pulse_suite.py:799 | can_view_dashboard |
| POST | `/suite/schedule/swaps/create` | `cogs.pulse_suite.register_routes.<locals>.schedule_swap_create` | cogs/pulse_suite.py:978 | can_view_dashboard |
| POST | `/suite/schedule/swaps/{request_id}/cancel` | `cogs.pulse_suite.register_routes.<locals>.schedule_swap_cancel` | cogs/pulse_suite.py:1035 | can_view_dashboard |
| POST | `/suite/schedule/swaps/{request_id}/decision` | `cogs.pulse_suite.register_routes.<locals>.schedule_swap_decision` | cogs/pulse_suite.py:1043 | can_view_dashboard |
| POST | `/suite/schedule/swaps/{request_id}/respond` | `cogs.pulse_suite.register_routes.<locals>.schedule_swap_respond` | cogs/pulse_suite.py:1006 | can_view_dashboard |
| POST | `/suite/schedule/{shift_id}/delete` | `cogs.pulse_suite.register_routes.<locals>.schedule_delete` | cogs/pulse_suite.py:830 | can_view_dashboard |
| GET | `/suite/search` | `cogs.pulse_suite.register_routes.<locals>.search_page` | cogs/pulse_suite.py:1355 | can_view_dashboard |
| GET | `/suite/task/{task_id}` | `cogs.pulse_suite.register_routes.<locals>.task_detail` | cogs/pulse_suite.py:1261 | can_view_dashboard |
| POST | `/suite/task/{task_id}/meta` | `cogs.pulse_suite.register_routes.<locals>.task_meta` | cogs/pulse_suite.py:1270 | can_view_dashboard |
| GET | `/suite/tasks` | `cogs.pulse_suite.register_routes.<locals>.tasks_page` | cogs/pulse_suite.py:1250 | can_view_dashboard |
| POST | `/suite/tasks/{task_id}/move` | `cogs.pulse_suite.register_routes.<locals>.task_move` | cogs/pulse_suite.py:1286 | can_view_dashboard |
| GET | `/suite/team` | `cogs.pulse_suite.register_routes.<locals>.team_page` | cogs/pulse_suite.py:638 | can_view_dashboard |
| GET | `/suite/time` | `cogs.pulse_suite.register_routes.<locals>.time_page` | cogs/pulse_suite.py:675 | can_view_dashboard |
| GET | `/suite/training` | `cogs.pulse_suite.register_routes.<locals>.training_page` | cogs/pulse_suite.py:1234 | can_view_dashboard |
| POST | `/suite/training/assign` | `cogs.pulse_suite.register_routes.<locals>.training_assign` | cogs/pulse_suite.py:1242 | can_view_dashboard |

### `/sw.js` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/sw.js` | `pulse_pro.register.<locals>.service_worker` | pulse_pro.py:3914 | — (öffentlich/keine erkennbare Prüfung) |

### `/system` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/system` | `pulse_pro.register.<locals>.system_v5` | pulse_pro.py:3796 | Admin |

### `/tasks` (4)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/tasks` | `pulse_pro.register.<locals>.tasks_v5` | pulse_pro.py:2248 | can_view_dashboard (+ can_manage_tasks, can_promote, is_admin) |
| POST | `/tasks/create` | `pulse_features.register.<locals>.task_create` | pulse_features.py:61 | angemeldet |
| POST | `/tasks/distribute` | `pulse_features.register.<locals>.tasks_distribute` | pulse_features.py:23 | angemeldet (+ can_manage_tasks, can_promote, is_admin) |
| POST | `/tasks/update` | `pulse_pro.register.<locals>.update_task_v5` | pulse_pro.py:2271 | can_view_dashboard |

### `/team` (14)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/team` | `pulse_web.team.team_list_page` | pulse_web/team.py:7 | can_view_dashboard (+ can_promote, is_admin) |
| GET | `/team/capacity` | `pulse_phase7.register.<locals>.capacity_page` | pulse_phase7.py:291 | angemeldet (+ can_promote, can_view_analytics, is_admin) |
| GET | `/team/checklists` | `pulse_web.tickets.team_checklists_page` | pulse_web/tickets.py:1296 | — (öffentlich/keine erkennbare Prüfung) |
| POST | `/team/checklists/mentor` | `pulse_web.tickets.team_checklists_mentor` | pulse_web/tickets.py:1357 | — (öffentlich/keine erkennbare Prüfung) |
| POST | `/team/checklists/toggle` | `pulse_web.tickets.team_checklists_toggle` | pulse_web/tickets.py:1341 | — (öffentlich/keine erkennbare Prüfung) |
| GET | `/team/intelligenz` | `pulse_teamakte_pages.register.<locals>.team_intelligence` | pulse_teamakte_pages.py:296 | — (öffentlich/keine erkennbare Prüfung) |
| GET | `/team/planung` | `pulse_teamakte_pages.register.<locals>.team_planning` | pulse_teamakte_pages.py:285 | — (öffentlich/keine erkennbare Prüfung) |
| GET | `/team/readiness` | `pulse_phase7.register.<locals>.readiness_page` | pulse_phase7.py:326 | angemeldet (+ can_promote, can_warn, is_admin) |
| POST | `/team/readiness/criteria` | `pulse_phase7.register.<locals>.readiness_criteria` | pulse_phase7.py:373 | angemeldet (+ can_promote, is_admin) |
| POST | `/team/voice-support/audio` | `pulse_web.team.manage_voice_support_audio` | pulse_web/team.py:397 | can_promote |
| POST | `/team/{user_id}/entwicklung/neu` | `pulse_teamakte_pages.register.<locals>.goal_create` | pulse_teamakte_pages.py:323 | — (öffentlich/keine erkennbare Prüfung) |
| POST | `/team/{user_id}/entwicklung/{goal_id}/status` | `pulse_teamakte_pages.register.<locals>.goal_status` | pulse_teamakte_pages.py:335 | — (öffentlich/keine erkennbare Prüfung) |
| GET | `/team/{user_id}/roles` | `pulse_web.team.team_role_manager` | pulse_web/team.py:514 | can_promote (+ is_admin) |
| POST | `/team/{user_id}/roles` | `pulse_web.team.team_role_manager_save` | pulse_web/team.py:545 | can_promote (+ is_admin) |

### `/team-login` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/team-login` | `pulse_web.dashboard.team_login_page` | pulse_web/dashboard.py:26 | — (öffentlich/keine erkennbare Prüfung) |

### `/team-status` (2)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/team-status` | `pulse_pro.register.<locals>.team_status_page` | pulse_pro.py:2010 | can_view_dashboard (+ can_promote, can_view_team_status, is_admin) |
| POST | `/team-status/set` | `pulse_pro.register.<locals>.set_team_status` | pulse_pro.py:2080 | can_view_dashboard |

### `/team-vorstellung` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/team-vorstellung` | `pulse_public_site.register.<locals>.add_page.<locals>.handler` | pulse_public_site.py:780 | — (öffentlich/keine erkennbare Prüfung) |

### `/ticket` (3)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/ticket/transcript/{filename}` | `pulse_features.register.<locals>.ticket_transcript` | pulse_features.py:73 | ctx (+ can_manage_tickets, is_admin) |
| GET | `/ticket/{ticket_id}` | `pulse_pro.register.<locals>.ticket_detail_v5` | pulse_pro.py:3246 | can_manage_tickets |
| GET | `/ticket/{ticket_id}/summary` | `pulse_pro.register.<locals>.ticket_summary_v5` | pulse_pro.py:3255 | can_manage_tickets |

### `/tickets` (20)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/tickets` | `pulse_pro.register.<locals>.tickets_v5` | pulse_pro.py:2293 | can_view_dashboard (+ can_manage_tickets, can_promote, is_admin) |
| POST | `/tickets/action` | `pulse_web.tickets.pulse_tickets_dashboard_action` | pulse_web/tickets.py:1801 | can_manage_tickets (+ can_manage_tickets, is_admin) |
| POST | `/tickets/automation` | `pulse_web.tickets.pulse_ticket_automation_save` | pulse_web/tickets.py:913 | Admin |
| POST | `/tickets/categories/delete` | `pulse_web.tickets.pulse_ticket_category_delete` | pulse_web/tickets.py:1204 | Admin |
| POST | `/tickets/categories/save` | `pulse_web.tickets.pulse_ticket_category_save` | pulse_web/tickets.py:1026 | Admin |
| GET | `/tickets/panels` | `pulse_web.tickets.pulse_tickets_dashboard` | pulse_web/tickets.py:246 | can_manage_tickets (+ is_admin) |
| POST | `/tickets/panels/delete` | `pulse_web.tickets.pulse_ticket_panel_delete` | pulse_web/tickets.py:994 | Admin |
| POST | `/tickets/panels/duplicate` | `pulse_web.tickets.pulse_ticket_panel_duplicate` | pulse_web/tickets.py:895 | Admin |
| GET | `/tickets/panels/media/{filename}` | `pulse_web.tickets.pulse_ticket_panel_media` | pulse_web/tickets.py:883 | Admin |
| POST | `/tickets/panels/publish` | `pulse_web.tickets.pulse_ticket_panel_publish` | pulse_web/tickets.py:971 | Admin |
| POST | `/tickets/panels/save` | `pulse_web.tickets.pulse_ticket_panel_save` | pulse_web/tickets.py:714 | Admin |
| POST | `/tickets/rate` | `pulse_features.register.<locals>.ticket_rate` | pulse_features.py:87 | ctx |
| POST | `/tickets/settings` | `pulse_web.tickets.pulse_tickets_dashboard_settings` | pulse_web/tickets.py:620 | Admin |
| POST | `/tickets/setup-panels` | `pulse_web.tickets.pulse_ticket_panels_setup` | pulse_web/tickets.py:691 | Admin |
| GET | `/tickets/tools` | `pulse_web.tickets.pulse_ticket_tools` | pulse_web/tickets.py:1723 | can_manage_tickets (+ is_admin) |
| POST | `/tickets/tools/replies` | `pulse_web.tickets.pulse_ticket_tools_replies` | pulse_web/tickets.py:1771 | Admin |
| POST | `/tickets/tools/unblock` | `pulse_web.tickets.pulse_ticket_tools_unblock` | pulse_web/tickets.py:1790 | can_manage_tickets |
| GET | `/tickets/transcript/{ticket_id}` | `pulse_web.tickets.pulse_ticket_transcript` | pulse_web/tickets.py:1240 | can_manage_tickets |
| GET | `/tickets/transcript/{ticket_id}/view` | `pulse_web.tickets.pulse_ticket_transcript_view` | pulse_web/tickets.py:1265 | can_manage_tickets |
| POST | `/tickets/update` | `pulse_pro.register.<locals>.ticket_update_v5` | pulse_pro.py:2303 | can_view_dashboard (+ can_manage_tickets, can_promote, is_admin) |

### `/training` (12)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/training` | `pulse_features.register.<locals>.training_page` | pulse_features.py:203 | ctx (+ can_manage_training, can_promote, is_admin) |
| GET | `/training/certificate/{code}` | `pulse_features.register.<locals>.training_certificate_verify` | pulse_features.py:672 | ctx (+ can_manage_training, can_promote, is_admin) |
| POST | `/training/create` | `pulse_features.register.<locals>.training_create` | pulse_features.py:566 | angemeldet (+ can_manage_training, can_promote, is_admin) |
| GET | `/training/matrix` | `pulse_features.register.<locals>.training_competency_matrix` | pulse_features.py:384 | angemeldet (+ can_manage_training, can_promote, is_admin) |
| POST | `/training/paths/save` | `pulse_features.register.<locals>.training_path_save` | pulse_features.py:532 | angemeldet (+ can_manage_training, can_promote, is_admin) |
| GET | `/training/{training_id}` | `pulse_features.register.<locals>.training_detail` | pulse_features.py:582 | ctx (+ can_manage_training, can_promote, is_admin) |
| POST | `/training/{training_id}/feedback` | `pulse_features.register.<locals>.training_feedback_submit` | pulse_features.py:725 | ctx |
| POST | `/training/{training_id}/refresher` | `pulse_features.register.<locals>.training_refresher_save` | pulse_features.py:658 | ctx (+ can_manage_training, can_promote, is_admin) |
| POST | `/training/{training_id}/submit` | `pulse_features.register.<locals>.training_submit` | pulse_features.py:692 | ctx |
| GET | `/training/{training_id}/verwaltung` | `pulse_quiz_admin.register.<locals>.quiz_admin_page` | pulse_quiz_admin.py:167 | — (öffentlich/keine erkennbare Prüfung) |
| POST | `/training/{training_id}/verwaltung/einstellungen` | `pulse_quiz_admin.register.<locals>.quiz_admin_settings` | pulse_quiz_admin.py:205 | — (öffentlich/keine erkennbare Prüfung) |
| POST | `/training/{training_id}/verwaltung/frage` | `pulse_quiz_admin.register.<locals>.quiz_admin_question` | pulse_quiz_admin.py:218 | — (öffentlich/keine erkennbare Prüfung) |

### `/ultimate` (116)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/ultimate` | `pulse_web.dashboard.ultimate_dashboard` | pulse_web/dashboard.py:786 | can_view_dashboard (+ can_promote, can_warn, is_admin) |
| GET | `/ultimate` | `pulse_ultimate.register.<locals>.ultimate_home` | pulse_ultimate.py:1077 | can_view_dashboard |
| GET | `/ultimate/achievements` | `pulse_next.register.<locals>.achievements` | pulse_next.py:1070 | can_view_dashboard |
| GET | `/ultimate/analytics` | `pulse_ultimate.register.<locals>.ultimate_analytics` | pulse_ultimate.py:2730 | can_view_analytics |
| GET | `/ultimate/announcements` | `pulse_next.register.<locals>.announcements` | pulse_next.py:957 | can_view_dashboard |
| POST | `/ultimate/announcements/create` | `pulse_next.register.<locals>.announcements_create` | pulse_next.py:980 | can_view_dashboard |
| POST | `/ultimate/announcements/{announcement_id}/read` | `pulse_next.register.<locals>.announcement_read` | pulse_next.py:994 | can_view_dashboard |
| GET | `/ultimate/api/case/{case_id}` | `pulse_ultimate.register.<locals>.ultimate_api_case` | pulse_ultimate.py:2682 | can_view_dashboard |
| GET | `/ultimate/api/copilot` | `pulse_intelligence.register.<locals>.pulse_copilot_api` | pulse_intelligence.py:636 | can_view_dashboard |
| POST | `/ultimate/api/feedback/{feedback_id}` | `pulse_ultimate.register.<locals>.ultimate_api_feedback` | pulse_ultimate.py:2693 | can_view_dashboard |
| GET | `/ultimate/api/monitor` | `pulse_ultimate.register.<locals>.ultimate_api_monitor` | pulse_ultimate.py:2912 | can_view_dashboard |
| GET | `/ultimate/api/overview` | `pulse_ultimate.register.<locals>.ultimate_api_overview` | pulse_ultimate.py:2645 | can_view_dashboard |
| GET | `/ultimate/api/system-twin` | `pulse_intelligence.register.<locals>.pulse_system_twin_api` | pulse_intelligence.py:693 | can_view_dashboard |
| GET | `/ultimate/api/team` | `pulse_ultimate.register.<locals>.ultimate_api_team` | pulse_ultimate.py:2665 | can_view_dashboard |
| GET | `/ultimate/api/v2/overview` | `pulse_ultimate.register.<locals>.ultimate_api_v2_overview` | pulse_ultimate.py:2860 | API-Schlüssel (X-Pulse-API-Key) |
| GET | `/ultimate/api/v2/team` | `pulse_ultimate.register.<locals>.ultimate_api_v2_team` | pulse_ultimate.py:2882 | API-Schlüssel (X-Pulse-API-Key) |
| GET | `/ultimate/approvals` | `pulse_next.register.<locals>.approvals` | pulse_next.py:869 | can_view_dashboard, require_perm |
| POST | `/ultimate/approvals/{approval_id}/decide` | `pulse_next.register.<locals>.approval_decide` | pulse_next.py:900 | can_view_dashboard |
| GET | `/ultimate/automations` | `pulse_ultimate.register.<locals>.ultimate_automations` | pulse_ultimate.py:2163 | can_view_dashboard (+ can_promote) |
| POST | `/ultimate/automations/approvals/{request_id}/cancel` | `pulse_workflows.register.<locals>.ultimate_automation_approval_cancel` | pulse_workflows.py:895 | can_view_dashboard |
| POST | `/ultimate/automations/approvals/{request_id}/decide` | `pulse_workflows.register.<locals>.ultimate_automation_approval_decide` | pulse_workflows.py:862 | can_view_dashboard (+ can_promote) |
| POST | `/ultimate/automations/create` | `pulse_ultimate.register.<locals>.ultimate_automation_create` | pulse_ultimate.py:2412 | can_view_dashboard |
| POST | `/ultimate/automations/retries/{retry_id}/cancel` | `pulse_workflows.register.<locals>.ultimate_automation_retry_cancel` | pulse_workflows.py:933 | can_view_dashboard |
| POST | `/ultimate/automations/retries/{retry_id}/resume` | `pulse_workflows.register.<locals>.ultimate_automation_retry_resume` | pulse_workflows.py:919 | can_view_dashboard |
| POST | `/ultimate/automations/save` | `pulse_ultimate.register.<locals>.ultimate_automation_save` | pulse_ultimate.py:2388 | can_view_dashboard |
| GET | `/ultimate/automations/vorlagen` | `pulse_ultimate.register.<locals>.ultimate_automation_templates` | pulse_ultimate.py:2353 | can_view_dashboard |
| POST | `/ultimate/automations/vorlagen/{template_id}` | `pulse_ultimate.register.<locals>.ultimate_automation_template_use` | pulse_ultimate.py:2372 | can_view_dashboard |
| POST | `/ultimate/automations/{automation_id}/delete` | `pulse_ultimate.register.<locals>.ultimate_automation_delete` | pulse_ultimate.py:2466 | can_view_dashboard |
| POST | `/ultimate/automations/{automation_id}/run` | `pulse_ultimate.register.<locals>.ultimate_automation_run` | pulse_ultimate.py:2504 | — (öffentlich/keine erkennbare Prüfung) |
| POST | `/ultimate/automations/{automation_id}/test` | `pulse_ultimate.register.<locals>.ultimate_automation_test` | pulse_ultimate.py:2500 | — (öffentlich/keine erkennbare Prüfung) |
| POST | `/ultimate/automations/{automation_id}/toggle` | `pulse_ultimate.register.<locals>.ultimate_automation_toggle` | pulse_ultimate.py:2443 | can_view_dashboard |
| GET | `/ultimate/automations/{automation_id}/versions` | `pulse_workflows.register.<locals>.ultimate_automation_versions` | pulse_workflows.py:792 | can_view_dashboard |
| POST | `/ultimate/automations/{automation_id}/versions/{version}/restore` | `pulse_workflows.register.<locals>.ultimate_automation_version_restore` | pulse_workflows.py:834 | can_view_dashboard |
| GET | `/ultimate/awards` | `pulse_ultimate.register.<locals>.ultimate_awards` | pulse_ultimate.py:1981 | can_view_dashboard |
| POST | `/ultimate/awards/create` | `pulse_ultimate.register.<locals>.ultimate_award_create` | pulse_ultimate.py:1995 | can_view_dashboard |
| GET | `/ultimate/backup` | `pulse_next.register.<locals>.backup` | pulse_next.py:1204 | can_view_dashboard |
| POST | `/ultimate/backup/create` | `pulse_next.register.<locals>.backup_create` | pulse_next.py:1212 | can_view_dashboard |
| GET | `/ultimate/backup/{name}` | `pulse_next.register.<locals>.backup_download` | pulse_next.py:1218 | can_view_dashboard (+ is_admin) |
| POST | `/ultimate/backup/{name}/restore` | `pulse_next.register.<locals>.backup_restore` | pulse_next.py:1227 | can_view_dashboard (+ is_admin) |
| GET | `/ultimate/calendar` | `pulse_ultimate.register.<locals>.ultimate_calendar` | pulse_ultimate.py:1680 | can_view_dashboard (+ can_promote, is_admin) |
| POST | `/ultimate/calendar/create` | `pulse_ultimate.register.<locals>.ultimate_calendar_create` | pulse_ultimate.py:1771 | can_view_dashboard |
| GET | `/ultimate/calendar/{event_id}/delete` | `pulse_ultimate.register.<locals>.ultimate_calendar_delete_link` | pulse_ultimate.py:1795 | can_view_dashboard |
| POST | `/ultimate/calendar/{event_id}/delete` | `pulse_ultimate.register.<locals>.ultimate_calendar_delete` | pulse_ultimate.py:1801 | can_view_dashboard |
| POST | `/ultimate/calendar/{event_id}/rsvp` | `pulse_ultimate.register.<locals>.ultimate_calendar_rsvp` | pulse_ultimate.py:1755 | can_view_dashboard |
| GET | `/ultimate/cases` | `pulse_ultimate.register.<locals>.ultimate_cases` | pulse_ultimate.py:1308 | can_view_dashboard |
| POST | `/ultimate/cases/create` | `pulse_ultimate.register.<locals>.ultimate_case_create` | pulse_ultimate.py:1338 | can_view_dashboard |
| GET | `/ultimate/cases/{case_id}` | `pulse_ultimate.register.<locals>.ultimate_case_detail` | pulse_ultimate.py:1390 | can_view_dashboard |
| GET | `/ultimate/cases/{case_id}/abschluss` | `pulse_case_report.register.<locals>.closing_form` | pulse_case_report.py:95 | can_view_dashboard |
| POST | `/ultimate/cases/{case_id}/abschluss` | `pulse_case_report.register.<locals>.closing_save` | pulse_case_report.py:112 | can_view_dashboard |
| GET | `/ultimate/cases/{case_id}/attachments/{attachment_id}` | `pulse_ultimate.register.<locals>.ultimate_attachment_download` | pulse_ultimate.py:2610 | can_view_dashboard |
| POST | `/ultimate/cases/{case_id}/notiz` | `pulse_case_report.register.<locals>.internal_note` | pulse_case_report.py:126 | can_view_dashboard |
| POST | `/ultimate/cases/{case_id}/players/add` | `pulse_ultimate.register.<locals>.ultimate_case_player_add` | pulse_ultimate.py:1556 | can_view_dashboard |
| POST | `/ultimate/cases/{case_id}/realm` | `pulse_ultimate.register.<locals>.ultimate_case_realm` | pulse_ultimate.py:1372 | can_view_dashboard |
| POST | `/ultimate/cases/{case_id}/update` | `pulse_ultimate.register.<locals>.ultimate_case_update` | pulse_ultimate.py:1436 | can_view_dashboard |
| GET | `/ultimate/cases/{case_id}/upload` | `pulse_ultimate.register.<locals>.ultimate_upload_page` | pulse_ultimate.py:1458 | can_view_dashboard |
| POST | `/ultimate/cases/{case_id}/upload` | `pulse_ultimate.register.<locals>.ultimate_upload` | pulse_ultimate.py:1465 | can_view_dashboard |
| POST | `/ultimate/certificates/create` | `pulse_ultimate.register.<locals>.ultimate_certificate_create` | pulse_ultimate.py:2780 | can_view_dashboard |
| GET | `/ultimate/copilot` | `pulse_intelligence.register.<locals>.pulse_copilot_page` | pulse_intelligence.py:625 | can_view_dashboard |
| POST | `/ultimate/copilot` | `pulse_intelligence.register.<locals>.pulse_copilot_ask` | pulse_intelligence.py:630 | can_view_dashboard |
| GET | `/ultimate/departments` | `pulse_ultimate.register.<locals>.ultimate_departments` | pulse_ultimate.py:1583 | can_view_dashboard |
| POST | `/ultimate/departments/create` | `pulse_ultimate.register.<locals>.ultimate_department_create` | pulse_ultimate.py:1603 | can_view_dashboard |
| GET | `/ultimate/departments/{department_id}` | `pulse_ultimate.register.<locals>.ultimate_department_detail` | pulse_ultimate.py:1624 | can_view_dashboard |
| POST | `/ultimate/departments/{department_id}/add` | `pulse_ultimate.register.<locals>.ultimate_department_add` | pulse_ultimate.py:1646 | can_view_dashboard |
| POST | `/ultimate/departments/{department_id}/remove` | `pulse_ultimate.register.<locals>.ultimate_department_remove` | pulse_ultimate.py:1665 | can_view_dashboard |
| GET | `/ultimate/export.json` | `pulse_next.register.<locals>.export_json` | pulse_next.py:1388 | can_view_analytics |
| GET | `/ultimate/feedback` | `pulse_ultimate.register.<locals>.ultimate_feedback` | pulse_ultimate.py:1871 | can_view_dashboard (+ can_promote, is_admin) |
| POST | `/ultimate/feedback/create` | `pulse_ultimate.register.<locals>.ultimate_feedback_create` | pulse_ultimate.py:1905 | can_view_dashboard |
| GET | `/ultimate/goals` | `pulse_next.register.<locals>.goals` | pulse_next.py:1034 | can_view_dashboard |
| POST | `/ultimate/goals/create` | `pulse_next.register.<locals>.goals_create` | pulse_next.py:1051 | can_view_dashboard |
| POST | `/ultimate/goals/{goal_id}/update` | `pulse_next.register.<locals>.goals_update` | pulse_next.py:1059 | can_view_dashboard |
| GET | `/ultimate/handovers` | `pulse_ultimate.register.<locals>.ultimate_handovers` | pulse_ultimate.py:1923 | can_view_dashboard |
| POST | `/ultimate/handovers/create` | `pulse_ultimate.register.<locals>.ultimate_handover_create` | pulse_ultimate.py:1944 | can_view_dashboard |
| POST | `/ultimate/handovers/{handover_id}/close` | `pulse_ultimate.register.<locals>.ultimate_handover_close` | pulse_ultimate.py:1965 | can_view_dashboard (+ can_promote, is_admin) |
| GET | `/ultimate/health-detail` | `pulse_next.register.<locals>.health_detail` | pulse_next.py:1277 | can_view_dashboard |
| GET | `/ultimate/ideas` | `pulse_next.register.<locals>.ideas` | pulse_next.py:1003 | can_view_dashboard |
| POST | `/ultimate/ideas/create` | `pulse_next.register.<locals>.ideas_create` | pulse_next.py:1018 | can_view_dashboard |
| POST | `/ultimate/ideas/{idea_id}/vote` | `pulse_next.register.<locals>.idea_vote` | pulse_next.py:1026 | can_view_dashboard |
| GET | `/ultimate/metrics` | `pulse_ultimate.register.<locals>.ultimate_metrics` | pulse_ultimate.py:2901 | can_view_analytics |
| GET | `/ultimate/onboarding` | `pulse_next.register.<locals>.onboarding` | pulse_next.py:1144 | can_view_dashboard |
| GET | `/ultimate/orgchart` | `pulse_next.register.<locals>.orgchart` | pulse_next.py:1159 | can_view_dashboard |
| GET | `/ultimate/permissions` | `pulse_next.register.<locals>.permissions` | pulse_next.py:1103 | can_view_dashboard |
| POST | `/ultimate/permissions/save` | `pulse_next.register.<locals>.permissions_save` | pulse_next.py:1115 | can_view_dashboard (+ is_admin) |
| GET | `/ultimate/person/{user_id}` | `pulse_next.register.<locals>.person` | pulse_next.py:803 | can_view_dashboard (+ can_promote) |
| GET | `/ultimate/person/{user_id}/export` | `pulse_next.register.<locals>.person_export` | pulse_next.py:857 | can_view_dashboard (+ can_promote, is_admin) |
| GET | `/ultimate/polls` | `pulse_ultimate.register.<locals>.ultimate_polls` | pulse_ultimate.py:1810 | can_view_dashboard (+ can_promote, is_admin) |
| POST | `/ultimate/polls/create` | `pulse_ultimate.register.<locals>.ultimate_poll_create` | pulse_ultimate.py:1839 | can_view_dashboard |
| POST | `/ultimate/polls/{poll_id}/vote` | `pulse_ultimate.register.<locals>.ultimate_poll_vote` | pulse_ultimate.py:1859 | can_view_dashboard |
| POST | `/ultimate/promotions/create` | `pulse_next.register.<locals>.promotions_create` | pulse_next.py:883 | can_view_dashboard |
| GET | `/ultimate/reports` | `pulse_next.register.<locals>.reports` | pulse_next.py:1181 | can_view_analytics |
| GET | `/ultimate/reports.csv` | `pulse_next.register.<locals>.reports_csv` | pulse_next.py:1196 | can_view_analytics |
| GET | `/ultimate/roblox` | `pulse_ultimate.register.<locals>.ultimate_roblox` | pulse_ultimate.py:1509 | can_view_dashboard |
| POST | `/ultimate/roblox/save` | `pulse_ultimate.register.<locals>.ultimate_roblox_save` | pulse_ultimate.py:1532 | can_view_dashboard |
| GET | `/ultimate/sandbox` | `pulse_intelligence.register.<locals>.pulse_sandbox_page` | pulse_intelligence.py:785 | can_view_dashboard |
| POST | `/ultimate/sandbox` | `pulse_intelligence.register.<locals>.pulse_sandbox_validate` | pulse_intelligence.py:790 | can_view_dashboard |
| GET | `/ultimate/scoreboard` | `pulse_next.register.<locals>.scoreboard` | pulse_next.py:1301 | can_view_analytics |
| GET | `/ultimate/search` | `pulse_next.register.<locals>.global_search` | pulse_next.py:934 | can_view_dashboard |
| GET | `/ultimate/security` | `pulse_next.register.<locals>.security_center` | pulse_next.py:1403 | can_view_dashboard |
| GET | `/ultimate/shifts` | `pulse_next.register.<locals>.shifts` | pulse_next.py:1265 | can_view_dashboard |
| GET | `/ultimate/simulator` | `pulse_intelligence.register.<locals>.pulse_simulator_page` | pulse_intelligence.py:733 | can_view_dashboard |
| POST | `/ultimate/simulator/run` | `pulse_intelligence.register.<locals>.pulse_simulator_run` | pulse_intelligence.py:738 | can_view_dashboard |
| GET | `/ultimate/status` | `pulse_ultimate.register.<locals>.ultimate_public_status` | pulse_ultimate.py:2803 | — (öffentlich/keine erkennbare Prüfung) |
| GET | `/ultimate/support` | `pulse_next.register.<locals>.support` | pulse_next.py:1313 | can_view_analytics |
| GET | `/ultimate/system` | `pulse_ultimate.register.<locals>.ultimate_system` | pulse_ultimate.py:2512 | can_view_dashboard |
| GET | `/ultimate/system-twin` | `pulse_intelligence.register.<locals>.pulse_system_twin` | pulse_intelligence.py:642 | can_view_dashboard |
| POST | `/ultimate/system/api-key` | `pulse_ultimate.register.<locals>.ultimate_api_key_create` | pulse_ultimate.py:2827 | can_view_dashboard (+ is_admin) |
| POST | `/ultimate/system/api-key/{key_id}/revoke` | `pulse_ultimate.register.<locals>.ultimate_api_key_revoke` | pulse_ultimate.py:2602 | can_view_dashboard |
| GET | `/ultimate/system/api-keys` | `pulse_ultimate.register.<locals>.ultimate_api_keys` | pulse_ultimate.py:2588 | can_view_dashboard |
| POST | `/ultimate/system/branding` | `pulse_ultimate.register.<locals>.ultimate_branding` | pulse_ultimate.py:2633 | can_view_dashboard |
| GET | `/ultimate/tasks` | `pulse_next.register.<locals>.taskboard` | pulse_next.py:1343 | can_view_dashboard |
| GET | `/ultimate/team` | `pulse_ultimate.register.<locals>.ultimate_team` | pulse_ultimate.py:1175 | can_view_dashboard |
| GET | `/ultimate/team-trends` | `pulse_next.register.<locals>.team_trends` | pulse_next.py:1372 | can_view_analytics |
| POST | `/ultimate/team/status` | `pulse_ultimate.register.<locals>.ultimate_team_status` | pulse_ultimate.py:2760 | can_view_dashboard |
| GET | `/ultimate/team/{user_id}` | `pulse_ultimate.register.<locals>.ultimate_member` | pulse_ultimate.py:1228 | can_view_dashboard (+ can_promote, is_admin) |
| POST | `/ultimate/team/{user_id}/update` | `pulse_ultimate.register.<locals>.ultimate_member_update` | pulse_ultimate.py:1284 | can_view_dashboard |
| GET | `/ultimate/workflows` | `pulse_next.register.<locals>.workflows` | pulse_next.py:1237 | can_view_dashboard |
| POST | `/ultimate/workflows/create` | `pulse_next.register.<locals>.workflows_create` | pulse_next.py:1250 | can_view_dashboard |

### `/voice-support` (2)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/voice-support` | `pulse_pro.register.<locals>.voice_support_v5` | pulse_pro.py:2314 | can_view_dashboard (+ can_manage_tickets, can_promote, is_admin) |
| POST | `/voice-support/save` | `pulse_pro.register.<locals>.voice_support_save` | pulse_pro.py:2459 | Admin |

### `/vorlagen` (7)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/vorlagen` | `pulse_templates_pages.register.<locals>.templates_page` | pulse_templates_pages.py:51 | can_view_dashboard (+ is_admin) |
| POST | `/vorlagen/import` | `pulse_templates_pages.register.<locals>.template_import` | pulse_templates_pages.py:167 | Admin |
| POST | `/vorlagen/neu` | `pulse_templates_pages.register.<locals>.template_create` | pulse_templates_pages.py:109 | can_view_dashboard |
| GET | `/vorlagen/{template_id}` | `pulse_templates_pages.register.<locals>.template_detail` | pulse_templates_pages.py:82 | can_view_dashboard |
| POST | `/vorlagen/{template_id}/archivieren` | `pulse_templates_pages.register.<locals>.toggle.<locals>.handler` | pulse_templates_pages.py:148 | can_view_dashboard |
| POST | `/vorlagen/{template_id}/speichern` | `pulse_templates_pages.register.<locals>.template_save` | pulse_templates_pages.py:125 | can_view_dashboard |
| POST | `/vorlagen/{template_id}/wiederherstellen` | `pulse_templates_pages.register.<locals>.toggle.<locals>.handler` | pulse_templates_pages.py:148 | can_view_dashboard |

### `/warns` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/warns` | `pulse_pro.register.<locals>.warns_v5` | pulse_pro.py:3192 | angemeldet (+ can_manage_team_warnings) |

### `/warns.csv` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/warns.csv` | `pulse_pro.register.<locals>.warns_csv` | pulse_pro.py:3231 | angemeldet (+ can_manage_team_warnings) |

### `/wiki` (4)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/wiki` | `pulse_features.register.<locals>.wiki_page` | pulse_features.py:97 | ctx (+ can_manage_wiki, can_promote, is_admin) |
| POST | `/wiki/save` | `pulse_features.register.<locals>.wiki_save` | pulse_features.py:159 | angemeldet (+ can_manage_wiki, can_promote, is_admin) |
| GET | `/wiki/{page_id}` | `pulse_features.register.<locals>.wiki_detail` | pulse_features.py:109 | ctx (+ can_manage_wiki, can_promote, is_admin) |
| POST | `/wiki/{page_id}/restore/{version_number}` | `pulse_features.register.<locals>.wiki_restore_version` | pulse_features.py:181 | angemeldet (+ can_manage_wiki, can_promote, is_admin) |

### `/wissen` (4)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/wissen` | `pulse_wiki_pages.register.<locals>.wiki_search_page` | pulse_wiki_pages.py:92 | can_view_dashboard |
| POST | `/wissen/{page_id}/freigabe` | `pulse_wiki_pages.register.<locals>.wiki_review_action` | pulse_wiki_pages.py:122 | angemeldet |
| POST | `/wissen/{page_id}/gelesen` | `pulse_wiki_pages.register.<locals>.wiki_acknowledge` | pulse_wiki_pages.py:113 | can_view_dashboard |
| POST | `/wissen/{page_id}/pflicht` | `pulse_wiki_pages.register.<locals>.wiki_set_required` | pulse_wiki_pages.py:149 | angemeldet |

