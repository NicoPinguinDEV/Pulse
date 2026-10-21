# Routen-Landkarte (effektiv zur Laufzeit)

Automatisch erzeugt von `python tools/route_map.py` aus dem laufenden FastAPI-Objekt (nicht von Hand bearbeiten).
Spalte *Prüfung* ist eine Quelltext-Heuristik (`auth(...)`, `cctx`, `ctx_for`, `admin_ctx`, `perm=`) – Hinweis, kein Beweis.

- **Effektive Routen (Methode × Pfad):** 276
- **Doppelt registriert:** 1 → `GET /ultimate`
- **Im Quelltext registriert, zur Laufzeit verdrängt:** 21
- **Ohne erkennbare Prüfung:** 13 (Login/OAuth/Health/Bewerbungsportal/öffentliche Seiten – einzeln prüfen)

> Achtung `remove_and_add` (`pulse_pro.py`): entfernt **alle Methoden** eines Pfads, bevor es die neue Methode registriert.

## Doppelte Registrierungen

| Methode | Pfad | aktive Handler (Reihenfolge) |
|---|---|---|
| GET | `/ultimate` | `webserver.ultimate_dashboard` (webserver.py:3666) → `pulse_ultimate.register.<locals>.ultimate_home` (pulse_ultimate.py:759) (FastAPI nimmt den **ersten** Treffer) |

## Verdrängte Handler (toter Code)

| Methode | Pfad | verdrängte Funktion | Ort | aktiv stattdessen |
|---|---|---|---|---|
| GET | `/achievements` | `achievements_page` | pulse_features.py:228 | `pulse_pro.register.<locals>.achievements_v5` |
| POST | `/backup/restore` | `restore_backup` | pulse_features.py:368 | `webserver.restore_backup` |
| GET | `/calendar` | `calendar_page` | pulse_features.py:291 | `pulse_pro.register.<locals>.calendar_v5` |
| GET | `/dashboard` | `dashboard_main` | webserver.py:3350 | `pulse_pro.register.<locals>.dashboard_v5` |
| GET | `/health` | `health` | pulse_features.py:311 | `pulse_pro.register.<locals>.api_health` |
| GET | `/manifest.webmanifest` | `manifest` | pulse_features.py:315 | `pulse_pro.register.<locals>.manifest` |
| GET | `/meetings-history` | `meetings_history_page` | pulse_features.py:275 | `pulse_pro.register.<locals>.meetings_history_v5` |
| GET | `/melonly` | `melonly_alias` | webserver.py:3873 | `pulse_pro.register.<locals>.melonly_page` |
| GET | `/member/{user_id}` | `member_detail` | webserver.py:6373 | `pulse_pro.register.<locals>.member_v5` |
| GET | `/pulse-inbox` | `inbox_page` | pulse_features.py:39 | `pulse_pro.register.<locals>.inbox_v5` |
| POST | `/pulse-inbox/read` | `inbox_read` | pulse_features.py:60 | `pulse_pro.register.<locals>.inbox_read_all` |
| GET | `/search` | `search_page` | pulse_features.py:64 | `pulse_pro.register.<locals>.search_v5` |
| GET | `/settings` | `settings_page` | webserver.py:9068 | `pulse_pro.register.<locals>.settings_v5` |
| GET | `/stats` | `stats_page` | pulse_features.py:261 | `pulse_pro.register.<locals>.stats_v5` |
| GET | `/sw.js` | `service_worker` | pulse_features.py:319 | `pulse_pro.register.<locals>.service_worker` |
| GET | `/system` | `system_page` | pulse_features.py:300 | `pulse_pro.register.<locals>.system_v5` |
| GET | `/tasks` | `tasks_page` | pulse_features.py:84 | `pulse_pro.register.<locals>.tasks_v5` |
| POST | `/tasks/update` | `task_update` | pulse_features.py:115 | `pulse_pro.register.<locals>.update_task_v5` |
| GET | `/tickets` | `tickets_page` | pulse_features.py:125 | `pulse_pro.register.<locals>.tickets_v5` |
| GET | `/tickets` | `pulse_tickets_dashboard` | webserver.py:4585 | `pulse_pro.register.<locals>.tickets_v5` |
| POST | `/tickets/update` | `ticket_update` | pulse_features.py:282 | `pulse_pro.register.<locals>.ticket_update_v5` |

## Effektive Routen nach Bereich

### `/` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/` | `webserver.home` | webserver.py:2951 | — (öffentlich/keine erkennbare Prüfung) |

### `/achievements` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/achievements` | `pulse_pro.register.<locals>.achievements_v5` | pulse_pro.py:2059 | can_view_dashboard |

### `/action` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| POST | `/action` | `webserver.handle_action` | webserver.py:9374 | angemeldet (+ can_manage_applications, can_promote, is_admin) |

### `/activity-check` (2)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/activity-check` | `pulse_pro.register.<locals>.activity_check_page` | pulse_pro.py:847 | can_manage_checkins |
| POST | `/activity-check/setup` | `pulse_pro.register.<locals>.activity_check_setup` | pulse_pro.py:973 | can_manage_checkins |

### `/admin` (13)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/admin` | `pulse_admin.register.<locals>.admin_hub` | pulse_admin.py:173 | angemeldet |
| POST | `/admin/commands/sync` | `pulse_admin.register.<locals>.admin_commands_sync` | pulse_admin.py:593 | Admin |
| GET | `/admin/discord` | `pulse_admin.register.<locals>.admin_discord` | pulse_admin.py:434 | Admin |
| GET | `/admin/discord/banner/{filename}` | `pulse_admin.register.<locals>.admin_discord_banner` | pulse_admin.py:716 | Admin |
| POST | `/admin/discord/branding` | `pulse_admin.register.<locals>.admin_discord_branding` | pulse_admin.py:509 | Admin |
| POST | `/admin/discord/logs` | `pulse_admin.register.<locals>.admin_discord_logs` | pulse_admin.py:608 | Admin |
| POST | `/admin/discord/test` | `pulse_admin.register.<locals>.admin_discord_test` | pulse_admin.py:629 | Admin |
| POST | `/admin/discord/verification` | `pulse_admin.register.<locals>.admin_verification_save` | pulse_admin.py:651 | Admin |
| POST | `/admin/discord/verification/publish` | `pulse_admin.register.<locals>.admin_verification_publish` | pulse_admin.py:697 | Admin |
| POST | `/admin/discord/welcome` | `pulse_admin.register.<locals>.admin_discord_welcome` | pulse_admin.py:457 | Admin |
| GET | `/admin/privacy` | `pulse_admin.register.<locals>.admin_privacy` | pulse_admin.py:528 | Admin |
| POST | `/admin/privacy` | `pulse_admin.register.<locals>.admin_privacy_save` | pulse_admin.py:564 | Admin |
| GET | `/admin/privacy/export` | `pulse_admin.register.<locals>.admin_privacy_export` | pulse_admin.py:579 | Admin |

