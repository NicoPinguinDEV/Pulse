# Routen-Landkarte (effektiv zur Laufzeit)

Automatisch erzeugt von `python tools/route_map.py` aus dem laufenden FastAPI-Objekt (nicht von Hand bearbeiten).
Spalte *Prüfung* ist eine Quelltext-Heuristik (`auth(...)`, `cctx`, `ctx_for`, `admin_ctx`, `perm=`) – Hinweis, kein Beweis.

- **Effektive Routen (Methode × Pfad):** 392
- **Doppelt registriert:** 1 → `GET /ultimate`
- **Im Quelltext registriert, zur Laufzeit verdrängt:** 5
- **Ohne erkennbare Prüfung:** 45 (Login/OAuth/Health/Bewerbungsportal/öffentliche Seiten – einzeln prüfen)

> Achtung `remove_and_add` (`pulse_pro.py`): ersetzt nur die angegebenen HTTP-Methoden; andere Methoden desselben Pfads bleiben erhalten.

## Doppelte Registrierungen

| Methode | Pfad | aktive Handler (Reihenfolge) |
|---|---|---|
| GET | `/ultimate` | `webserver.ultimate_dashboard` (webserver.py:4528) → `pulse_ultimate.register.<locals>.ultimate_home` (pulse_ultimate.py:914) (FastAPI nimmt den **ersten** Treffer) |

## Verdrängte Handler (toter Code)

| Methode | Pfad | verdrängte Funktion | Ort | aktiv stattdessen |
|---|---|---|---|---|
| GET | `/dashboard` | `dashboard_main` | webserver.py:4249 | `pulse_pro.register.<locals>.dashboard_v5` |
| GET | `/melonly` | `melonly_alias` | webserver.py:4734 | `pulse_pro.register.<locals>.melonly_page` |
| GET | `/member/{user_id}` | `member_detail` | webserver.py:7594 | `pulse_pro.register.<locals>.member_v5` |
| GET | `/settings` | `settings_page` | webserver.py:10651 | `pulse_pro.register.<locals>.settings_v5` |
| GET | `/tickets` | `pulse_tickets_dashboard` | webserver.py:5516 | `pulse_pro.register.<locals>.tickets_v5` |

## Effektive Routen nach Bereich

### `/` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/` | `webserver.home` | webserver.py:3769 | — (öffentlich/keine erkennbare Prüfung) |

### `/achievements` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/achievements` | `pulse_pro.register.<locals>.achievements_v5` | pulse_pro.py:2839 | can_view_dashboard |

### `/action` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| POST | `/action` | `webserver.handle_action` | webserver.py:11307 | angemeldet (+ can_manage_applications, can_promote, is_admin) |

### `/activity-check` (2)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/activity-check` | `pulse_pro.register.<locals>.activity_check_page` | pulse_pro.py:1489 | can_manage_checkins |
| POST | `/activity-check/setup` | `pulse_pro.register.<locals>.activity_check_setup` | pulse_pro.py:1615 | can_manage_checkins |

### `/admin` (65)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/admin` | `pulse_admin.register.<locals>.admin_hub` | pulse_admin.py:193 | angemeldet |
| POST | `/admin/commands/sync` | `pulse_admin.register.<locals>.admin_commands_sync` | pulse_admin.py:713 | Admin |
| GET | `/admin/console` | `pulse_admin.register.<locals>.admin_console` | pulse_admin.py:814 | Admin |
| POST | `/admin/console/command` | `pulse_admin.register.<locals>.admin_console_command` | pulse_admin.py:873 | Admin |
| GET | `/admin/console/logs` | `pulse_admin.register.<locals>.admin_console_logs` | pulse_admin.py:867 | Admin |
| GET | `/admin/discord` | `pulse_admin.register.<locals>.admin_discord` | pulse_admin.py:382 | Admin |
| GET | `/admin/discord/banner/{filename}` | `pulse_admin.register.<locals>.admin_discord_banner` | pulse_admin.py:801 | Admin |
| POST | `/admin/discord/branding` | `pulse_admin.register.<locals>.admin_discord_branding` | pulse_admin.py:457 | Admin |
| POST | `/admin/discord/log-routing` | `pulse_admin.register.<locals>.admin_discord_log_routing` | pulse_admin.py:749 | Admin |
| POST | `/admin/discord/logs` | `pulse_admin.register.<locals>.admin_discord_logs` | pulse_admin.py:728 | Admin |
| POST | `/admin/discord/test` | `pulse_admin.register.<locals>.admin_discord_test` | pulse_admin.py:779 | Admin |
| POST | `/admin/discord/welcome` | `pulse_admin.register.<locals>.admin_discord_welcome` | pulse_admin.py:405 | Admin |
| GET | `/admin/features` | `pulse_phase7.register.<locals>.features_page` | pulse_phase7.py:116 | Admin |
| POST | `/admin/features/maintenance` | `pulse_phase7.register.<locals>.features_maintenance` | pulse_phase7.py:158 | Admin |
| POST | `/admin/features/toggle` | `pulse_phase7.register.<locals>.features_toggle` | pulse_phase7.py:145 | Admin |
| GET | `/admin/live-console` | `webserver.admin_live_console` | webserver.py:3591 | Admin |
| POST | `/admin/live-console/command` | `webserver.admin_live_console_command` | webserver.py:3669 | Admin |
| GET | `/admin/live-console/logs` | `webserver.admin_live_console_logs` | webserver.py:3663 | Admin |
| GET | `/admin/panels` | `pulse_admin.register.<locals>.admin_panels` | pulse_admin.py:668 | Admin |
| POST | `/admin/panels/republish` | `pulse_admin.register.<locals>.admin_panels_republish` | pulse_admin.py:694 | Admin |
| GET | `/admin/privacy` | `pulse_admin.register.<locals>.admin_privacy` | pulse_admin.py:476 | Admin |
| POST | `/admin/privacy` | `pulse_admin.register.<locals>.admin_privacy_save` | pulse_admin.py:558 | Admin |
| GET | `/admin/privacy/export` | `pulse_admin.register.<locals>.admin_privacy_export` | pulse_admin.py:651 | Admin |
| POST | `/admin/recovery/save` | `pulse_admin.register.<locals>.admin_recovery_save` | pulse_admin.py:573 | Admin |
| GET | `/admin/rights` | `pulse_phase7.register.<locals>.rights_page` | pulse_phase7.py:169 | Admin |
| GET | `/admin/security` | `pulse_guard_admin.register.<locals>.admin_security` | pulse_guard_admin.py:507 | Admin |
| POST | `/admin/security/antinuke` | `pulse_guard_admin.register.<locals>.admin_security_antinuke` | pulse_guard_admin.py:565 | Admin |
| POST | `/admin/security/antinuke/rules/delete` | `pulse_antinuke_admin.register.<locals>.antinuke_rule_delete` | pulse_antinuke_admin.py:732 | — (öffentlich/keine erkennbare Prüfung) |
| POST | `/admin/security/antinuke/rules/preset` | `pulse_antinuke_admin.register.<locals>.antinuke_rule_preset` | pulse_antinuke_admin.py:736 | Admin |
| POST | `/admin/security/antinuke/rules/save` | `pulse_antinuke_admin.register.<locals>.antinuke_rule_save` | pulse_antinuke_admin.py:658 | Admin |
| POST | `/admin/security/antinuke/rules/simulate` | `pulse_antinuke_admin.register.<locals>.antinuke_rule_simulate` | pulse_antinuke_admin.py:761 | Admin |
| POST | `/admin/security/antinuke/rules/toggle` | `pulse_antinuke_admin.register.<locals>.antinuke_rule_toggle` | pulse_antinuke_admin.py:728 | — (öffentlich/keine erkennbare Prüfung) |
| POST | `/admin/security/antinuke/trusted` | `pulse_antinuke_admin.register.<locals>.antinuke_trusted_save` | pulse_antinuke_admin.py:783 | Admin |
| POST | `/admin/security/antinuke/trusted/check` | `pulse_antinuke_admin.register.<locals>.antinuke_trusted_check` | pulse_antinuke_admin.py:811 | Admin |
| POST | `/admin/security/automod` | `pulse_guard_admin.register.<locals>.admin_security_automod` | pulse_guard_admin.py:512 | Admin |
| POST | `/admin/security/lockdown/lift` | `pulse_antinuke_admin.register.<locals>.antinuke_lockdown_lift` | pulse_antinuke_admin.py:853 | — (öffentlich/keine erkennbare Prüfung) |
| POST | `/admin/security/lockdown/start` | `pulse_antinuke_admin.register.<locals>.antinuke_lockdown_start` | pulse_antinuke_admin.py:849 | — (öffentlich/keine erkennbare Prüfung) |
| POST | `/admin/security/quarantine/dismiss` | `pulse_guard_admin.register.<locals>.admin_security_quarantine_dismiss` | pulse_guard_admin.py:702 | — (öffentlich/keine erkennbare Prüfung) |
| POST | `/admin/security/quarantine/restore` | `pulse_guard_admin.register.<locals>.admin_security_quarantine_restore` | pulse_guard_admin.py:698 | — (öffentlich/keine erkennbare Prüfung) |
| POST | `/admin/security/test` | `pulse_guard_admin.register.<locals>.admin_security_test` | pulse_guard_admin.py:631 | Admin |
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
| GET | `/admin/settings-history` | `pulse_phase7.register.<locals>.history_page` | pulse_phase7.py:246 | Admin |
| POST | `/admin/settings-history/revert` | `pulse_phase7.register.<locals>.history_revert` | pulse_phase7.py:267 | Admin |
| GET | `/admin/visibility` | `pulse_phase7.register.<locals>.visibility_page` | pulse_phase7.py:206 | Admin |
| POST | `/admin/visibility/expected` | `pulse_phase7.register.<locals>.visibility_expected` | pulse_phase7.py:230 | Admin |
| GET | `/admin/website` | `pulse_public_site.register.<locals>.website_admin` | pulse_public_site.py:635 | Admin |
| POST | `/admin/website/news` | `pulse_public_site.register.<locals>.website_news_add` | pulse_public_site.py:739 | Admin |
| POST | `/admin/website/news/{news_id}/delete` | `pulse_public_site.register.<locals>.website_news_delete` | pulse_public_site.py:750 | Admin |
| GET | `/admin/website/team` | `pulse_public_site.register.<locals>.website_team_admin` | pulse_public_site.py:670 | Admin |
| POST | `/admin/website/team` | `pulse_public_site.register.<locals>.website_team_add` | pulse_public_site.py:707 | Admin |
| POST | `/admin/website/team/{profile_id}/delete` | `pulse_public_site.register.<locals>.website_team_delete` | pulse_public_site.py:732 | Admin |
| POST | `/admin/website/team/{profile_id}/publish` | `pulse_public_site.register.<locals>.website_team_publish` | pulse_public_site.py:718 | Admin |

