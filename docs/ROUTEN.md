# Routen-Landkarte (effektiv zur Laufzeit)

Automatisch erzeugt von `python tools/route_map.py` aus dem laufenden FastAPI-Objekt (nicht von Hand bearbeiten).
Spalte *Prüfung* ist eine Quelltext-Heuristik (`auth(...)`, `cctx`, `ctx_for`, `admin_ctx`, `perm=`) – Hinweis, kein Beweis.

- **Effektive Routen (Methode × Pfad):** 298
- **Doppelt registriert:** 1 → `GET /ultimate`
- **Im Quelltext registriert, zur Laufzeit verdrängt:** 21
- **Ohne erkennbare Prüfung:** 18 (Login/OAuth/Health/Bewerbungsportal/öffentliche Seiten – einzeln prüfen)

> Achtung `remove_and_add` (`pulse_pro.py`): entfernt **alle Methoden** eines Pfads, bevor es die neue Methode registriert.

## Doppelte Registrierungen

| Methode | Pfad | aktive Handler (Reihenfolge) |
|---|---|---|
| GET | `/ultimate` | `webserver.ultimate_dashboard` (webserver.py:3707) → `pulse_ultimate.register.<locals>.ultimate_home` (pulse_ultimate.py:795) (FastAPI nimmt den **ersten** Treffer) |

## Verdrängte Handler (toter Code)

| Methode | Pfad | verdrängte Funktion | Ort | aktiv stattdessen |
|---|---|---|---|---|
| GET | `/achievements` | `achievements_page` | pulse_features.py:228 | `pulse_pro.register.<locals>.achievements_v5` |
| POST | `/backup/restore` | `restore_backup` | pulse_features.py:368 | `webserver.restore_backup` |
| GET | `/calendar` | `calendar_page` | pulse_features.py:291 | `pulse_pro.register.<locals>.calendar_v5` |
| GET | `/dashboard` | `dashboard_main` | webserver.py:3391 | `pulse_pro.register.<locals>.dashboard_v5` |
| GET | `/health` | `health` | pulse_features.py:311 | `pulse_pro.register.<locals>.api_health` |
| GET | `/manifest.webmanifest` | `manifest` | pulse_features.py:315 | `pulse_pro.register.<locals>.manifest` |
| GET | `/meetings-history` | `meetings_history_page` | pulse_features.py:275 | `pulse_pro.register.<locals>.meetings_history_v5` |
| GET | `/melonly` | `melonly_alias` | webserver.py:3914 | `pulse_pro.register.<locals>.melonly_page` |
| GET | `/member/{user_id}` | `member_detail` | webserver.py:6689 | `pulse_pro.register.<locals>.member_v5` |
| GET | `/pulse-inbox` | `inbox_page` | pulse_features.py:39 | `pulse_pro.register.<locals>.inbox_v5` |
| POST | `/pulse-inbox/read` | `inbox_read` | pulse_features.py:60 | `pulse_pro.register.<locals>.inbox_read_all` |
| GET | `/search` | `search_page` | pulse_features.py:64 | `pulse_pro.register.<locals>.search_v5` |
| GET | `/settings` | `settings_page` | webserver.py:9504 | `pulse_pro.register.<locals>.settings_v5` |
| GET | `/stats` | `stats_page` | pulse_features.py:261 | `pulse_pro.register.<locals>.stats_v5` |
| GET | `/sw.js` | `service_worker` | pulse_features.py:319 | `pulse_pro.register.<locals>.service_worker` |
| GET | `/system` | `system_page` | pulse_features.py:300 | `pulse_pro.register.<locals>.system_v5` |
| GET | `/tasks` | `tasks_page` | pulse_features.py:84 | `pulse_pro.register.<locals>.tasks_v5` |
| POST | `/tasks/update` | `task_update` | pulse_features.py:115 | `pulse_pro.register.<locals>.update_task_v5` |
| GET | `/tickets` | `tickets_page` | pulse_features.py:125 | `pulse_pro.register.<locals>.tickets_v5` |
| GET | `/tickets` | `pulse_tickets_dashboard` | webserver.py:4652 | `pulse_pro.register.<locals>.tickets_v5` |
| POST | `/tickets/update` | `ticket_update` | pulse_features.py:282 | `pulse_pro.register.<locals>.ticket_update_v5` |

## Effektive Routen nach Bereich

### `/` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/` | `webserver.home` | webserver.py:2976 | — (öffentlich/keine erkennbare Prüfung) |

### `/achievements` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/achievements` | `pulse_pro.register.<locals>.achievements_v5` | pulse_pro.py:2172 | can_view_dashboard |

### `/action` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| POST | `/action` | `webserver.handle_action` | webserver.py:10120 | angemeldet (+ can_manage_applications, can_promote, is_admin) |

### `/activity-check` (2)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/activity-check` | `pulse_pro.register.<locals>.activity_check_page` | pulse_pro.py:903 | can_manage_checkins |
| POST | `/activity-check/setup` | `pulse_pro.register.<locals>.activity_check_setup` | pulse_pro.py:1029 | can_manage_checkins |

### `/admin` (16)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/admin` | `pulse_admin.register.<locals>.admin_hub` | pulse_admin.py:178 | angemeldet |
| POST | `/admin/commands/sync` | `pulse_admin.register.<locals>.admin_commands_sync` | pulse_admin.py:674 | Admin |
| GET | `/admin/discord` | `pulse_admin.register.<locals>.admin_discord` | pulse_admin.py:469 | Admin |
| GET | `/admin/discord/banner/{filename}` | `pulse_admin.register.<locals>.admin_discord_banner` | pulse_admin.py:827 | Admin |
| POST | `/admin/discord/branding` | `pulse_admin.register.<locals>.admin_discord_branding` | pulse_admin.py:544 | Admin |
| POST | `/admin/discord/log-routing` | `pulse_admin.register.<locals>.admin_discord_log_routing` | pulse_admin.py:710 | Admin |
| POST | `/admin/discord/logs` | `pulse_admin.register.<locals>.admin_discord_logs` | pulse_admin.py:689 | Admin |
| POST | `/admin/discord/test` | `pulse_admin.register.<locals>.admin_discord_test` | pulse_admin.py:740 | Admin |
| POST | `/admin/discord/verification` | `pulse_admin.register.<locals>.admin_verification_save` | pulse_admin.py:762 | Admin |
| POST | `/admin/discord/verification/publish` | `pulse_admin.register.<locals>.admin_verification_publish` | pulse_admin.py:808 | Admin |
| POST | `/admin/discord/welcome` | `pulse_admin.register.<locals>.admin_discord_welcome` | pulse_admin.py:492 | Admin |
| GET | `/admin/panels` | `pulse_admin.register.<locals>.admin_panels` | pulse_admin.py:629 | Admin |
| POST | `/admin/panels/republish` | `pulse_admin.register.<locals>.admin_panels_republish` | pulse_admin.py:655 | Admin |
| GET | `/admin/privacy` | `pulse_admin.register.<locals>.admin_privacy` | pulse_admin.py:563 | Admin |
| POST | `/admin/privacy` | `pulse_admin.register.<locals>.admin_privacy_save` | pulse_admin.py:599 | Admin |
| GET | `/admin/privacy/export` | `pulse_admin.register.<locals>.admin_privacy_export` | pulse_admin.py:614 | Admin |

