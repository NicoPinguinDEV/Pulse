# Pulse TeamOS – Verbesserungsauftrag für Claude Code
**Discord-Bot + Team-Dashboard · © BOCHUM RP | V1**

> **Analysegrundlage:** `main` @ `8d66bdf` (pulse_version 7.9.7), Stand 20.10.2026. Zeilenangaben sind Orientierung – such im Zweifel nach dem Funktions- oder Variablennamen.
> **Kennzeichnung:** **[Nachgewiesen]** im Code belegt · **[Prüfen]** begründeter Verdacht, erst verifizieren · **[Idee]** neue Funktion, nur nach Freigabe und abschaltbar.

---

## 0. Auftrag

Mach Pulse in abgesicherten Phasen zuverlässiger, sicherer, schneller und übersichtlicher – **ohne eine bestehende Funktion, URL, Slash-Command, `custom_id` oder Datenzeile zu verlieren** – und baue danach gezielt neue Funktionen für Bot und Dashboard aus.

`CLAUDE.md` gilt vollständig. Lies vor der ersten Änderung zusätzlich `docs/CLAUDE_CODE_HANDOFF.md`, `docs/PULSE_INNOVATION_ROADMAP.md` und `docs/PULSE_DISCORD_DESIGN.md`. Widerspricht dieser Auftrag `CLAUDE.md`, gilt `CLAUDE.md`, bis ich ausdrücklich etwas anderes entscheide.

---

## 1. Arbeitsregeln für diesen Auftrag

1. **Plan-Datei = dein Gedächtnis.** Liegt dieser Auftrag noch nicht als `docs/VERBESSERUNGSPLAN.md` im Repo, lege ihn dort unverändert an (erster Commit). Pflege pro Punkt `[ ]` → `[x]` mit Commit-Hash und einem Satz Testnachweis, bzw. `verworfen: <Grund>`. Startet eine neue Session oder wird der Kontext knapp: zuerst diese Datei lesen, dann beim ersten offenen Punkt weitermachen.
2. **Phasen nacheinander.** Phase fertig = Prüfungen grün + Review + Phasenbericht (Abschnitt 13) → **STOPP**, auf mein OK warten. Innerhalb einer Phase darfst du die Reihenfolge nach Abhängigkeiten ändern.
3. **Git:** ein Branch pro Phase (`verbesserung/phase-1-kritisch`, …), ein kleiner Commit pro Punkt, deutsche Commit-Nachricht mit ID (z. B. `K-04: Warnungen gehen bei parallelen Aktionen nicht mehr verloren`). **Kein Push, kein Merge nach `main`, kein Deployment** ohne meine ausdrückliche Freigabe.
4. **Erst belegen, dann ändern.** [Prüfen]-Punkte zuerst verifizieren (Code-Pfad, Test, Reproduktion). Stimmt ein Punkt nicht: im Plan als verworfen markieren, nicht trotzdem umbauen.
5. **Fehler test-first:** wo machbar zuerst ein fehlschlagender Regressionstest, dann der Fix. Tests verbinden sich nie mit echtem Discord und ändern keine Live-Daten (Fakes, Temp-Verzeichnisse, gepatchte Pfade – Vorbild: `tests/test_pulse_admin_area.py`).
6. **Prüfungen** je Punkt (die relevanten Tests) und je Phase (alle):
   ```bash
   python -m compileall -q .
   python tests/feature_smoke.py
   python tests/test_pulse_intelligence.py
   python tests/smoke_test.py
   python -m unittest discover -s tests -p 'test_*.py'
   ```
   Vorher eine venv mit Python 3.14 (notfalls 3.13, dann im Bericht vermerken) und `pip install -r requirements.txt`. Ergebnisse immer mit echtem Output berichten; was nicht ausführbar war, ehrlich als solches melden.
7. **`tests/feature_smoke.py` richtig behandeln:** Rund 820 der 911 Asserts prüfen nur, ob ein bestimmter String im **Quelltext** einer Datei steht (`assert "…" in read("datei.py")`). Bricht ein Umbau so einen Assert, baust du den String nicht einfach wieder ein: Absicht klären, durch einen Verhaltenstest ersetzen, im Bericht nennen.
8. **Falle „toter Code“:** Viele Seiten aus `webserver.py` werden zur Laufzeit ersetzt (`/dashboard`, `/member/{user_id}`, `/settings`, `/melonly`, `/tickets` u. a. durch `pulse_pro.py` via `remove_and_add`). `GET /ultimate` ist doppelt registriert – nach statischer Analyse ist `webserver.py:3344` aktiv und die Version in `pulse_ultimate.py:751` wird nie erreicht (P0-2 bestätigt das am laufenden Objekt). Prüfe vor jeder Seitenänderung in `docs/ROUTEN.md` (Phase 0), welcher Handler wirklich aktiv ist – sonst reparierst du toten Code.
9. **Kontext schonen:** `webserver.py` (~9.900 Zeilen), `pulse_pro.py` (~2.700), `pulse_ultimate.py` (~2.300), `cogs/voice_support.py`, `cogs/tickets.py`, `cogs/pulse_suite.py` nie komplett einlesen, sondern gezielt suchen. Breite Recherchen („alle Stellen, die `team_data.json` schreiben“) an Subagents geben; die Änderungen machst du selbst.
10. **Review vor Phasenabschluss:** Ein Subagent mit frischem Kontext prüft den Phasen-Diff gegen diesen Plan – nur Korrektheit, Sicherheit, verlorene Funktionen, fehlende Tests; keine Stilfragen.
11. **Kompatibilität:** URLs, Formularfelder, Slash-Command-Namen, `custom_id`s, JSON-Schlüssel, DB-Spalten und die festen Discord-IDs bleiben gültig. Wo etwas umbenannt oder zusammengelegt wird: Weiterleitung, Alias oder Lese-Fallback **plus** Test.
12. **Nicht tun:** Massenformatierung, Big-Bang-Rewrites, neue kostenpflichtige Dienste, Sanktionen durch KI, Laufzeitdaten löschen, `.env`-Inhalte ausgeben (höchstens zählen/vergleichen, z. B. `grep -c`, ohne Werte zu zeigen).

---

## 2. Entscheidungen, die du mir gebündelt stellst

Stelle diese Fragen am Ende von Phase 0 gesammelt (als Auswahlfragen). Bis zur Antwort arbeitest du nur an Punkten, die nicht davon abhängen. *Kursiv* = mein Vorschlag.

- **E-1 Honeypot (K-11):** Teamler/Admins ausnehmen? Ban oder Softban? Wie viele Stunden Nachrichten mitlöschen? *Team ausnehmen (nur löschen + Alarm), Ban mit 1 h Nachrichtenlöschung, Entbannen-Knopf im Dashboard.*
- **E-2 Vergessene Schichten:** Nach 24 h automatisch beendete Schichten werden heute mit bis zu 24 h gutgeschrieben (`webserver.py:898` und `:937`, `min(shift_elapsed, 24 h)`). Wie viel soll zählen? *0 h, bis eine Führungskraft die echte Dauer bestätigt.*
- **E-3 Teamakte:** Jeder mit Dashboard-Zugang sieht alle Warnungen (auch zurückgenommene, mit Grund) und internen Notizen anderer Teamler (`member_v5`, `pulse_pro.py:1828`). So lassen? *Notizen nur mit `can_add_notes`/Leitung; Warnungen nur die betroffene Person selbst und `can_warn`.*
- **E-4 Standardrechte:** Nie konfigurierte Teamrollen bekommen automatisch „Dashboard + Analytics“ (`webserver.py:781`) – Analytics umfasst Team-Exporte (CSV/JSON inkl. Warnungszahlen). *Nur Dashboard, Analytics nur explizit.*
- **E-5 RAM-Budget:** Pulse läuft auf einem VPS mit 2 GiB Host-RAM. Containerlimit: 768 MiB RAM plus höchstens 256 MiB zusätzlicher Swap; interne Warnschwellen 500/620/700 MB mit Hysterese. Bei dauerhaft hoher Host-Auslastung bleiben mindestens 4 GiB empfehlenswert.
- **E-6 Doppelte Seiten (U-01):** Welche Variante wird jeweils führend? *Du legst mir eine Tabelle „alt → neu“ vor, bevor irgendetwas zusammengeführt wird.*
- **E-7 Ticket-Transkripte:** Anhänge mitsichern (Speicherbedarf)? Aufbewahrungsdauer?
- **E-8 Bürger-Moderation:** Soll Pulse Timeout/Kick/Ban für normale Mitglieder mit Fallakte bekommen – oder macht das ein anderer Bot?
- **E-9 Datenschutz:** Aufbewahrungsfristen (Bewerbungen, Transkripte, KI-Moderationsfälle, Audit) und verantwortlicher Kontakt für den Hinweis auf `/apply`.
- **E-10 Session-Secret (K-03):** Wird der Platzhalter erkannt – Start verweigern oder auf die generierte `.session_secret` ausweichen (alle werden einmal abgemeldet)? *Ausweichen + laute Warnung.*
- **E-11 Slash-Befehle (BOT-04):** In Gruppen ordnen, alte Namen eine Übergangszeit als Alias? Prefix-Altbefehle `!ticketsetup`/`!ticketstats` entfernen?
- **E-12 Bewerbungsportal (K-01):** Sitzungsdauer? Entwürfe nur im Browser oder zusätzlich serverseitig? *3 h gleitend, Entwurf im Browser.*

### Entscheidungen (beantwortet am 20.10.2026)

| Frage | Entscheidung |
|---|---|
| E-1 Honeypot | **Alles wie bisher** – jeder wird gebannt, ohne Nachrichtenlöschung; K-11 entfällt bis auf Protokollierung (nur nach erneuter Freigabe). |
| E-2 Vergessene Schichten | **0 h bis eine Führungskraft die echte Dauer bestätigt.** ✅ 23.10.2026 aus `origin/verbesserung/phase-6-bot` übernommen und auf alle 17 Leser ausgeweitet – `tests/test_shift_confirmation.py` (13). |
| E-3 Teamakte | **Eingeschränkt:** Notizen nur `can_add_notes`/Leitung; Warnungen nur die Person selbst und `can_warn`. |
| E-4 Standardrechte | **Nur Dashboard**; Analytics nur explizit. |
| E-5 RAM | **VPS mit 2 GiB RAM: Pulse-Limit 768 MiB RAM + höchstens 256 MiB zusätzlicher Swap. Warnung 500 MB, kritisch 620 MB, Notfallreserve 700 MB; bei dauerhaft hoher Host-Auslastung 4 GiB erwägen.** |
| E-6 Doppelte Seiten | **Erst Tabelle „alt → neu“ vorlegen**, Entscheidung je Funktion. |
| E-7 Transkripte | **Anhänge bis 8 MB mitsichern, unbegrenzt aufbewahren.** |
| E-8 Bürger-Moderation | **Ja, mit Fallakte** (BOT-09, abschaltbar, Standard aus). |
| E-9 Fristen | **Bewerbungen 6 Monate, KI-Moderationsfälle 3 Monate, Audit 2 Jahre** (Bereinigung protokolliert, Standard aus bis zur Aktivierung). Offen: verantwortlicher Kontakt für den Hinweis auf `/apply`. |
| E-10 Session-Secret | **Ausweichen auf `.session_secret` + laute Warnung.** |
| E-11 Slash-Befehle | **Gruppen + alte Namen als Alias**; `!ticketsetup`/`!ticketstats` entfernen. |
| E-12 Bewerbungsportal | **3 h gleitend, Entwurf nur im Browser.** |

---

## 3. Phase 0 – Bestandsaufnahme (nur `docs/` und `tools/` ändern)