### `/announcements` (2)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/announcements` | `pulse_pro.register.<locals>.announcements_page` | pulse_pro.py:1800 | angemeldet (+ can_manage_announcements, can_promote, is_admin) |
| POST | `/announcements/create` | `pulse_pro.register.<locals>.create_announcement` | pulse_pro.py:1810 | angemeldet (+ can_manage_announcements, can_promote, is_admin) |

### `/api` (6)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/api/pulse/live` | `pulse_pro.register.<locals>.api_live` | pulse_pro.py:3449 | can_view_dashboard |
| GET | `/api/roblox-search` | `webserver.roblox_search` | webserver.py:3458 | can_view_dashboard |
| GET | `/api/roblox-user` | `webserver.roblox_user_lookup` | webserver.py:3394 | can_view_dashboard |
| GET | `/api/team/activity` | `webserver.team_activity_api` | webserver.py:5275 | can_view_dashboard |
| GET | `/api/team/discord-status` | `pulse_pro.register.<locals>.discord_status_api` | pulse_pro.py:1752 | angemeldet (+ can_promote, can_view_team_status, is_admin) |
| GET | `/api/team/warn-roles` | `webserver.warn_roles_api` | webserver.py:5312 | can_warn |

### `/applications` (8)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/applications` | `webserver.applications_page` | webserver.py:8442 | angemeldet (+ can_manage_applications, can_promote, can_view_dashboard, is_admin) |
| GET | `/applications/board` | `pulse_phase7.register.<locals>.board_page` | pulse_phase7.py:383 | angemeldet (+ can_manage_applications, can_promote, is_admin) |
| POST | `/applications/board/deadlines` | `pulse_phase7.register.<locals>.board_deadlines` | pulse_phase7.py:416 | angemeldet (+ can_manage_applications, can_promote, is_admin) |
| GET | `/applications/config` | `webserver.application_modules_config_page` | webserver.py:8812 | angemeldet (+ can_manage_applications, can_promote, is_admin) |
| POST | `/applications/config` | `webserver.save_application_modules_config` | webserver.py:8859 | angemeldet (+ can_manage_applications, can_promote, is_admin) |
| POST | `/applications/interview-slot` | `webserver.application_interview_slot` | webserver.py:8687 | angemeldet (+ can_manage_applications, can_promote, is_admin) |
| POST | `/applications/review` | `webserver.save_application_review` | webserver.py:8754 | angemeldet (+ can_manage_applications, can_promote, is_admin) |
| POST | `/applications/templates` | `webserver.application_templates_save` | webserver.py:8720 | Admin |

### `/apply` (4)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/apply` | `webserver.public_apply_page` | webserver.py:8155 | Session (eigene Prüfung) |
| GET | `/apply/callback` | `webserver.apply_callback` | webserver.py:8414 | Session (eigene Prüfung) |
| POST | `/apply/keepalive` | `webserver.apply_keepalive` | webserver.py:8428 | Session (eigene Prüfung) |
| GET | `/apply/login` | `webserver.apply_login` | webserver.py:8398 | — (öffentlich/keine erkennbare Prüfung) |

### `/approvals` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/approvals` | `pulse_pro.register.<locals>.approvals_page` | pulse_pro.py:1919 | can_view_dashboard (+ can_manage_applications, can_promote, is_admin) |

### `/backup` (10)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| POST | `/backup/create` | `webserver.create_backup` | webserver.py:10024 | Admin |
| POST | `/backup/delete` | `webserver.delete_backup` | webserver.py:10228 | Admin |
| GET | `/backup/download/{filename}` | `webserver.download_backup` | webserver.py:10087 | Admin |
| POST | `/backup/full/create` | `webserver.full_backup_create` | webserver.py:10354 | Admin |
| POST | `/backup/full/delete` | `webserver.full_backup_delete` | webserver.py:10449 | Admin |
| GET | `/backup/full/download/{filename}` | `webserver.full_backup_download` | webserver.py:10367 | Admin |
| GET | `/backup/full/preview/{filename}` | `webserver.full_backup_preview` | webserver.py:10376 | Admin |
| POST | `/backup/full/restore` | `webserver.full_backup_restore` | webserver.py:10422 | Admin |
| POST | `/backup/restore` | `webserver.restore_backup` | webserver.py:10109 | Admin |
| POST | `/backup/upload` | `webserver.upload_backup` | webserver.py:10036 | Admin |