### `/announcements` (2)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/announcements` | `pulse_pro.register.<locals>.announcements_page` | pulse_pro.py:1214 | angemeldet (+ can_manage_announcements, can_promote, is_admin) |
| POST | `/announcements/create` | `pulse_pro.register.<locals>.create_announcement` | pulse_pro.py:1224 | angemeldet (+ can_manage_announcements, can_promote, is_admin) |

### `/api` (6)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/api/pulse/live` | `pulse_pro.register.<locals>.api_live` | pulse_pro.py:2782 | can_view_dashboard |
| GET | `/api/roblox-search` | `webserver.roblox_search` | webserver.py:2869 | Session (eigene Prüfung) |
| GET | `/api/roblox-user` | `webserver.roblox_user_lookup` | webserver.py:2807 | Session (eigene Prüfung) |
| GET | `/api/team/activity` | `webserver.team_activity_api` | webserver.py:4411 | can_view_dashboard |
| GET | `/api/team/discord-status` | `pulse_pro.register.<locals>.discord_status_api` | pulse_pro.py:1166 | angemeldet (+ can_promote, can_view_team_status, is_admin) |
| GET | `/api/team/warn-roles` | `webserver.warn_roles_api` | webserver.py:4448 | can_warn |

### `/applications` (6)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/applications` | `webserver.applications_page` | webserver.py:7527 | angemeldet (+ can_manage_applications, can_promote, can_view_dashboard, is_admin) |
| GET | `/applications/config` | `webserver.application_modules_config_page` | webserver.py:7894 | angemeldet (+ can_manage_applications, can_promote, is_admin) |
| POST | `/applications/config` | `webserver.save_application_modules_config` | webserver.py:7941 | angemeldet (+ can_manage_applications, can_promote, is_admin) |
| POST | `/applications/interview-slot` | `webserver.application_interview_slot` | webserver.py:7771 | angemeldet (+ can_manage_applications, can_promote, is_admin) |
| POST | `/applications/review` | `webserver.save_application_review` | webserver.py:7836 | angemeldet (+ can_manage_applications, can_promote, is_admin) |
| POST | `/applications/templates` | `webserver.application_templates_save` | webserver.py:7802 | Admin |

### `/apply` (4)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/apply` | `webserver.public_apply_page` | webserver.py:7244 | Session (eigene Prüfung) |
| GET | `/apply/callback` | `webserver.apply_callback` | webserver.py:7501 | Session (eigene Prüfung) |
| POST | `/apply/keepalive` | `webserver.apply_keepalive` | webserver.py:7513 | Session (eigene Prüfung) |
| GET | `/apply/login` | `webserver.apply_login` | webserver.py:7487 | — (öffentlich/keine erkennbare Prüfung) |

### `/approvals` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/approvals` | `pulse_pro.register.<locals>.approvals_page` | pulse_pro.py:1291 | can_view_dashboard (+ can_manage_applications, can_promote, is_admin) |

### `/backup` (10)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| POST | `/backup/create` | `webserver.create_backup` | webserver.py:9081 | Admin |
| POST | `/backup/delete` | `webserver.delete_backup` | webserver.py:9280 | Admin |
| GET | `/backup/download/{filename}` | `webserver.download_backup` | webserver.py:9144 | Admin |
| POST | `/backup/full/create` | `webserver.full_backup_create` | webserver.py:9399 | Admin |
| POST | `/backup/full/delete` | `webserver.full_backup_delete` | webserver.py:9490 | Admin |
| GET | `/backup/full/download/{filename}` | `webserver.full_backup_download` | webserver.py:9412 | Admin |
| GET | `/backup/full/preview/{filename}` | `webserver.full_backup_preview` | webserver.py:9421 | Admin |
| POST | `/backup/full/restore` | `webserver.full_backup_restore` | webserver.py:9463 | Admin |
| POST | `/backup/restore` | `webserver.restore_backup` | webserver.py:9166 | Admin |
| POST | `/backup/upload` | `webserver.upload_backup` | webserver.py:9093 | Admin |

### `/backups` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/backups` | `webserver.backups_page` | webserver.py:9006 | Admin |

### `/calendar` (2)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/calendar` | `pulse_pro.register.<locals>.calendar_v5` | pulse_pro.py:1676 | can_view_dashboard |
| POST | `/calendar/create` | `pulse_pro.register.<locals>.create_calendar_event_v5` | pulse_pro.py:1843 | can_view_dashboard |

### `/callback` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/callback` | `webserver.callback` | webserver.py:3049 | Session (eigene Prüfung) |

### `/cockpit` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/cockpit` | `pulse_cockpit.register.<locals>.leadership_cockpit` | pulse_cockpit.py:43 | angemeldet (+ can_promote, is_admin) |

### `/dashboard` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/dashboard` | `pulse_pro.register.<locals>.dashboard_v5` | pulse_pro.py:810 | can_view_dashboard |

### `/export` (2)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/export/logs` | `webserver.export_logs` | webserver.py:4340 | can_view_analytics |
| GET | `/export/team.csv` | `pulse_pro.register.<locals>.team_export` | pulse_pro.py:2765 | can_view_analytics |

### `/favicon.ico` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/favicon.ico` | `webserver.favicon` | webserver.py:241 | — (öffentlich/keine erkennbare Prüfung) |