### `/announcements` (2)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/announcements` | `pulse_pro.register.<locals>.announcements_page` | pulse_pro.py:1158 | angemeldet (+ can_manage_announcements, can_promote, is_admin) |
| POST | `/announcements/create` | `pulse_pro.register.<locals>.create_announcement` | pulse_pro.py:1168 | angemeldet (+ can_manage_announcements, can_promote, is_admin) |

### `/api` (6)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/api/pulse/live` | `pulse_pro.register.<locals>.api_live` | pulse_pro.py:2666 | can_view_dashboard |
| GET | `/api/roblox-search` | `webserver.roblox_search` | webserver.py:2844 | Session (eigene Prüfung) |
| GET | `/api/roblox-user` | `webserver.roblox_user_lookup` | webserver.py:2782 | Session (eigene Prüfung) |
| GET | `/api/team/activity` | `webserver.team_activity_api` | webserver.py:4344 | can_view_dashboard |
| GET | `/api/team/discord-status` | `pulse_pro.register.<locals>.discord_status_api` | pulse_pro.py:1110 | angemeldet (+ can_promote, can_view_team_status, is_admin) |
| GET | `/api/team/warn-roles` | `webserver.warn_roles_api` | webserver.py:4381 | can_warn |

### `/applications` (4)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/applications` | `webserver.applications_page` | webserver.py:7197 | angemeldet (+ can_manage_applications, can_promote, can_view_dashboard, is_admin) |
| GET | `/applications/config` | `webserver.application_modules_config_page` | webserver.py:7459 | angemeldet (+ can_manage_applications, can_promote, is_admin) |
| POST | `/applications/config` | `webserver.save_application_modules_config` | webserver.py:7505 | angemeldet (+ can_manage_applications, can_promote, is_admin) |
| POST | `/applications/review` | `webserver.save_application_review` | webserver.py:7401 | angemeldet (+ can_manage_applications, can_promote, is_admin) |

### `/apply` (4)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/apply` | `webserver.public_apply_page` | webserver.py:6914 | Session (eigene Prüfung) |
| GET | `/apply/callback` | `webserver.apply_callback` | webserver.py:7171 | Session (eigene Prüfung) |
| POST | `/apply/keepalive` | `webserver.apply_keepalive` | webserver.py:7183 | Session (eigene Prüfung) |
| GET | `/apply/login` | `webserver.apply_login` | webserver.py:7157 | — (öffentlich/keine erkennbare Prüfung) |

### `/approvals` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/approvals` | `pulse_pro.register.<locals>.approvals_page` | pulse_pro.py:1235 | can_view_dashboard (+ can_manage_applications, can_promote, is_admin) |

### `/backup` (10)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| POST | `/backup/create` | `webserver.create_backup` | webserver.py:8645 | Admin |
| POST | `/backup/delete` | `webserver.delete_backup` | webserver.py:8844 | Admin |
| GET | `/backup/download/{filename}` | `webserver.download_backup` | webserver.py:8708 | Admin |
| POST | `/backup/full/create` | `webserver.full_backup_create` | webserver.py:8963 | Admin |
| POST | `/backup/full/delete` | `webserver.full_backup_delete` | webserver.py:9054 | Admin |
| GET | `/backup/full/download/{filename}` | `webserver.full_backup_download` | webserver.py:8976 | Admin |
| GET | `/backup/full/preview/{filename}` | `webserver.full_backup_preview` | webserver.py:8985 | Admin |
| POST | `/backup/full/restore` | `webserver.full_backup_restore` | webserver.py:9027 | Admin |
| POST | `/backup/restore` | `webserver.restore_backup` | webserver.py:8730 | Admin |
| POST | `/backup/upload` | `webserver.upload_backup` | webserver.py:8657 | Admin |

### `/backups` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/backups` | `webserver.backups_page` | webserver.py:8570 | Admin |

### `/calendar` (2)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/calendar` | `pulse_pro.register.<locals>.calendar_v5` | pulse_pro.py:1600 | can_view_dashboard |
| POST | `/calendar/create` | `pulse_pro.register.<locals>.create_calendar_event_v5` | pulse_pro.py:1767 | can_view_dashboard |

### `/callback` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/callback` | `webserver.callback` | webserver.py:3013 | Session (eigene Prüfung) |

### `/cockpit` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/cockpit` | `pulse_cockpit.register.<locals>.leadership_cockpit` | pulse_cockpit.py:43 | angemeldet (+ can_promote, is_admin) |

### `/dashboard` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/dashboard` | `pulse_pro.register.<locals>.dashboard_v5` | pulse_pro.py:791 | can_view_dashboard |

### `/export` (2)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/export/logs` | `webserver.export_logs` | webserver.py:4273 | can_view_analytics |
| GET | `/export/team.csv` | `pulse_pro.register.<locals>.team_export` | pulse_pro.py:2652 | can_view_analytics |

### `/favicon.ico` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/favicon.ico` | `webserver.favicon` | webserver.py:241 | — (öffentlich/keine erkennbare Prüfung) |

### `/handover` (2)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/handover` | `pulse_pro.register.<locals>.handover_page` | pulse_pro.py:1131 | can_view_dashboard (+ can_manage_handover, can_promote, can_view_dashboard, is_admin) |
| POST | `/handover/create` | `pulse_pro.register.<locals>.create_handover` | pulse_pro.py:1145 | angemeldet (+ can_manage_handover, can_view_dashboard) |