### `/backups` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/backups` | `webserver.backups_page` | webserver.py:9949 | Admin |

### `/calendar` (2)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/calendar` | `pulse_pro.register.<locals>.calendar_v5` | pulse_pro.py:2312 | can_view_dashboard |
| POST | `/calendar/create` | `pulse_pro.register.<locals>.create_calendar_event_v5` | pulse_pro.py:2479 | can_view_dashboard |

### `/callback` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/callback` | `webserver.callback` | webserver.py:3861 | Session (eigene Prüfung) |

### `/cockpit` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/cockpit` | `pulse_cockpit.register.<locals>.leadership_cockpit` | pulse_cockpit.py:45 | angemeldet (+ can_promote, is_admin) |

### `/cookies` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/cookies` | `pulse_public_site.register.<locals>.add_page.<locals>.handler` | pulse_public_site.py:576 | — (öffentlich/keine erkennbare Prüfung) |

### `/dashboard` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/dashboard` | `pulse_pro.register.<locals>.dashboard_v5` | pulse_pro.py:1396 | can_view_dashboard |

### `/datenschutz` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/datenschutz` | `pulse_public_site.register.<locals>.add_page.<locals>.handler` | pulse_public_site.py:576 | — (öffentlich/keine erkennbare Prüfung) |

### `/export` (2)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/export/logs` | `webserver.export_logs` | webserver.py:5204 | can_view_analytics |
| GET | `/export/team.csv` | `pulse_pro.register.<locals>.team_export` | pulse_pro.py:3432 | can_view_analytics |

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
| GET | `/faq` | `pulse_public_site.register.<locals>.add_page.<locals>.handler` | pulse_public_site.py:576 | — (öffentlich/keine erkennbare Prüfung) |

### `/favicon.ico` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/favicon.ico` | `webserver.favicon` | webserver.py:266 | — (öffentlich/keine erkennbare Prüfung) |

### `/fraktionen` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/fraktionen` | `pulse_public_site.register.<locals>.add_page.<locals>.handler` | pulse_public_site.py:576 | — (öffentlich/keine erkennbare Prüfung) |

### `/handover` (2)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/handover` | `pulse_pro.register.<locals>.handover_page` | pulse_pro.py:1773 | can_view_dashboard (+ can_manage_handover, can_promote, can_view_dashboard, is_admin) |
| POST | `/handover/create` | `pulse_pro.register.<locals>.create_handover` | pulse_pro.py:1787 | angemeldet (+ can_manage_handover, can_view_dashboard) |

### `/health` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/health` | `pulse_pro.register.<locals>.api_health` | pulse_pro.py:3456 | — (öffentlich/keine erkennbare Prüfung) |

### `/healthz` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/healthz` | `pulse_health_api.healthz` | pulse_health_api.py:23 | — (öffentlich/keine erkennbare Prüfung) |

### `/impressum` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/impressum` | `pulse_public_site.register.<locals>.add_page.<locals>.handler` | pulse_public_site.py:576 | — (öffentlich/keine erkennbare Prüfung) |

### `/loa` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/loa` | `webserver.loa_page` | webserver.py:7892 | can_view_dashboard |

### `/log` (3)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| POST | `/log/create` | `webserver.create_log` | webserver.py:4997 | can_view_dashboard (+ can_add_notes, can_moderate_bans, can_moderate_bolo, can_moderate_kicks) |
| POST | `/log/delete` | `webserver.delete_log` | webserver.py:5092 | can_view_dashboard |
| POST | `/log/edit` | `webserver.edit_log` | webserver.py:5041 | can_view_dashboard |

### `/login` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/login` | `webserver.login` | webserver.py:3820 | — (öffentlich/keine erkennbare Prüfung) |

### `/logout` (2)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/logout` | `webserver.logout_confirm` | webserver.py:3834 | — (öffentlich/keine erkennbare Prüfung) |
| POST | `/logout` | `webserver.logout` | webserver.py:3844 | — (öffentlich/keine erkennbare Prüfung) |

### `/manifest.webmanifest` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/manifest.webmanifest` | `pulse_pro.register.<locals>.manifest` | pulse_pro.py:3464 | — (öffentlich/keine erkennbare Prüfung) |

### `/me` (8)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/me` | `pulse_home.register.<locals>.my_pulse` | pulse_home.py:60 | can_view_dashboard (+ can_manage_applications, can_manage_tickets, can_promote, is_admin) |
| GET | `/me/month` | `pulse_phase7.register.<locals>.my_month_page` | pulse_phase7.py:483 | can_view_dashboard |
| POST | `/me/month/optin` | `pulse_phase7.register.<locals>.my_month_optin` | pulse_phase7.py:512 | can_view_dashboard |
| GET | `/me/sessions` | `pulse_home.register.<locals>.my_sessions` | pulse_home.py:293 | can_view_dashboard |
| POST | `/me/sessions/revoke` | `pulse_home.register.<locals>.my_session_revoke` | pulse_home.py:322 | can_view_dashboard |
| POST | `/me/sessions/revoke-all` | `pulse_home.register.<locals>.my_session_revoke_all` | pulse_home.py:341 | can_view_dashboard |
| POST | `/me/sessions/revoke-others` | `pulse_home.register.<locals>.my_session_revoke_others` | pulse_home.py:331 | can_view_dashboard |
| POST | `/me/start-page` | `pulse_home.register.<locals>.my_start_page` | pulse_home.py:283 | can_view_dashboard |

### `/meetings` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/meetings` | `webserver.meetings_page` | webserver.py:7756 | can_view_dashboard |

### `/meetings-history` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/meetings-history` | `pulse_pro.register.<locals>.meetings_history_v5` | pulse_pro.py:2833 | can_view_dashboard |

### `/melonly` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/melonly` | `pulse_pro.register.<locals>.melonly_page` | pulse_pro.py:1085 | can_view_dashboard |

### `/member` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/member/{user_id}` | `pulse_pro.register.<locals>.member_v5` | pulse_pro.py:2557 | can_view_dashboard (+ can_add_notes, can_promote, can_warn, is_admin) |

### `/mod-cases` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/mod-cases` | `webserver.mod_cases_page` | webserver.py:6616 | — (öffentlich/keine erkennbare Prüfung) |

### `/mod-cases.csv` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/mod-cases.csv` | `webserver.mod_cases_csv` | webserver.py:6650 | — (öffentlich/keine erkennbare Prüfung) |

### `/news` (2)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/news` | `pulse_public_site.register.<locals>.add_page.<locals>.handler` | pulse_public_site.py:576 | — (öffentlich/keine erkennbare Prüfung) |
| GET | `/news/{news_id}` | `pulse_public_site.register.<locals>.public_news_detail` | pulse_public_site.py:596 | — (öffentlich/keine erkennbare Prüfung) |

### `/nutzungsbedingungen` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/nutzungsbedingungen` | `pulse_public_site.register.<locals>.add_page.<locals>.handler` | pulse_public_site.py:576 | — (öffentlich/keine erkennbare Prüfung) |

### `/promotions` (3)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/promotions` | `pulse_pro.register.<locals>.promotions_page` | pulse_pro.py:1821 | angemeldet (+ can_manage_promotions, can_promote, is_admin) |
| POST | `/promotions/create` | `pulse_pro.register.<locals>.create_promotion` | pulse_pro.py:1858 | angemeldet (+ can_manage_promotions, can_promote, is_admin) |
| POST | `/promotions/decide` | `pulse_pro.register.<locals>.decide_promotion` | pulse_pro.py:1876 | angemeldet (+ can_manage_promotions, can_promote, is_admin) |

