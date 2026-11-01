# Doppelte Seiten: Tabelle „alt → neu“ (Entscheidungsvorlage nach E-6)

Stand 20.10.2026, Version 7.12.0; **Teil A am 01.11.2026 (7.17.0) zur Laufzeit nachgeprüft**, Details und Pakete in `docs/PHASE14_2_VORBEREITUNG.md`. **Es wurde nichts zusammengeführt oder gelöscht.** Das passiert erst nach deiner Entscheidung je Zeile (A-01/U-01).
Grundlage: `docs/ROUTEN.md` und `docs/routen_snapshot.json`. Der Routen-Snapshot-Test (K-16) belegt nach jedem Schritt, welche Handler aktiv sind.

## A. Verdrängte Handler – heute nicht erreichbar (toter Code)

Jeder dieser Handler wird beim Start registriert und danach durch eine neuere Version ersetzt. Aufgerufen wird nur die Version in der rechten Spalte. Für Nutzer ändert ein Löschen nichts, es macht den Code aber kleiner und verständlicher.

| Pfad | toter Handler | aktiv ist | Vorschlag |
|---|---|---|---|
| `/dashboard` | `webserver.dashboard_main` | `pulse_pro.dashboard_v5` | löschen |
| `/member/{id}` | `webserver.member_detail` | `pulse_pro.member_v5` | löschen |
| `/settings` | `webserver.settings_page` | `pulse_pro.settings_v5` | löschen |
| `/tickets` | `pulse_features.tickets_page`; bei `webserver.pulse_tickets_dashboard` **nur der Decorator** `@app.get("/tickets")` | `pulse_pro.tickets_v5` | `tickets_page` löschen; an `pulse_tickets_dashboard` nur den `/tickets`-Decorator entfernen – **die Funktion bedient weiterhin `/tickets/panels` (Panel-Builder) und bleibt**. Rechte weichen ab (alt `can_manage_tickets`, aktiv `can_view_dashboard`) → Entscheidung E-6b |
| `/melonly` | `webserver.melonly_alias` | `pulse_pro.melonly_page` | löschen |
| `/system` | `pulse_features.system_page` | `pulse_pro.system_v5` | löschen |
| `/achievements`, `/calendar`, `/stats`, `/tasks`, `/search`, `/pulse-inbox`, `/meetings-history` | jeweils `pulse_features.*_page` | `pulse_pro.*_v5` | löschen |
| `POST /tasks/update`, `/tickets/update`, `/pulse-inbox/read`, `/backup/restore` | `pulse_features.*` | `pulse_pro.*` bzw. **`webserver.restore_backup` (aktiv, bleibt)** | `pulse_features.*` löschen |
| `/health`, `/manifest.webmanifest`, `/sw.js` | `pulse_features.*` | `pulse_pro.*` | löschen |
| – (Hintergrundschleife) | `pulse_features.housekeeping` – wird nie gestartet, `pulse_pro` hat die aktive Housekeeping-Schleife | `cogs/pulse_pro.housekeeping_loop` | löschen |
| `GET /ultimate` (doppelt registriert) | `pulse_ultimate.ultimate_home` (2. Treffer) | `webserver.ultimate_dashboard` (1. Treffer) | den nie erreichten Zweig löschen – vorher bestätigen, dass die heute aktive Seite die gewollte ist (E-6c) |
| – (Helfer) | `pulse_pro.replace_paths` – wird nirgends aufgerufen | – | löschen |

Zählung (01.11.2026): 31 `remove_and_add`-Aufrufe; 20 verdrängte Handler-Funktionen + der zweite `/ultimate`-Handler = **21 unerreichbare Routen-Handler**, dazu ein toter Decorator, die nie gestartete Schleife und ein ungenutzter Helfer. Kein versionierter Test hängt an einem toten Handler.

## B. Gleiche Aufgabe unter mehreren Adressen (sichtbar für Nutzer)

**Aktueller Stand:** Die Hauptnavigation (Team-Bereich) führt auf die Spalte „Hauptseite heute“. „Command Center“ (`/ultimate/*`) und „Operations Suite“ (`/suite/*`) sind einklappbare Zusatzbereiche in derselben Seitenleiste.