- [x] **P0-1 Baseline** (aa0076a…fb12a06, Bericht `docs/PHASE0_BERICHT.md`: venv 3.14.6, ohne tzdata 4 Fehler, mit tzdata 275 Tests grün) nach `CLAUDE.md` §6: Branch, offene Änderungen, Version, Testlauf mit Ergebnis, Doku-Abweichungen (z. B. nennt `CLAUDE.md` Commit `b1019dc`; `CHANGELOG.md` beginnt mit unversionierten Abschnitten, letzte Versionsüberschrift ist 7.8.27; README nennt „Pulse Intelligence 7.9.0“).
- [x] **P0-2 Routen-Landkarte** (aa0076a: 266 Routen, 1 Doppelung, 21 verdrängt; neu: /docs, /redoc, /openapi.json öffentlich → K-17) `docs/ROUTEN.md`, erzeugt von einem Skript unter `tools/` (importiert `webserver` mit Temp-Daten wie die Tests): alle effektiven Routen mit Methode, Pfad, Modul/Funktion und Rechteprüfung (`auth(...)`, `ctx_auth`, `cctx`, `require_perm` – soweit ableitbar) sowie alle verdrängten Handler. Meine statische Zählung: ~260 Routen, eine echte Doppelung (`GET /ultimate`), viele per `remove_and_add` (`pulse_pro.py:299`) verdrängte Altversionen. Achtung: `remove_and_add` entfernt **alle Methoden** eines Pfads, nicht nur die ersetzte.
- [x] **P0-3 Datenlandkarte** (f9bfd91: 59 von 81 Tabellen fehlen im JSON-Backup, 5 DBs + uploads/ in keinem Backup) `docs/DATENLANDKARTE.md`: jede Datei/Tabelle mit Besitzer-Modul, schreibenden Stellen, Wachstum und Abdeckung durch die beiden Backup-Systeme. Bekannt: 8 SQLite-Dateien (`pulse.db`, `abmeldungen.db`, `activity_check.db`, `pulse_suite.db`, `teamliste.db`, `buero_system.db`, `rp_verwaltung.db`, `flaggenquiz.db`), 11 JSON-Dateien (`config`, `team_data`, `applications`, `shifts`, `logs`, `audit_logs`, `meetings`, `pulse_announcement_archive`, `role_nickname_state`, `social_promo_sources`, `ai_moderation_cases`), Ordner `uploads/*`, `attachments/`, `transcripts/`, `backups/`, dazu `.session_secret`.
- [x] **P0-4 Jobs & Rechte:** (fb12a06: 13 Loops, 4 Dauerschleifen, 11 create_task, 20 Rechte-Schlüssel; A-03 bestätigt) alle Hintergrundschleifen (heute 13 `@tasks.loop`s in 10 Cogs – u. a. `voice_support` alle 8 s und 1 min, `activity_check` 1 min/5 min/06:00 – plus eigene Endlosschleifen: Ultimate-Automation, Auto-Backup, Server-Statistik) mit Intervall und Fehlerbehandlung; alle Rechte-Schlüssel mit Herkunft (siehe A-03).
- [x] **P0-5** Bericht + Fragen E-1 bis E-12 → **STOPP**. (Bericht `docs/PHASE0_BERICHT.md`, Entscheidungen in Abschnitt 2 eingetragen)

---

## 4. Phase 1 – Kritische Fehler & Sicherheit

- [x] **K-01 Bewerbungsportal: lange Bewerbungen gehen verloren** [Nachgewiesen] — **erledigt:** 0bc6e2b – `tests/test_apply_session.py` (6 Tests): Sitzung 3 h gleitend, Entwurf im Browser, abgelaufene Sitzung → Login führt zur Laufbahn zurück, Fehlerseiten verlinken das ausgefüllte Formular.
  - *Befund:* Die Bewerbungssitzung gilt nur 30 Minuten (`webserver.py:2718` `apply_exp = jetzt + 1800`, Cookie `max_age=1800` in `:2727` und `:6802`). Der Eignungstest hat 12/22/30 Fragen plus Praxisaufgaben und Motivation. Wer länger braucht, bekommt beim Absenden „Discord-Verknüpfung fehlt“ (`:8775`) – alle Antworten sind weg: Fehlerseiten verlinken nur zurück auf ein leeres `/apply`, und global ist `Cache-Control: no-store` gesetzt (`:192`). Dasselbe bei Rate-Limit, Validierungsfehler im Test (`:8803`) usw.
  - *Umsetzung:* Sitzung nach E-12 verlängern bzw. gleitend erneuern; Entwurf automatisch im Browser speichern (localStorage je Discord-ID + Laufbahn, in try/catch, nach erfolgreichem Absenden löschen); jede Fehlerseite bietet „Zurück zum Formular – deine Antworten sind noch da“; Anzeige der Restzeit + „Sitzung verlängern“ (erneuter Discord-Login ohne Datenverlust). Serverseitige Prüfungen (Rolle, Score, Module) bleiben unverändert.
  - *Fertig wenn:* Tests – abgelaufene Sitzung → Entwurf bleibt, nach erneutem Login wiederhergestellt; Validierungsfehler → Eingaben erhalten; Score weiterhin nicht manipulierbar.

- [x] **K-02 RP-Verwaltung: jeder Panel-Nutzer kann @everyone auslösen und Nachrichten löschen** [Nachgewiesen] — **erledigt:** 823837c – `tests/test_rp_verwaltung.py` (7 Tests): Unberechtigte bekommen eine ephemere Absage, nichts wird gesendet/gelöscht; nur die eigene letzte Ankündigung wird entfernt.
  - *Befund:* `RPSelect.callback` (`cogs/rp_verwaltung.py:113`) und `RPStopModal.on_submit` prüfen keine Rolle. Wer das Panel sieht, sendet eine @everyone-Ankündigung; vorher löscht `send_rp_announcement` per `ch.purge(limit=10)` (`:52`) die letzten 10 Nachrichten **beliebiger Autoren**; ist kein Kanal gespeichert, fällt es auf den aktuellen Kanal zurück (`:47`). Die Uhrzeit aus dem Modal landet ungefiltert in der @everyone-Nachricht.
  - *Umsetzung:* Rechteprüfung (Standard: Admin oder `can_promote`, Rollen im Dashboard konfigurierbar – frag mich, falls unklar ist, wer das Panel heute bedient), Cooldown, nur die eigene letzte Ankündigung löschen (Nachrichten-ID speichern) statt `purge`, kein Fallback-Kanal (klare Fehlermeldung), Eingabe prüfen (Uhrzeit oder „Offen“) und Erwähnungen entschärfen, `AllowedMentions(everyone=True)` nur für genau diese Nachricht, Audit-Eintrag.
  - *Fertig wenn:* Test mit Fake-Interaction – Unberechtigte bekommen eine ephemere Ablehnung, es wird nichts gesendet oder gelöscht.

- [x] **K-03 Session-Secret und Token-Typen** [Nachgewiesen] — **erledigt:** 31840e0 + 6aacfb5 – `tests/test_session_tokens.py` (7 Tests): Platzhalter/zu kurze Secrets werden nicht genutzt, Dashboard- und Bewerbungs-Token nicht austauschbar, aktiver Systemstatus zeigt das Problem.
  - *Befund:* `_load_secret()` (`webserver.py:717`) übernimmt `SESSION_SECRET` ungeprüft – auch den Platzhalter `change-me-to-a-long-random-secret` aus `.env.example`. Ist er gesetzt, kann jeder `user_session`-Cookies für beliebige Discord-IDs signieren (Server-Owner-ID = Vollzugriff). Außerdem werden `user_session` und `apply_session` mit demselben Schlüssel ohne Typ-Feld signiert; unterschieden wird nur über `exp` vs. `apply_exp`.
  - *Umsetzung:* Platzhalter und zu kurze Secrets (< 32 Zeichen) nach E-10 behandeln, Warnung in Systemstatus und `/pulse-diagnose`; neue Tokens mit `typ` und Ausstellungszeit; alte Tokens bis zu ihrem Ablauf weiter akzeptieren. Prüfe in der echten `.env`, ob der Platzhalter steht – nur per `grep -c`, nichts ausgeben.
  - *Fertig wenn:* Tests – Platzhalter wird nicht verwendet; ein Bewerbungs-Token wird als Dashboard-Token abgelehnt und umgekehrt; bestehende gültige Sitzungen funktionieren weiter.

- [x] **K-04 Verlorene Änderungen durch parallele Aktionen (Race Condition)** [Nachgewiesen] — **erledigt:** a769a3a + 41293d9 – `tests/test_concurrent_writes.py` (8 Tests): zwei überlappende Warn-Abläufe mit `asyncio.sleep` behalten beide Warnungen; Schicht-, Activity-Check- und Voice-Fall-Schreibvorgänge verlieren nichts. **Teilumsetzung**, Reststellen im Phasenbericht.
  - *Befund:* Warnung im Dashboard: `team_db = load_json(DATA_FILE)` (`webserver.py:9214`) → zwei `await`s (`:9220`, `:9226`) → `save_json` (`:9239`) → weitere `await`s mit Discord-Rollenänderungen (`:9241–9242`) → erneutes `save_json` des **alten Schnappschusses** (`:9243`). Gleiches Muster in `/warn` (`cogs/pulse_suite.py:1001–1037`), in `process_overdue_shifts` (`webserver.py:911`) und bei Warn-Rücknahme/Rangwechseln. Überlappen zwei Aktionen (zwei Leitungen verwarnen gleichzeitig, automatische Schicht-Warnung, Notiz), überschreibt die zweite die erste → Warnungen/Notizen verschwinden. [Prüfen] Gleiches Risiko beim Voice-Support: die Pro-Fall-Locks (`_case_action_lock`) schützen nicht den gemeinsamen `voice_support_cases`-Blob, der nach mehreren `await`s komplett zurückgeschrieben wird (`cogs/voice_support.py` z. B. `:491`, `:511`, `:540`).
  - *Umsetzung:* zentraler Helfer (z. B. `async with store.mutate(DATA_FILE) as data:`) mit `asyncio.Lock` je Datei/Schlüssel; Laden → Ändern → Speichern ohne `await` dazwischen; Discord-Aufrufe vor oder nach dem kritischen Abschnitt; Folgeänderungen als eigenes `mutate`. Alle schreibenden Stellen für `team_data.json`, `applications.json`, `shifts.json`, `meetings.json` und den Voice-Support-Blob umstellen (Fundstellen per Subagent sammeln).
  - *Fertig wenn:* Nebenläufigkeitstest (zwei Coroutinen mit künstlichem `await asyncio.sleep(...)` dazwischen) verliert keine Warnung und keinen Fall; alle bestehenden Tests grün.

- [x] **K-05 Beschädigte JSON-Datei wird still mit Standardwerten überschrieben** [Nachgewiesen] — **erledigt:** 751d491 – `tests/test_json_safety.py` (4 Tests): kaputte `config.json` wird gesichert (`.corrupt-…`), nicht überschrieben, Dashboard zeigt 503-Hinweis, Systemstatus listet die Datei.
  - *Befund:* `load_json` (`webserver.py:256`) liefert bei jedem Lese-/Parse-Fehler still den Default (`{}`/`[]`); das nächste `save_json` überschreibt dann z. B. `config.json` (Rollen, Rechte, Einstellungen) oder `team_data.json` (alle Warnungen) mit fast leeren Daten. `save_json` (`:267`) schreibt ohne `flush`/`fsync` vor `os.replace` – nach Stromausfall oder hartem Container-Stopp ist eine leere Datei möglich.
  - *Umsetzung:* Parse-Fehler → Datei als `<name>.corrupt-<zeitstempel>` sichern, laut loggen + Systemalarm, Schreibzugriffe auf diese Datei sperren (sichtbarer Degraded-Modus) statt überschreiben; `fsync` für Datei und Verzeichnis.
  - *Fertig wenn:* Test – kaputte `config.json` wird nicht überschrieben, Sicherungskopie existiert, verständliche Meldung im Dashboard.

- [x] **K-06 Ticket-Kanal wird gelöscht, obwohl das Transkript fehlschlug** [Nachgewiesen] — **erledigt:** fd0177c – `tests/test_ticket_archive.py` (3 Tests): `history()` schlägt fehl → Kanal bleibt, Ticket bleibt offen; Erfolg → Transkript + Anhänge ≤ 8 MB gesichert, Upload in den Log-Kanal.
  - *Befund:* `archive_ticket` (`cogs/tickets.py:690`) fängt Fehler beim Transkript ab, schließt das Ticket trotzdem und löscht den Kanal → Verlauf unwiederbringlich weg. `transcript()` (`:448`) speichert Anhänge nur als Discord-CDN-Links; diese signierten Links laufen ab.
  - *Umsetzung:* ohne gespeichertes Transkript **kein** Löschen – Kanal sperren und umbenennen (`geschlossen-…`), Team informieren, Knopf „Erneut archivieren“; Transkript zusätzlich in den Ticket-Log-Kanal hochladen; Anhänge bis zu einem Größenlimit mitsichern (E-7); optional lesbares HTML-Transkript.
  - *Fertig wenn:* Test mit Fake-Kanal, dessen `history()` fehlschlägt → Kanal bleibt bestehen, Ticketstatus nachvollziehbar.