### `/pulse-inbox` (5)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/pulse-inbox` | `pulse_pro.register.<locals>.inbox_v5` | pulse_pro.py:1436 | can_view_dashboard (+ is_admin) |
| POST | `/pulse-inbox/read` | `pulse_pro.register.<locals>.inbox_read_all` | pulse_pro.py:1482 | can_view_dashboard |
| POST | `/pulse-inbox/read/{nid}` | `pulse_pro.register.<locals>.inbox_read_one` | pulse_pro.py:1485 | can_view_dashboard |
| POST | `/pulse-inbox/server-quiet` | `pulse_pro.register.<locals>.inbox_server_quiet` | pulse_pro.py:1468 | Admin |
| POST | `/pulse-inbox/settings` | `pulse_pro.register.<locals>.inbox_settings` | pulse_pro.py:1476 | can_view_dashboard |

### `/recovery` (2)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/recovery/authorize` | `pulse_admin.register.<locals>.recovery_authorize` | pulse_admin.py:606 | — (öffentlich/keine erkennbare Prüfung) |
| POST | `/recovery/authorize/start` | `pulse_admin.register.<locals>.recovery_authorize_start` | pulse_admin.py:626 | — (öffentlich/keine erkennbare Prüfung) |

### `/regelwerk` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/regelwerk` | `pulse_public_site.register.<locals>.add_page.<locals>.handler` | pulse_public_site.py:576 | — (öffentlich/keine erkennbare Prüfung) |

### `/robots.txt` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/robots.txt` | `pulse_pro.register.<locals>.robots_txt` | pulse_pro.py:3506 | — (öffentlich/keine erkennbare Prüfung) |

### `/search` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/search` | `pulse_pro.register.<locals>.search_v5` | pulse_pro.py:2535 | can_view_dashboard |

### `/security` (2)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/security` | `webserver.security_page` | webserver.py:10550 | Admin |
| POST | `/security` | `webserver.save_security_settings` | webserver.py:10557 | Admin |

### `/settings` (14)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/settings` | `pulse_pro.register.<locals>.settings_v5` | pulse_pro.py:2864 | Admin |
| POST | `/settings/dashboard-profile/delete` | `pulse_pro.register.<locals>.settings_dashboard_profile_delete` | pulse_pro.py:3161 | Admin |
| POST | `/settings/dashboard-profile/save` | `pulse_pro.register.<locals>.settings_dashboard_profile_save` | pulse_pro.py:3106 | Admin |
| POST | `/settings/nickname-prefix/default` | `pulse_pro.register.<locals>.settings_nickname_prefix_default` | pulse_pro.py:3211 | Admin |
| POST | `/settings/nickname-prefix/delete` | `pulse_pro.register.<locals>.settings_nickname_prefix_delete` | pulse_pro.py:3235 | Admin |
| POST | `/settings/nickname-prefix/save` | `pulse_pro.register.<locals>.settings_nickname_prefix_save` | pulse_pro.py:3178 | Admin |
| POST | `/settings/nickname-prefix/sync` | `pulse_pro.register.<locals>.settings_nickname_prefix_sync` | pulse_pro.py:3259 | Admin |
| POST | `/settings/pro-clear-all` | `pulse_pro.register.<locals>.settings_clear_all_permissions` | pulse_pro.py:3315 | Admin |
| POST | `/settings/pro-goal` | `pulse_pro.register.<locals>.settings_goal` | pulse_pro.py:3346 | Admin |
| POST | `/settings/pro-presets` | `pulse_pro.register.<locals>.settings_profiles` | pulse_pro.py:3308 | Admin |
| POST | `/settings/pro-roles` | `pulse_pro.register.<locals>.settings_roles` | pulse_pro.py:3282 | Admin |
| POST | `/settings/pro-save` | `pulse_pro.register.<locals>.settings_save` | pulse_pro.py:3274 | Admin |
| POST | `/settings/pro-warn-roles` | `pulse_pro.register.<locals>.settings_warn_roles` | pulse_pro.py:3320 | Admin |
| POST | `/settings/pro-warn-sync` | `pulse_pro.register.<locals>.settings_warn_sync` | pulse_pro.py:3339 | Admin |

### `/setup` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/setup` | `webserver.setup_wizard` | webserver.py:10466 | Admin |

### `/shift` (3)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| POST | `/shift/action` | `webserver.handle_shift_action` | webserver.py:4967 | can_view_dashboard |
| POST | `/shift/admin-end` | `pulse_pro.register.<locals>.admin_end_shift` | pulse_pro.py:1347 | can_view_dashboard (+ can_promote, is_admin) |
| POST | `/shift/history/confirm-duration` | `pulse_pro.register.<locals>.confirm_auto_closed_shift_duration` | pulse_pro.py:1293 | can_view_dashboard (+ can_promote, is_admin) |

### `/sicherheit` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/sicherheit` | `pulse_public_site.register.<locals>.add_page.<locals>.handler` | pulse_public_site.py:576 | — (öffentlich/keine erkennbare Prüfung) |

### `/sitemap.xml` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/sitemap.xml` | `pulse_public_site.register.<locals>.sitemap` | pulse_public_site.py:613 | — (öffentlich/keine erkennbare Prüfung) |

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

### `/static` (3)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/static/public.css` | `pulse_public_site.register.<locals>.add_static.<locals>.handler` | pulse_public_site.py:622 | — (öffentlich/keine erkennbare Prüfung) |
| GET | `/static/public.js` | `pulse_public_site.register.<locals>.add_static.<locals>.handler` | pulse_public_site.py:622 | — (öffentlich/keine erkennbare Prüfung) |
| GET | `/static/pulse.css` | `webserver.static_pulse_css` | webserver.py:3761 | — (öffentlich/keine erkennbare Prüfung) |

### `/stats` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/stats` | `pulse_pro.register.<locals>.stats_v5` | pulse_pro.py:2295 | can_view_analytics |

### `/status` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/status` | `pulse_phase7.register.<locals>.public_status_page` | pulse_phase7.py:452 | — (öffentlich/keine erkennbare Prüfung) |

### `/status.json` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/status.json` | `pulse_phase7.register.<locals>.public_status_json` | pulse_phase7.py:444 | — (öffentlich/keine erkennbare Prüfung) |