### `/handover` (2)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/handover` | `pulse_pro.register.<locals>.handover_page` | pulse_pro.py:1187 | can_view_dashboard (+ can_manage_handover, can_promote, can_view_dashboard, is_admin) |
| POST | `/handover/create` | `pulse_pro.register.<locals>.create_handover` | pulse_pro.py:1201 | angemeldet (+ can_manage_handover, can_view_dashboard) |

### `/health` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/health` | `pulse_pro.register.<locals>.api_health` | pulse_pro.py:2789 | — (öffentlich/keine erkennbare Prüfung) |

### `/healthz` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/healthz` | `webserver.healthz` | webserver.py:2927 | — (öffentlich/keine erkennbare Prüfung) |

### `/loa` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/loa` | `webserver.loa_page` | webserver.py:6986 | can_view_dashboard |

### `/log` (3)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| POST | `/log/create` | `webserver.create_log` | webserver.py:4198 | can_view_dashboard (+ can_add_notes, can_moderate_bans, can_moderate_bolo, can_moderate_kicks) |
| POST | `/log/delete` | `webserver.delete_log` | webserver.py:4293 | can_view_dashboard |
| POST | `/log/edit` | `webserver.edit_log` | webserver.py:4244 | can_view_dashboard |

### `/login` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/login` | `webserver.login` | webserver.py:3013 | — (öffentlich/keine erkennbare Prüfung) |

### `/logout` (2)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/logout` | `webserver.logout_confirm` | webserver.py:3025 | — (öffentlich/keine erkennbare Prüfung) |
| POST | `/logout` | `webserver.logout` | webserver.py:3035 | — (öffentlich/keine erkennbare Prüfung) |

### `/manifest.webmanifest` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/manifest.webmanifest` | `pulse_pro.register.<locals>.manifest` | pulse_pro.py:2797 | — (öffentlich/keine erkennbare Prüfung) |

### `/me` (6)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/me` | `pulse_home.register.<locals>.my_pulse` | pulse_home.py:59 | can_view_dashboard (+ can_manage_applications, can_manage_tickets, can_promote, is_admin) |
| GET | `/me/sessions` | `pulse_home.register.<locals>.my_sessions` | pulse_home.py:290 | can_view_dashboard |
| POST | `/me/sessions/revoke` | `pulse_home.register.<locals>.my_session_revoke` | pulse_home.py:319 | can_view_dashboard |
| POST | `/me/sessions/revoke-all` | `pulse_home.register.<locals>.my_session_revoke_all` | pulse_home.py:338 | can_view_dashboard |
| POST | `/me/sessions/revoke-others` | `pulse_home.register.<locals>.my_session_revoke_others` | pulse_home.py:328 | can_view_dashboard |
| POST | `/me/start-page` | `pulse_home.register.<locals>.my_start_page` | pulse_home.py:280 | can_view_dashboard |

### `/meetings` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/meetings` | `webserver.meetings_page` | webserver.py:6850 | can_view_dashboard |

### `/meetings-history` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/meetings-history` | `pulse_pro.register.<locals>.meetings_history_v5` | pulse_pro.py:2166 | can_view_dashboard |

### `/melonly` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/melonly` | `pulse_pro.register.<locals>.melonly_page` | pulse_pro.py:553 | can_view_dashboard (+ is_admin) |

### `/member` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/member/{user_id}` | `pulse_pro.register.<locals>.member_v5` | pulse_pro.py:1921 | can_view_dashboard (+ can_add_notes, can_promote, can_warn, is_admin) |

### `/mod-cases` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/mod-cases` | `webserver.mod_cases_page` | webserver.py:5748 | — (öffentlich/keine erkennbare Prüfung) |

### `/mod-cases.csv` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/mod-cases.csv` | `webserver.mod_cases_csv` | webserver.py:5782 | — (öffentlich/keine erkennbare Prüfung) |

### `/promotions` (3)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/promotions` | `pulse_pro.register.<locals>.promotions_page` | pulse_pro.py:1235 | angemeldet (+ can_manage_promotions, can_promote, is_admin) |
| POST | `/promotions/create` | `pulse_pro.register.<locals>.create_promotion` | pulse_pro.py:1248 | angemeldet (+ can_manage_promotions, can_promote, is_admin) |
| POST | `/promotions/decide` | `pulse_pro.register.<locals>.decide_promotion` | pulse_pro.py:1264 | angemeldet (+ can_manage_promotions, can_promote, is_admin) |

### `/pulse-inbox` (5)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/pulse-inbox` | `pulse_pro.register.<locals>.inbox_v5` | pulse_pro.py:850 | can_view_dashboard (+ is_admin) |
| POST | `/pulse-inbox/read` | `pulse_pro.register.<locals>.inbox_read_all` | pulse_pro.py:896 | can_view_dashboard |
| POST | `/pulse-inbox/read/{nid}` | `pulse_pro.register.<locals>.inbox_read_one` | pulse_pro.py:899 | can_view_dashboard |
| POST | `/pulse-inbox/server-quiet` | `pulse_pro.register.<locals>.inbox_server_quiet` | pulse_pro.py:882 | Admin |
| POST | `/pulse-inbox/settings` | `pulse_pro.register.<locals>.inbox_settings` | pulse_pro.py:890 | can_view_dashboard |

### `/robots.txt` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/robots.txt` | `pulse_pro.register.<locals>.robots_txt` | pulse_pro.py:2838 | — (öffentlich/keine erkennbare Prüfung) |

### `/search` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/search` | `pulse_pro.register.<locals>.search_v5` | pulse_pro.py:1899 | can_view_dashboard |