### `/health` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/health` | `pulse_pro.register.<locals>.api_health` | pulse_pro.py:2673 | — (öffentlich/keine erkennbare Prüfung) |

### `/healthz` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/healthz` | `webserver.healthz` | webserver.py:2902 | — (öffentlich/keine erkennbare Prüfung) |

### `/loa` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/loa` | `webserver.loa_page` | webserver.py:6670 | can_view_dashboard |

### `/log` (3)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| POST | `/log/create` | `webserver.create_log` | webserver.py:4131 | can_view_dashboard (+ can_add_notes, can_moderate_bans, can_moderate_bolo, can_moderate_kicks) |
| POST | `/log/delete` | `webserver.delete_log` | webserver.py:4226 | can_view_dashboard |
| POST | `/log/edit` | `webserver.edit_log` | webserver.py:4177 | can_view_dashboard |

### `/login` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/login` | `webserver.login` | webserver.py:2984 | — (öffentlich/keine erkennbare Prüfung) |

### `/logout` (2)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/logout` | `webserver.logout_confirm` | webserver.py:2996 | — (öffentlich/keine erkennbare Prüfung) |
| POST | `/logout` | `webserver.logout` | webserver.py:3006 | — (öffentlich/keine erkennbare Prüfung) |

### `/manifest.webmanifest` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/manifest.webmanifest` | `pulse_pro.register.<locals>.manifest` | pulse_pro.py:2681 | — (öffentlich/keine erkennbare Prüfung) |

### `/me` (2)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/me` | `pulse_home.register.<locals>.my_pulse` | pulse_home.py:59 | can_view_dashboard (+ can_manage_applications, can_manage_tickets, can_promote, is_admin) |
| POST | `/me/start-page` | `pulse_home.register.<locals>.my_start_page` | pulse_home.py:279 | can_view_dashboard |

### `/meetings` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/meetings` | `webserver.meetings_page` | webserver.py:6534 | can_view_dashboard |

### `/meetings-history` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/meetings-history` | `pulse_pro.register.<locals>.meetings_history_v5` | pulse_pro.py:2053 | can_view_dashboard |

### `/melonly` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/melonly` | `pulse_pro.register.<locals>.melonly_page` | pulse_pro.py:534 | can_view_dashboard (+ is_admin) |

### `/member` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/member/{user_id}` | `pulse_pro.register.<locals>.member_v5` | pulse_pro.py:1845 | can_view_dashboard (+ can_add_notes, can_promote, can_warn, is_admin) |

### `/promotions` (3)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/promotions` | `pulse_pro.register.<locals>.promotions_page` | pulse_pro.py:1179 | angemeldet (+ can_manage_promotions, can_promote, is_admin) |
| POST | `/promotions/create` | `pulse_pro.register.<locals>.create_promotion` | pulse_pro.py:1192 | angemeldet (+ can_manage_promotions, can_promote, is_admin) |
| POST | `/promotions/decide` | `pulse_pro.register.<locals>.decide_promotion` | pulse_pro.py:1208 | angemeldet (+ can_manage_promotions, can_promote, is_admin) |

### `/pulse-inbox` (3)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/pulse-inbox` | `pulse_pro.register.<locals>.inbox_v5` | pulse_pro.py:831 | can_view_dashboard |
| POST | `/pulse-inbox/read` | `pulse_pro.register.<locals>.inbox_read_all` | pulse_pro.py:840 | can_view_dashboard |
| POST | `/pulse-inbox/read/{nid}` | `pulse_pro.register.<locals>.inbox_read_one` | pulse_pro.py:843 | can_view_dashboard |

### `/robots.txt` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/robots.txt` | `pulse_pro.register.<locals>.robots_txt` | pulse_pro.py:2719 | — (öffentlich/keine erkennbare Prüfung) |

### `/search` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/search` | `pulse_pro.register.<locals>.search_v5` | pulse_pro.py:1823 | can_view_dashboard |

### `/settings` (14)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/settings` | `pulse_pro.register.<locals>.settings_v5` | pulse_pro.py:2084 | Admin |
| POST | `/settings/dashboard-profile/delete` | `pulse_pro.register.<locals>.settings_dashboard_profile_delete` | pulse_pro.py:2381 | Admin |
| POST | `/settings/dashboard-profile/save` | `pulse_pro.register.<locals>.settings_dashboard_profile_save` | pulse_pro.py:2326 | Admin |
| POST | `/settings/nickname-prefix/default` | `pulse_pro.register.<locals>.settings_nickname_prefix_default` | pulse_pro.py:2431 | Admin |
| POST | `/settings/nickname-prefix/delete` | `pulse_pro.register.<locals>.settings_nickname_prefix_delete` | pulse_pro.py:2455 | Admin |
| POST | `/settings/nickname-prefix/save` | `pulse_pro.register.<locals>.settings_nickname_prefix_save` | pulse_pro.py:2398 | Admin |
| POST | `/settings/nickname-prefix/sync` | `pulse_pro.register.<locals>.settings_nickname_prefix_sync` | pulse_pro.py:2479 | Admin |
| POST | `/settings/pro-clear-all` | `pulse_pro.register.<locals>.settings_clear_all_permissions` | pulse_pro.py:2535 | Admin |
| POST | `/settings/pro-goal` | `pulse_pro.register.<locals>.settings_goal` | pulse_pro.py:2566 | Admin |
| POST | `/settings/pro-presets` | `pulse_pro.register.<locals>.settings_profiles` | pulse_pro.py:2528 | Admin |
| POST | `/settings/pro-roles` | `pulse_pro.register.<locals>.settings_roles` | pulse_pro.py:2502 | Admin |
| POST | `/settings/pro-save` | `pulse_pro.register.<locals>.settings_save` | pulse_pro.py:2494 | Admin |
| POST | `/settings/pro-warn-roles` | `pulse_pro.register.<locals>.settings_warn_roles` | pulse_pro.py:2540 | Admin |
| POST | `/settings/pro-warn-sync` | `pulse_pro.register.<locals>.settings_warn_sync` | pulse_pro.py:2559 | Admin |

