# Phasenbericht Phase 6 – Discord-Bot: Bedienung & Funktionen

Branch `verbesserung/phase-6-discord-bot` (von `main` = 7.14.0), **nicht gepusht, nicht gemergt, nicht deployt**. Stand 21.10.2026.

## 1. Erledigt

| ID | Commits | Testnachweis |
|---|---|---|
| BOT-05 Eine Logik für Teamaktionen | f8b4155, d3b8327 | `tests/test_team_warning_domain.py` (12) |
| BOT-13 Status an einer Stelle | 80dac22 | `tests/test_presence.py` (4) |
| BOT-01 Grundregeln für Interaktionen | 9e35cad | `tests/test_interactions.py` (11) |
| BOT-03 Dienst-Panel | 30a7756 | `tests/test_dienst.py` (6) |
| BOT-04 Slash-Befehle ordnen | cfeb1e9, bf44244 | `tests/test_command_help.py` (7) |
| BOT-12 Ruhezeiten | 97846a8 | `tests/test_notify_prefs.py` (+4) |
| BOT-14 Panel-Übersicht | a5c87aa | `tests/test_panels.py` (5) |
| BOT-08 Raid-/Spam-Frühwarnung | 2e11200 | `tests/test_raid_watch.py` (7) |
| BOT-02 Protokollierung | c7e695c | `tests/test_log_routing.py` (13) |
| BOT-06 Tickets | 7ddc1a5 | `tests/test_ticket_extras.py` (10) |
| BOT-07 Voice-Support | efad20f | `tests/test_voice_queue.py` (6) |
| BOT-09 Bürger-Moderation (E-8) | 1761b96, d78582d | `tests/test_mod_cases.py` (11) |
| BOT-10 Bewerbungs-Kommunikation | 8b3cab5 | `tests/test_app_comms.py` (6) |
| BOT-11 On-/Offboarding | d49a0a5 | `tests/test_checklists.py` (6) |
| Review-Fixes | 9b900b8 | siehe unten |

**Gates** (venv `Q:\venv`, Python 3.14, ohne Discord):
- `python -m compileall -q .` → OK
- `python tests/feature_smoke.py` → OK
- `python tests/test_pulse_intelligence.py` → OK
- `python tests/smoke_test.py` → OK
- `python -m ruff check .` → „All checks passed!“
- `python -m unittest discover -s tests` → **600 Tests OK** (nach Phase 5: 483)
- Bei jedem Gate-Lauf geprüft, dass keine Datei im Repo-Verzeichnis entsteht oder sich ändert → keine Änderung.

**Review** durch einen Subagenten mit frischem Kontext: **2 hohe, 5 mittlere, 8 geringe Befunde**. Alle sind in 9b900b8 behoben, außer Nr. 15 (siehe Abschnitt 4):
- **Hoch:**
  - Das Moderations-Log eines Kanals blieb nach einem fehlgeschlagenen Sammelversand bis zum Neustart stehen.
  - Große Einträge überschritten die Discord-Grenze von 6000 Zeichen pro Nachricht.
- **Mittel:**
  - `/pulse-panel` war für Teammitglieder versehentlich ausgeblendet.
  - Ein unerwarteter Netzwerkfehler leerte die DM-Warteschlange.
  - Ein abgelaufener `/mod`-Ban hätte einen später von Hand gesetzten Dauerban aufgehoben.
  - Die Timeout-DM ging auch raus, wenn Discord den Timeout ablehnte.
  - Ein geänderter Gesprächstermin hinterließ doppelte Kalendereinträge.
- **Gering:**
  - Die Ticket-Sperre ließ sich auch auf Team, Bots und sich selbst setzen.
  - Als Mentor war jede beliebige Person wählbar.
  - Rang und Rechte wurden beim Bestätigen von Kick/Ban nicht erneut geprüft.
  - Format-Angaben in Vorlagen konnten Speicher belegen.
  - Der Warngrund war kürzer als früher bei `/warn` (500 statt 1000 Zeichen).
  - Ein verweigertes Entbannen wurde jede Minute erneut versucht.
  - Eine Rollen-Namensänderung ging verloren, wenn sich gleichzeitig die Position änderte.