### `/settings` (14)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/settings` | `pulse_pro.register.<locals>.settings_v5` | pulse_pro.py:2197 | Admin |
| POST | `/settings/dashboard-profile/delete` | `pulse_pro.register.<locals>.settings_dashboard_profile_delete` | pulse_pro.py:2494 | Admin |
| POST | `/settings/dashboard-profile/save` | `pulse_pro.register.<locals>.settings_dashboard_profile_save` | pulse_pro.py:2439 | Admin |
| POST | `/settings/nickname-prefix/default` | `pulse_pro.register.<locals>.settings_nickname_prefix_default` | pulse_pro.py:2544 | Admin |
| POST | `/settings/nickname-prefix/delete` | `pulse_pro.register.<locals>.settings_nickname_prefix_delete` | pulse_pro.py:2568 | Admin |
| POST | `/settings/nickname-prefix/save` | `pulse_pro.register.<locals>.settings_nickname_prefix_save` | pulse_pro.py:2511 | Admin |
| POST | `/settings/nickname-prefix/sync` | `pulse_pro.register.<locals>.settings_nickname_prefix_sync` | pulse_pro.py:2592 | Admin |
| POST | `/settings/pro-clear-all` | `pulse_pro.register.<locals>.settings_clear_all_permissions` | pulse_pro.py:2648 | Admin |
| POST | `/settings/pro-goal` | `pulse_pro.register.<locals>.settings_goal` | pulse_pro.py:2679 | Admin |
| POST | `/settings/pro-presets` | `pulse_pro.register.<locals>.settings_profiles` | pulse_pro.py:2641 | Admin |
| POST | `/settings/pro-roles` | `pulse_pro.register.<locals>.settings_roles` | pulse_pro.py:2615 | Admin |
| POST | `/settings/pro-save` | `pulse_pro.register.<locals>.settings_save` | pulse_pro.py:2607 | Admin |
| POST | `/settings/pro-warn-roles` | `pulse_pro.register.<locals>.settings_warn_roles` | pulse_pro.py:2653 | Admin |
| POST | `/settings/pro-warn-sync` | `pulse_pro.register.<locals>.settings_warn_sync` | pulse_pro.py:2672 | Admin |

### `/shift` (2)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| POST | `/shift/action` | `webserver.handle_shift_action` | webserver.py:4168 | can_view_dashboard |
| POST | `/shift/admin-end` | `pulse_pro.register.<locals>.admin_end_shift` | pulse_pro.py:761 | can_view_dashboard (+ can_promote, is_admin) |

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
| GET | `/static/pulse.css` | `webserver.static_pulse_css` | webserver.py:2968 | — (öffentlich/keine erkennbare Prüfung) |

### `/stats` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/stats` | `pulse_pro.register.<locals>.stats_v5` | pulse_pro.py:1667 | can_view_analytics |

### `/suite` (34)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/suite` | `cogs.pulse_suite.register_routes.<locals>.leadership` | cogs/pulse_suite.py:308 | can_view_dashboard |
| GET | `/suite/achievements` | `cogs.pulse_suite.register_routes.<locals>.achievements_page` | cogs/pulse_suite.py:645 | can_view_dashboard |
| GET | `/suite/api/search` | `cogs.pulse_suite.register_routes.<locals>.search_api` | cogs/pulse_suite.py:627 | can_view_dashboard |
| GET | `/suite/applications` | `cogs.pulse_suite.register_routes.<locals>.applications_page` | cogs/pulse_suite.py:424 | can_view_dashboard |
| GET | `/suite/applications/{app_id}` | `cogs.pulse_suite.register_routes.<locals>.application_page` | cogs/pulse_suite.py:429 | can_view_dashboard |
| POST | `/suite/applications/{app_id}/update` | `cogs.pulse_suite.register_routes.<locals>.application_update` | cogs/pulse_suite.py:436 | can_view_dashboard |
| GET | `/suite/audit` | `cogs.pulse_suite.register_routes.<locals>.audit_page` | cogs/pulse_suite.py:570 | can_view_dashboard |
| GET | `/suite/audit.csv` | `cogs.pulse_suite.register_routes.<locals>.audit_csv` | cogs/pulse_suite.py:610 | can_view_dashboard |
| GET | `/suite/calendar` | `cogs.pulse_suite.register_routes.<locals>.calendar_page` | cogs/pulse_suite.py:405 | can_view_dashboard |
| POST | `/suite/calendar/create` | `cogs.pulse_suite.register_routes.<locals>.calendar_create` | cogs/pulse_suite.py:418 | can_view_dashboard |
| GET | `/suite/cases` | `cogs.pulse_suite.register_routes.<locals>.cases_page` | cogs/pulse_suite.py:631 | can_view_dashboard |
| POST | `/suite/cases/{case_id}/action` | `cogs.pulse_suite.register_routes.<locals>.case_action` | cogs/pulse_suite.py:638 | can_view_dashboard |
| GET | `/suite/interview/create` | `cogs.pulse_suite.register_routes.<locals>.interview_form` | cogs/pulse_suite.py:450 | can_view_dashboard |
| POST | `/suite/interview/create` | `cogs.pulse_suite.register_routes.<locals>.interview_create` | cogs/pulse_suite.py:456 | can_view_dashboard |
| GET | `/suite/interviews` | `cogs.pulse_suite.register_routes.<locals>.interviews_page` | cogs/pulse_suite.py:443 | can_view_dashboard |
| GET | `/suite/member/{member_id}` | `cogs.pulse_suite.register_routes.<locals>.member_page` | cogs/pulse_suite.py:350 | can_view_dashboard |
| POST | `/suite/member/{member_id}/note` | `cogs.pulse_suite.register_routes.<locals>.member_note` | cogs/pulse_suite.py:364 | can_view_dashboard |
| GET | `/suite/my-schedule` | `cogs.pulse_suite.register_routes.<locals>.my_schedule` | cogs/pulse_suite.py:381 | can_view_dashboard |
| GET | `/suite/news` | `cogs.pulse_suite.register_routes.<locals>.news_page` | cogs/pulse_suite.py:562 | can_view_dashboard |
| POST | `/suite/news/create` | `cogs.pulse_suite.register_routes.<locals>.news_create` | cogs/pulse_suite.py:566 | can_view_dashboard |
| GET | `/suite/promotions` | `cogs.pulse_suite.register_routes.<locals>.promotions_page` | cogs/pulse_suite.py:464 | can_view_dashboard |
| POST | `/suite/promotions/create` | `cogs.pulse_suite.register_routes.<locals>.promotions_create` | cogs/pulse_suite.py:471 | can_view_dashboard |
| POST | `/suite/promotions/{pid}/action` | `cogs.pulse_suite.register_routes.<locals>.promotions_action` | cogs/pulse_suite.py:479 | can_view_dashboard |
| GET | `/suite/schedule` | `cogs.pulse_suite.register_routes.<locals>.schedule_page` | cogs/pulse_suite.py:389 | can_view_dashboard |
| POST | `/suite/schedule/create` | `cogs.pulse_suite.register_routes.<locals>.schedule_create` | cogs/pulse_suite.py:397 | can_view_dashboard |
| GET | `/suite/search` | `cogs.pulse_suite.register_routes.<locals>.search_page` | cogs/pulse_suite.py:622 | can_view_dashboard |
| GET | `/suite/task/{task_id}` | `cogs.pulse_suite.register_routes.<locals>.task_detail` | cogs/pulse_suite.py:531 | can_view_dashboard |
| POST | `/suite/task/{task_id}/meta` | `cogs.pulse_suite.register_routes.<locals>.task_meta` | cogs/pulse_suite.py:540 | can_view_dashboard |
| GET | `/suite/tasks` | `cogs.pulse_suite.register_routes.<locals>.tasks_page` | cogs/pulse_suite.py:520 | can_view_dashboard |
| POST | `/suite/tasks/{task_id}/move` | `cogs.pulse_suite.register_routes.<locals>.task_move` | cogs/pulse_suite.py:554 | can_view_dashboard |
| GET | `/suite/team` | `cogs.pulse_suite.register_routes.<locals>.team_page` | cogs/pulse_suite.py:336 | can_view_dashboard |
| GET | `/suite/time` | `cogs.pulse_suite.register_routes.<locals>.time_page` | cogs/pulse_suite.py:373 | can_view_dashboard |
| GET | `/suite/training` | `cogs.pulse_suite.register_routes.<locals>.training_page` | cogs/pulse_suite.py:504 | can_view_dashboard |
| POST | `/suite/training/assign` | `cogs.pulse_suite.register_routes.<locals>.training_assign` | cogs/pulse_suite.py:512 | can_view_dashboard |