### `/shift` (2)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| POST | `/shift/action` | `webserver.handle_shift_action` | webserver.py:4037 | can_view_dashboard |
| POST | `/shift/admin-end` | `pulse_pro.register.<locals>.admin_end_shift` | pulse_pro.py:742 | can_view_dashboard (+ can_promote, is_admin) |

### `/social` (7)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/social` | `pulse_social_dashboard.register.<locals>.social_page` | pulse_social_dashboard.py:60 | angemeldet + eigene Prüfung |
| POST | `/social/add` | `pulse_social_dashboard.register.<locals>.social_add_link` | pulse_social_dashboard.py:160 | angemeldet + eigene Prüfung |
| POST | `/social/announce-latest` | `pulse_social_dashboard.register.<locals>.social_announce_latest` | pulse_social_dashboard.py:207 | angemeldet + eigene Prüfung |
| POST | `/social/remove` | `pulse_social_dashboard.register.<locals>.social_remove_web` | pulse_social_dashboard.py:197 | angemeldet + eigene Prüfung |
| POST | `/social/test` | `pulse_social_dashboard.register.<locals>.social_test_web` | pulse_social_dashboard.py:216 | angemeldet + eigene Prüfung |
| POST | `/social/toggle` | `pulse_social_dashboard.register.<locals>.social_toggle_web` | pulse_social_dashboard.py:188 | angemeldet + eigene Prüfung |
| POST | `/social/update` | `pulse_social_dashboard.register.<locals>.social_update` | pulse_social_dashboard.py:176 | angemeldet + eigene Prüfung |

### `/static` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/static/pulse.css` | `webserver.static_pulse_css` | webserver.py:2943 | — (öffentlich/keine erkennbare Prüfung) |

### `/stats` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/stats` | `pulse_pro.register.<locals>.stats_v5` | pulse_pro.py:1591 | can_view_analytics |

### `/suite` (33)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/suite` | `cogs.pulse_suite.register_routes.<locals>.leadership` | cogs/pulse_suite.py:291 | can_view_dashboard |
| GET | `/suite/achievements` | `cogs.pulse_suite.register_routes.<locals>.achievements_page` | cogs/pulse_suite.py:617 | can_view_dashboard |
| GET | `/suite/api/search` | `cogs.pulse_suite.register_routes.<locals>.search_api` | cogs/pulse_suite.py:599 | can_view_dashboard |
| GET | `/suite/applications` | `cogs.pulse_suite.register_routes.<locals>.applications_page` | cogs/pulse_suite.py:407 | can_view_dashboard |
| GET | `/suite/applications/{app_id}` | `cogs.pulse_suite.register_routes.<locals>.application_page` | cogs/pulse_suite.py:412 | can_view_dashboard |
| POST | `/suite/applications/{app_id}/update` | `cogs.pulse_suite.register_routes.<locals>.application_update` | cogs/pulse_suite.py:419 | can_view_dashboard |
| GET | `/suite/audit` | `cogs.pulse_suite.register_routes.<locals>.audit_page` | cogs/pulse_suite.py:553 | can_view_dashboard |
| GET | `/suite/calendar` | `cogs.pulse_suite.register_routes.<locals>.calendar_page` | cogs/pulse_suite.py:388 | can_view_dashboard |
| POST | `/suite/calendar/create` | `cogs.pulse_suite.register_routes.<locals>.calendar_create` | cogs/pulse_suite.py:401 | can_view_dashboard |
| GET | `/suite/cases` | `cogs.pulse_suite.register_routes.<locals>.cases_page` | cogs/pulse_suite.py:603 | can_view_dashboard |
| POST | `/suite/cases/{case_id}/action` | `cogs.pulse_suite.register_routes.<locals>.case_action` | cogs/pulse_suite.py:610 | can_view_dashboard |
| GET | `/suite/interview/create` | `cogs.pulse_suite.register_routes.<locals>.interview_form` | cogs/pulse_suite.py:433 | can_view_dashboard |
| POST | `/suite/interview/create` | `cogs.pulse_suite.register_routes.<locals>.interview_create` | cogs/pulse_suite.py:439 | can_view_dashboard |
| GET | `/suite/interviews` | `cogs.pulse_suite.register_routes.<locals>.interviews_page` | cogs/pulse_suite.py:426 | can_view_dashboard |
| GET | `/suite/member/{member_id}` | `cogs.pulse_suite.register_routes.<locals>.member_page` | cogs/pulse_suite.py:333 | can_view_dashboard |
| POST | `/suite/member/{member_id}/note` | `cogs.pulse_suite.register_routes.<locals>.member_note` | cogs/pulse_suite.py:347 | can_view_dashboard |
| GET | `/suite/my-schedule` | `cogs.pulse_suite.register_routes.<locals>.my_schedule` | cogs/pulse_suite.py:364 | can_view_dashboard |
| GET | `/suite/news` | `cogs.pulse_suite.register_routes.<locals>.news_page` | cogs/pulse_suite.py:545 | can_view_dashboard |
| POST | `/suite/news/create` | `cogs.pulse_suite.register_routes.<locals>.news_create` | cogs/pulse_suite.py:549 | can_view_dashboard |
| GET | `/suite/promotions` | `cogs.pulse_suite.register_routes.<locals>.promotions_page` | cogs/pulse_suite.py:447 | can_view_dashboard |
| POST | `/suite/promotions/create` | `cogs.pulse_suite.register_routes.<locals>.promotions_create` | cogs/pulse_suite.py:454 | can_view_dashboard |
| POST | `/suite/promotions/{pid}/action` | `cogs.pulse_suite.register_routes.<locals>.promotions_action` | cogs/pulse_suite.py:462 | can_view_dashboard |
| GET | `/suite/schedule` | `cogs.pulse_suite.register_routes.<locals>.schedule_page` | cogs/pulse_suite.py:372 | can_view_dashboard |
| POST | `/suite/schedule/create` | `cogs.pulse_suite.register_routes.<locals>.schedule_create` | cogs/pulse_suite.py:380 | can_view_dashboard |
| GET | `/suite/search` | `cogs.pulse_suite.register_routes.<locals>.search_page` | cogs/pulse_suite.py:594 | can_view_dashboard |
| GET | `/suite/task/{task_id}` | `cogs.pulse_suite.register_routes.<locals>.task_detail` | cogs/pulse_suite.py:514 | can_view_dashboard |
| POST | `/suite/task/{task_id}/meta` | `cogs.pulse_suite.register_routes.<locals>.task_meta` | cogs/pulse_suite.py:523 | can_view_dashboard |
| GET | `/suite/tasks` | `cogs.pulse_suite.register_routes.<locals>.tasks_page` | cogs/pulse_suite.py:503 | can_view_dashboard |
| POST | `/suite/tasks/{task_id}/move` | `cogs.pulse_suite.register_routes.<locals>.task_move` | cogs/pulse_suite.py:537 | can_view_dashboard |
| GET | `/suite/team` | `cogs.pulse_suite.register_routes.<locals>.team_page` | cogs/pulse_suite.py:319 | can_view_dashboard |
| GET | `/suite/time` | `cogs.pulse_suite.register_routes.<locals>.time_page` | cogs/pulse_suite.py:356 | can_view_dashboard |
| GET | `/suite/training` | `cogs.pulse_suite.register_routes.<locals>.training_page` | cogs/pulse_suite.py:487 | can_view_dashboard |
| POST | `/suite/training/assign` | `cogs.pulse_suite.register_routes.<locals>.training_assign` | cogs/pulse_suite.py:495 | can_view_dashboard |