**Ersetzte Literal-Prüfungen** (Planregel: Absicht klären, durch Verhalten ersetzen):
- feature_smoke:
  - Teamschutz/4-Augen bei `/warn`
  - Warn-Stufe/Gültigkeit
  - Voice-Bereitschaft nach Schichtaktion
  - Selbst-DM bei langer Schicht
  - Admin-Abschnitt der Hilfe
  - Klassenzeile der Lösch-Bestätigung
- test_concurrent_writes: Warn-Flüsse prüfen jetzt die Domänenfunktionen, dazu ein echter Parallel-Test.
- test_discord_log_regressions: Schichtstart während Abmeldung.
- test_application_eligibility: Pflicht-Gespräch-Hinweis jetzt in der Vorlage.
- test_advanced_logs: wartet wegen des Sammelversands kurz auf den Versand; die Prüfungen selbst sind unverändert.

## 2. Verhalten vorher → nachher

- **Teamaktionen (BOT-05):**
  - Dashboard und Slash-Befehle machen jetzt dasselbe.
  - **Behobene Lücken:** `/warn-remove` hob Uprank- bzw. IC-Sperren nicht auf. `/promote` setzte keine Pflicht-Probezeit (jetzt Pflichtfeld 3/7 Tage). `/warn` kannte weder Dauer noch Beweis, schickte keine DM und eskalierte nicht bei 5/5.
- **Buttons und Menüs (BOT-01):**
  - Statt „Interaktion fehlgeschlagen“ kommt eine Meldung mit Fehler-ID, Doppelklicks werden abgefangen.
  - Pulse pingt `@everyone` oder Rollen nur noch dort, wo es ausdrücklich gewollt ist. Alle bisherigen gewollten Pings funktionieren weiter: Recruiting, Verifizierung, Ticket-Rollen, Büro, RP-Ankündigung, Social.
- **Dienst in Discord (BOT-03):**
  - Neu: `/dienst start|pause|weiter|ende|status` und ein Dienst-Panel mit Knöpfen (`/dienst panel` im Zielkanal), mit derselben Logik wie im Dashboard.
  - Vier Stunden vor dem automatischen Schichtende kommt eine zweite Erinnerung.
- **Befehle (BOT-04):**
  - Einrichtungsbefehle sehen normale Mitglieder nicht mehr.
  - `/pulse-hilfe` listet automatisch alle Befehle, die man nutzen darf.
  - Neue Gruppen `/team …`, `/setup …`, `/dienst …`, `/ticket …`, `/mod …`. Alle alten Namen funktionieren weiter.
- **Ruhezeiten (BOT-12):** Erinnerungs-DMs kommen nicht zwischen 23 und 8 Uhr; Admins stellen die Zeit in der Inbox ein. Wartende DMs gehen danach gebündelt als eine Nachricht raus. Warnungen kommen weiter sofort.
- **Status (BOT-13):** Der Bot zeigt „N im Dienst · M Tickets offen“, statt dass drei Stellen sich gegenseitig überschreiben.
- **Panels (BOT-14):** Unter Verwaltung → Discord-Panels stehen alle Panels mit Status und Link. Tickets, Verifizierung und Dienst lassen sich per Knopf neu veröffentlichen.
- **Frühwarnung (BOT-08):** Pulse warnt das Team bei Beitrittswellen, vielen neuen Konten, Massen-Erwähnungen und Kopier-Spam. Es ändert nichts automatisch.
- **Logs (BOT-02):** Neu im Log stehen:
  - gelöschte Nachrichten mit Anhangsnamen,
  - Beitritt und Austritt mit Kontoalter, Nicknames,
  - Bans, Kicks und Timeouts samt Täter,
  - Rollen- und Kanaländerungen, Voice-Wechsel.

  Jede Kategorie kann in einen eigenen Kanal gehen, dazu kommen Ignorierlisten. Einstellen unter Verwaltung → Discord-Auftritt.