**Vorschlag:** Die Hauptseite bleibt führend. Funktionen, die es nur in der Zusatzseite gibt, werden in die Hauptseite übernommen. Die alte Adresse leitet danach weiter (alte Links funktionieren weiter). Bitte je Zeile entscheiden: **führend**, **behalten (beide)** oder **anders**.

| Aufgabe | Hauptseite heute | weitere Seiten | Vorschlag |
|---|---|---|---|
| Startseite | `/me` (Mein Pulse), `/dashboard` (Übersicht) | `/ultimate`, `/suite`, `/cockpit` (Leitung) | `/me` führend für alle, `/cockpit` für Leitung; `/dashboard`, `/ultimate`, `/suite` → Weiterleitung |
| Teamliste | `/team` | `/ultimate/team`, `/suite/team` | `/team` führend |
| Teamakte | `/member/{id}` | `/ultimate/team/{id}`, `/suite/member/{id}` | `/member/{id}` führend; Suite-Reiter (Beförderungen, Audit) übernehmen |
| Aufgaben | `/tasks` | `/ultimate/tasks`, `/suite/tasks` | `/tasks` führend |
| Kalender | `/calendar` | `/ultimate/calendar`, `/suite/calendar` | `/calendar` führend |
| Achievements | `/achievements` | `/ultimate/achievements`, `/ultimate/awards`, `/suite/achievements` | `/achievements` führend |
| Suche | `/search` (+ Schnellsuche Strg+K) | `/ultimate/search`, `/suite/search` | `/search` führend |
| Bewerbungen | `/applications` | `/suite/applications`, `/suite/interviews` | `/applications` führend; Gespräche übernehmen |
| Beförderungen/Freigaben | `/promotions`, `/approvals` | `/suite/promotions`, `/ultimate/approvals` | eine Seite „Freigaben“ |
| Ankündigungen | `/announcements` (Verwaltung) | `/ultimate/announcements`, `/suite/news` | `/announcements` führend |
| Übergaben | `/handover` | `/ultimate/handovers` | `/handover` führend |
| Fälle / Meldungen | – | `/ultimate/cases`, `/suite/cases` | **zusammenführen**, Ort klären (Moderation?) |
| Dienstzeit / Dienstplan | `/team-status` | `/ultimate/shifts`, `/suite/time`, `/suite/schedule`, `/suite/my-schedule` | Dienstplan aus der Suite übernehmen, Rest weiterleiten |
| Auswertungen | `/stats` | `/ultimate/analytics`, `/ultimate/metrics`, `/ultimate/reports`, `/ultimate/scoreboard`, `/ultimate/team-trends` | `/stats` mit Reitern |
| Systemstatus | `/system` | `/ultimate/system`, `/ultimate/health-detail` | `/system` führend (enthält jetzt Jobs, RAM, Backups) |
| Backups | `/backups` | `/ultimate/backup` (altes ZIP) | `/backups` führend; alte ZIPs bleiben importierbar |
| Rechte | `/settings` | `/ultimate/permissions` (Einzel-Overrides) | `/settings` + künftige Seite „Wer darf was?“ (F-01) |
| Schulungen | `/training` | `/suite/training` | `/training` führend |
| Audit-Log | `/settings` (Audit) | `/suite/audit` (Hash-Kette) | beide behalten, Suite-Audit verlinken |

**Nur im Command Center, ohne Doppelung** (bleiben, bis du anders entscheidest): Abteilungen, Organigramm, Automationen, Workflows, Ziele, Umfragen, Ideen, Feedback, Onboarding, Roblox, Copilot, Simulator, Sandbox, System-Twin, Sicherheit.

## Was nach deiner Entscheidung passiert

1. Toter Code (Teil A) wird gelöscht, mit Routen-Snapshot vorher/nachher.
2. Je Zeile in Teil B, die du freigibst:
   - fehlende Funktionen in die führende Seite übernehmen,
   - danach die alte Adresse per 303-Weiterleitung umlenken,
   - Rechte prüfen (Routen-Matrix-Test) und die Navigation anpassen.
3. Nichts wird gelöscht, das noch Daten anzeigt, die es nur dort gibt.