- [x] **K-07 Buttons funktionieren nach einem Neustart nicht mehr** [Nachgewiesen] — **erledigt:** 8a5014b – `tests/test_persistent_buttons.py` (5 Tests): Handler für neue (`pulse:suite-ticket:…`) und alte custom_ids nach simuliertem Neustart; Büro-Anfrage wird aus der DB wiederhergestellt.
  - *Befund:* `TicketView` in `cogs/pulse_suite.py:681` (interne Fälle aus `/pulse-ticket`) hat `timeout=None`, aber keine `custom_id`s und wird nie per `add_view` registriert. `BueroUebernehmenView` (`cogs/buero_system.py:251`) speichert Nutzer und Büro im Objekt, nutzt eine feste `custom_id` (`buero_uebernehmen_btn`) und wird nach einem Neustart ebenfalls nicht registriert. Folge nach jedem Deploy: „Interaktion fehlgeschlagen“ auf allen offenen Fällen und Büro-Anfragen.
  - *Umsetzung:* `discord.ui.DynamicItem` mit dem Zustand in der `custom_id` (z. B. `pulse:suite-ticket:claim:<id>`, `pulse:buero:take:<user>:<kanal>`), Registrierung per `bot.add_dynamic_items(...)`; Fallback für bereits gepostete alte Nachrichten (alte `custom_id` erkennen, Zustand aus DB/Nachricht ableiten oder freundlich auf eine neue Anfrage verweisen). Alle übrigen Views prüfen und als Tabelle im Bericht aufführen.
  - *Fertig wenn:* Test – nach simuliertem Neustart gibt es Handler für neue und alte `custom_id`s.

- [x] **K-08 Der globale Slash-Fehlerhandler wird nie aufgerufen** [Nachgewiesen] — **erledigt:** 1d0f5d3 – `tests/test_command_errors.py` (5 Tests): jeder Slash-Fehler wird beantwortet, unerwartete Fehler mit Fehler-ID.
  - *Befund:* `cogs/pulse_pro.py:470` registriert `on_app_command_error` als Listener – dieses Event dispatcht discord.py nicht (nur `app_command_completion`). Fehler und fehlende Rechte aus `app_commands.checks.has_permissions(...)` landen nur im Log; Nutzer sehen „Die Anwendung reagiert nicht“.
  - *Umsetzung:* Handler über `bot.tree.error` bzw. eine eigene `CommandTree`-Unterklasse in `main.py`: `MissingPermissions`, `CheckFailure`, `CommandOnCooldown`, `TransformerError` → passende ephemere Meldung; sonst Fehler-ID + Log (P-10); je nach `interaction.response.is_done()` per Followup.
  - *Fertig wenn:* Unit-Test mit Fake-Interaction für jede Fehlerart.

- [x] **K-09 Bestätigungsdialoge (Warnung, Kick, Rangwechsel) laufen in Discord-Timeouts** [Nachgewiesen] — **erledigt:** 1470120 – `tests/test_confirm_dialog.py` (4 Tests): Antwort kommt vor der Arbeit, Doppelklick erzeugt keine zweite Aktion, Rechte werden beim Klick neu geprüft, Ablauf wird angezeigt.
  - *Befund:* Die `Confirm`-Callbacks in `cogs/pulse_suite.py` (z. B. `/warn`, `:994–1063`) führen mehrere Discord-Aufrufe aus (Rollen, Team-Update, DM), **bevor** sie die Interaktion beantworten (`x.response.edit_message` erst in `:1063`). Dauert das wegen Rate-Limits länger als 3 s: „Interaktion fehlgeschlagen“, obwohl die Warnung gespeichert ist – Gefahr doppelter Warnungen. `Confirm` (`:666`) hat kein `on_timeout` (Buttons bleiben klickbar und schlagen fehl), zeigt rohe Exception-Texte (`str(e)`) und prüft Rechte/Notfallsperre beim Klick nicht erneut.
  - *Umsetzung:* sofort `edit_message(content="⏳ Wird ausgeführt …", view=None)` bzw. `defer`, danach `edit_original_response`; `on_timeout` deaktiviert die Buttons; generische Fehlermeldung mit Fehler-ID; `allowed()`/`locked()` beim Bestätigen erneut prüfen; ein Klick = genau eine Aktion.
  - *Fertig wenn:* Test mit verzögertem Fake-Discord – Antwort kommt vor der Arbeit; Doppelklick erzeugt keine zweite Warnung.

- [x] **K-10 Voice-Support-Audio im Docker-Image nicht lauffähig** [Nachgewiesen] — **erledigt:** 63a7743 – `tests/test_audio_check.py` (3 Tests); Prüfskript `python pulse_audio_check.py`. Im gebauten Container **nicht ausführbar** (kein Docker auf dem Entwicklungsrechner); lokal: ffmpeg fehlt, opus OK.
  - *Befund:* `cogs/voice_support.py:1122` nutzt `discord.FFmpegPCMAudio` → braucht das Programm `ffmpeg` und die Bibliothek `libopus`. Das `Dockerfile` (python:3.14-slim) installiert nur `ca-certificates`. Auf dem VPS fallen Ansage und Wartemusik damit aus.
  - *Umsetzung:* `ffmpeg` und `libopus0` installieren; Start-Diagnose (`shutil.which("ffmpeg")`, `discord.opus.is_loaded()`) in Systemstatus und `/pulse-diagnose`; klare Log-Meldung statt stillem Ausfall.
  - *Fertig wenn:* Prüfskript im gebauten Container meldet ffmpeg und opus OK (ohne Docker: als „nicht ausführbar“ berichten).

- [x] **K-11 Honeypot bannt jeden – ohne Spam aufzuräumen** [Nachgewiesen, Umsetzung nach E-1] — **verworfen: Entscheidung E-1** (Honeypot bleibt wie bisher).
  - *Befund:* `cogs/honeypot.py:167` `guild.ban(author, reason=…)` ohne Ausnahme für Team/Admins und ohne `delete_message_seconds` → ein Fehlklick eines Teamlers = Bann; Spam eines gehackten Kontos in anderen Kanälen bleibt stehen. Protokolliert wird nur per `print`.
  - *Umsetzung:* Ausnahmen (Teamrollen/Admins → nur löschen + Alarm), `delete_message_seconds`, optional Softban, Eintrag im Mod-Log, Liste der Auslösungen im Dashboard mit „Entbannen“, Mehrfachauslösung pro Nutzer entprellen.
  - *Fertig wenn:* Tests für Teamler, normales Mitglied, Webhook.

- [x] **K-12 Das Log-Modul protokolliert nie** [Nachgewiesen] — **erledigt:** 08cbd5b – `tests/test_advanced_logs.py` (3 Tests): konfigurierter Kanal erhält Logs, ohne Kanal einmaliger Hinweis + Zeile im Systemstatus.
  - *Befund:* `cogs/advanced_logs.py:9` `self.log_channel_id = 0` (fest im Code) → `get_log_channel` liefert immer `None`; Bearbeitungen, Voice- und Rollenänderungen werden nirgends protokolliert, obwohl `docs/PULSE_DISCORD_DESIGN.md` das Modul als umgestellt führt.
  - *Umsetzung:* Log-Kanal in Verwaltung → Discord-Auftritt konfigurierbar (Setting in `pulse.db`); Ausbau in BOT-02.
  - *Fertig wenn:* Test – konfigurierter Kanal erhält Logs; nicht konfiguriert → einmaliger Hinweis im Systemstatus statt Stille.

- [x] **K-13 Abhängigkeiten** [Nachgewiesen/Prüfen] — **erledigt:** f7730d8 – `tests/test_dependencies.py`: `discord.py>=2.6,<3`, `tzdata`, alle 63 Module importierbar.
  - `requirements.txt` erlaubt `discord.py>=2.3`, der Code nutzt aber `ui.LayoutView`/`ui.Container` (Components V2, erst ab discord.py 2.6) → Untergrenze `>=2.6` (prüfen, welche Version produktiv läuft).
  - [Prüfen] Zeitzonendaten: `cogs/activity_check.py:24` und `pulse_activity_utils.py:8` laden `ZoneInfo("Europe/Berlin")` beim Import – fehlen die IANA-Daten im Container, lädt der ganze Cog nicht; `webserver.py:159` fällt dagegen still auf die Container-Zeitzone (meist UTC) zurück (falsche Uhrzeiten). → `tzdata` in die Requirements + Start-Check.
  - *Fertig wenn:* frische venv mit den Requirements importiert alle Module; Test für den Zeitzonen-Fallback.

- [x] **K-14 CSV-Formel-Injection in Exporten** [Nachgewiesen] — **erledigt:** efea9dc – `tests/test_phase1_security.py`: beide CSV-Exporte neutralisieren `=`/`+`/`-`/`@`-Zellen.
  - *Befund:* `/export/team.csv` (`pulse_pro.py:2589`) und `/ultimate/reports.csv` (`pulse_next.py:1041`) schreiben Discord-Anzeigenamen ungeschützt; ein Nickname wie `=HYPERLINK(…)` wird in Excel als Formel ausgeführt. `csv_safe` existiert bereits (`webserver.py:3945`).
  - *Umsetzung:* alle CSV-Exporte über `csv_safe`. *Fertig wenn:* Test mit gefährlichem Namen.

- [x] **K-15 Personenbezogene Laufzeitdaten können ins öffentliche Repo geraten** [Nachgewiesen] — **erledigt:** 6db9cf8 – `tests/test_ignore_files.py`: Laufzeitdateien von `.gitignore` und `.dockerignore` erfasst, keine im Git.
  - *Befund:* Der VPS-Checkout ist gleichzeitig Datenverzeichnis (`compose.yaml`: `./:/app`). `.gitignore` deckt `attachments/` (Beweis-Uploads der Fälle, `pulse_ultimate.py:42`), `ai_moderation_cases.json` (markierte Nachrichten), `social_promo_sources.json` und `*.restore.tmp` nicht ab; `.dockerignore` außerdem nicht `uploads/` und `attachments/`. Ein `git add -A` auf dem Server würde Beweise und Mitgliederdaten veröffentlichen.
  - *Umsetzung:* beide Ignore-Dateien ergänzen; Test, der alle Laufzeitpfade aus der Datenlandkarte per `git check-ignore` prüft. Langfristig D-06.

- [x] **K-16 Routen-Snapshot als Schutz** [Nachgewiesen] — **erledigt:** 1af2fc9 – `tests/test_route_snapshot.py` (3 Tests): jede Methode+Pfad einmal (bekannt: `GET /ultimate` bis A-01), aktive Handler == `docs/routen_snapshot.json`.
  - *Befund:* Welche Implementierung einer Seite aktiv ist, hängt von der Registrierungsreihenfolge am Ende von `webserver.py` und von `remove_and_add` ab; `GET /ultimate` ist doppelt registriert.
  - *Umsetzung (jetzt nur Absicherung):* Test, der nach vollständiger Registrierung prüft, dass jede Kombination aus Pfad und Methode genau einmal existiert und die erwarteten Handler aktiv sind (Snapshot aus P0-2); tote Handler mit Kommentar markieren. Aufräumen in A-01.

- [x] **K-17 Öffentliche API-Dokumentation** [Nachgewiesen in P0-2, neu] — **erledigt:** 4a0cac3 – `tests/test_phase1_security.py`: `/docs`, `/redoc`, `/openapi.json` liefern 404.
  - *Befund:* FastAPI liefert `/docs`, `/docs/oauth2-redirect`, `/redoc` und `/openapi.json` ohne Login aus – alle 266 Endpunkte mit Parametern sind für jeden sichtbar.
  - *Umsetzung:* `docs_url=None, redoc_url=None, openapi_url=None` (optional für Admins hinter `auth(..., admin=True)` wieder bereitstellen).
  - *Fertig wenn:* Test – die vier Pfade liefern ohne Login 404 bzw. Login-Redirect.

---