### `/sw.js` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/sw.js` | `pulse_pro.register.<locals>.service_worker` | pulse_pro.py:2800 | — (öffentlich/keine erkennbare Prüfung) |

### `/system` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/system` | `pulse_pro.register.<locals>.system_v5` | pulse_pro.py:2682 | Admin |

### `/tasks` (3)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/tasks` | `pulse_pro.register.<locals>.tasks_v5` | pulse_pro.py:1301 | can_view_dashboard (+ can_manage_tasks, can_promote, is_admin) |
| POST | `/tasks/create` | `pulse_features.register.<locals>.task_create` | pulse_features.py:104 | angemeldet (+ can_manage_tasks, can_promote, is_admin) |
| POST | `/tasks/update` | `pulse_pro.register.<locals>.update_task_v5` | pulse_pro.py:1318 | can_view_dashboard (+ can_manage_tasks, can_promote, is_admin) |

### `/team` (7)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/team` | `webserver.team_list_page` | webserver.py:6071 | can_view_dashboard (+ can_promote, is_admin) |
| GET | `/team/checklists` | `webserver.team_checklists_page` | webserver.py:5662 | — (öffentlich/keine erkennbare Prüfung) |
| POST | `/team/checklists/mentor` | `webserver.team_checklists_mentor` | webserver.py:5723 | — (öffentlich/keine erkennbare Prüfung) |
| POST | `/team/checklists/toggle` | `webserver.team_checklists_toggle` | webserver.py:5707 | — (öffentlich/keine erkennbare Prüfung) |
| POST | `/team/voice-support/audio` | `webserver.manage_voice_support_audio` | webserver.py:6454 | can_promote |
| GET | `/team/{user_id}/roles` | `webserver.team_role_manager` | webserver.py:6571 | can_promote (+ is_admin) |
| POST | `/team/{user_id}/roles` | `webserver.team_role_manager_save` | webserver.py:6596 | can_promote (+ is_admin) |

### `/team-status` (2)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/team-status` | `pulse_pro.register.<locals>.team_status_page` | pulse_pro.py:1113 | can_view_dashboard (+ can_promote, can_view_team_status, is_admin) |
| POST | `/team-status/set` | `pulse_pro.register.<locals>.set_team_status` | pulse_pro.py:1183 | can_view_dashboard |

### `/ticket` (2)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/ticket/transcript/{filename}` | `pulse_features.register.<locals>.ticket_transcript` | pulse_features.py:133 | ctx (+ can_manage_tickets, can_promote, is_admin) |
| GET | `/ticket/{ticket_id}` | `pulse_pro.register.<locals>.ticket_detail_v5` | pulse_pro.py:2157 | can_manage_tickets |