### `/suite` (34)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/suite` | `cogs.pulse_suite.register_routes.<locals>.leadership` | cogs/pulse_suite.py:320 | can_view_dashboard |
| GET | `/suite/achievements` | `cogs.pulse_suite.register_routes.<locals>.achievements_page` | cogs/pulse_suite.py:681 | can_view_dashboard |
| GET | `/suite/api/search` | `cogs.pulse_suite.register_routes.<locals>.search_api` | cogs/pulse_suite.py:663 | can_view_dashboard |
| GET | `/suite/applications` | `cogs.pulse_suite.register_routes.<locals>.applications_page` | cogs/pulse_suite.py:436 | can_view_dashboard |
| GET | `/suite/applications/{app_id}` | `cogs.pulse_suite.register_routes.<locals>.application_page` | cogs/pulse_suite.py:443 | can_view_dashboard |
| POST | `/suite/applications/{app_id}/update` | `cogs.pulse_suite.register_routes.<locals>.application_update` | cogs/pulse_suite.py:452 | can_view_dashboard |
| GET | `/suite/audit` | `cogs.pulse_suite.register_routes.<locals>.audit_page` | cogs/pulse_suite.py:606 | can_view_dashboard |
| GET | `/suite/audit.csv` | `cogs.pulse_suite.register_routes.<locals>.audit_csv` | cogs/pulse_suite.py:646 | can_view_dashboard |
| GET | `/suite/calendar` | `cogs.pulse_suite.register_routes.<locals>.calendar_page` | cogs/pulse_suite.py:417 | can_view_dashboard |
| POST | `/suite/calendar/create` | `cogs.pulse_suite.register_routes.<locals>.calendar_create` | cogs/pulse_suite.py:430 | can_view_dashboard |
| GET | `/suite/cases` | `cogs.pulse_suite.register_routes.<locals>.cases_page` | cogs/pulse_suite.py:667 | can_view_dashboard |
| POST | `/suite/cases/{case_id}/action` | `cogs.pulse_suite.register_routes.<locals>.case_action` | cogs/pulse_suite.py:674 | can_view_dashboard |
| GET | `/suite/interview/create` | `cogs.pulse_suite.register_routes.<locals>.interview_form` | cogs/pulse_suite.py:474 | can_view_dashboard |
| POST | `/suite/interview/create` | `cogs.pulse_suite.register_routes.<locals>.interview_create` | cogs/pulse_suite.py:480 | can_view_dashboard |
| GET | `/suite/interviews` | `cogs.pulse_suite.register_routes.<locals>.interviews_page` | cogs/pulse_suite.py:466 | can_view_dashboard |
| GET | `/suite/member/{member_id}` | `cogs.pulse_suite.register_routes.<locals>.member_page` | cogs/pulse_suite.py:362 | can_view_dashboard |
| POST | `/suite/member/{member_id}/note` | `cogs.pulse_suite.register_routes.<locals>.member_note` | cogs/pulse_suite.py:376 | can_view_dashboard |
| GET | `/suite/my-schedule` | `cogs.pulse_suite.register_routes.<locals>.my_schedule` | cogs/pulse_suite.py:393 | can_view_dashboard |
| GET | `/suite/news` | `cogs.pulse_suite.register_routes.<locals>.news_page` | cogs/pulse_suite.py:598 | can_view_dashboard |
| POST | `/suite/news/create` | `cogs.pulse_suite.register_routes.<locals>.news_create` | cogs/pulse_suite.py:602 | can_view_dashboard |
| GET | `/suite/promotions` | `cogs.pulse_suite.register_routes.<locals>.promotions_page` | cogs/pulse_suite.py:495 | can_view_dashboard |
| POST | `/suite/promotions/create` | `cogs.pulse_suite.register_routes.<locals>.promotions_create` | cogs/pulse_suite.py:502 | can_view_dashboard |
| POST | `/suite/promotions/{pid}/action` | `cogs.pulse_suite.register_routes.<locals>.promotions_action` | cogs/pulse_suite.py:512 | can_view_dashboard |
| GET | `/suite/schedule` | `cogs.pulse_suite.register_routes.<locals>.schedule_page` | cogs/pulse_suite.py:401 | can_view_dashboard |
| POST | `/suite/schedule/create` | `cogs.pulse_suite.register_routes.<locals>.schedule_create` | cogs/pulse_suite.py:409 | can_view_dashboard |
| GET | `/suite/search` | `cogs.pulse_suite.register_routes.<locals>.search_page` | cogs/pulse_suite.py:658 | can_view_dashboard |
| GET | `/suite/task/{task_id}` | `cogs.pulse_suite.register_routes.<locals>.task_detail` | cogs/pulse_suite.py:564 | can_view_dashboard |
| POST | `/suite/task/{task_id}/meta` | `cogs.pulse_suite.register_routes.<locals>.task_meta` | cogs/pulse_suite.py:573 | can_view_dashboard |
| GET | `/suite/tasks` | `cogs.pulse_suite.register_routes.<locals>.tasks_page` | cogs/pulse_suite.py:553 | can_view_dashboard |
| POST | `/suite/tasks/{task_id}/move` | `cogs.pulse_suite.register_routes.<locals>.task_move` | cogs/pulse_suite.py:589 | can_view_dashboard |
| GET | `/suite/team` | `cogs.pulse_suite.register_routes.<locals>.team_page` | cogs/pulse_suite.py:348 | can_view_dashboard |
| GET | `/suite/time` | `cogs.pulse_suite.register_routes.<locals>.time_page` | cogs/pulse_suite.py:385 | can_view_dashboard |
| GET | `/suite/training` | `cogs.pulse_suite.register_routes.<locals>.training_page` | cogs/pulse_suite.py:537 | can_view_dashboard |
| POST | `/suite/training/assign` | `cogs.pulse_suite.register_routes.<locals>.training_assign` | cogs/pulse_suite.py:545 | can_view_dashboard |

### `/sw.js` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/sw.js` | `pulse_pro.register.<locals>.service_worker` | pulse_pro.py:3467 | — (öffentlich/keine erkennbare Prüfung) |

### `/system` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/system` | `pulse_pro.register.<locals>.system_v5` | pulse_pro.py:3349 | Admin |

### `/tasks` (3)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/tasks` | `pulse_pro.register.<locals>.tasks_v5` | pulse_pro.py:1929 | can_view_dashboard (+ can_manage_tasks, can_promote, is_admin) |
| POST | `/tasks/create` | `pulse_features.register.<locals>.task_create` | pulse_features.py:23 | angemeldet (+ can_manage_tasks, can_promote, is_admin) |
| POST | `/tasks/update` | `pulse_pro.register.<locals>.update_task_v5` | pulse_pro.py:1946 | can_view_dashboard (+ can_manage_tasks, can_promote, is_admin) |

### `/team` (10)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/team` | `webserver.team_list_page` | webserver.py:6939 | can_view_dashboard (+ can_promote, is_admin) |
| GET | `/team/capacity` | `pulse_phase7.register.<locals>.capacity_page` | pulse_phase7.py:283 | angemeldet (+ can_promote, can_view_analytics, is_admin) |
| GET | `/team/checklists` | `webserver.team_checklists_page` | webserver.py:6526 | — (öffentlich/keine erkennbare Prüfung) |
| POST | `/team/checklists/mentor` | `webserver.team_checklists_mentor` | webserver.py:6587 | — (öffentlich/keine erkennbare Prüfung) |
| POST | `/team/checklists/toggle` | `webserver.team_checklists_toggle` | webserver.py:6571 | — (öffentlich/keine erkennbare Prüfung) |
| GET | `/team/readiness` | `pulse_phase7.register.<locals>.readiness_page` | pulse_phase7.py:318 | angemeldet (+ can_promote, can_warn, is_admin) |
| POST | `/team/readiness/criteria` | `pulse_phase7.register.<locals>.readiness_criteria` | pulse_phase7.py:365 | angemeldet (+ can_promote, is_admin) |
| POST | `/team/voice-support/audio` | `webserver.manage_voice_support_audio` | webserver.py:7329 | can_promote |
| GET | `/team/{user_id}/roles` | `webserver.team_role_manager` | webserver.py:7446 | can_promote (+ is_admin) |
| POST | `/team/{user_id}/roles` | `webserver.team_role_manager_save` | webserver.py:7477 | can_promote (+ is_admin) |

### `/team-login` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/team-login` | `webserver.team_login_page` | webserver.py:3781 | — (öffentlich/keine erkennbare Prüfung) |

### `/team-status` (2)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/team-status` | `pulse_pro.register.<locals>.team_status_page` | pulse_pro.py:1699 | can_view_dashboard (+ can_promote, can_view_team_status, is_admin) |
| POST | `/team-status/set` | `pulse_pro.register.<locals>.set_team_status` | pulse_pro.py:1769 | can_view_dashboard |

### `/team-vorstellung` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/team-vorstellung` | `pulse_public_site.register.<locals>.add_page.<locals>.handler` | pulse_public_site.py:576 | — (öffentlich/keine erkennbare Prüfung) |