## 5. Phase 2 – Datenintegrität, Backups, Datenschutz

- [x] **D-01 Ein vollständiges Backup** [Nachgewiesen] — **erledigt:** 924cf5f + 1397a03 – `tests/test_full_backup.py`: Roundtrip (Backup → Änderung → Restore) stellt Datenbanken, JSON, Uploads und Transkripte identisch wieder her; ein Test meldet neue `*.db`/`*.json` ohne Backup-Eintrag.
  - *Befund:* Zwei Backup-Systeme mit unterschiedlichen Lücken – zusammen sichern sie trotzdem nicht alles.
    - JSON-Backup `/backups` (`build_backup`, `webserver.py:7214`): enthält 6 JSON-Dateien, `pulse_db.export_state()` und `activity_check.db`. Es fehlen `audit_logs.json`, `pulse_announcement_archive.json`, `role_nickname_state.json`, `social_promo_sources.json`, `ai_moderation_cases.json`, die Datenbanken `pulse_suite.db` (Dienstplan, Bewerbungsgespräche, Meldefälle aus `/melden`, Suite-Einstellungen), `teamliste.db`, `buero_system.db`, `rp_verwaltung.db`, `flaggenquiz.db` sowie alle Uploads und Beweise. `export_state` (`pulse_db.py:1310`, `TABLES`) exportiert nur 22 Tabellen – `voice_support_case_history` und die rund 35 `next_*`/`ultimate_*`-Tabellen in **derselben** `pulse.db` (Fälle, Abteilungen, Umfragen, Workflows, Rechte-Overrides, API-Schlüssel …) fehlen. Transkripte über 2 MB bzw. über 10 MB gesamt werden still übersprungen.
    - ZIP-Backup `/ultimate/backup` (`pulse_next.py:541`): ganze `pulse.db` + 7 JSON-Dateien + Anhänge – aber ohne `abmeldungen.db`, `activity_check.db`, die fünf weiteren Datenbanken, Uploads und Transkripte.
  - *Umsetzung:* **ein** Format (ZIP mit `manifest.json`: Format-Version, Pulse-Version, Dateiliste, SHA-256, Größen), das alles aus der Datenlandkarte enthält; SQLite-Schnappschüsse über `sqlite3.Connection.backup()` (konsistent auch im WAL-Modus); Discord-Struktur wie bisher; Größenbudget mit sichtbarer Warnung statt stillem Auslassen; beide bisherigen Formate bleiben importierbar; Restore mit Probelauf/Diff-Vorschau (Roadmap P1) und automatischer Sicherheitskopie davor.
  - *Fertig wenn:* Roundtrip-Test (Backup → Restore in Temp-Verzeichnis → alle Dateien und Tabellen identisch) und ein Test, der neue Tabellen/Dateien ohne Backup-Eintrag als Fehler meldet.

- [x] **D-02 Sicheres Wiederherstellen** [Nachgewiesen] — **erledigt:** 924cf5f – Probelauf mit Diff, Restore nur nach Eingabe `WIEDERHERSTELLEN`, Sicherheitskopie davor, Prüfsummen + `integrity_check`, Backup-API statt `os.replace` (auch `/ultimate/backup`), manipuliertes Backup wird vor jeder Änderung abgelehnt.
  - *Befund:* `restore_backup` (`pulse_next.py:584`) ersetzt die laufende `pulse.db` per `os.replace` (`:594`), während Bot, Dashboard und Schleifen Verbindungen öffnen und WAL-Dateien existieren – Korruptions- und Mischstand-Risiko; die JSON-Dateien werden danach einzeln ersetzt.
  - *Umsetzung:* Wartungsmodus (F-04) an → Schreibzugriffe und Jobs pausieren → Restore per Backup-API in die bestehende Datei → `PRAGMA integrity_check` → Wartungsmodus aus; alles im Audit-Log.

- [x] **D-03 Backups blockieren den Bot nicht** [Nachgewiesen] — **erledigt:** 924cf5f – Discord-Struktur im Event-Loop, Datei-/ZIP-Arbeit im Thread; Verhaltenstest prüft die Threads.
  - *Befund:* `create_backup()` (ZIP mit bis zu 250 MB Anhängen) läuft synchron in einer async-Route (`pulse_next.py:1060`) und blockiert Event-Loop und Discord-Heartbeat. `build_backup` läuft per `asyncio.to_thread` (`webserver.py:7480`), iteriert dort aber `guild.members`/`roles`/`channels` – die discord.py-Caches sind nicht threadsicher.
  - *Umsetzung:* Discord-Schnappschuss im Event-Loop (nur Daten kopieren), Datei- und ZIP-Arbeit im Thread; Fortschritt und Ergebnis im Dashboard.

- [x] **D-04 Externe Kopie & automatischer Restore-Test** [Idee] — **erledigt:** 924cf5f – Komplett-Backup im bestehenden Auto-Lauf, Aufbewahrung 7/4/6, wöchentlicher Restore-Test, Ergebnis im Systemstatus; externe Kopie bleibt der vorhandene Discord-Upload des JSON-Backups (Komplett-ZIP meist zu groß für Discord).
  - Aufbewahrung nach Schema (z. B. 7 täglich / 4 wöchentlich / 6 monatlich); Kopie außerhalb des Servers (der vorhandene private Discord-Kanal-Upload, optional S3-kompatibel – nur konfigurierbar, kein Pflichtdienst); wöchentlicher automatischer Restore-Test in ein Temp-Verzeichnis, Ergebnis im Systemstatus.

- [x] **D-05 Ein einziger Datenbankpfad** [Nachgewiesen] — **erledigt:** a25ed3a – `tests/test_db_path.py`: eigener Pfad und `PULSE_DB_PATH` gelten für alle Module.
  - `PULSE_DB_PATH` gilt nur in `pulse_db.py`; `pulse_ultimate.py:44` (`ULTIMATE_DB = BASE / "pulse.db"`) und damit `pulse_next.py` nutzen fest `pulse.db` → bei abweichendem Pfad entstehen zwei verschiedene Datenbanken. Eine Quelle für alle Module + Test.