- **Tickets (BOT-06):**
  - `/ticket antwort` (Schnellantworten), `/ticket uebergeben` (Pflichtnotiz), `/ticket tag`, `/ticket sperre` mit Ablauf.
  - Im Dashboard: Ticket-Werkzeuge mit Bewertungsauswertung und lesbaren Transkripten.
- **Voice-Support (BOT-07):**
  - Bürger bekommen beim Warten ihre Position und eine geschätzte Wartezeit, außerhalb der Öffnungszeiten das Angebot „Ticket statt Warten“.
  - Im Dashboard gibt es eine Supporter-Statistik und einen Hinweis, wenn das Warteschleifen-Audio fehlt.
- **Bürger-Moderation (BOT-09, E-8):**
  - `/mod warn|timeout|kick|ban|akte` mit Fall-ID, Beweis, Ablauf und DM mit Einspruchsweg. Kick und Ban brauchen eine Bestätigung.
  - Fallakte unter Verwaltung → Bürger-Moderation.
  - Es braucht das Pulse-Recht **und** das passende Discord-Recht.
- **Bewerbungen (BOT-10):**
  - Neu sind die Eingangsbestätigung und der Gesprächstermin mit Kalendereintrag, DM und Erinnerung eine Stunde vorher.
  - Alle Bewerber-Texte sind als Vorlagen bearbeitbar; der Standard ist der bisherige Text.
- **On-/Offboarding (BOT-11):** Bei Teambeitritt und -austritt entsteht eine Checkliste unter Verwaltung → On- & Offboarding, inklusive Mentor-Zuweisung.

## 3. Verworfen oder verschoben

- Nichts verworfen. Einschränkungen:
  - **BOT-14:** „Neu veröffentlichen“ gibt es nur, wo schon eine geprüfte Veröffentlichungsfunktion existiert (Tickets, Verifizierung, Dienst). Für die anderen Panels nennt die Übersicht den Einrichtungsbefehl.
  - **BOT-06:** Den SLA-Timer und die Prüfer-Rubrik (BOT-10) gab es bereits.
  - **BOT-04:** Die alten Befehlsnamen bleiben vorerst bestehen. Wann sie entfernt werden, entscheidest du.

## 4. Risiken und Schritte außerhalb des Codes

- **Discord-Rechte des Bots:**
  - „Audit-Log anzeigen“ für Täter bei Bans, Kicks und Timeouts im Log.
  - „Mitglieder moderieren/kicken/bannen“ für `/mod`.
- **Befehls-Sync:** Nach dem Update synchronisiert Pulse die Befehle einmal neu. Neue Gruppen erscheinen in Discord, Einrichtungsbefehle verschwinden für normale Mitglieder. Einzelne Rollen lassen sich unter Servereinstellungen → Integrationen → Pulse freischalten.
- **Neue optionale `.env`-Werte:** `PULSE_RAID_WATCH` (Standard an) und die Raid-Schwellen, beschrieben in `.env.example`.
- **Pflichtparameter:** `/promote` verlangt jetzt eine Probezeit (3/7 Tage), genau wie das Dashboard.
- **Statistik und Schätzung:** Supporter-Statistik und Wartezeit-Schätzung füllen sich erst mit Fällen, die ab dem Update abgeschlossen werden.
- **Fund 15 aus dem Review, nicht geändert:** `buero_system.db` und `rp_verwaltung.db` werden relativ zum Startordner geöffnet. Startet der Bot nicht im Repo-Ordner, zeigt die Panel-Übersicht „Nicht eingerichtet“. Das betrifft nur die Anzeige, bei bot-hosting.net nicht der Fall.

## 5. Offene Fragen

- Merge nach `main` als **Version 7.15.0** mit CHANGELOG-Eintrag?
- Sollen die alten Befehlsnamen später entfernt werden, z. B. nach 4 Wochen?
- E-6 (Seiten zusammenlegen) ist weiterhin offen.