### `/ticket` (2)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/ticket/transcript/{filename}` | `pulse_features.register.<locals>.ticket_transcript` | pulse_features.py:33 | ctx (+ can_manage_tickets, is_admin) |
| GET | `/ticket/{ticket_id}` | `pulse_pro.register.<locals>.ticket_detail_v5` | pulse_pro.py:2824 | can_manage_tickets |

### `/tickets` (20)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/tickets` | `pulse_pro.register.<locals>.tickets_v5` | pulse_pro.py:1960 | can_view_dashboard |
| POST | `/tickets/action` | `webserver.pulse_tickets_dashboard_action` | webserver.py:6739 | can_manage_tickets (+ can_manage_tickets, is_admin) |
| POST | `/tickets/automation` | `webserver.pulse_ticket_automation_save` | webserver.py:6181 | Admin |
| POST | `/tickets/categories/delete` | `webserver.pulse_ticket_category_delete` | webserver.py:6434 | Admin |
| POST | `/tickets/categories/save` | `webserver.pulse_ticket_category_save` | webserver.py:6256 | Admin |
| GET | `/tickets/panels` | `webserver.pulse_tickets_dashboard` | webserver.py:5514 | can_manage_tickets (+ is_admin) |
| POST | `/tickets/panels/delete` | `webserver.pulse_ticket_panel_delete` | webserver.py:6224 | Admin |
| POST | `/tickets/panels/duplicate` | `webserver.pulse_ticket_panel_duplicate` | webserver.py:6163 | Admin |
| GET | `/tickets/panels/media/{filename}` | `webserver.pulse_ticket_panel_media` | webserver.py:6151 | Admin |
| POST | `/tickets/panels/publish` | `webserver.pulse_ticket_panel_publish` | webserver.py:6201 | Admin |
| POST | `/tickets/panels/save` | `webserver.pulse_ticket_panel_save` | webserver.py:5982 | Admin |
| POST | `/tickets/rate` | `pulse_features.register.<locals>.ticket_rate` | pulse_features.py:43 | ctx |
| POST | `/tickets/settings` | `webserver.pulse_tickets_dashboard_settings` | webserver.py:5888 | Admin |
| POST | `/tickets/setup-panels` | `webserver.pulse_ticket_panels_setup` | webserver.py:5959 | Admin |
| GET | `/tickets/tools` | `webserver.pulse_ticket_tools` | webserver.py:6664 | can_manage_tickets (+ is_admin) |
| POST | `/tickets/tools/replies` | `webserver.pulse_ticket_tools_replies` | webserver.py:6712 | Admin |
| POST | `/tickets/tools/unblock` | `webserver.pulse_ticket_tools_unblock` | webserver.py:6728 | can_manage_tickets |
| GET | `/tickets/transcript/{ticket_id}` | `webserver.pulse_ticket_transcript` | webserver.py:6470 | can_manage_tickets |
| GET | `/tickets/transcript/{ticket_id}/view` | `webserver.pulse_ticket_transcript_view` | webserver.py:6495 | can_manage_tickets |
| POST | `/tickets/update` | `pulse_pro.register.<locals>.ticket_update_v5` | pulse_pro.py:1966 | can_view_dashboard (+ can_manage_tickets, can_promote, is_admin) |

### `/training` (4)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/training` | `pulse_features.register.<locals>.training_page` | pulse_features.py:83 | ctx (+ can_manage_training, can_promote, is_admin) |
| POST | `/training/create` | `pulse_features.register.<locals>.training_create` | pulse_features.py:94 | angemeldet (+ can_manage_training, can_promote, is_admin) |
| GET | `/training/{training_id}` | `pulse_features.register.<locals>.training_detail` | pulse_features.py:110 | ctx |
| POST | `/training/{training_id}/submit` | `pulse_features.register.<locals>.training_submit` | pulse_features.py:118 | ctx |

