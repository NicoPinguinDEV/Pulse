# Phase 14.2 – Vorbereitung A-01b: `remove_and_add` und Routen-Architektur

**Nur Analyse.** Es wurden keine Routen, Handler, Rechte, Daten, IDs oder `custom_id`s geändert. Die
Empfehlungen sind Vorschläge, keine Freigaben.

## 1. Ausgangszustand

| Punkt | Befund (geprüft am 01.11.2026) |
|---|---|
| `origin/main` | `06acee9d0a8a…` = erwarteter Stand (Merge PR #97) |
| PR #97 | gemergt 07:37 UTC, 13/13 Checks grün; auf `main` alle Pflicht-Workflows (Quality, Sicherheit & Abdeckung, CodeQL) grün |
| Offene PRs | keine |
| Version | 7.17.0 (`pulse_version.py`) |
| Routen | 372 Einträge im laufenden Objekt (inkl. `cogs.pulse_suite`), Routen-Snapshot-Test grün |
| Arbeitsbaum | sauber (nur ungetrackte lokale `.claude/`-Dateien des Betreibers) |

### VPS-Migrations-Gate

**Ungeprüft.** Für die rein lesende Prüfung gibt es ein Skript (`.claude/vps_readonly_check.sh`, nicht
versioniert). Es prüft `/opt/pulse/data`, `.pulse-data.json`, den Container-Status, den installierten
Commit, `/healthz`, die Root-Helfer und den Crash-Restart-Dienst. Es gibt keine `.env`-Werte aus und
ändert nichts. Gestartet wurde es per SSH im Terminal, die Eingabe der Schlüssel-Passphrase durch den
Betreiber steht aber noch aus. Belegt ist nur, was GitHub sagt: Der letzte erfolgreiche Deploy über den
Workflow „Deploy Pulse VPS“ war `d221b6c` (Phase 12, 31.10.2026); alle späteren Läufe wurden
übersprungen (Auto-Deploy aus). Ob die Phase-13-Migration danach von Hand per SSH erfolgt ist, ist
damit **nicht bestätigt**. → Bis zur Bestätigung keine Änderung am Produktivsystem; A-01b selbst
berührt den VPS nicht.

## 2. Wie `remove_and_add` wirkt

```python
def remove_and_add(app, path, methods, endpoint, **kwargs):          # pulse_pro.py:721
    app.router.routes[:] = [r for r in app.router.routes if r.path != path]   # ALLE Methoden des Pfads
    for method in methods: (app.get if method == "GET" else app.post)(path, **kwargs)(endpoint)
```

- Entfernt **alle** Routen eines Pfads, **egal welche Methode**, und hängt die neue Route **ans Ende** an.
  Derzeit gehen dadurch keine weiteren Methoden verloren (geprüft: kein verdrängter Eintrag hatte eine
  andere Methode). Eine künftige `POST /dashboard` in `webserver` würde aber still gelöscht.
- Kennt nur GET und alles andere als POST.
- `pulse_pro.replace_paths` (`pulse_pro.py:463`) entfernt Pfade in Masse, wird aber **nirgends aufgerufen**
  (toter Helfer).

### Registrierungsreihenfolge (Index im laufenden `app.router.routes`)

| Index | Modul | Routen |
|---|---|---|
| 0–72 | `webserver` (+ `pulse_health_api` auf 6) | 73 |
| 73–82 | `pulse_features` (von ursprünglich 26 überleben 10) | 10 |
| 83–141 | `pulse_pro` (darin die 31 Neuregistrierungen) | 59 |
| 142 | `webserver.restore_backup` (von `pulse_pro` neu angehängt) | 1 |
| 143–199 | `pulse_ultimate` | 57 |
| 200–337 | `pulse_next`, `pulse_intelligence`, `pulse_home`, `pulse_cockpit`, `pulse_social_dashboard`, `pulse_admin`, `pulse_phase7`, `pulse_server_control`, `pulse_antinuke_admin`, `pulse_guard_admin`, `pulse_factions_dashboard` | 138 |
| 338–371 | `cogs.pulse_suite` – **erst beim Laden des Cogs** (Bot-Start), nicht beim Import | 34 |

FastAPI bedient den **ersten** passenden Eintrag. Weil `remove_and_add` ans Ende anhängt, liegen die
v5-Seiten hinter allen `webserver`-Routen. Die Laufzeitprüfung (jede Route mit Beispielpfad gegen die
Liste gematcht) findet nur **eine** echte Verdeckung: `GET /ultimate` (s. A-22).

Methode der Inventur: `remove_and_add` beim Import von `webserver` instrumentiert (Temp-Datenbanken,
ohne Discord). Je Aufruf festgehalten: was entfernt wurde, was neu hinzukam und wer am Ende antwortet.
Danach für jeden verdrängten Handler geprüft, ob dieselbe Funktion noch unter einem anderen Pfad aktiv ist.
Die Rechteprüfung ist aus dem Handler-Quelltext erkannt (`tools/route_map._auth_kind`).

## 3. Alle `remove_and_add`-Aufrufe

**31 Aufrufe** (die bisherige Angabe „32“ war eins zu hoch), alle in `pulse_pro.register` (`pulse_pro.py:3298–3362`).
Aktiv ist in **allen 31 Fällen** der neu registrierte Handler.

### 3a. 20 Aufrufe, die einen Vorgänger verdrängen

| Zeile | Methode Pfad | aktiv (neu) | verdrängt | Recht neu / alt | Bereich |
|---|---|---|---|---|---|
| 3298 | GET `/dashboard` | `pulse_pro.dashboard_v5` (:1231) | `webserver.dashboard_main` (:4036, 312 Z.) | dashboard / dashboard | Start |
| 3299 | GET `/pulse-inbox` | `inbox_v5` (:1271) | `pulse_features.inbox_page` (:39) | dashboard / angemeldet | Inbox |
| 3301 | POST `/pulse-inbox/read` | `inbox_read_all` (:1317) | `pulse_features.inbox_read` (:60) | dashboard / angemeldet | Inbox |
| 3304 | GET `/tasks` | `tasks_v5` (:1764) | `pulse_features.tasks_page` (:84) | dashboard + Aufgabenrechte / gleich | Aufgaben |
| 3305 | POST `/tasks/update` | `update_task_v5` (:1781) | `pulse_features.task_update` (:115) | gleich | Aufgaben |
| 3306 | GET `/tickets` | `tickets_v5` (:1795) → `pulse_ticket_center` | `webserver.pulse_tickets_dashboard` (:5296) **und** `pulse_features.tickets_page` (:125) | **dashboard** / **can_manage_tickets** | Tickets |
| 3309 | POST `/tickets/update` | `ticket_update_v5` (:1801) | `pulse_features.ticket_update` (:288) | gleich | Tickets |
| 3310 | GET `/stats` | `stats_v5` (:2130) | `pulse_features.stats_page` (:267) | can_view_analytics / gleich | Auswertung |
| 3311 | GET `/calendar` | `calendar_v5` (:2139) | `pulse_features.calendar_page` (:297) | dashboard / angemeldet | Planung |
| 3313 | GET `/search` | `search_v5` (:2362) | `pulse_features.search_page` (:64) | dashboard / angemeldet | Suche |
| 3314 | GET `/settings` | `settings_v5` (:2691) | `webserver.settings_page` (:10384, 185 Z.) | Admin / Admin | Verwaltung |
| 3315 | GET `/health` | `api_health` (:3283) | `pulse_features.health` (:317) | öffentlich / öffentlich | System |
| 3316 | GET `/manifest.webmanifest` | `manifest` (:3291) | `pulse_features.manifest` (:321) | öffentlich | PWA |
| 3317 | GET `/sw.js` | `service_worker` (:3294) | `pulse_features.service_worker` (:325) | öffentlich | PWA |
| 3318 | GET `/system` | `system_v5` (:3176) | `pulse_features.system_page` (:306) | Admin / Admin | System |
| 3319 | GET `/member/{user_id}` | `member_v5` (:2384) | `webserver.member_detail` (:7361, 159 Z.) | dashboard + Notiz/Warn/Beförderung / dashboard | Teamakte |
| 3325 | GET `/achievements` | `achievements_v5` (:2666) | `pulse_features.achievements_page` (:234) | dashboard / angemeldet | Auswertung |
| 3326 | GET `/meetings-history` | `meetings_history_v5` (:2660) | `pulse_features.meetings_history_page` (:281) | dashboard / angemeldet | Planung |
| 3328 | GET `/melonly` | `melonly_page` (:920) | `webserver.melonly_alias` (:4559, 141 Z.) | dashboard + Moderationsrechte je Typ / Warn oder Notiz | Moderation |
| 3362 | POST `/backup/restore` | **`webserver.restore_backup`** (:9848) | `webserver.restore_backup` (derselbe) und `pulse_features.restore_backup` (:374) | Admin / Admin | Backups |

(Zeilen ohne Modulnamen meinen `pulse_pro.register.<locals>`; „dashboard“ = `can_view_dashboard`.)
`/tickets` verdrängt zwei Handler. `/backup/restore` registriert denselben `webserver`-Handler nur neu, um
den `pulse_features`-Handler loszuwerden.

### 3b. 11 Aufrufe ohne Vorgänger (reine Erstregistrierung)

`POST /pulse-inbox/read/{nid}`, `POST /pulse-inbox/settings`, `POST /pulse-inbox/server-quiet` (Admin),
`GET /voice-support`, `POST /voice-support/save` (Admin), `GET /ticket/{ticket_id}` (can_manage_tickets),
`GET /export/team.csv` (can_view_analytics), `POST /shift/admin-end`, `POST /shift/history/confirm-duration`
(Leitung/Admin), `GET /api/team/discord-status`, `GET /robots.txt` (öffentlich).
Hier entfernt `remove_and_add` nichts. Ein Wechsel zu `app.get/app.post` ändert weder Position noch
Verhalten (beide hängen ans Ende an).

### 3c. Ohne `remove_and_add`

- `GET /ultimate`: `webserver.ultimate_dashboard` (Index 13) und `pulse_ultimate.ultimate_home`
  (Index 143) sind beide registriert. Es antwortet immer `webserver`, `pulse_ultimate.ultimate_home`
  ist unerreichbar.
- `GET /tickets/panels` läuft weiterhin über `webserver.pulse_tickets_dashboard` (zweiter Decorator
  derselben Funktion).

## 4. Entscheidungstabelle

### A. Verdrängte Handler – heute unerreichbar

Zur Laufzeit geprüft: **20 von `remove_and_add` verdrängte Handler-Funktionen sind unter keinem Pfad mehr
aktiv.** Dazu kommt der nie erreichte zweite `/ultimate`-Handler, also **21 unerreichbare Routen-Handler**,
sowie ein toter Decorator (`/tickets` an `pulse_tickets_dashboard`). Außerdem wird
die Schleife `pulse_features.housekeeping` nie gestartet (`pulse_features.py:371`, aktiv ist
`cogs/pulse_pro.housekeeping_loop`).

**Nicht tot**, entgegen der Liste vom 20.10.:
- `webserver.pulse_tickets_dashboard` bedient weiterhin `GET /tickets/panels` (Panel-Builder). Nur der
  `/tickets`-Decorator ist tot; die Funktion muss bleiben.
- `webserver.restore_backup` ist der **aktive** Handler von `POST /backup/restore`; tot ist nur
  `pulse_features.restore_backup`.

| # | toter Handler | Pfad | aktiv ist | Vorschlag | Bemerkung |
|---|---|---|---|---|---|
| A-01 | `webserver.dashboard_main` | GET `/dashboard` | `pulse_pro.dashboard_v5` | löschen | 312 Zeilen; Helfer erst prüfen |
| A-02 | `webserver.settings_page` | GET `/settings` | `pulse_pro.settings_v5` | löschen | Admin; Formularziele (`/action`) bleiben |
| A-03 | `webserver.member_detail` | GET `/member/{user_id}` | `pulse_pro.member_v5` | löschen | Teamakte |
| A-04 | `webserver.melonly_alias` | GET `/melonly` | `pulse_pro.melonly_page` | löschen | |
| A-05 | `webserver.pulse_tickets_dashboard` **nur Decorator** `@app.get("/tickets")` | GET `/tickets` | `pulse_pro.tickets_v5` | nur den Decorator entfernen, Funktion behalten | bedient `/tickets/panels` |
| A-06…A-18 | `pulse_features`: `inbox_page`, `inbox_read`, `tasks_page`, `task_update`, `tickets_page`, `ticket_update`, `stats_page`, `calendar_page`, `search_page`, `system_page`, `achievements_page`, `meetings_history_page`, `restore_backup` | s. §3a | `pulse_pro.*` / `webserver.restore_backup` | löschen | Closures in `pulse_features.register`; 10 Routen dort bleiben aktiv |
| A-19…A-21 | `pulse_features.health`, `manifest`, `service_worker` | `/health`, `/manifest.webmanifest`, `/sw.js` | `pulse_pro.*` | löschen | öffentlich, geringstes Risiko |
| A-22 | `pulse_ultimate.ultimate_home` | GET `/ultimate` | `webserver.ultimate_dashboard` | nie erreichten Zweig löschen **oder** bewusst tauschen | weitere Untersuchung: welche Seite ist die gewollte? |
| A-23 | `pulse_features.housekeeping` (Schleife) | – | `cogs/pulse_pro.housekeeping_loop` | löschen | |
| A-24 | `pulse_pro.replace_paths` | – | – | löschen | ungenutzter Helfer |

Kein versionierter Test hängt an einem der toten Handler: Die Treffer in `test_discord_log_regressions`
und `test_stability` und die Namen im Smoke-Test meinen die aktiven Funktionen.

### B. Gleiche Aufgabe unter mehreren Adressen (für Nutzer sichtbar)

Die Tabelle B in `docs/SEITEN_ALT_NEU.md` ist weiterhin gültig. Alle 61 dort genannten Adressen sind heute
registriert (58 × `/ultimate/*`, 22 × `/suite/*` als GET). Je Zeile mögliche Entscheidung: **führend
behalten · beide behalten · zusammenführen · alte URL per 303 weiterleiten · unangetastet · weitere
Untersuchung**. B ist **nicht** Teil von A-01b, weil dort keine Routen verdrängt werden. Es geht um
Navigation und Daten (U-01), z. B. drei getrennte Kalender-Tabellen. Diese Bereiche erst nach A-01b und
nur mit lesender Datenzusammenführung vor jeder Migration angehen.

## 5. Technische Abhängigkeiten

- **`pulse_pro.register` ist ein ~2.700-Zeilen-Closure.** Alle v5-Handler teilen lokale Helfer (`cctx`,
  `card`, `pill`, `work_stats`, `LIVE_CACHE`). A-01b ändert daran nichts; das Herauslösen in Module ist A-02d.
- **`pulse_features.register`** mischt 13 tote und 10 aktive Routen (`/tasks/create`,
  `/ticket/transcript/{filename}`, `/tickets/rate`, `/wiki`, `/wiki/{page_id}`, `/wiki/save`,
  `/training`, `/training/create`, `/training/{training_id}`, `/training/{training_id}/submit`). Vor dem
  Löschen prüfen, welche lokalen Helfer nur die toten Handler benutzen.
- `webserver` → `pulse_features` → `pulse_pro` → … ist eine feste Importkette am Ende von `webserver.py`
  (jedes Modul in eigenem `try/except`). Scheitert `pulse_pro.register`, sind die v4-/`webserver`-Fassungen
  aktiv. Die toten Handler sind damit heute der stille **Rückfall**. Nach dem Löschen fehlen diese Seiten
  in diesem Fehlerfall, statt alt zu erscheinen. Das ist gewollt, sollte aber bewusst entschieden werden.
- `cogs.pulse_suite.register_routes()` registriert 34 Routen **zur Laufzeit beim Laden des Cogs**, also erst
  nach dem Start des Webservers. Die Tests (`test_route_snapshot`, `tools/route_map.py`) rufen das selbst auf.
- `tools/route_map.py` erkennt `remove_and_add` per AST. Das Werkzeug und `docs/ROUTEN.md` müssen bei jedem
  Paket neu erzeugt werden. **`docs/ROUTEN.md` hat veraltete Zeilennummern** (z. B. `pulse_tickets_dashboard`
  :5187 statt :5296). Der maßgebliche Snapshot `docs/routen_snapshot.json` ist aktuell (Test grün).
- `KNOWN_DUPLICATES = {("GET", "/ultimate")}` in `tests/test_route_snapshot.py` muss mit A-22 entfallen.

## 6. Risiken

| Risiko | Bereich | Bewertung |
|---|---|---|
| **Rechte-Abweichung `/tickets`**: aktive Seite verlangt nur `can_view_dashboard` und listet alle Tickets der Gilde; der verdrängte Handler verlangte `can_manage_tickets`. Für GET-Seiten gibt es keinen Rechte-Test (der Sweep prüft nur schreibende Routen). | Tickets | **hoch** – Betreiberentscheidung, nicht in A-01b ändern |
| `pulse_tickets_dashboard` versehentlich ganz löschen → Panel-Builder `/tickets/panels` weg | Tickets | hoch, durch Snapshot abgedeckt |
| `webserver.restore_backup` als „tot“ löschen (alte Liste) → Wiederherstellung kaputt | Backups | hoch, durch Snapshot und `test_legacy_backup_restore` abgedeckt |
| Wegfall von `remove_and_add` bei `/backup/restore`: der Handler läge wieder an Index 63 statt 142 | Backups | gering (keine Verdeckung), Snapshot prüfen |
| `/ultimate`: unklar, welche der beiden Seiten gewollt ist | Command Center | mittel → weitere Untersuchung |
| Helfer, die nur tote `webserver`-Handler nutzen, bleiben als toter Code oder werden zu viel mitgelöscht | `webserver` | mittel – je Funktion Aufrufer suchen, im Zweifel stehen lassen |
| `remove_and_add` löscht künftig neu hinzugefügte Methoden eines Pfads still | alle | mittel – bis zum Ausbau mit einem Test absichern |
| Ausfall von `pulse_pro.register`: nach dem Löschen kein v4-Rückfall mehr | Start | gering, bewusst entscheiden |

## 7. Vorgeschlagene Pakete für A-01b

Jedes Paket: eigener Branch/PR, kleiner Diff, `docs/routen_snapshot.json` vorher/nachher (nur erwartete
Zeilen ändern sich), Rechte unverändert, Formularziele und `custom_id`s unverändert, voller CI-Satz und E2E.

| Prio | Paket | Inhalt | Absicherung | Risiko |
|---|---|---|---|---|
| 0 | **Absicherung zuerst** | Test: `remove_and_add` darf keine Methode entfernen, die es nicht neu registriert; Inventur-Test, der die 31 Aufrufe und 20 verdrängten Handler festschreibt; `ROUTEN.md` neu erzeugen | neue Tests, kein Verhalten geändert | sehr gering |
| 1 | **11 Erstregistrierungen** (§3b) | `remove_and_add` → `app.get/app.post` | Routen-Snapshot identisch | sehr gering |
| 2 | **PWA/Health** (A-19…A-21) | `pulse_features.health/manifest/service_worker` löschen, Aufrufe 3315–3317 → `app.get` | Snapshot, `test_health` | gering |
| 3 | **`pulse_features` Seiten** (A-06…A-17) | tote Closures löschen (Inbox, Aufgaben, Tickets-Update, Stats, Kalender, Suche, System, Achievements, Meeting-Historie), Aufrufe → `app.get/post`; `housekeeping` (A-23) mit | Snapshot, Rechte-Sweep, E2E | gering–mittel |
| 4 | **Backup-Restore** (A-18) | `pulse_features.restore_backup` löschen, Zeile 3362 entfernen | Snapshot, `test_legacy_backup_restore`, `test_full_backup` | mittel |
| 5 | **`webserver`-Altseiten** (A-01…A-04) | `dashboard_main`, `settings_page`, `member_detail`, `melonly_alias` je ein Commit; nur exklusiv genutzte Helfer mitnehmen | Snapshot, E2E (Dashboard, Teamakte, Einstellungen, Melonly) | mittel |
| 6 | **Tickets** (A-05) | nur `@app.get("/tickets")` an `pulse_tickets_dashboard` entfernen | Snapshot (`/tickets/panels` bleibt), Ticket-Tests | mittel |
| 7 | **`/ultimate`** (A-22) | nach Entscheidung den toten Zweig löschen, `KNOWN_DUPLICATES` leeren | Snapshot, E2E | mittel |
| 8 | **Abschluss** | `remove_and_add` und `replace_paths` (A-24) entfernen, `tools/route_map.py` anpassen | Snapshot, alle Tests | gering |

Besonders riskant, weil mehrere Module beteiligt sind: Pakete 4–7 (`webserver` ↔ `pulse_features` ↔
`pulse_pro` ↔ `pulse_ultimate`, Ticket-Center in `pulse_ticket_center`, Backups in `pulse_next`).

## 8. Erforderliche Entscheidungen des Betreibers

1. **E-6a**: Teil A (toter Code) löschen – freigeben für alle Zeilen oder einzeln (A-01 … A-24).
2. **E-6b `/tickets`-Rechte**: Soll jedes Teammitglied mit Dashboard-Zugang die Ticketübersicht aller
   Tickets sehen (heutiges Verhalten), oder nur `can_manage_tickets`/Leitung? (Getrennt von A-01b umsetzen.)
3. **E-6c `/ultimate`**: Bleibt `webserver.ultimate_dashboard` (heute aktiv) führend?
4. **E-6d Rückfall**: Ist es in Ordnung, dass bei einem Ausfall von `pulse_pro.register` keine v4-Seiten mehr erscheinen?
5. **E-6 (Teil B)**: je Zeile der Tabelle B in `docs/SEITEN_ALT_NEU.md` – später, getrennt von A-01b.
6. **VPS-Gate**: Bestätigung des Migrationsstands (Passphrase für die Leseprüfung).

## 9. Empfehlung für die erste Umsetzungsphase

**Phase 14.3 = Pakete 0 und 1.** Das ändert kein Verhalten und braucht keine Lösch-Freigabe. Danach ist
jeder weitere Schritt durch Tests abgesichert, die eine verlorene Methode oder einen unerwartet anders
verdrängten Handler sofort zeigen. Danach Pakete 2 → 3 → 4 nach Freigabe E-6a; 5–7 erst mit E-6b/E-6c.