### `/sw.js` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/sw.js` | `pulse_pro.register.<locals>.service_worker` | pulse_pro.py:2684 | — (öffentlich/keine erkennbare Prüfung) |

### `/system` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/system` | `pulse_pro.register.<locals>.system_v5` | pulse_pro.py:2569 | Admin |

### `/tasks` (3)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/tasks` | `pulse_pro.register.<locals>.tasks_v5` | pulse_pro.py:1245 | can_view_dashboard (+ can_manage_tasks, can_promote, is_admin) |
| POST | `/tasks/create` | `pulse_features.register.<locals>.task_create` | pulse_features.py:104 | angemeldet (+ can_manage_tasks, can_promote, is_admin) |
| POST | `/tasks/update` | `pulse_pro.register.<locals>.update_task_v5` | pulse_pro.py:1262 | can_view_dashboard (+ can_manage_tasks, can_promote, is_admin) |

### `/team` (4)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/team` | `webserver.team_list_page` | webserver.py:5755 | can_view_dashboard (+ can_promote, is_admin) |
| POST | `/team/voice-support/audio` | `webserver.manage_voice_support_audio` | webserver.py:6138 | can_promote |
| GET | `/team/{user_id}/roles` | `webserver.team_role_manager` | webserver.py:6255 | can_promote (+ is_admin) |
| POST | `/team/{user_id}/roles` | `webserver.team_role_manager_save` | webserver.py:6280 | can_promote (+ is_admin) |

### `/team-status` (2)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/team-status` | `pulse_pro.register.<locals>.team_status_page` | pulse_pro.py:1057 | can_view_dashboard (+ can_promote, can_view_team_status, is_admin) |
| POST | `/team-status/set` | `pulse_pro.register.<locals>.set_team_status` | pulse_pro.py:1127 | can_view_dashboard |

### `/ticket` (2)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/ticket/transcript/{filename}` | `pulse_features.register.<locals>.ticket_transcript` | pulse_features.py:133 | ctx (+ can_manage_tickets, can_promote, is_admin) |
| GET | `/ticket/{ticket_id}` | `pulse_pro.register.<locals>.ticket_detail_v5` | pulse_pro.py:2044 | can_manage_tickets |

### `/tickets` (16)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/tickets` | `pulse_pro.register.<locals>.tickets_v5` | pulse_pro.py:1276 | can_view_dashboard |
| POST | `/tickets/action` | `webserver.pulse_tickets_dashboard_action` | webserver.py:5555 | can_manage_tickets (+ can_manage_tickets, is_admin) |
| POST | `/tickets/automation` | `webserver.pulse_ticket_automation_save` | webserver.py:5250 | Admin |
| POST | `/tickets/categories/delete` | `webserver.pulse_ticket_category_delete` | webserver.py:5503 | Admin |
| POST | `/tickets/categories/save` | `webserver.pulse_ticket_category_save` | webserver.py:5325 | Admin |
| GET | `/tickets/panels` | `webserver.pulse_tickets_dashboard` | webserver.py:4583 | can_manage_tickets (+ is_admin) |
| POST | `/tickets/panels/delete` | `webserver.pulse_ticket_panel_delete` | webserver.py:5293 | Admin |
| POST | `/tickets/panels/duplicate` | `webserver.pulse_ticket_panel_duplicate` | webserver.py:5232 | Admin |
| GET | `/tickets/panels/media/{filename}` | `webserver.pulse_ticket_panel_media` | webserver.py:5220 | Admin |
| POST | `/tickets/panels/publish` | `webserver.pulse_ticket_panel_publish` | webserver.py:5270 | Admin |
| POST | `/tickets/panels/save` | `webserver.pulse_ticket_panel_save` | webserver.py:5051 | Admin |
| POST | `/tickets/rate` | `pulse_features.register.<locals>.ticket_rate` | pulse_features.py:143 | ctx |
| POST | `/tickets/settings` | `webserver.pulse_tickets_dashboard_settings` | webserver.py:4957 | Admin |
| POST | `/tickets/setup-panels` | `webserver.pulse_ticket_panels_setup` | webserver.py:5028 | Admin |
| GET | `/tickets/transcript/{ticket_id}` | `webserver.pulse_ticket_transcript` | webserver.py:5539 | can_manage_tickets |
| POST | `/tickets/update` | `pulse_pro.register.<locals>.ticket_update_v5` | pulse_pro.py:1282 | can_view_dashboard (+ can_manage_tickets, can_promote, is_admin) |

