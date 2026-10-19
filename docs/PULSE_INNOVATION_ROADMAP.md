# Pulse Innovation Roadmap

Stand: 19.10.2026 · Version laut `pulse_version.py`: 7.9.7 · Commit-Basis `cb1fc69`.
Kennzeichnung: **[Nachgewiesen]** = im Code/Laufzeitlog belegt · **[Hypothese]** = begründete Vermutung · **[Idee]** = geplant/vorgeschlagen · **[Implementiert]** = umgesetzt und getestet.

## Funktionslandkarte (Kurzfassung)

| Bereich | Einstieg | Kern-Module | Tests |
|---|---|---|---|
| Start | `main.py` | lädt `.env`, alle `cogs/*.py`, Slash-Sync, `pulse_ultimate`, Uvicorn | `smoke_test.py` |
| Dashboard | `webserver.py` (+ `pulse_pro`, `pulse_features`, `pulse_home`, `pulse_cockpit`, `pulse_admin`, `pulse_social_dashboard`, `pulse_ticket_center`) | OAuth, `auth()`/`compute_perms` (deny-by-default) | `feature_smoke.py`, `test_pulse_admin_area.py` |
| Verwaltung | `/admin`, `/admin/discord` | `pulse_admin.py` | `test_pulse_admin_area.py` |
| Discord-Design | `pulse_embed_design.py` | Palette `discord_branding` | `test_pulse_embed_design.py` |
| Willkommen/Abschied | `cogs/welcome.py` | `pulse_welcome.py` | `test_pulse_welcome.py` |
| Verifikation | `cogs/verification.py` | `pulse_verification.py` | `test_pulse_verification.py` |
| Tickets | `cogs/tickets.py`, `/tickets` | `pulse_ticket_*` | `test_pulse_ticket_*` |
| Voice-Support | `cogs/voice_support.py` | `pulse_voice_support_utils.py` | `test_voice_support_*` |
| Abmeldungen | `cogs/abmeldung.py`, `/loa` | `pulse_absence_repair.py` | `test_pulse_absence_repair.py` |
| Namenspräfixe | `cogs/role_nickname_prefixes.py` | `pulse_nickname_utils.py` | `test_role_nickname_utils.py` |
| Persistenz | `pulse_db.py` (SQLite `pulse.db`), `abmeldungen.db`, JSON-Dateien | | |

## Implementiert

| Prio | Bereich | Problem | Lösung | Tests |
|---|---|---|---|---|
| P0 | Verifikation (B.1) | **[Nachgewiesen]** Kein Verifikationssystem vorhanden; Bürgerrolle `1531303733311307997` wurde nur für Bewerbungen genutzt. | Beitritt → Unverifiziert-Rolle `1532762237284913363`; persistenter Button `pulse:verify:v1` → **zuerst** Bürgerrolle, **dann** Unverifiziert entfernen. Rechte-/Hierarchieprüfung vor jeder Rollenänderung, Klick-Sperre + Cooldown, Duplikatschutz beim Join, Fehlerprotokoll (max. 10) im Dashboard, Panel bearbeitet statt dupliziert. Standardmäßig **aus**. | `test_pulse_verification.py` (Abnahmepunkte 1–8), `test_pulse_admin_area.py` (9) |
| P0 | Abmeldungen | **[Nachgewiesen]** Dashboard-Abmeldungen wurden in der migrierten Tabelle mit vertauschten Spalten gespeichert: Ende = Startdatum, Nickname = Enddatum (Live: „Stv.TL ✗ 2026-10-19“). | Insert repariert (88059cc) **und** neue einmalige, idempotente Reparatur bestehender Einträge beim Start mit Dateisicherung vorher (`pulse_absence_repair.py`). | `test_pulse_absence_repair.py` |
| P0 | Namenspräfixe | **[Nachgewiesen]** Gestapelte Präfixe nach Rangwechsel („Jr.Admin ✗ L.Mod ✗ Dawo“). | Pulse-eigene Präfixe werden vollständig entfernt; „Unverifiziert ✗“ ebenfalls, damit es nach der Verifikation nicht stapelt. | `test_role_nickname_utils.py` |
| P1 | Discord-Design | Farben in 19 Dateien hart codiert. | Zentrale, änderbare Palette; Willkommen/Abschied mit Avatar + Banner. | siehe oben |
| P1 | Team ↔ Verwaltung | Admin-Funktionen in der Team-Navigation; Schnellsuche zeigte allen Admin-Links. | Umschalter „Team | Verwaltung“, rechteabhängige Schnellsuche, alle Routen serverseitig geprüft. | `test_pulse_admin_area.py` |
| P1 | Ticket-Zentrale | Keine Triage/Filter. | Wartet-auf-wen, Überfällig, Meine, Suche, Aktionen. | `test_pulse_ticket_triage.py` |

## Teilweise implementiert
- **Embed-Vereinheitlichung:** Farben zentral; Footer/Feldstruktur der älteren Embeds noch individuell (Plan in `docs/PULSE_DISCORD_DESIGN.md`).
- **Wartezeiten in Tickets:** nur für Tickets mit erfassten Aktivitätszeitstempeln.

## Priorisierte nächste Schritte

| Prio | Idee | Nutzen | Risiko / Tests |
|---|---|---|---|
| P1 | **Restore-Probelauf** (`/backup/restore`): Vorschau „was würde sich ändern“ vor der Bestätigung. | Verhindert irreversible Fehl-Restores. | Hoch – eigene Etappe mit Diff-Tests. |
| P1 | **Kanal-Sichtprüfung für Unverifizierte**: rein lesender Check, welche Kanäle die Unverifiziert-Rolle sehen kann, mit Warnliste. | Verhindert, dass Unverifizierte geschützte Bereiche sehen. | Niedrig – nur lesend. |
| P2 | **Ticket-Übergabe mit Pflicht-Notiz** (geprüft/offen/nächster Schritt). | Kontext bleibt bei Wechsel erhalten. | Mittel. |
| P2 | **Schnellantworten pro Ticketkategorie** aus freigegebenen Texten. | Spart Tipparbeit. | Niedrig. |
| P2 | **Rollen-Simulation** „Was darf diese Person im Dashboard?“ (lesend). | Fehlkonfiguration früh sehen. | Niedrig. |
| P2 | **Konsistenz-Diagnose** für Daten (Ticket-/Schicht-/Abmeldungszustände), nur berichtend. | Datenfehler wie die Abmeldungs-Verschiebung früh erkennen. | Niedrig. |
| P3 | Ruhezeiten für Erinnerungen, Fallverteilung pro Teamler im Cockpit, Bewerbungs-Rubriken mit mehreren Prüfern. | | |

### Bewusst verworfen
- KI-gestützte Verifikation oder automatische Sanktionen (Projektregel 7).
- Automatisches Umbauen der Kanalrechte für Unverifizierte (zu riskant ohne explizite Adminaktion).