### `/ultimate` (103)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/ultimate` | `webserver.ultimate_dashboard` | webserver.py:4528 | can_view_dashboard (+ can_promote, can_warn, is_admin) |
| GET | `/ultimate` | `pulse_ultimate.register.<locals>.ultimate_home` | pulse_ultimate.py:914 | can_view_dashboard |
| GET | `/ultimate/achievements` | `pulse_next.register.<locals>.achievements` | pulse_next.py:1035 | can_view_dashboard |
| GET | `/ultimate/analytics` | `pulse_ultimate.register.<locals>.ultimate_analytics` | pulse_ultimate.py:2348 | can_view_analytics |
| GET | `/ultimate/announcements` | `pulse_next.register.<locals>.announcements` | pulse_next.py:926 | can_view_dashboard |
| POST | `/ultimate/announcements/create` | `pulse_next.register.<locals>.announcements_create` | pulse_next.py:949 | can_view_dashboard |
| POST | `/ultimate/announcements/{announcement_id}/read` | `pulse_next.register.<locals>.announcement_read` | pulse_next.py:960 | can_view_dashboard |
| GET | `/ultimate/api/case/{case_id}` | `pulse_ultimate.register.<locals>.ultimate_api_case` | pulse_ultimate.py:2320 | can_view_dashboard |
| GET | `/ultimate/api/copilot` | `pulse_intelligence.register.<locals>.pulse_copilot_api` | pulse_intelligence.py:595 | can_view_dashboard |
| POST | `/ultimate/api/feedback/{feedback_id}` | `pulse_ultimate.register.<locals>.ultimate_api_feedback` | pulse_ultimate.py:2331 | can_view_dashboard |
| GET | `/ultimate/api/monitor` | `pulse_ultimate.register.<locals>.ultimate_api_monitor` | pulse_ultimate.py:2530 | can_view_dashboard |
| GET | `/ultimate/api/overview` | `pulse_ultimate.register.<locals>.ultimate_api_overview` | pulse_ultimate.py:2283 | can_view_dashboard |
| GET | `/ultimate/api/system-twin` | `pulse_intelligence.register.<locals>.pulse_system_twin_api` | pulse_intelligence.py:652 | can_view_dashboard |
| GET | `/ultimate/api/team` | `pulse_ultimate.register.<locals>.ultimate_api_team` | pulse_ultimate.py:2303 | can_view_dashboard |
| GET | `/ultimate/api/v2/overview` | `pulse_ultimate.register.<locals>.ultimate_api_v2_overview` | pulse_ultimate.py:2478 | API-Schlüssel (X-Pulse-API-Key) |
| GET | `/ultimate/api/v2/team` | `pulse_ultimate.register.<locals>.ultimate_api_v2_team` | pulse_ultimate.py:2500 | API-Schlüssel (X-Pulse-API-Key) |
| GET | `/ultimate/approvals` | `pulse_next.register.<locals>.approvals` | pulse_next.py:845 | can_view_dashboard, require_perm |
| POST | `/ultimate/approvals/{approval_id}/decide` | `pulse_next.register.<locals>.approval_decide` | pulse_next.py:876 | can_view_dashboard |
| GET | `/ultimate/automations` | `pulse_ultimate.register.<locals>.ultimate_automations` | pulse_ultimate.py:1841 | can_view_dashboard |
| POST | `/ultimate/automations/create` | `pulse_ultimate.register.<locals>.ultimate_automation_create` | pulse_ultimate.py:2056 | can_view_dashboard |
| POST | `/ultimate/automations/save` | `pulse_ultimate.register.<locals>.ultimate_automation_save` | pulse_ultimate.py:2018 | can_view_dashboard |
| POST | `/ultimate/automations/{automation_id}/delete` | `pulse_ultimate.register.<locals>.ultimate_automation_delete` | pulse_ultimate.py:2110 | can_view_dashboard |
| POST | `/ultimate/automations/{automation_id}/run` | `pulse_ultimate.register.<locals>.ultimate_automation_run` | pulse_ultimate.py:2146 | — (öffentlich/keine erkennbare Prüfung) |
| POST | `/ultimate/automations/{automation_id}/test` | `pulse_ultimate.register.<locals>.ultimate_automation_test` | pulse_ultimate.py:2142 | — (öffentlich/keine erkennbare Prüfung) |
| POST | `/ultimate/automations/{automation_id}/toggle` | `pulse_ultimate.register.<locals>.ultimate_automation_toggle` | pulse_ultimate.py:2087 | can_view_dashboard |
| GET | `/ultimate/awards` | `pulse_ultimate.register.<locals>.ultimate_awards` | pulse_ultimate.py:1706 | can_view_dashboard |
| POST | `/ultimate/awards/create` | `pulse_ultimate.register.<locals>.ultimate_award_create` | pulse_ultimate.py:1720 | can_view_dashboard |
| GET | `/ultimate/backup` | `pulse_next.register.<locals>.backup` | pulse_next.py:1169 | can_view_dashboard |
| POST | `/ultimate/backup/create` | `pulse_next.register.<locals>.backup_create` | pulse_next.py:1177 | can_view_dashboard |
| GET | `/ultimate/backup/{name}` | `pulse_next.register.<locals>.backup_download` | pulse_next.py:1183 | can_view_dashboard (+ is_admin) |
| POST | `/ultimate/backup/{name}/restore` | `pulse_next.register.<locals>.backup_restore` | pulse_next.py:1192 | can_view_dashboard (+ is_admin) |
| GET | `/ultimate/calendar` | `pulse_ultimate.register.<locals>.ultimate_calendar` | pulse_ultimate.py:1469 | can_view_dashboard |
| POST | `/ultimate/calendar/create` | `pulse_ultimate.register.<locals>.ultimate_calendar_create` | pulse_ultimate.py:1490 | can_view_dashboard |
| GET | `/ultimate/calendar/{event_id}/delete` | `pulse_ultimate.register.<locals>.ultimate_calendar_delete_link` | pulse_ultimate.py:1513 | can_view_dashboard |
| POST | `/ultimate/calendar/{event_id}/delete` | `pulse_ultimate.register.<locals>.ultimate_calendar_delete` | pulse_ultimate.py:1519 | can_view_dashboard |
| GET | `/ultimate/cases` | `pulse_ultimate.register.<locals>.ultimate_cases` | pulse_ultimate.py:1128 | can_view_dashboard |
| POST | `/ultimate/cases/create` | `pulse_ultimate.register.<locals>.ultimate_case_create` | pulse_ultimate.py:1151 | can_view_dashboard |
| GET | `/ultimate/cases/{case_id}` | `pulse_ultimate.register.<locals>.ultimate_case_detail` | pulse_ultimate.py:1180 | can_view_dashboard |
| GET | `/ultimate/cases/{case_id}/attachments/{attachment_id}` | `pulse_ultimate.register.<locals>.ultimate_attachment_download` | pulse_ultimate.py:2248 | can_view_dashboard |
| POST | `/ultimate/cases/{case_id}/players/add` | `pulse_ultimate.register.<locals>.ultimate_case_player_add` | pulse_ultimate.py:1345 | can_view_dashboard |
| POST | `/ultimate/cases/{case_id}/update` | `pulse_ultimate.register.<locals>.ultimate_case_update` | pulse_ultimate.py:1225 | can_view_dashboard |
| GET | `/ultimate/cases/{case_id}/upload` | `pulse_ultimate.register.<locals>.ultimate_upload_page` | pulse_ultimate.py:1247 | can_view_dashboard |
| POST | `/ultimate/cases/{case_id}/upload` | `pulse_ultimate.register.<locals>.ultimate_upload` | pulse_ultimate.py:1254 | can_view_dashboard |
| POST | `/ultimate/certificates/create` | `pulse_ultimate.register.<locals>.ultimate_certificate_create` | pulse_ultimate.py:2398 | can_view_dashboard |
| GET | `/ultimate/copilot` | `pulse_intelligence.register.<locals>.pulse_copilot_page` | pulse_intelligence.py:584 | can_view_dashboard |
| POST | `/ultimate/copilot` | `pulse_intelligence.register.<locals>.pulse_copilot_ask` | pulse_intelligence.py:589 | can_view_dashboard |
| GET | `/ultimate/departments` | `pulse_ultimate.register.<locals>.ultimate_departments` | pulse_ultimate.py:1372 | can_view_dashboard |
| POST | `/ultimate/departments/create` | `pulse_ultimate.register.<locals>.ultimate_department_create` | pulse_ultimate.py:1392 | can_view_dashboard |
| GET | `/ultimate/departments/{department_id}` | `pulse_ultimate.register.<locals>.ultimate_department_detail` | pulse_ultimate.py:1413 | can_view_dashboard |
| POST | `/ultimate/departments/{department_id}/add` | `pulse_ultimate.register.<locals>.ultimate_department_add` | pulse_ultimate.py:1435 | can_view_dashboard |
| POST | `/ultimate/departments/{department_id}/remove` | `pulse_ultimate.register.<locals>.ultimate_department_remove` | pulse_ultimate.py:1454 | can_view_dashboard |
| GET | `/ultimate/export.json` | `pulse_next.register.<locals>.export_json` | pulse_next.py:1353 | can_view_analytics |
| GET | `/ultimate/feedback` | `pulse_ultimate.register.<locals>.ultimate_feedback` | pulse_ultimate.py:1609 | can_view_dashboard (+ can_promote, is_admin) |
| POST | `/ultimate/feedback/create` | `pulse_ultimate.register.<locals>.ultimate_feedback_create` | pulse_ultimate.py:1630 | can_view_dashboard |
| GET | `/ultimate/goals` | `pulse_next.register.<locals>.goals` | pulse_next.py:999 | can_view_dashboard |
| POST | `/ultimate/goals/create` | `pulse_next.register.<locals>.goals_create` | pulse_next.py:1016 | can_view_dashboard |
| POST | `/ultimate/goals/{goal_id}/update` | `pulse_next.register.<locals>.goals_update` | pulse_next.py:1024 | can_view_dashboard |
| GET | `/ultimate/handovers` | `pulse_ultimate.register.<locals>.ultimate_handovers` | pulse_ultimate.py:1648 | can_view_dashboard |
| POST | `/ultimate/handovers/create` | `pulse_ultimate.register.<locals>.ultimate_handover_create` | pulse_ultimate.py:1669 | can_view_dashboard |
| POST | `/ultimate/handovers/{handover_id}/close` | `pulse_ultimate.register.<locals>.ultimate_handover_close` | pulse_ultimate.py:1690 | can_view_dashboard (+ can_promote, is_admin) |
| GET | `/ultimate/health-detail` | `pulse_next.register.<locals>.health_detail` | pulse_next.py:1242 | can_view_dashboard |
| GET | `/ultimate/ideas` | `pulse_next.register.<locals>.ideas` | pulse_next.py:968 | can_view_dashboard |
| POST | `/ultimate/ideas/create` | `pulse_next.register.<locals>.ideas_create` | pulse_next.py:983 | can_view_dashboard |
| POST | `/ultimate/ideas/{idea_id}/vote` | `pulse_next.register.<locals>.idea_vote` | pulse_next.py:991 | can_view_dashboard |
| GET | `/ultimate/metrics` | `pulse_ultimate.register.<locals>.ultimate_metrics` | pulse_ultimate.py:2519 | can_view_analytics |
| GET | `/ultimate/onboarding` | `pulse_next.register.<locals>.onboarding` | pulse_next.py:1109 | can_view_dashboard |
| GET | `/ultimate/orgchart` | `pulse_next.register.<locals>.orgchart` | pulse_next.py:1124 | can_view_dashboard |
| GET | `/ultimate/permissions` | `pulse_next.register.<locals>.permissions` | pulse_next.py:1068 | can_view_dashboard |
| POST | `/ultimate/permissions/save` | `pulse_next.register.<locals>.permissions_save` | pulse_next.py:1080 | can_view_dashboard (+ is_admin) |
| GET | `/ultimate/person/{user_id}` | `pulse_next.register.<locals>.person` | pulse_next.py:779 | can_view_dashboard (+ can_promote) |
| GET | `/ultimate/person/{user_id}/export` | `pulse_next.register.<locals>.person_export` | pulse_next.py:833 | can_view_dashboard (+ can_promote, is_admin) |
| GET | `/ultimate/polls` | `pulse_ultimate.register.<locals>.ultimate_polls` | pulse_ultimate.py:1527 | can_view_dashboard (+ can_promote, is_admin) |
| POST | `/ultimate/polls/create` | `pulse_ultimate.register.<locals>.ultimate_poll_create` | pulse_ultimate.py:1556 | can_view_dashboard |
| POST | `/ultimate/polls/{poll_id}/vote` | `pulse_ultimate.register.<locals>.ultimate_poll_vote` | pulse_ultimate.py:1576 | can_view_dashboard |
| POST | `/ultimate/promotions/create` | `pulse_next.register.<locals>.promotions_create` | pulse_next.py:859 | can_view_dashboard |
| GET | `/ultimate/reports` | `pulse_next.register.<locals>.reports` | pulse_next.py:1146 | can_view_analytics |
| GET | `/ultimate/reports.csv` | `pulse_next.register.<locals>.reports_csv` | pulse_next.py:1161 | can_view_analytics |
| GET | `/ultimate/roblox` | `pulse_ultimate.register.<locals>.ultimate_roblox` | pulse_ultimate.py:1298 | can_view_dashboard |
| POST | `/ultimate/roblox/save` | `pulse_ultimate.register.<locals>.ultimate_roblox_save` | pulse_ultimate.py:1321 | can_view_dashboard |
| GET | `/ultimate/sandbox` | `pulse_intelligence.register.<locals>.pulse_sandbox_page` | pulse_intelligence.py:744 | can_view_dashboard |
| POST | `/ultimate/sandbox` | `pulse_intelligence.register.<locals>.pulse_sandbox_validate` | pulse_intelligence.py:749 | can_view_dashboard |
| GET | `/ultimate/scoreboard` | `pulse_next.register.<locals>.scoreboard` | pulse_next.py:1266 | can_view_analytics |
| GET | `/ultimate/search` | `pulse_next.register.<locals>.global_search` | pulse_next.py:903 | can_view_dashboard |
| GET | `/ultimate/security` | `pulse_next.register.<locals>.security_center` | pulse_next.py:1368 | can_view_dashboard |
| GET | `/ultimate/shifts` | `pulse_next.register.<locals>.shifts` | pulse_next.py:1230 | can_view_dashboard |
| GET | `/ultimate/simulator` | `pulse_intelligence.register.<locals>.pulse_simulator_page` | pulse_intelligence.py:692 | can_view_dashboard |
| POST | `/ultimate/simulator/run` | `pulse_intelligence.register.<locals>.pulse_simulator_run` | pulse_intelligence.py:697 | can_view_dashboard |
| GET | `/ultimate/status` | `pulse_ultimate.register.<locals>.ultimate_public_status` | pulse_ultimate.py:2421 | — (öffentlich/keine erkennbare Prüfung) |
| GET | `/ultimate/support` | `pulse_next.register.<locals>.support` | pulse_next.py:1278 | can_view_analytics |
| GET | `/ultimate/system` | `pulse_ultimate.register.<locals>.ultimate_system` | pulse_ultimate.py:2150 | can_view_dashboard |
| GET | `/ultimate/system-twin` | `pulse_intelligence.register.<locals>.pulse_system_twin` | pulse_intelligence.py:601 | can_view_dashboard |
| POST | `/ultimate/system/api-key` | `pulse_ultimate.register.<locals>.ultimate_api_key_create` | pulse_ultimate.py:2445 | can_view_dashboard (+ is_admin) |
| POST | `/ultimate/system/api-key/{key_id}/revoke` | `pulse_ultimate.register.<locals>.ultimate_api_key_revoke` | pulse_ultimate.py:2240 | can_view_dashboard |
| GET | `/ultimate/system/api-keys` | `pulse_ultimate.register.<locals>.ultimate_api_keys` | pulse_ultimate.py:2226 | can_view_dashboard |
| POST | `/ultimate/system/branding` | `pulse_ultimate.register.<locals>.ultimate_branding` | pulse_ultimate.py:2271 | can_view_dashboard |
| GET | `/ultimate/tasks` | `pulse_next.register.<locals>.taskboard` | pulse_next.py:1308 | can_view_dashboard |
| GET | `/ultimate/team` | `pulse_ultimate.register.<locals>.ultimate_team` | pulse_ultimate.py:1012 | can_view_dashboard |
| GET | `/ultimate/team-trends` | `pulse_next.register.<locals>.team_trends` | pulse_next.py:1337 | can_view_analytics |
| POST | `/ultimate/team/status` | `pulse_ultimate.register.<locals>.ultimate_team_status` | pulse_ultimate.py:2378 | can_view_dashboard |
| GET | `/ultimate/team/{user_id}` | `pulse_ultimate.register.<locals>.ultimate_member` | pulse_ultimate.py:1048 | can_view_dashboard (+ can_promote, is_admin) |
| POST | `/ultimate/team/{user_id}/update` | `pulse_ultimate.register.<locals>.ultimate_member_update` | pulse_ultimate.py:1104 | can_view_dashboard |
| GET | `/ultimate/workflows` | `pulse_next.register.<locals>.workflows` | pulse_next.py:1202 | can_view_dashboard |
| POST | `/ultimate/workflows/create` | `pulse_next.register.<locals>.workflows_create` | pulse_next.py:1215 | can_view_dashboard |