### `/training` (4)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/training` | `pulse_features.register.<locals>.training_page` | pulse_features.py:181 | ctx (+ can_manage_training, can_promote, is_admin) |
| POST | `/training/create` | `pulse_features.register.<locals>.training_create` | pulse_features.py:192 | angemeldet (+ can_manage_training, can_promote, is_admin) |
| GET | `/training/{training_id}` | `pulse_features.register.<locals>.training_detail` | pulse_features.py:204 | ctx |
| POST | `/training/{training_id}/submit` | `pulse_features.register.<locals>.training_submit` | pulse_features.py:212 | ctx |

### `/ultimate` (99)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/ultimate` | `webserver.ultimate_dashboard` | webserver.py:3666 | can_view_dashboard (+ can_promote, can_warn, is_admin) |
| GET | `/ultimate` | `pulse_ultimate.register.<locals>.ultimate_home` | pulse_ultimate.py:759 | can_view_dashboard |
| GET | `/ultimate/achievements` | `pulse_next.register.<locals>.achievements` | pulse_next.py:957 | can_view_dashboard |
| GET | `/ultimate/analytics` | `pulse_ultimate.register.<locals>.ultimate_analytics` | pulse_ultimate.py:1850 | can_view_analytics |
| GET | `/ultimate/announcements` | `pulse_next.register.<locals>.announcements` | pulse_next.py:848 | can_view_dashboard |
| POST | `/ultimate/announcements/create` | `pulse_next.register.<locals>.announcements_create` | pulse_next.py:871 | can_view_dashboard, require_perm |
| POST | `/ultimate/announcements/{announcement_id}/read` | `pulse_next.register.<locals>.announcement_read` | pulse_next.py:882 | can_view_dashboard |
| GET | `/ultimate/api/case/{case_id}` | `pulse_ultimate.register.<locals>.ultimate_api_case` | pulse_ultimate.py:1822 | can_view_dashboard |
| GET | `/ultimate/api/copilot` | `pulse_intelligence.register.<locals>.pulse_copilot_api` | pulse_intelligence.py:593 | can_view_dashboard |
| POST | `/ultimate/api/feedback/{feedback_id}` | `pulse_ultimate.register.<locals>.ultimate_api_feedback` | pulse_ultimate.py:1833 | can_view_dashboard |
| GET | `/ultimate/api/monitor` | `pulse_ultimate.register.<locals>.ultimate_api_monitor` | pulse_ultimate.py:2017 | can_view_dashboard |
| GET | `/ultimate/api/overview` | `pulse_ultimate.register.<locals>.ultimate_api_overview` | pulse_ultimate.py:1786 | can_view_dashboard |
| GET | `/ultimate/api/system-twin` | `pulse_intelligence.register.<locals>.pulse_system_twin_api` | pulse_intelligence.py:650 | can_view_dashboard |
| GET | `/ultimate/api/team` | `pulse_ultimate.register.<locals>.ultimate_api_team` | pulse_ultimate.py:1806 | can_view_dashboard |
| GET | `/ultimate/api/v2/overview` | `pulse_ultimate.register.<locals>.ultimate_api_v2_overview` | pulse_ultimate.py:1963 | API-Schlüssel (X-Pulse-API-Key) |
| GET | `/ultimate/api/v2/team` | `pulse_ultimate.register.<locals>.ultimate_api_v2_team` | pulse_ultimate.py:1987 | API-Schlüssel (X-Pulse-API-Key) |
| GET | `/ultimate/approvals` | `pulse_next.register.<locals>.approvals` | pulse_next.py:768 | can_view_dashboard, require_perm |
| POST | `/ultimate/approvals/{approval_id}/decide` | `pulse_next.register.<locals>.approval_decide` | pulse_next.py:798 | can_view_dashboard, require_perm |
| GET | `/ultimate/automations` | `pulse_ultimate.register.<locals>.ultimate_automations` | pulse_ultimate.py:1555 | can_view_dashboard |
| POST | `/ultimate/automations/create` | `pulse_ultimate.register.<locals>.ultimate_automation_create` | pulse_ultimate.py:1606 | can_view_dashboard |
| POST | `/ultimate/automations/{automation_id}/toggle` | `pulse_ultimate.register.<locals>.ultimate_automation_toggle` | pulse_ultimate.py:1630 | can_view_dashboard |
| GET | `/ultimate/awards` | `pulse_ultimate.register.<locals>.ultimate_awards` | pulse_ultimate.py:1523 | can_view_dashboard |
| POST | `/ultimate/awards/create` | `pulse_ultimate.register.<locals>.ultimate_award_create` | pulse_ultimate.py:1537 | can_view_dashboard |
| GET | `/ultimate/backup` | `pulse_next.register.<locals>.backup` | pulse_next.py:1058 | can_view_dashboard, require_perm |
| POST | `/ultimate/backup/create` | `pulse_next.register.<locals>.backup_create` | pulse_next.py:1066 | can_view_dashboard, require_perm |
| GET | `/ultimate/backup/{name}` | `pulse_next.register.<locals>.backup_download` | pulse_next.py:1072 | can_view_dashboard, require_perm |
| POST | `/ultimate/backup/{name}/restore` | `pulse_next.register.<locals>.backup_restore` | pulse_next.py:1079 | can_view_dashboard, require_perm |
| GET | `/ultimate/calendar` | `pulse_ultimate.register.<locals>.ultimate_calendar` | pulse_ultimate.py:1294 | can_view_dashboard |
| POST | `/ultimate/calendar/create` | `pulse_ultimate.register.<locals>.ultimate_calendar_create` | pulse_ultimate.py:1315 | can_view_dashboard |
| GET | `/ultimate/calendar/{event_id}/delete` | `pulse_ultimate.register.<locals>.ultimate_calendar_delete_link` | pulse_ultimate.py:1338 | can_view_dashboard |
| POST | `/ultimate/calendar/{event_id}/delete` | `pulse_ultimate.register.<locals>.ultimate_calendar_delete` | pulse_ultimate.py:1344 | can_view_dashboard |
| GET | `/ultimate/cases` | `pulse_ultimate.register.<locals>.ultimate_cases` | pulse_ultimate.py:967 | can_view_dashboard |
| POST | `/ultimate/cases/create` | `pulse_ultimate.register.<locals>.ultimate_case_create` | pulse_ultimate.py:990 | can_view_dashboard |
| GET | `/ultimate/cases/{case_id}` | `pulse_ultimate.register.<locals>.ultimate_case_detail` | pulse_ultimate.py:1018 | can_view_dashboard |
| GET | `/ultimate/cases/{case_id}/attachments/{attachment_id}` | `pulse_ultimate.register.<locals>.ultimate_attachment_download` | pulse_ultimate.py:1756 | can_view_dashboard |
| POST | `/ultimate/cases/{case_id}/players/add` | `pulse_ultimate.register.<locals>.ultimate_case_player_add` | pulse_ultimate.py:1171 | can_view_dashboard |
| POST | `/ultimate/cases/{case_id}/update` | `pulse_ultimate.register.<locals>.ultimate_case_update` | pulse_ultimate.py:1063 | can_view_dashboard |
| GET | `/ultimate/cases/{case_id}/upload` | `pulse_ultimate.register.<locals>.ultimate_upload_page` | pulse_ultimate.py:1085 | can_view_dashboard |
| POST | `/ultimate/cases/{case_id}/upload` | `pulse_ultimate.register.<locals>.ultimate_upload` | pulse_ultimate.py:1092 | can_view_dashboard |
| POST | `/ultimate/certificates/create` | `pulse_ultimate.register.<locals>.ultimate_certificate_create` | pulse_ultimate.py:1899 | can_view_dashboard |
| GET | `/ultimate/copilot` | `pulse_intelligence.register.<locals>.pulse_copilot_page` | pulse_intelligence.py:582 | can_view_dashboard |
| POST | `/ultimate/copilot` | `pulse_intelligence.register.<locals>.pulse_copilot_ask` | pulse_intelligence.py:587 | can_view_dashboard |
| GET | `/ultimate/departments` | `pulse_ultimate.register.<locals>.ultimate_departments` | pulse_ultimate.py:1198 | can_view_dashboard |
| POST | `/ultimate/departments/create` | `pulse_ultimate.register.<locals>.ultimate_department_create` | pulse_ultimate.py:1218 | can_view_dashboard |
| GET | `/ultimate/departments/{department_id}` | `pulse_ultimate.register.<locals>.ultimate_department_detail` | pulse_ultimate.py:1239 | can_view_dashboard |
| POST | `/ultimate/departments/{department_id}/add` | `pulse_ultimate.register.<locals>.ultimate_department_add` | pulse_ultimate.py:1261 | can_view_dashboard |
| POST | `/ultimate/departments/{department_id}/remove` | `pulse_ultimate.register.<locals>.ultimate_department_remove` | pulse_ultimate.py:1280 | can_view_dashboard |
| GET | `/ultimate/export.json` | `pulse_next.register.<locals>.export_json` | pulse_next.py:1238 | can_view_analytics |
| GET | `/ultimate/feedback` | `pulse_ultimate.register.<locals>.ultimate_feedback` | pulse_ultimate.py:1434 | can_view_dashboard (+ can_promote, is_admin) |
| POST | `/ultimate/feedback/create` | `pulse_ultimate.register.<locals>.ultimate_feedback_create` | pulse_ultimate.py:1455 | can_view_dashboard |
| GET | `/ultimate/goals` | `pulse_next.register.<locals>.goals` | pulse_next.py:921 | can_view_dashboard |
| POST | `/ultimate/goals/create` | `pulse_next.register.<locals>.goals_create` | pulse_next.py:938 | can_view_dashboard, require_perm |
| POST | `/ultimate/goals/{goal_id}/update` | `pulse_next.register.<locals>.goals_update` | pulse_next.py:946 | can_view_dashboard, require_perm |
| GET | `/ultimate/handovers` | `pulse_ultimate.register.<locals>.ultimate_handovers` | pulse_ultimate.py:1473 | can_view_dashboard |
| POST | `/ultimate/handovers/create` | `pulse_ultimate.register.<locals>.ultimate_handover_create` | pulse_ultimate.py:1494 | can_view_dashboard |
| POST | `/ultimate/handovers/{handover_id}/close` | `pulse_ultimate.register.<locals>.ultimate_handover_close` | pulse_ultimate.py:1515 | can_view_dashboard |
| GET | `/ultimate/health-detail` | `pulse_next.register.<locals>.health_detail` | pulse_next.py:1127 | can_view_dashboard |
| GET | `/ultimate/ideas` | `pulse_next.register.<locals>.ideas` | pulse_next.py:890 | can_view_dashboard |
| POST | `/ultimate/ideas/create` | `pulse_next.register.<locals>.ideas_create` | pulse_next.py:905 | can_view_dashboard |
| POST | `/ultimate/ideas/{idea_id}/vote` | `pulse_next.register.<locals>.idea_vote` | pulse_next.py:913 | can_view_dashboard |
| GET | `/ultimate/metrics` | `pulse_ultimate.register.<locals>.ultimate_metrics` | pulse_ultimate.py:2007 | can_view_analytics |
| GET | `/ultimate/onboarding` | `pulse_next.register.<locals>.onboarding` | pulse_next.py:998 | can_view_dashboard |
| GET | `/ultimate/orgchart` | `pulse_next.register.<locals>.orgchart` | pulse_next.py:1013 | can_view_dashboard |
| GET | `/ultimate/permissions` | `pulse_next.register.<locals>.permissions` | pulse_next.py:970 | can_view_dashboard, require_perm |
| POST | `/ultimate/permissions/save` | `pulse_next.register.<locals>.permissions_save` | pulse_next.py:982 | can_view_dashboard, require_perm |
| GET | `/ultimate/person/{user_id}` | `pulse_next.register.<locals>.person` | pulse_next.py:709 | can_view_dashboard |
| GET | `/ultimate/person/{user_id}/export` | `pulse_next.register.<locals>.person_export` | pulse_next.py:759 | can_view_dashboard |
| GET | `/ultimate/polls` | `pulse_ultimate.register.<locals>.ultimate_polls` | pulse_ultimate.py:1352 | can_view_dashboard (+ can_promote, is_admin) |
| POST | `/ultimate/polls/create` | `pulse_ultimate.register.<locals>.ultimate_poll_create` | pulse_ultimate.py:1381 | can_view_dashboard |
| POST | `/ultimate/polls/{poll_id}/vote` | `pulse_ultimate.register.<locals>.ultimate_poll_vote` | pulse_ultimate.py:1401 | can_view_dashboard |
| POST | `/ultimate/promotions/create` | `pulse_next.register.<locals>.promotions_create` | pulse_next.py:782 | can_view_dashboard, require_perm |
| GET | `/ultimate/reports` | `pulse_next.register.<locals>.reports` | pulse_next.py:1035 | can_view_analytics, require_perm |
| GET | `/ultimate/reports.csv` | `pulse_next.register.<locals>.reports_csv` | pulse_next.py:1050 | can_view_analytics, require_perm |
| GET | `/ultimate/roblox` | `pulse_ultimate.register.<locals>.ultimate_roblox` | pulse_ultimate.py:1124 | can_view_dashboard |
| POST | `/ultimate/roblox/save` | `pulse_ultimate.register.<locals>.ultimate_roblox_save` | pulse_ultimate.py:1147 | can_view_dashboard |
| GET | `/ultimate/sandbox` | `pulse_intelligence.register.<locals>.pulse_sandbox_page` | pulse_intelligence.py:742 | can_view_dashboard |
| POST | `/ultimate/sandbox` | `pulse_intelligence.register.<locals>.pulse_sandbox_validate` | pulse_intelligence.py:747 | can_view_dashboard |
| GET | `/ultimate/scoreboard` | `pulse_next.register.<locals>.scoreboard` | pulse_next.py:1151 | can_view_analytics |
| GET | `/ultimate/search` | `pulse_next.register.<locals>.global_search` | pulse_next.py:825 | can_view_dashboard |
| GET | `/ultimate/security` | `pulse_next.register.<locals>.security_center` | pulse_next.py:1252 | can_view_dashboard |
| GET | `/ultimate/shifts` | `pulse_next.register.<locals>.shifts` | pulse_next.py:1115 | can_view_dashboard |
| GET | `/ultimate/simulator` | `pulse_intelligence.register.<locals>.pulse_simulator_page` | pulse_intelligence.py:690 | can_view_dashboard |
| POST | `/ultimate/simulator/run` | `pulse_intelligence.register.<locals>.pulse_simulator_run` | pulse_intelligence.py:695 | can_view_dashboard |
| GET | `/ultimate/status` | `pulse_ultimate.register.<locals>.ultimate_public_status` | pulse_ultimate.py:1920 | — (öffentlich/keine erkennbare Prüfung) |
| GET | `/ultimate/support` | `pulse_next.register.<locals>.support` | pulse_next.py:1163 | can_view_analytics |
| GET | `/ultimate/system` | `pulse_ultimate.register.<locals>.ultimate_system` | pulse_ultimate.py:1658 | can_view_dashboard |
| GET | `/ultimate/system-twin` | `pulse_intelligence.register.<locals>.pulse_system_twin` | pulse_intelligence.py:599 | can_view_dashboard |
| POST | `/ultimate/system/api-key` | `pulse_ultimate.register.<locals>.ultimate_api_key_create` | pulse_ultimate.py:1941 | can_view_dashboard |
| POST | `/ultimate/system/api-key/{key_id}/revoke` | `pulse_ultimate.register.<locals>.ultimate_api_key_revoke` | pulse_ultimate.py:1748 | can_view_dashboard |
| GET | `/ultimate/system/api-keys` | `pulse_ultimate.register.<locals>.ultimate_api_keys` | pulse_ultimate.py:1734 | can_view_dashboard |
| POST | `/ultimate/system/branding` | `pulse_ultimate.register.<locals>.ultimate_branding` | pulse_ultimate.py:1774 | can_view_dashboard |
| GET | `/ultimate/tasks` | `pulse_next.register.<locals>.taskboard` | pulse_next.py:1193 | can_view_dashboard |
| GET | `/ultimate/team` | `pulse_ultimate.register.<locals>.ultimate_team` | pulse_ultimate.py:856 | can_view_dashboard |
| GET | `/ultimate/team-trends` | `pulse_next.register.<locals>.team_trends` | pulse_next.py:1222 | can_view_analytics |
| POST | `/ultimate/team/status` | `pulse_ultimate.register.<locals>.ultimate_team_status` | pulse_ultimate.py:1879 | can_view_dashboard |
| GET | `/ultimate/team/{user_id}` | `pulse_ultimate.register.<locals>.ultimate_member` | pulse_ultimate.py:888 | can_view_dashboard (+ can_promote, is_admin) |
| POST | `/ultimate/team/{user_id}/update` | `pulse_ultimate.register.<locals>.ultimate_member_update` | pulse_ultimate.py:944 | can_view_dashboard |
| GET | `/ultimate/workflows` | `pulse_next.register.<locals>.workflows` | pulse_next.py:1087 | can_view_dashboard, require_perm |
| POST | `/ultimate/workflows/create` | `pulse_next.register.<locals>.workflows_create` | pulse_next.py:1100 | can_view_dashboard, require_perm |

### `/voice-support` (2)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/voice-support` | `pulse_pro.register.<locals>.voice_support_v5` | pulse_pro.py:1291 | can_view_dashboard (+ can_manage_tickets, can_promote, is_admin) |
| POST | `/voice-support/save` | `pulse_pro.register.<locals>.voice_support_save` | pulse_pro.py:1416 | Admin |

### `/warns` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/warns` | `pulse_pro.register.<locals>.warns_v5` | pulse_pro.py:1999 | angemeldet (+ can_warn, is_admin) |

### `/wiki` (3)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/wiki` | `pulse_features.register.<locals>.wiki_page` | pulse_features.py:151 | ctx (+ can_manage_wiki, can_promote, is_admin) |
| POST | `/wiki/save` | `pulse_features.register.<locals>.wiki_save` | pulse_features.py:173 | angemeldet (+ can_manage_wiki, can_promote, is_admin) |
| GET | `/wiki/{page_id}` | `pulse_features.register.<locals>.wiki_detail` | pulse_features.py:163 | ctx (+ can_manage_wiki, can_promote, is_admin) |