- [x] **D-06 Daten vom Code trennen** [Idee] — **erledigt in Phase 13** (`docs/PHASE13_BERICHT.md`, Branch `hardening/phase13-data-isolation`): zentrale Pfadverwaltung `pulse_paths.py` (`PULSE_DATA_DIR`, ohne Variable wie bisher das Arbeitsverzeichnis), alle Laufzeitpfade in 31 Modulen umgestellt, Container read-only mit `/data`-Mount, Migration `scripts/pulse_migrate_data.py` (Probelauf, Backup, Prüfsummen, idempotent, Start-Sperre bei abgebrochener Migration). Die VPS-Migration selbst ist ein manueller, freigabepflichtiger Schritt (`VPS_DEPLOYMENT.md`). Tests: `tests/test_phase13_data_isolation.py`. **Phase 13.1** (`docs/PHASE13_1_BERICHT.md`, PR #94): Migration fail-closed (Docker-Nachweis), atomar ohne Überschreiben (`os.link`), Symlink-/Traversal-Schutz, Rückweg-Werkzeug `--rollback`, Deploy-Sperre in allen Root-Skripten, kein Start auf veralteten Daten nach gescheitertem Layoutwechsel, echter Docker-Compose-Integrationstest in CI, Gitleaks-Altbefunde per Fingerprint bewertet. Offen: VPS-Migration (Freigabe).
  - `PULSE_DATA_DIR` (Default = heutiges Verzeichnis, damit nichts bricht), alle Pfade darüber auflösen; optionale, idempotente Migration mit Backup vorher; im Docker-Setup Daten auf eigenem Volume statt `./:/app`. Voraussetzung für sichere Deploys (O-03).

- [ ] **D-07 Wachsende Dateien begrenzen** [Nachgewiesen] — **verschoben (Teil erledigt):** 8fb2ce1 – Größenwarnung im Systemstatus ab 5 MB; das Auslagern selbst folgt mit A-05, weil 31 Stellen die Schicht-Historie für Gesamtwerte lesen und ein Archiv Zahlen verändern würde. **Phase 14.7 (Teil, Schichthistorie):** `pulse_shift_history` verschiebt `shifts.json → history` auf Wunsch (`PULSE_SHIFT_HISTORY_BACKEND=sqlite`, Start) verlustfrei nach `pulse.db` und zurück; nachgewiesen in `tests/test_shift_history_sqlite.py` (20.000 Einträge, gleiche Teamstunden/Wochenwerte/Dashboard-Ausgaben, Abbruch, Wiederholung, Rückweg, Backup/Restore) – `docs/PHASE14_7_BERICHT.md`. Offen bleiben bewusst: Produktionsumstellung (Freigabe), `logs.json`, `applications.json`; daher noch nicht abgehakt.
  - `shifts.json` (die `history` wird nie gekürzt), `logs.json` und `applications.json` wachsen unbegrenzt und werden bei fast jeder Seite komplett geparst (P-03, P-05). Schrittweise nach SQLite (A-05) oder archivieren (z. B. Jahresdateien) – mit Migration, Lese-Fallback und Backup davor.

- [x] **D-08 Datenschutz (DSGVO)** [Idee, nach E-3 und E-9] — **erledigt:** ff42797, 46af453, e841115, 04f63b9 – `tests/test_privacy.py` (14): Teamakte nach E-3, Fristen standardmäßig aus und nur mit frischem Backup, offene Bewerbungen bleiben, Bereinigung protokolliert, Auskunft je Discord-ID, Hinweis auf `/apply`.
  - Datenschutzhinweis auf `/apply` (welche Daten, Zweck, wer sieht sie, wie lange); konfigurierbare Aufbewahrungsfristen mit automatischer, protokollierter Bereinigung; Export „Alles über Discord-ID X“ (Auskunft) und geprüfte Löschung; Hinweis auf externe KI-Verarbeitung, sobald KI-Moderation oder KI-Support aktiv sind; Sichtbarkeit der Teamakte nach E-3.

- [ ] **D-09 Zeitstempel vereinheitlichen** [Nachgewiesen] — **teilweise:** 5597f26 – `tests/test_pulse_time.py`: toleranter Parser für alle Altformate, Anzeige in Berlin-Zeit, drei Leser umgestellt; neue Daten werden noch in den bisherigen Formaten geschrieben (Umstellung der Schreibstellen mit A-05, damit keine Anzeige bricht).
  - Heute gemischt: `%d.%m.%Y %H:%M` (Warnungen, Bewerbungen), `%d.%m.%Y %H:%M:%S` (Audit), `%Y-%m-%d` (Schichten), ISO mit Berlin-Offset, ISO in UTC (`pulse_db._now`), naive `datetime.now()`. Neue Daten: ISO 8601 mit Zeitzone; ein toleranter Parser für alle Altformate; Anzeige in Europe/Berlin. Keine Massenumschreibung alter Daten ohne Backup und Test.

- [x] **D-10 Konsistenz-Diagnose** [Idee, Roadmap P2] – nur berichtend: aktive Schichten ohne Mitglied, offene Tickets ohne Kanal, Warnrollen ≠ aktive Warnungen, Abmeldungen mit Ende vor Beginn, verwaiste Voice-Fälle, Panels ohne Nachricht. Ergebnis im Systemstatus, keine automatische Korrektur. — **erledigt:** 8fb2ce1 – `tests/test_consistency.py`: verwaiste Schichten/Tickets/Voice-Fälle, falsche Warnrollen, Abmeldungen mit Ende vor Beginn werden gemeldet, nichts wird geändert.

---

## 6. Phase 3 – Performance & Stabilität

- [x] **P-01 Event-Loop entlasten** [Nachgewiesen] — **erledigt:** 7951768 – Event-Loop-Verzögerung wird im Automationszyklus gemessen (Warnung ab 250 ms, Systemstatus); 30 SQLite-Verbindungen werden jetzt geschlossen; Live-Abfrage, Backups und Export laufen im Thread.
  - Bot und Dashboard teilen sich einen Event-Loop. Synchrones SQLite (`pulse_db.connect`: globales `RLock`, `timeout=15`, `busy_timeout=3000`), synchrones JSON-Lesen/Schreiben und Datei-I/O laufen direkt in async-Routen, Listenern und Loops. Ein langsamer Schreibzugriff friert Dashboard **und** Discord-Gateway ein.
  - Erst messen: Event-Loop-Verzögerung im bestehenden Automationszyklus (`pulse_ultimate.automation_loop`) erfassen und ab ~250 ms protokollieren. Dann heiße Pfade über `asyncio.to_thread` bzw. eine kleine DB-Fassade. Verbindungen mit `contextlib.closing` schließen – `with sqlite3.connect(...) as c` beendet nur die Transaktion, nicht die Verbindung.

- [x] **P-02 Live-Abfrage verbilligen** [Nachgewiesen] — **erledigt:** 7951768 – `tests/test_stability.py`: Live-Zahlen höchstens alle 15 s und im Thread berechnet; Browser pausiert in Hintergrund-Tabs, Backoff 30 s → 5 min.
  - Jede offene Dashboard-Seite fragt alle 30 s `/api/pulse/live` ab (`pulse_pro.py:72`, auch in Hintergrund-Tabs). `api_live` (`:2597`) ruft `work_stats` (`:354`) auf, das jedes Mal `shifts.json`, `logs.json`, `applications.json`, Abmeldungen, 2.000 Tickets und 2.000 Aufgaben lädt.
  - Geteilter Server-Cache (10–15 s), Polling pausieren bei `document.visibilityState === "hidden"`, Backoff bei Fehlern; später optional SSE (E-5).

- [x] **P-03 Statistik ohne Mehrfachladen** [Nachgewiesen] — **erledigt:** b7a2a2e – `tests/test_performance.py`: identische Ergebnisse zur alten Berechnung (3000 Einträge, Altformate); Historie einmal gruppiert.
  - `stats_v5` (`pulse_pro.py:1582`) ruft `ws.load_shifts()` **in der Schleife pro Teammitglied** auf. `calculate_weekly_seconds` (`webserver.py:1099`) durchläuft pro Mitglied die gesamte ungekürzte Historie mit `strptime` je Eintrag (Mitglieder × Historie) und wird an ~15 Stellen genutzt.
  - Einmal laden, einmal nach Mitglied/Woche gruppieren, kurz cachen. *Fertig wenn:* Test belegt identische Ergebnisse.

- [x] **P-04 `load_config()` cachen** [Nachgewiesen] – liest und parst `config.json` bei jeder angemeldeten Anfrage (`auth`, `webserver.py:816`). Cache über Datei-mtime, Invalidierung bei jedem Speichern. — **erledigt:** b7a2a2e – Config nur bei geänderter Datei geparst, Invalidierung bei jedem Speichern, Kopie je Aufrufer.

- [x] **P-05 Lesen ohne Schreiben** [Nachgewiesen] – `load_shifts()` (`webserver.py:879`) beendet beim Lesen überfällige Schichten und schreibt dabei; es wird an ~40 Stellen aufgerufen (Seiten, Loops, Befehle). Auto-Beenden nur noch im Automationszyklus (`process_overdue_shifts` existiert), die Lesefunktion ohne Seiteneffekt. — **erledigt:** b7a2a2e – `load_shifts` schreibt nicht mehr; Auto-Beenden speichert nur `process_overdue_shifts`.

- [x] **P-06 Job-Registry** [Nachgewiesen/Idee] – 13 `@tasks.loop`s plus eigene Endlosschleifen; `automation_loop` (`pulse_ultimate.py:2272`) verschluckt jede Exception ohne Log (`:2316`). Kleine Registry (Name, Intervall, letzter Lauf, Dauer, letzter Fehler, nächster Lauf) im Systemstatus; Fehler je Job isoliert und geloggt; Jitter. Keine zusätzliche Dauerschleife (`CLAUDE.md` §4) – bestehende Loops schrittweise anmelden. — **erledigt:** 7951768 – Registry (`pulse_jobs`) ohne eigene Schleife: 8 Loops melden Läufe/Dauer/Fehler im Systemstatus; Automationszyklus protokolliert Fehler.

- [x] **P-07 Hintergrund-Tasks absichern** [Nachgewiesen] – `create_task` ohne gehaltene Referenz (z. B. `cogs/pulse_suite.py:227`): der Task kann vom Garbage Collector eingesammelt werden, Fehler gehen verloren. Zentraler `spawn()`-Helfer (Referenz-Set + Fehler-Logging). — **erledigt:** 361ed21 – `pulse_tasks.spawn` hält Referenzen (max. 200) und protokolliert Fehler mit Fehler-ID.

- [x] **P-08 `on_ready`-Arbeit nur einmal** [Nachgewiesen] – `on_ready` feuert nach Reconnects erneut. Betroffen u. a.: `main.py:113` (Warnrollen-Abgleich über alle Mitglieder), `cogs/honeypot.py`, `cogs/server_stats.py`, `cogs/pulse_suite.py` (Security-Check), `cogs/voice_support.py`, `cogs/flaggenquiz.py`, `cogs/pulse_pro.py`. Einmal pro Prozess (Flag) oder `setup_hook`/`cog_load` + `wait_until_ready`. — **erledigt:** 361ed21 – Start-Arbeit einmal pro Prozess, Voice-Support/Server-Statistik höchstens alle 10 min.

- [x] **P-09 Slash-Command-Sync** [Nachgewiesen] – `main.py:70` synchronisiert bei jedem Start global. Besser: Hash des Befehlsbaums speichern und nur bei Änderung syncen; für den einen Server `tree.copy_global_to(guild=…)` + `tree.sync(guild=…)` (sofort sichtbar) – vorher klären, wie doppelte Einträge (global + Server) vermieden werden; manueller Sync-Knopf für Admins. — **erledigt:** 361ed21 – Sync nur bei geändertem Befehlsbaum (Hash), `PULSE_FORCE_COMMAND_SYNC`, Knopf im Systemstatus; Befehle bleiben global (keine Doppelung).

- [ ] **P-10 Logging statt `print`** [Nachgewiesen] – ~320 `print()`-Aufrufe, das `logging`-Modul wird nirgends genutzt; dazu ~290 `except Exception`-Blöcke, viele davon mit `pass`. Logger je Modul, Level, einheitliches Format mit Zeit, begrenzter `RotatingFileHandler`, Fehler-ID; Ringpuffer der letzten 200 Fehler für den Systemstatus. Stille `except … pass` dort, wo Fehler relevant sind, durch geloggte Behandlung ersetzen. Nie Tokens, Secrets oder Nachrichteninhalte von Nutzern loggen. — **teilweise:** 7951768 – zentraler Logger mit `logs/pulse.log` (1 MB × 3) und Ringpuffer der letzten 200 Fehler im Systemstatus; die übrigen ~320 `print()` und stillen `except … pass` werden modulweise in Phase 4 (A-02/A-07) umgestellt. Weitere stille Stellen behoben: Ticketkanal-Löschung (`tests/test_ticket_archive.py`), Sitzungs-Widerruf bei Teamaustritt/Logout.

- [x] **P-11 Freundliche Fehlerseite** [Nachgewiesen] – unerwartete Exceptions liefern „Internal Server Error“. Globaler Handler mit Fehlerseite + Fehler-ID; Traceback nur ins Log. — **erledigt:** 62d3744 – Fehlerseite/JSON mit Fehler-ID, Traceback nur im Log.

- [x] **P-12 OAuth robust** [Nachgewiesen] – `callback` (`webserver.py:2699–2705`) prüft weder Statuscode noch JSON-Fehler und setzt keine expliziten Timeouts; ein Discord-429/5xx wird zum 500er. Status prüfen, Timeouts setzen, verständliche Meldung mit „erneut versuchen“. — **erledigt:** 62d3744 – Timeouts, 429/5xx/4xx, ungültiges JSON → verständliche Meldung statt 500.

- [x] **P-13 Keine doppelten Tickets** [Nachgewiesen] – `start_ticket_flow` (`cogs/tickets.py:769`) lädt 2.000 Tickets und filtert in Python; zwei schnelle Klicks können zwei Tickets erzeugen. Indizierte Abfrage + Sperre pro Nutzer, bis der Kanal existiert. — **erledigt:** 62d3744 – indizierte Abfrage + Sperre pro Nutzer; Test: zwei schnelle Klicks → ein Ticket.

- [x] **P-14 Speicher messen statt schätzen** [Prüfen, nach E-5] – tatsächliche Kosten von Presence-Intent und Member-Cache messen (Startbericht/Systemstatus), bevor etwas geändert wird; den Doku-Widerspruch 250 MB vs. 4–8 GB auflösen. — **erledigt:** 7951768 – Systemstatus zeigt gemessenen RAM (Prozess, Spitze, Container), Mitglieder im Cache und Presence-Status; Entscheidung über Intents erst mit echten Messwerten.

---

## 7. Phase 4 – Architektur & Wartbarkeit (schrittweise, testgesichert)

- [ ] **A-01 Module statt Überschreiben** [Nachgewiesen] – Die Versionsschichten v4 (`pulse_features`) → v5 (`pulse_pro`) → `pulse_ultimate` → TeamOS 2.0 (`pulse_next`) → `pulse_intelligence` → Suite (`cogs/pulse_suite.py`) ersetzen sich gegenseitig per `remove_and_add` und Monkeypatching (`pulse_pro.make_shell_helpers` überschreibt `ws.compute_perms`, `ws.log_audit`, `ws.get_sidebar_html`, `ws.get_head_html`). Ziel: je Fachbereich ein Modul mit `APIRouter`, feste Registrierungsreihenfolge, keine Laufzeit-Ersetzungen. Tote Handler erst löschen, wenn der Routen-Snapshot (K-16) belegt, dass sie unerreichbar sind. Bereich für Bereich, jeweils mit Snapshot-Test (gleiche URLs, Methoden, Rechte). — **vorbereitet:** 6d58788 – Entscheidungsvorlage `docs/SEITEN_ALT_NEU.md` (22 tote Handler/Schleifen, 19 Aufgaben mit mehreren Adressen); `pulse_pro` ersetzt `compute_perms` nicht mehr zur Laufzeit. Zusammenführen/Löschen nach deiner Entscheidung (E-6). — **Schritt 2026-10-29:** `GET /healthz` liegt in `pulse_health_api.py` (gleiche URL/Position/Statuslogik, Snapshot aktualisiert). **Befund:** FastAPI 0.162 legt bei `include_router` einen verzögerten `_IncludedRouter` ohne `.path` in `app.routes` – das brach `pulse_pro.register` (`'NoneType' … startswith`). Echte `APIRouter` erst, wenn `remove_and_add`, `tools/route_map.py` und die Routen-/Rechte-Tests damit umgehen; bis dahin flach per `app.add_api_route`. — *Phase-14-Planung (§11 Phase-13.1-Bericht):* A-01a die 3 Laufzeit-Patches aus `pulse_pro.make_shell_helpers` durch explizite Registrierung ersetzen; A-01b die 32 `remove_and_add`-Stellen je Bereich hinter dem Routen-Snapshot abbauen. — **A-01a erledigt (Phase 14.1, `docs/PHASE14_1_BERICHT.md`):** `webserver.register_shell_hook` mit den Slots `head_extra`/`sidebar`/`audit_listener`; `pulse_pro` registriert statt `get_head_html`/`get_sidebar_html`/`log_audit` zu ersetzen; HTML byte-identisch (Snapshot aus dem alten Stand), `tests/test_shell_hooks.py`. Offen: A-01b – **analysiert (Phase 14.2, `docs/PHASE14_2_VORBEREITUNG.md`):** 31 `remove_and_add`-Aufrufe (nicht 32), 21 unerreichbare Handler, Pakete 0–8; Umsetzung erst nach Freigabe.

- [ ] **A-02 `webserver.py` zerlegen** [Nachgewiesen] – ~9.900 Zeilen. Reihenfolge: Konstanten/IDs (Werte unverändert, z. B. `pulse_ids.py`) → Auth/Session → Persistenz-Helfer → UI-Bausteine → Routen je Bereich. Die Sammelroute `POST /action` (`webserver.py:8711`, 51 Parameter, über 20 Aktionen von Bewerbung bis Rechteverwaltung) in eigene Endpunkte aufteilen; `/action` nimmt alte Formulare weiter an und delegiert. — **Schritt 1 erledigt:** Konstanten/IDs liegen in `pulse_ids.py` (A-09). Auth/Session, Persistenz, UI-Bausteine und die Aufteilung von `POST /action` folgen schrittweise. — *Phase-14-Planung:* A-02a Auth/Session, A-02b Persistenz-Helfer (`pulse_store`), A-02c `POST /action` aufteilen, A-02d UI/Routen je Bereich (Stand: 11.981 Zeilen, 78 eigene Routen).

- [x] **A-03 Ein Rechte-Modell** [Nachgewiesen] – heute vier Ebenen: `config.permissions` je Discord-Rolle (`compute_perms`, `webserver.py:764`), Dashboard-Profile `dashboard_roles` (+ `EXTRA_PERMS` in `pulse_pro.patched_compute`, `:130`), Nutzer-Overrides `next_permission_overrides` (`pulse_next.has_perm`, `:264`) und Slash-Befehlsrollen `command_roles` (`cogs/pulse_suite.py:237`) – plus implizite Vererbungen (`can_promote` ⇒ Bewerbungen, Tickets, Ankündigungen, Beförderungen) an mehreren Stellen. [Prüfen] `patched_compute` übernimmt `EXTRA_PERMS` aus `config.permissions` für **jede** Rolle des Mitglieds, das Original nur für Teamrollen → Verstoß gegen deny-by-default. Ziel: `effective_permissions(member)` mit Herkunft je Recht, eine Stelle für Vererbungen, Seite „Wer darf was?“ (F-01) und ein Test, der für jede Route prüft: nicht angemeldet → Login-Redirect, ohne Recht → 403. — **erledigt:** 7e62315 + 6c5f12f – `tests/test_permissions.py`: ein Modell (`pulse_permissions`) mit Herkunft je Recht; Sicherheitsfix – Zusatzrechte nur noch über Teamrollen; E-4 umgesetzt; Routen-Matrix (ohne Login nie Inhalt und keine Änderung – 111 GET- und 120 schreibende Routen; Verwaltungsseiten → 403). Seite „Wer darf was?“ folgt in F-01.

- [ ] **A-04 Keine Seiteneffekte beim Import** [Nachgewiesen] – `import webserver` legt `abmeldungen.db` an (`webserver.py:248`), erstellt `backups/` (`:156`) und ggf. `.session_secret` (`:736`); `pulse_db` legt beim Import `pulse.db` an. Explizite Initialisierung beim Start (FastAPI-`lifespan` statt des veralteten `@app.on_event`, `webserver.py:8148`). — **teilweise:** cbe3f45 – `webserver` und `pulse_db` ohne Seiteneffekte beim Import (`init_runtime()` in `main.py` und FastAPI-`lifespan` statt `on_event`), Session-Schlüssel erst bei Bedarf; `pulse_ultimate`/`pulse_next` legen `attachments/` und `backups/` nicht mehr beim Import an (`tests/test_import_side_effects.py`). Offen: Tabellenanlage der Altmodule während der Registrierung. — **Phase 9 (31.10.2026):** erledigt – `import webserver`/`import main` ohne chdir, `.env`, DB-Zugriff und Ordner; Registrierung ohne Tabellenanlage, `pulse_db.connect` legt das Schema beim ersten Zugriff an; dazu Lifecycle-Fixes (Automation-Stopp, Cog-Unload, Lifespan-Aufräumen). Details `docs/PHASE9_BERICHT.md`, Tests `tests/test_lifecycle.py`.

- [ ] **A-05 Persistenz zusammenführen** [Idee] — *Phase-14-Planung:* `docs/PHASE13_1_BERICHT.md` §11 (A-05a Migrationsrahmen JSON → Tabelle mit Lese-Fallback und Rückweg, A-05b `logs.json`/Schicht-Historie, A-05c kleine Datenbanken). – neue Daten nur noch in `pulse.db`; versionierte, idempotente Migrationen (`schema_migrations`), Backup vor jeder Migration; JSON → SQLite Datei für Datei mit Lese-Fallback und Rückweg; die kleinen Datenbanken (`rp_verwaltung.db`, `buero_system.db`, `flaggenquiz.db`, `teamliste.db`, `pulse_suite.db`) später in `pulse.db` überführen. — **Grundlage:** b29f10e + 6c5f12f – `pulse_migrations` (versioniert, idempotent, Sicherung vorher, eine Transaktion je Migration); die JSON → SQLite-Umzüge folgen Datei für Datei. — **A-05b Teil 1 (Phase 14.7):** Schichthistorie als erster Umzug über `pulse_migrations.run` (Sicherung, eine Transaktion, `schema_migrations`), Marker in `shifts.json`, Rückweg und Lese-Pfad über `load_json`/`save_json`; Nachweis `tests/test_shift_history_sqlite.py`, `docs/PHASE14_7_BERICHT.md`. `logs.json`, `applications.json` und die kleinen Datenbanken folgen – A-05 bleibt offen.

- [ ] **A-06 Templates mit Autoescape** [Idee] – HTML entsteht in f-Strings (das Escaping über `esc()`/`e()` ist heute meist sauber). Neue und grundlegend überarbeitete Seiten mit Jinja2 + Autoescape; keine Komplettumstellung. — **verschoben:** in dieser Phase entstand keine neue Seite; Jinja2 kommt mit der ersten neuen bzw. grundlegend überarbeiteten Seite (z. B. F-01, U-Phase), keine Komplettumstellung.

- [x] **A-07 Code-Qualität** [Idee] – `ruff` mit konservativem Regelset (ungenutzte Imports/Variablen, `except:` ohne Typ, veränderbare Default-Argumente …) in der CI, zunächst nur für geänderte Dateien; Formatierung nur für angefasste Funktionen; Typ-Hinweise in neuen Modulen. — **erledigt:** 50f65b9 – `ruff.toml` (konservativ, zunächst neue Module), CI prüft mit ruff und installiert die Abhängigkeiten; Funde: Dashboard-Profil-Speichern brach mit NameError ab (behoben, Test), ein toter Housekeeping-Zweig; `tests/test_lint_findings.py` verhindert neue undefinierte Namen.

- [x] **A-08 Testbasis** [Idee] – zentrale Discord-Fakes (`tests/fakes.py`: Guild, Member, Role, Channel, Interaction mit Response/Followup), Routen-×-Rollen-Matrix, Nebenläufigkeitstests (K-04), Backup-Roundtrip (D-01); Quelltext-String-Asserts in `feature_smoke.py` schrittweise durch Verhaltenstests ersetzen. — **erledigt:** b29f10e – `tests/fakes.py` (Guild, Member, Role, Channel, Interaction) mit `tests/test_fakes.py`; Routen-Matrix (A-03), Nebenläufigkeit (K-04) und Backup-Roundtrip (D-01) gibt es; String-Asserts wurden bei jeder Änderung durch Verhaltenstests ersetzt.

- [x] **A-09 IDs zentral und konfigurierbar** [Idee] – alle festen Kanal- und Rollen-IDs an einem Ort (Werte unverändert); wo sinnvoll im Dashboard überschreibbar mit Fallback auf die festen Werte (z. B. Log-Kanal, Honeypot-Kanal, RP-Ankündigungskanal). — **erledigt:** 6dc0bcb – `pulse_ids.py` (28 Werte, unverändert), `tests/test_ids.py` mit Werte-Snapshot und Schutz gegen neue ID-Literale; im Dashboard überschreibbar sind bereits Log-Kanal, RP-Kanal/-Rollen und Ticket-Logkanäle.

---

## 8. Phase 5 – Team-Dashboard: Bedienung & Design

- [ ] **U-01 Navigation aufräumen** [Nachgewiesen, nach E-6] – Leitungen sehen ~66 Navigationseinträge (20 im Hauptbereich + 31 „Command Center“ + 15 „Operations Suite“; `pulse_pro.py` `patched_sidebar`, `pulse_design.py`), und viele Funktionen existieren mehrfach als eigene Seiten: Kalender 3× (`/calendar`, `/ultimate/calendar`, `/suite/calendar` – **drei getrennte Tabellen**, ein Termin erscheint nur im Kalender, in dem er angelegt wurde), Aufgaben 3×, Suche 3×, Team 3×, Team-News 3×, Erfolge/Awards 3×, System 3×, Fälle/Meldungen 2×, Analytics 2×, Übergaben 2×, Freigaben 2×, Backup 2×, Schulungen 2×, Recruiting 2×. Ziel: ~6 Gruppen (Mein Bereich · Team · Support · Recruiting · Planung · Auswertung) + Verwaltung; je Funktion eine kanonische Seite; alte URLs per 308 weiterleiten; Daten zuerst lesend zusammenführen (z. B. ein Kalender zeigt alle drei Quellen), Migration erst nach Freigabe. Checkliste „alte Seite → neue Stelle“ in `docs/ROUTEN.md` – keine Funktion darf verschwinden.

- [x] **U-02 Eine Startseite** (f8cd03f: Startseite je Person, Standard /me; `tests/test_start_page.py` (2)) [Nachgewiesen] – Login und `/` leiten auf `/ultimate`, die Navigation beginnt aber mit „Mein Pulse“ (`/me`). Pro Nutzer wählbare Startseite, Standard `/me`.

- [x] **U-03 Tailwind ohne Play-CDN** (e0c8551, 0556674: /static/pulse.css (Standalone-CLI v3.4.17, Prüfsumme geprüft), Cache „immutable“, CDN nur noch als Rückfall mit Warnung; Vorher/Nachher-Screenshots identisch; `tests/test_static_css.py` (3). PRO_CSS/Inline-JS bleiben vorerst inline → CSP-Nonces später) [Nachgewiesen] – `webserver.py:2267` lädt `https://cdn.tailwindcss.com`; das ist laut Tailwind nur für die Entwicklung gedacht (Kompilierung im Browser, Flackern beim Laden, unversionierte Fremdabhängigkeit, kein SRI möglich). CSS einmalig mit der Tailwind-Standalone-CLI bauen (kein Node nötig) und als `/static/pulse.css` mit Cache-Headern ausliefern; große Inline-Blöcke (`PRO_CSS`, `LEGACY_CSS`, Inline-JS) in statische Dateien. Vorher/Nachher per Screenshot vergleichen.

- [x] **U-04 Formulare** (763d587: Eingaben überleben ?t=err, Doppelklick-Sperre, Fehler bleiben stehen; `tests/test_ui_basics.py`. Inline-Fehler am einzelnen Feld: verschoben (bräuchte Umbau jeder Route auf Feldfehler)) [Nachgewiesen] – Fehler kommen als Redirect mit `?msg=` zurück, Eingaben sind weg. Fehler inline am Feld, Eingaben erhalten, Absenden-Knopf gegen Doppelklick sperren, Ladezustand, einheitliche Bestätigung für destruktive Aktionen.

- [x] **U-05 Listen** (da9ba54: pulse_lists (Suche, Seiten, CSV über csv_safe) in /suite/audit (+ /suite/audit.csv) und /warns (+ /warns.csv); Ticket-Center hatte es schon; Bewerbungs-Historie/Melonly nach E-6; `tests/test_lists.py` (6)) [Idee] – Pagination, Filter, Sortierung und Suche für Audit, Ticket-Historie, Bewerbungs-Historie, Warnungen und Logs; einheitlicher CSV-Export über `csv_safe`.

- [x] **U-06 Barrierefreiheit** (763d587: aria-labels, :focus-visible, prefers-reduced-motion; `tests/test_ui_basics.py`) [Idee] – `aria-label` für Icon-Buttons (☰, ⌕, Emoji-Navigation), sichtbarer Fokus, Kontraste in Hell und Dunkel, Tastaturbedienung der Schnellsuche, `prefers-reduced-motion`.

- [x] **U-07 Mobil** (763d587: 44-px-Touch-Ziele, Tabellen scrollbar, Toast mobil; 375-px-Prüfung im Browser) [Idee] – Tabellen auf schmalen Displays als Karten, Touch-Ziele ≥ 44 px, Sidebar-Verhalten; Screenshots bei 375 px, 768 px und Desktop.

- [x] **U-08 Live-Ansichten** (0556674: Live-Streifen Ticket-Warteschlange und Voice-Warteraum, Teamstatus ohne Hintergrund-Polling, „zuletzt aktualisiert“, Backoff; Polling statt SSE wegen E-5; `tests/test_live_views.py` (5), im Browser gemessen) [Idee, nach E-5] – Ticket-Warteschlange, Voice-Warteraum und Teamstatus mit SSE oder sichtbarkeitsabhängigem Polling, Anzeige „zuletzt aktualisiert“.

- [x] **U-09 Einheitliche Zustände** (763d587: ws.empty_state, ws.time_tag (relative Zeit mit exaktem Tooltip); `tests/test_ui_basics.py`) [Idee] – Leer-, Fehler- und Ladezustände als gemeinsame Komponente; relative Zeiten („vor 5 Min.“) mit exaktem Tooltip.

- [x] **U-10 Teamakte als Zeitleiste** (cd99f17, 245bc2d: pulse_timeline in /member/{id}, E-3 im Builder; Zusammenlegen der vier Personen-Seiten nach E-6; `tests/test_timeline.py` (3)) [Idee, nach E-3] – Warnungen, Beförderungen, Schichten, Tickets, Voice-Fälle, Schulungen und Notizen chronologisch; heute verteilt auf `/member/{id}`, `/ultimate/person/{id}`, `/ultimate/team/{id}` und `/suite/member/{id}` → zusammenführen.

- [x] **U-11 Benachrichtigungen** (d1c0f59: Bereiche abwählen (still als gelesen), Discord-DM opt-in über begrenzte Queue in der 60-s-Schleife, Ruhezeiten, Gruppierung; `tests/test_notify_prefs.py` (9)) [Idee] – Inbox-Einstellungen je Nutzer (welche Ereignisse, optional als Discord-DM), Gruppierung, „alle gelesen“, Ruhezeiten (BOT-12).

- [x] **U-12 Ein Systemstatus** (1f8a02c: Gateway, Cog-Fehler, Zeitzone, feste IDs, Rollenhierarchie, .env-Namen; Zusammenlegen der Systemseiten nach E-6; `tests/test_system_checks.py` (7)) [Nachgewiesen/Idee] – `/system`, `/ultimate/system`, `/ultimate/health-detail`, `/ultimate/security` und Verwaltung → System zu einer Seite zusammenführen: Gateway-Latenz, Event-Loop-Verzögerung (P-01), RAM-Verlauf, Jobs (P-06), letzte Fehler (P-10), Backups + letzter Restore-Test (D-04), ffmpeg/opus (K-10), Zeitzonendaten (K-13), Konfigurationsprüfung (fehlende Kanäle/Rollen, Bot-Rollen-Hierarchie, Platzhalter-Secret, Pflichtwerte der `.env`), Cog-Ladefehler (`main.py` sammelt sie schon in `pulse_failed_cogs`).

- [x] **U-13 Sicherheits-Header** (1ec9e5e: eine Middleware, CSP-Basis, Referer-Fallback, Logout per POST; `tests/test_security_headers.py`) [Nachgewiesen] – zwei Middlewares setzen widersprüchliche Werte (`webserver.py:183` vs. `pulse_pro.py:313`, z. B. `Referrer-Policy`, `Cache-Control`) → eine. Content-Security-Policy schrittweise (sofort: `frame-ancestors 'self'`, `object-src 'none'`, `base-uri 'self'`, `form-action 'self'`; Nonces nach U-03). Der Origin-Check (`pulse_pro.py:313`) greift nur, wenn ein `Origin`-Header da ist → `Referer`-Fallback ergänzen. Logout als POST.

- [x] **U-14 Sitzungen & API-Schlüssel** (b81ab8f, 245bc2d: Sitzungsliste /me/sessions, überall abmelden, Leerlauf-Timeout, Widerruf bei Team-Austritt; API-Schlüssel mit Bereichen, Ablauf, last_used_at, Rate-Limit; `tests/test_sessions_api_keys.py` (13)) [Idee] – serverseitige Sitzungsliste (Gerät, letzte Aktivität) mit „überall abmelden“, automatischer Widerruf bei Team-Austritt, Idle-Timeout; API-Schlüssel (`ultimate_api_keys`) mit Scopes, Ablaufdatum, `last_used_at` und Rate-Limit.

---

## 9. Phase 6 – Discord-Bot: Bedienung & Funktionen

- [x] **BOT-01 Grundregeln für Interaktionen** (9e35cad: PulseView für alle 14 Views (Fehler-ID, Doppelklick-Sperre, Timeout), globaler allowed_mentions-Standard nach Inventur aller gewollten Pings; `tests/test_interactions.py` (11)) [Nachgewiesen/Idee] – ein Helfer für alle Buttons, Modals und Befehle: bei mehr als ~1 s Arbeit sofort `defer`, ephemere Fehler mit Fehler-ID, Rechte beim Klick erneut prüfen, `on_timeout` deaktiviert Buttons, Cooldown gegen Doppelklicks. Globaler `allowed_mentions`-Standard im Bot (`everyone=False, roles=False`) – vorher **alle** gewollten Pings inventarisieren und explizit freigeben (Recruiting-Ping, Verifizierungs-Erinnerung, Ticket-Ping-Rollen, Büro-Rollen, RP-Ankündigung, `/team-ping`).

- [x] **BOT-02 Protokollierung ausbauen** (c7e695c: gelöschte Nachrichten, Beitritt/Austritt mit Kontoalter, Nicknames, Bans/Unbans/Kicks/Timeouts mit Täter (Audit-Log), Rollen, Kanäle, Voice; Kategorien-Kanäle, Ignorierlisten, Sammelversand; `tests/test_log_routing.py` (10)) [Idee, baut auf K-12] – gelöschte Nachrichten (inkl. Anhangsnamen), Bearbeitungen, Beitritt/Austritt mit Kontoalter, Bans/Unbans/Timeouts/Kicks mit Täter über `on_audit_log_entry_create` (Bot braucht „Audit-Log anzeigen“), Rollen- und Nickname-Änderungen, Kanal- und Rollenänderungen, Voice-Bewegungen (abschaltbar, gedrosselt). Kategorien auf eigene Kanäle verteilbar, Ignorierlisten (Kanäle/Rollen/Bots), Sammelversand gegen Rate-Limits, moderner Container-Stil.

- [x] **BOT-03 Dienst-Panel in Discord** (30a7756: /dienst start|pause|weiter|ende|status + dauerhaftes Panel, gleiche Logik wie das Dashboard (apply_shift_action), zweite Erinnerung 4 h vor dem Auto-Ende; `tests/test_dienst.py` (6)) [Idee] – Schichten lassen sich heute nur im Dashboard starten, pausieren und beenden (`POST /shift/action`). Persistentes Panel + `/dienst start|pause|weiter|ende|status` mit derselben Logik und Datenquelle (inkl. der Abmeldungsregeln aus der README), Bereitschaftsrolle wie bisher, Erinnerung „Schicht läuft seit X h“ deutlich vor der 24-h-Grenze.

- [x] **BOT-04 Slash-Befehle ordnen** (cfeb1e9, bf44244: 35 Einrichtungsbefehle mit default_permissions, /pulse-hilfe aus dem Befehlsbaum, Gruppen /team und /setup aus den echten Befehlen (alte Namen als Alias), dazu /dienst, /ticket, /mod; 70 Befehle oben; `tests/test_command_help.py` (7)) [Nachgewiesen, nach E-11] – rund 70 Befehle mit gemischter Benennung (`ticketpanels_setup`, `setup_buero`, `team-kick`, `pulse-diagnose`, `pulsehealth` …) plus Prefix-Altbefehle. Gruppen (`/team …`, `/ticket …`, `/pulse …`, `/setup …`), alte Namen übergangsweise als Alias, `default_permissions`, damit normale Mitglieder Verwaltungsbefehle gar nicht sehen; `/pulse-hilfe` automatisch aus Befehlsbaum und Rechten erzeugen; Discord-Limit von 100 Befehlen im Blick behalten.

- [x] **BOT-05 Eine Logik für Teamaktionen** (f8b4155, d3b8327: issue_team_warning/revoke_team_warning, finish_promotion/finish_demotion für Dashboard und Slash; behoben: /warn-remove hob Sperren nicht auf, /promote ohne Pflicht-Probezeit, /warn ohne Dauer/Beweis/DM/Eskalation; `tests/test_team_warning_domain.py` (12)) [Nachgewiesen] – Warnen ist doppelt implementiert (Dashboard `webserver.py:9193`, Slash `/warn` `cogs/pulse_suite.py:987`); ebenso Warn-Rücknahme und Rangwechsel. Slash-Warnungen gelten fest 7 Tage ohne Beweis, im Dashboard sind 1–365 Tage + Beweis-Link möglich. Eine gemeinsame Domänenfunktion je Aktion (Prüfungen, Speichern über K-04, Rollen, Folgen, Team-Update, DM, Audit) für beide Wege; `/warn` bekommt optionale Dauer und Beweis. [Prüfen] ob die Slash-Rücknahme dieselben Warnfolgen aufhebt wie das Dashboard.

- [x] **BOT-06 Tickets** (7ddc1a5: Schnellantworten, Übergabe mit Pflichtnotiz, Ticket-Sperre mit Ablauf, Tags, Bewertungsauswertung, lesbares Transkript; SLA-Timer gab es schon; `tests/test_ticket_extras.py` (9)) [Idee] – Schnellantworten je Kategorie (Roadmap P2), Übergabe mit Pflicht-Notiz (Roadmap P2), Ticket-Sperre für Missbrauch (mit Ablauf), Tags, sichtbarer SLA-Timer, lesbares HTML-Transkript im Dashboard, Auswertung der Bewertungen je Kategorie und Teamler.

- [x] **BOT-07 Voice-Support** (efad20f: Warteposition und Wartezeit-Schätzung per DM, „Ticket statt Warten“ außerhalb der Zeiten, Supporter-Statistik, sichtbarer Audio-Hinweis; Fall-Historie (1000); K-04-Schutz bestand; `tests/test_voice_queue.py` (6)) [Idee] – Warteposition und geschätzte Wartezeit für den Bürger; außerhalb der Öffnungszeiten das Angebot „Ticket statt Warten“; Supporter-Statistik im Dashboard; sichtbarer Hinweis, wenn Audio nicht verfügbar ist (K-10). Die Blob-Race aus K-04 hier mit absichern.

- [x] **BOT-08 Raid- und Spam-Frühwarnung** (2e11200: Beitrittswelle/neue Konten, Massen-Erwähnungen, Kopier-Spam; nur Alarm mit Vorschlägen, keine automatische Änderung; `tests/test_raid_watch.py` (7)) [Idee] – viele Beitritte/neue Konten in kurzer Zeit, Massen-Erwähnungen, identische Nachrichten in vielen Kanälen → Alarm an das Team mit Handlungsvorschlägen. Keine automatische Änderung von Kanalrechten (laut Roadmap bewusst verworfen).

- [x] **BOT-09 Bürger-Moderation** (1761b96, d78582d: /mod warn|timeout|kick|ban|akte mit Fall-ID, Beweis, Ablauf, DM mit Einspruchsweg, Fallakte /mod-cases; Pulse-Recht UND Discord-Recht nötig; `tests/test_mod_cases.py` (8)) [Idee, nur nach E-8] – `/mod timeout|kick|ban|warn` mit Fall-ID, Beweis, Ablauf, DM-Vorlage, Einspruch per Ticket und Fallakte im Dashboard; dieselben Hierarchie- und Audit-Regeln wie bei Teamaktionen.

- [x] **BOT-10 Bewerbungs-Kommunikation** (8b3cab5: Vorlagen (Standard = bisherige Texte), Eingangs-DM, Gesprächstermin mit Kalendereintrag und Erinnerung; Prüfer-Rubrik bestand; `tests/test_app_comms.py` (6)) [Idee] – DMs mit Vorlagen für Eingang, Gesprächseinladung (Termin im Kalender + Erinnerung) und Ergebnis; mehrere Prüfer mit Bewertungsrubrik (Roadmap P3).

- [x] **BOT-11 On- und Offboarding** (d49a0a5: Checklisten automatisch bei Teambeitritt/-austritt, Mentor, Abschluss auditiert; `tests/test_checklists.py` (6)) [Idee] – die vorhandene Onboarding-Aufgabe (`cogs/pulse_suite.py` ~`:1393`) zur Checkliste ausbauen (Rollen, Kanäle, Pflichtschulungen, Mentor, Probezeit); Offboarding-Checkliste bei Team-Kick/-Exit.

- [x] **BOT-12 Ruhezeiten** (97846a8: Server-Ruhezeit (Standard 23–8) + persönliche, DMs warten statt verworfen und kommen gebündelt; `tests/test_notify_prefs.py` (+4)) [Idee, Roadmap P3] – Erinnerungs-DMs nicht nachts (konfigurierbar, z. B. 23–8 Uhr), mehrere Hinweise bündeln.

- [x] **BOT-13 Status-Anzeige an einer Stelle** (80dac22: pulse_presence, „N im Dienst · M Tickets offen“, höchstens alle 5 min und nur bei Änderung; `tests/test_presence.py` (4)) [Nachgewiesen] – die Bot-Präsenz wird an drei Stellen gesetzt (`main.py:116`, `cogs/pulse_pro.py:168/186`, `cogs/pulse_suite.py:858`). Eine Stelle, z. B. „N im Dienst · M Tickets offen“, höchstens alle paar Minuten aktualisiert.

- [x] **BOT-14 Panel-Übersicht** (a5c87aa: Verwaltung → Discord-Panels mit Status, Link und Neuveröffentlichen (Tickets, Verifizierung, Dienst); `tests/test_panels.py` (5)) [Idee] – die Verwaltung zeigt alle persistenten Panels (Tickets, Verifizierung, RP-Verwaltung, Büro, Abmeldung, Activity Check, Honeypot-Warnung, Quiz) mit Kanal, Nachrichten-Link, Stil, letzter Aktualisierung und dem Knopf „neu veröffentlichen/reparieren“.

---

## 10. Phase 7 – Neue Funktionen [Idee] (jeweils abschaltbar, Standard aus, Reihenfolge nach meinem OK)

- [x] **F-01 „Wer darf was?“** — **erledigt:** 3c9cf96 – je einzeln schaltbar (Standard aus), `tests/test_phase7.py` (18 Tests, grün am 23.10.2026). – Rechte-Matrix und Simulation für eine bestimmte Person (Roadmap P2), Grundlage A-03.
- [x] **F-02 Kanal-Sichtprüfung für Unverifizierte** — **erledigt:** 3c9cf96 – je einzeln schaltbar (Standard aus), `tests/test_phase7.py` (18 Tests, grün am 23.10.2026). – nur lesend, Warnliste (Roadmap P1).
- [x] **F-03 Einstellungs-Historie** — **erledigt:** 3c9cf96 – je einzeln schaltbar (Standard aus), `tests/test_phase7.py` (18 Tests, grün am 23.10.2026). – wer hat wann welche Einstellung geändert, mit Diff und „zurücksetzen“.
- [x] **F-04 Wartungsmodus** — **erledigt:** 3c9cf96 – je einzeln schaltbar (Standard aus), `tests/test_phase7.py` (18 Tests, grün am 23.10.2026). – Banner im Dashboard, Schreibaktionen gesperrt, Bot-Automationen pausiert (Voraussetzung für D-02 und Migrationen).
- [x] **F-05 Kapazitäts-Heatmap** — **erledigt:** 3c9cf96 – je einzeln schaltbar (Standard aus), `tests/test_phase7.py` (18 Tests, grün am 23.10.2026). – Ticket- und Voice-Aufkommen gegen Dienstzeiten je Wochentag und Stunde, Hinweise für den Dienstplan.
- [x] **F-06 Beförderungs-Reife (nur Hinweis)** — **erledigt:** 3c9cf96 – je einzeln schaltbar (Standard aus), `tests/test_phase7.py` (18 Tests, grün am 23.10.2026). – konfigurierbare Kriterien je Rang (Stunden, Tickets, Bewertungen, Pflichtschulungen, Warnfreiheit, Probezeit); entschieden wird immer von Menschen über den bestehenden 4-Augen-Ablauf.
- [x] **F-07 Bewerbungs-Pipeline als Board** — **erledigt:** 3c9cf96 – je einzeln schaltbar (Standard aus), `tests/test_phase7.py` (18 Tests, grün am 23.10.2026). – Eingang → Prüfung → Gespräch → Probezeit → Team/abgelehnt, mit Vorlagen und Fristen.
- [x] **F-08 Öffentliche Statusseite (optional)** — **erledigt:** 3c9cf96 – je einzeln schaltbar (Standard aus), `tests/test_phase7.py` (18 Tests, grün am 23.10.2026). – Support offen/geschlossen, Länge der Warteschlange, RP-Status (Start/Stop); ohne personenbezogene Daten.
- [x] **F-09 Spiel-Anbindung (optional)** — **erledigt:** 3c9cf96 – je einzeln schaltbar (Standard aus), `tests/test_phase7.py` (18 Tests, grün am 23.10.2026). – nur falls euer Roblox-Spiel eine offizielle API bietet (Serverstatus, Spielerzahl); Schlüssel optional in der `.env`, ohne Schlüssel bleibt alles wie bisher.
- [x] **F-10 „Mein Monat“** — **erledigt:** 3c9cf96 – je einzeln schaltbar (Standard aus), `tests/test_phase7.py` (18 Tests, grün am 23.10.2026). – persönlicher Rückblick in `/me` (Stunden, Tickets, Bewertungen, Schulungen, Erfolge), optional als DM am Monatsanfang (Opt-in).

---

## 11. Phase 8 – Deployment, CI & Betrieb

- [x] **O-01 Dockerfile** — **erledigt:** 8b77589 – `tests/test_deploy_config.py` (DockerTests): Nicht-root, HEALTHCHECK, ffmpeg/libopus0/tzdata, Laufzeitdaten nicht im Image. [Nachgewiesen] – `ffmpeg` + `libopus0` (K-10), Zeitzonendaten, eigener Nicht-root-Benutzer, `HEALTHCHECK` auf `/healthz`, `.dockerignore` um `uploads/` und `attachments/` ergänzen.

- [x] **O-02 Compose & HTTPS** — **erledigt:** 1c72320 – Caddy-Profil `https`, X-Forwarded-For nur von 172.30.0.10, Bindung an 127.0.0.1 (aus main); `tests/test_deploy_config.py`. [Nachgewiesen/Prüfen] – `compose.yaml` veröffentlicht Port 25095 direkt, einen Reverse-Proxy mit HTTPS gibt es in der Konfiguration nicht. Vorschlag: Pulse nur an `127.0.0.1` bzw. ein internes Netz binden, Reverse-Proxy (z. B. Caddy mit automatischen Zertifikaten) davor, Uvicorn `proxy_headers`/`forwarded_allow_ips` passend setzen. [Prüfen] Sonst sehen IP-Rate-Limits nur die Proxy-IP: das Bewerbungslimit (`rate_limited(f"apply:{ip}")`, 3 pro Stunde, `webserver.py:8770`) gälte dann für **alle** Bewerber gemeinsam. Ressourcenlimit passend zu E-5.

- [x] **O-03 Deploy-Workflow** — **erledigt:** d820046 + 30b073e (main) – getesteter SHA, Concurrency, Backup vorher, /healthz, Rollback, Discord-Meldung; `tests/test_deploy_config.py`, `tests/test_deploy_workflow.py`. [Nachgewiesen] – `.github/workflows/deploy-vps.yml` deployt `origin/main` (`git reset --hard origin/main`) statt des getesteten Commits (`github.event.workflow_run.head_sha`); bei schnellen Pushes kann so Ungetestetes live gehen. Außerdem fehlen Backup vor dem Deploy, Health-Check danach, Rollback und eine Concurrency-Gruppe. Ergänzen: Checkout des getesteten SHA, Daten-Backup vorher (Format aus D-01), `/healthz` muss innerhalb einer Frist 200 liefern, sonst automatischer Rollback auf den vorherigen Stand; Meldung in den Alert-Kanal.

- [x] **O-04 CI** — **erledigt:** 6bd7cb5 – `requirements.lock` mit Hashes, Dependabot, pip-audit, ruff, gitleaks, Coverage in `security.yml`; `tests/test_deploy_config.py` (LockTests). [Idee] – Abhängigkeiten mit Lock-Datei und Hashes (z. B. pip-tools), Dependabot oder Renovate, `pip-audit`, `ruff` (A-07), Secret-Scan (z. B. gitleaks), Coverage-Bericht.

- [x] **O-05 Git-Hygiene** — **erledigt:** c4977d7 + 2103599 – keine getrackten `.pyc` mehr (`git ls-files` geprüft am 23.10.2026). [Nachgewiesen] – 15 getrackte `__pycache__/*.pyc` (Python 3.13) per `git rm --cached` in einem eigenen Commit entfernen (Handoff §9.8).

- [x] **O-06 Konfiguration** — **erledigt (unveröffentlicht):** `pulse_env_validation.py` beim Start vor `import webserver` und im Systemstatus („Konfigurationswerte“), Bereiche aus dem tatsächlichen Code, nie Werte; `MAX_SHIFT_HOURS` = Bestätigungsobergrenze (E-2); `.env.example` vollständig – `tests/test_env_validation.py` (10).

- [x] **O-08 VPS-Grundeinrichtung (Vionity)** — **erledigt (unveröffentlicht):** `deploy/vps_bootstrap.sh` + `VPS_DEPLOYMENT.md` §0 + `.gitattributes` (LF) – `tests/test_deploy_config.py` (BootstrapTests: Firewall hält SSH offen, Swap/Checkout idempotent, keine Laufzeitdaten, kein Start).
- [x] **O-07 Überwachung** [Idee] — **erledigt (unveröffentlicht):** `/healthz` meldet `gateway_latency_ms`, `memory_mb`, `last_job_ok` und `jobs_failing`, Statuscode unverändert; `pulse_alerts.py` sendet gedrosselte Alarme (Jobs, Backups, Restore-Test, DB) über die bestehende Automationsschleife – `tests/test_alerts.py` (7), `tests/test_healthz.py` (5). Cog-Ladefehler stehen bereits im Startbericht; ein externer Uptime-Check ist Hosting-Sache. – `/healthz` um Gateway-Latenz und „letzter erfolgreicher Job“ erweitern; gedrosselte Alarme im `PULSE_SYSTEM_ALERT_CHANNEL_ID` bei Cog-Ladefehlern, Backup- und DB-Fehlern sowie Job-Ausfällen; optional externer Uptime-Check.

---

## 12. Phase 9 – Dokumentation

- [ ] **DOC-01** README, CHANGELOG, `CLAUDE.md` und Handoff auf den echten Stand bringen (Versions- und Commit-Angaben, neue Module, entfernte Altpfade). — **teilweise:** RAM-Budget nun auf den 2-GiB-VPS (768 MiB Container-RAM, bis zu 256 MiB Swap; Schwellen 500/620/700 MB) abgestimmt; Modul-/Altpfadliste und übrige Versions-/Commit-Angaben folgen mit A-01/A-02.
- [x] **DOC-02** — **erledigt (unveröffentlicht):** `docs/ARCHITEKTUR.md`, auf diesen Stand geprüft (Grundlage 17591f9).
- [x] **DOC-03** — **erledigt (unveröffentlicht):** `docs/BETRIEB.md`, auf diesen Stand geprüft (Grundlage 17591f9/82f8dba).
- [ ] **DOC-04** Pro Phase ein CHANGELOG-Eintrag; `pulse_version.py` erst nach meinem OK erhöhen (Vorschlag: 7.10.0 nach Phase 1).

---

## 13. Phasenbericht – Format

1. **Erledigt:** ID → Commit → Testnachweis (Befehl + echtes Ergebnis)
2. **Verhalten vorher → nachher**, so formuliert, dass ich es ohne Code verstehe
3. **Verworfen oder verschoben**, jeweils mit Grund
4. **Risiken und Schritte außerhalb des Codes** (Discord-Rechte/Rollenreihenfolge, Developer Portal, `.env`, Hosting)
5. **Offene Fragen** an mich

## 14. Definition of Done (pro Punkt)

Verifiziert · Test vorhanden und grün · keine Funktion entfernt · URLs, Befehle und IDs kompatibel · keine Secrets im Diff · `.env.example` und Doku aktualisiert, falls betroffen · Plan-Datei abgehakt.

## 15. Start

Beginne jetzt mit **Phase 0**. Ändere in Phase 0 nur `docs/` und `tools/`. Melde dich danach mit Baseline, Routen- und Datenlandkarte und den Fragen E-1 bis E-12.