### `/voice-support` (2)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/voice-support` | `pulse_pro.register.<locals>.voice_support_v5` | pulse_pro.py:1975 | can_view_dashboard (+ can_manage_tickets, can_promote, is_admin) |
| POST | `/voice-support/save` | `pulse_pro.register.<locals>.voice_support_save` | pulse_pro.py:2120 | Admin |

### `/warns` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/warns` | `pulse_pro.register.<locals>.warns_v5` | pulse_pro.py:2770 | angemeldet (+ can_warn, is_admin) |

### `/warns.csv` (1)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/warns.csv` | `pulse_pro.register.<locals>.warns_csv` | pulse_pro.py:2809 | angemeldet (+ can_warn, is_admin) |

### `/wiki` (3)

| Methode | Pfad | Handler | Ort | Prüfung |
|---|---|---|---|---|
| GET | `/wiki` | `pulse_features.register.<locals>.wiki_page` | pulse_features.py:53 | ctx (+ can_manage_wiki, can_promote, is_admin) |
| POST | `/wiki/save` | `pulse_features.register.<locals>.wiki_save` | pulse_features.py:75 | angemeldet (+ can_manage_wiki, can_promote, is_admin) |
| GET | `/wiki/{page_id}` | `pulse_features.register.<locals>.wiki_detail` | pulse_features.py:65 | ctx (+ can_manage_wiki, can_promote, is_admin) |