### `/tickets` (20)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/tickets` | `pulse_pro.register.<locals>.tickets_v5` | pulse_pro.py:1332 | can_view_dashboard |
| POST | `/tickets/action` | `webserver.pulse_tickets_dashboard_action` | webserver.py:5871 | can_manage_tickets (+ can_manage_tickets, is_admin) |
| POST | `/tickets/automation` | `webserver.pulse_ticket_automation_save` | webserver.py:5317 | Admin |
| POST | `/tickets/categories/delete` | `webserver.pulse_ticket_category_delete` | webserver.py:5570 | Admin |
| POST | `/tickets/categories/save` | `webserver.pulse_ticket_category_save` | webserver.py:5392 | Admin |
| GET | `/tickets/panels` | `webserver.pulse_tickets_dashboard` | webserver.py:4650 | can_manage_tickets (+ is_admin) |
| POST | `/tickets/panels/delete` | `webserver.pulse_ticket_panel_delete` | webserver.py:5360 | Admin |
| POST | `/tickets/panels/duplicate` | `webserver.pulse_ticket_panel_duplicate` | webserver.py:5299 | Admin |
| GET | `/tickets/panels/media/{filename}` | `webserver.pulse_ticket_panel_media` | webserver.py:5287 | Admin |
| POST | `/tickets/panels/publish` | `webserver.pulse_ticket_panel_publish` | webserver.py:5337 | Admin |
| POST | `/tickets/panels/save` | `webserver.pulse_ticket_panel_save` | webserver.py:5118 | Admin |
| POST | `/tickets/rate` | `pulse_features.register.<locals>.ticket_rate` | pulse_features.py:143 | ctx |
| POST | `/tickets/settings` | `webserver.pulse_tickets_dashboard_settings` | webserver.py:5024 | Admin |
| POST | `/tickets/setup-panels` | `webserver.pulse_ticket_panels_setup` | webserver.py:5095 | Admin |
| GET | `/tickets/tools` | `webserver.pulse_ticket_tools` | webserver.py:5796 | can_manage_tickets (+ is_admin) |
| POST | `/tickets/tools/replies` | `webserver.pulse_ticket_tools_replies` | webserver.py:5844 | Admin |
| POST | `/tickets/tools/unblock` | `webserver.pulse_ticket_tools_unblock` | webserver.py:5860 | can_manage_tickets |
| GET | `/tickets/transcript/{ticket_id}` | `webserver.pulse_ticket_transcript` | webserver.py:5606 | can_manage_tickets |
| GET | `/tickets/transcript/{ticket_id}/view` | `webserver.pulse_ticket_transcript_view` | webserver.py:5631 | can_manage_tickets |
| POST | `/tickets/update` | `pulse_pro.register.<locals>.ticket_update_v5` | pulse_pro.py:1338 | can_view_dashboard (+ can_manage_tickets, can_promote, is_admin) |

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
| GET | `/ultimate` | `webserver.ultimate_dashboard` | webserver.py:3707 | can_view_dashboard (+ can_promote, can_warn, is_admin) |
| GET | `/ultimate` | `pulse_ultimate.register.<locals>.ultimate_home` | pulse_ultimate.py:795 | can_view_dashboard |
| GET | `/ultimate/achievements` | `pulse_next.register.<locals>.achievements` | pulse_next.py:957 | can_view_dashboard |
| GET | `/ultimate/analytics` | `pulse_ultimate.register.<locals>.ultimate_analytics` | pulse_ultimate.py:1886 | can_view_analytics |
| GET | `/ultimate/announcements` | `pulse_next.register.<locals>.announcements` | pulse_next.py:848 | can_view_dashboard |
| POST | `/ultimate/announcements/create` | `pulse_next.register.<locals>.announcements_create` | pulse_next.py:871 | can_view_dashboard, require_perm |
| POST | `/ultimate/announcements/{announcement_id}/read` | `pulse_next.register.<locals>.announcement_read` | pulse_next.py:882 | can_view_dashboard |
| GET | `/ultimate/api/case/{case_id}` | `pulse_ultimate.register.<locals>.ultimate_api_case` | pulse_ultimate.py:1858 | can_view_dashboard |
| GET | `/ultimate/api/copilot` | `pulse_intelligence.register.<locals>.pulse_copilot_api` | pulse_intelligence.py:593 | can_view_dashboard |
| POST | `/ultimate/api/feedback/{feedback_id}` | `pulse_ultimate.register.<locals>.ultimate_api_feedback` | pulse_ultimate.py:1869 | can_view_dashboard |
| GET | `/ultimate/api/monitor` | `pulse_ultimate.register.<locals>.ultimate_api_monitor` | pulse_ultimate.py:2057 | can_view_dashboard |
| GET | `/ultimate/api/overview` | `pulse_ultimate.register.<locals>.ultimate_api_overview` | pulse_ultimate.py:1822 | can_view_dashboard |
| GET | `/ultimate/api/system-twin` | `pulse_intelligence.register.<locals>.pulse_system_twin_api` | pulse_intelligence.py:650 | can_view_dashboard |
| GET | `/ultimate/api/team` | `pulse_ultimate.register.<locals>.ultimate_api_team` | pulse_ultimate.py:1842 | can_view_dashboard |
| GET | `/ultimate/api/v2/overview` | `pulse_ultimate.register.<locals>.ultimate_api_v2_overview` | pulse_ultimate.py:2007 | API-Schlüssel (X-Pulse-API-Key) |
| GET | `/ultimate/api/v2/team` | `pulse_ultimate.register.<locals>.ultimate_api_v2_team` | pulse_ultimate.py:2029 | API-Schlüssel (X-Pulse-API-Key) |
| GET | `/ultimate/approvals` | `pulse_next.register.<locals>.approvals` | pulse_next.py:768 | can_view_dashboard, require_perm |
| POST | `/ultimate/approvals/{approval_id}/decide` | `pulse_next.register.<locals>.approval_decide` | pulse_next.py:798 | can_view_dashboard, require_perm |
| GET | `/ultimate/automations` | `pulse_ultimate.register.<locals>.ultimate_automations` | pulse_ultimate.py:1591 | can_view_dashboard |
| POST | `/ultimate/automations/create` | `pulse_ultimate.register.<locals>.ultimate_automation_create` | pulse_ultimate.py:1642 | can_view_dashboard |
| POST | `/ultimate/automations/{automation_id}/toggle` | `pulse_ultimate.register.<locals>.ultimate_automation_toggle` | pulse_ultimate.py:1666 | can_view_dashboard |
| GET | `/ultimate/awards` | `pulse_ultimate.register.<locals>.ultimate_awards` | pulse_ultimate.py:1559 | can_view_dashboard |
| POST | `/ultimate/awards/create` | `pulse_ultimate.register.<locals>.ultimate_award_create` | pulse_ultimate.py:1573 | can_view_dashboard |
| GET | `/ultimate/backup` | `pulse_next.register.<locals>.backup` | pulse_next.py:1058 | can_view_dashboard, require_perm |
| POST | `/ultimate/backup/create` | `pulse_next.register.<locals>.backup_create` | pulse_next.py:1066 | can_view_dashboard, require_perm |
| GET | `/ultimate/backup/{name}` | `pulse_next.register.<locals>.backup_download` | pulse_next.py:1072 | can_view_dashboard, require_perm |
| POST | `/ultimate/backup/{name}/restore` | `pulse_next.register.<locals>.backup_restore` | pulse_next.py:1079 | can_view_dashboard, require_perm |
| GET | `/ultimate/calendar` | `pulse_ultimate.register.<locals>.ultimate_calendar` | pulse_ultimate.py:1330 | can_view_dashboard |
| POST | `/ultimate/calendar/create` | `pulse_ultimate.register.<locals>.ultimate_calendar_create` | pulse_ultimate.py:1351 | can_view_dashboard |
| GET | `/ultimate/calendar/{event_id}/delete` | `pulse_ultimate.register.<locals>.ultimate_calendar_delete_link` | pulse_ultimate.py:1374 | can_view_dashboard |
| POST | `/ultimate/calendar/{event_id}/delete` | `pulse_ultimate.register.<locals>.ultimate_calendar_delete` | pulse_ultimate.py:1380 | can_view_dashboard |
| GET | `/ultimate/cases` | `pulse_ultimate.register.<locals>.ultimate_cases` | pulse_ultimate.py:1003 | can_view_dashboard |
| POST | `/ultimate/cases/create` | `pulse_ultimate.register.<locals>.ultimate_case_create` | pulse_ultimate.py:1026 | can_view_dashboard |
| GET | `/ultimate/cases/{case_id}` | `pulse_ultimate.register.<locals>.ultimate_case_detail` | pulse_ultimate.py:1054 | can_view_dashboard |
| GET | `/ultimate/cases/{case_id}/attachments/{attachment_id}` | `pulse_ultimate.register.<locals>.ultimate_attachment_download` | pulse_ultimate.py:1792 | can_view_dashboard |
| POST | `/ultimate/cases/{case_id}/players/add` | `pulse_ultimate.register.<locals>.ultimate_case_player_add` | pulse_ultimate.py:1207 | can_view_dashboard |
| POST | `/ultimate/cases/{case_id}/update` | `pulse_ultimate.register.<locals>.ultimate_case_update` | pulse_ultimate.py:1099 | can_view_dashboard |
| GET | `/ultimate/cases/{case_id}/upload` | `pulse_ultimate.register.<locals>.ultimate_upload_page` | pulse_ultimate.py:1121 | can_view_dashboard |
| POST | `/ultimate/cases/{case_id}/upload` | `pulse_ultimate.register.<locals>.ultimate_upload` | pulse_ultimate.py:1128 | can_view_dashboard |
| POST | `/ultimate/certificates/create` | `pulse_ultimate.register.<locals>.ultimate_certificate_create` | pulse_ultimate.py:1935 | can_view_dashboard |
| GET | `/ultimate/copilot` | `pulse_intelligence.register.<locals>.pulse_copilot_page` | pulse_intelligence.py:582 | can_view_dashboard |
| POST | `/ultimate/copilot` | `pulse_intelligence.register.<locals>.pulse_copilot_ask` | pulse_intelligence.py:587 | can_view_dashboard |
| GET | `/ultimate/departments` | `pulse_ultimate.register.<locals>.ultimate_departments` | pulse_ultimate.py:1234 | can_view_dashboard |
| POST | `/ultimate/departments/create` | `pulse_ultimate.register.<locals>.ultimate_department_create` | pulse_ultimate.py:1254 | can_view_dashboard |
| GET | `/ultimate/departments/{department_id}` | `pulse_ultimate.register.<locals>.ultimate_department_detail` | pulse_ultimate.py:1275 | can_view_dashboard |
| POST | `/ultimate/departments/{department_id}/add` | `pulse_ultimate.register.<locals>.ultimate_department_add` | pulse_ultimate.py:1297 | can_view_dashboard |
| POST | `/ultimate/departments/{department_id}/remove` | `pulse_ultimate.register.<locals>.ultimate_department_remove` | pulse_ultimate.py:1316 | can_view_dashboard |
| GET | `/ultimate/export.json` | `pulse_next.register.<locals>.export_json` | pulse_next.py:1238 | can_view_analytics |
| GET | `/ultimate/feedback` | `pulse_ultimate.register.<locals>.ultimate_feedback` | pulse_ultimate.py:1470 | can_view_dashboard (+ can_promote, is_admin) |
| POST | `/ultimate/feedback/create` | `pulse_ultimate.register.<locals>.ultimate_feedback_create` | pulse_ultimate.py:1491 | can_view_dashboard |
| GET | `/ultimate/goals` | `pulse_next.register.<locals>.goals` | pulse_next.py:921 | can_view_dashboard |
| POST | `/ultimate/goals/create` | `pulse_next.register.<locals>.goals_create` | pulse_next.py:938 | can_view_dashboard, require_perm |
| POST | `/ultimate/goals/{goal_id}/update` | `pulse_next.register.<locals>.goals_update` | pulse_next.py:946 | can_view_dashboard, require_perm |
| GET | `/ultimate/handovers` | `pulse_ultimate.register.<locals>.ultimate_handovers` | pulse_ultimate.py:1509 | can_view_dashboard |
| POST | `/ultimate/handovers/create` | `pulse_ultimate.register.<locals>.ultimate_handover_create` | pulse_ultimate.py:1530 | can_view_dashboard |
| POST | `/ultimate/handovers/{handover_id}/close` | `pulse_ultimate.register.<locals>.ultimate_handover_close` | pulse_ultimate.py:1551 | can_view_dashboard |
| GET | `/ultimate/health-detail` | `pulse_next.register.<locals>.health_detail` | pulse_next.py:1127 | can_view_dashboard |
| GET | `/ultimate/ideas` | `pulse_next.register.<locals>.ideas` | pulse_next.py:890 | can_view_dashboard |
| POST | `/ultimate/ideas/create` | `pulse_next.register.<locals>.ideas_create` | pulse_next.py:905 | can_view_dashboard |
| POST | `/ultimate/ideas/{idea_id}/vote` | `pulse_next.register.<locals>.idea_vote` | pulse_next.py:913 | can_view_dashboard |
| GET | `/ultimate/metrics` | `pulse_ultimate.register.<locals>.ultimate_metrics` | pulse_ultimate.py:2047 | can_view_analytics |
| GET | `/ultimate/onboarding` | `pulse_next.register.<locals>.onboarding` | pulse_next.py:998 | can_view_dashboard |
| GET | `/ultimate/orgchart` | `pulse_next.register.<locals>.orgchart` | pulse_next.py:1013 | can_view_dashboard |
| GET | `/ultimate/permissions` | `pulse_next.register.<locals>.permissions` | pulse_next.py:970 | can_view_dashboard, require_perm |
| POST | `/ultimate/permissions/save` | `pulse_next.register.<locals>.permissions_save` | pulse_next.py:982 | can_view_dashboard, require_perm |
| GET | `/ultimate/person/{user_id}` | `pulse_next.register.<locals>.person` | pulse_next.py:709 | can_view_dashboard |
| GET | `/ultimate/person/{user_id}/export` | `pulse_next.register.<locals>.person_export` | pulse_next.py:759 | can_view_dashboard |
| GET | `/ultimate/polls` | `pulse_ultimate.register.<locals>.ultimate_polls` | pulse_ultimate.py:1388 | can_view_dashboard (+ can_promote, is_admin) |
| POST | `/ultimate/polls/create` | `pulse_ultimate.register.<locals>.ultimate_poll_create` | pulse_ultimate.py:1417 | can_view_dashboard |
| POST | `/ultimate/polls/{poll_id}/vote` | `pulse_ultimate.register.<locals>.ultimate_poll_vote` | pulse_ultimate.py:1437 | can_view_dashboard |
| POST | `/ultimate/promotions/create` | `pulse_next.register.<locals>.promotions_create` | pulse_next.py:782 | can_view_dashboard, require_perm |
| GET | `/ultimate/reports` | `pulse_next.register.<locals>.reports` | pulse_next.py:1035 | can_view_analytics, require_perm |
| GET | `/ultimate/reports.csv` | `pulse_next.register.<locals>.reports_csv` | pulse_next.py:1050 | can_view_analytics, require_perm |
| GET | `/ultimate/roblox` | `pulse_ultimate.register.<locals>.ultimate_roblox` | pulse_ultimate.py:1160 | can_view_dashboard |
| POST | `/ultimate/roblox/save` | `pulse_ultimate.register.<locals>.ultimate_roblox_save` | pulse_ultimate.py:1183 | can_view_dashboard |
| GET | `/ultimate/sandbox` | `pulse_intelligence.register.<locals>.pulse_sandbox_page` | pulse_intelligence.py:742 | can_view_dashboard |
| POST | `/ultimate/sandbox` | `pulse_intelligence.register.<locals>.pulse_sandbox_validate` | pulse_intelligence.py:747 | can_view_dashboard |
| GET | `/ultimate/scoreboard` | `pulse_next.register.<locals>.scoreboard` | pulse_next.py:1151 | can_view_analytics |
| GET | `/ultimate/search` | `pulse_next.register.<locals>.global_search` | pulse_next.py:825 | can_view_dashboard |
| GET | `/ultimate/security` | `pulse_next.register.<locals>.security_center` | pulse_next.py:1252 | can_view_dashboard |
| GET | `/ultimate/shifts` | `pulse_next.register.<locals>.shifts` | pulse_next.py:1115 | can_view_dashboard |
| GET | `/ultimate/simulator` | `pulse_intelligence.register.<locals>.pulse_simulator_page` | pulse_intelligence.py:690 | can_view_dashboard |
| POST | `/ultimate/simulator/run` | `pulse_intelligence.register.<locals>.pulse_simulator_run` | pulse_intelligence.py:695 | can_view_dashboard |
| GET | `/ultimate/status` | `pulse_ultimate.register.<locals>.ultimate_public_status` | pulse_ultimate.py:1956 | — (öffentlich/keine erkennbare Prüfung) |
| GET | `/ultimate/support` | `pulse_next.register.<locals>.support` | pulse_next.py:1163 | can_view_analytics |
| GET | `/ultimate/system` | `pulse_ultimate.register.<locals>.ultimate_system` | pulse_ultimate.py:1694 | can_view_dashboard |
| GET | `/ultimate/system-twin` | `pulse_intelligence.register.<locals>.pulse_system_twin` | pulse_intelligence.py:599 | can_view_dashboard |
| POST | `/ultimate/system/api-key` | `pulse_ultimate.register.<locals>.ultimate_api_key_create` | pulse_ultimate.py:1977 | can_view_dashboard |
| POST | `/ultimate/system/api-key/{key_id}/revoke` | `pulse_ultimate.register.<locals>.ultimate_api_key_revoke` | pulse_ultimate.py:1784 | can_view_dashboard |
| GET | `/ultimate/system/api-keys` | `pulse_ultimate.register.<locals>.ultimate_api_keys` | pulse_ultimate.py:1770 | can_view_dashboard |
| POST | `/ultimate/system/branding` | `pulse_ultimate.register.<locals>.ultimate_branding` | pulse_ultimate.py:1810 | can_view_dashboard |
| GET | `/ultimate/tasks` | `pulse_next.register.<locals>.taskboard` | pulse_next.py:1193 | can_view_dashboard |
| GET | `/ultimate/team` | `pulse_ultimate.register.<locals>.ultimate_team` | pulse_ultimate.py:892 | can_view_dashboard |
| GET | `/ultimate/team-trends` | `pulse_next.register.<locals>.team_trends` | pulse_next.py:1222 | can_view_analytics |
| POST | `/ultimate/team/status` | `pulse_ultimate.register.<locals>.ultimate_team_status` | pulse_ultimate.py:1915 | can_view_dashboard |
| GET | `/ultimate/team/{user_id}` | `pulse_ultimate.register.<locals>.ultimate_member` | pulse_ultimate.py:924 | can_view_dashboard (+ can_promote, is_admin) |
| POST | `/ultimate/team/{user_id}/update` | `pulse_ultimate.register.<locals>.ultimate_member_update` | pulse_ultimate.py:980 | can_view_dashboard |
| GET | `/ultimate/workflows` | `pulse_next.register.<locals>.workflows` | pulse_next.py:1087 | can_view_dashboard, require_perm |
| POST | `/ultimate/workflows/create` | `pulse_next.register.<locals>.workflows_create` | pulse_next.py:1100 | can_view_dashboard, require_perm |

### `/voice-support` (2)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/voice-support` | `pulse_pro.register.<locals>.voice_support_v5` | pulse_pro.py:1347 | can_view_dashboard (+ can_manage_tickets, can_promote, is_admin) |
| POST | `/voice-support/save` | `pulse_pro.register.<locals>.voice_support_save` | pulse_pro.py:1492 | Admin |

### `/warns` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/warns` | `pulse_pro.register.<locals>.warns_v5` | pulse_pro.py:2103 | angemeldet (+ can_warn, is_admin) |

### `/warns.csv` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/warns.csv` | `pulse_pro.register.<locals>.warns_csv` | pulse_pro.py:2142 | angemeldet (+ can_warn, is_admin) |

### `/wiki` (3)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/wiki` | `pulse_features.register.<locals>.wiki_page` | pulse_features.py:151 | ctx (+ can_manage_wiki, can_promote, is_admin) |
| POST | `/wiki/save` | `pulse_features.register.<locals>.wiki_save` | pulse_features.py:173 | angemeldet (+ can_manage_wiki, can_promote, is_admin) |
| GET | `/wiki/{page_id}` | `pulse_features.register.<locals>.wiki_detail` | pulse_features.py:163 | ctx (+ can_manage_wiki, can_promote, is_admin) |

