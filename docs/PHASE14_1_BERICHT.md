# Phase 14.1 – A-01a: explizite Shell-Hooks statt Laufzeit-Monkeypatches

Ausgangsstand: `main` @ `f01a8be` (Phase 13.1/13.2 gemergt), Version laut `pulse_version.py`: 7.17.0.
Branch: `refactor/phase14.1-explicit-shell-hooks`. Nur A-01a; A-01b (`remove_and_add`) und alle
weiteren Pakete sind unangetastet.

## 1. Gefundene Monkeypatches (im Code verifiziert)

`pulse_pro.make_shell_helpers(ws)` – aufgerufen aus `pulse_pro.register(app)`, das `webserver` am
Modulende registriert – hat drei Modulattribute von `webserver` ersetzt:

| Attribut | Ersatz | Wirkung |
|---|---|---|
| `ws.get_head_html` | `patched_head` (Closure über das alte `get_head_html`) | alter Kopf + Manifest/Theme-Meta + `PRO_CSS` + `PRO_JS` + `PARTICLE_JS` |
| `ws.get_sidebar_html` | `patched_sidebar` | ersetzt die eingebaute Sidebar komplett durch die TeamOS-Navigation |
| `ws.log_audit` | `patched_log` (Closure über das alte `log_audit`) | Audit-Datei schreiben, danach `pulse_db.record_event("audit", "panel", …)` |

Weitere Zuweisungen auf diese drei Namen gibt es in keinem Modul (AST-Suche über `*.py`, `cogs/*.py`).
`compute_perms` wird seit A-03 nicht mehr ersetzt.

Aufrufer: `webserver` selbst (globale Namen, z. B. `render_page`, Login-, Bewerbungsseiten, >60 interne
`log_audit`-Aufrufe), dazu über `ws.log_audit`/`webserver.log_audit` `pulse_admin`, `pulse_features`,
`pulse_phase7`, `pulse_retention`, `pulse_guard_admin`, `pulse_ultimate`, `pulse_home`, mehrere Cogs.
Kein Modul importiert die Funktionen per `from webserver import …` – alle Aufrufe lösen den Namen zur
Laufzeit auf, deshalb wirkten die Patches überall und wirkt jetzt der Hook überall.

Latenter Fehler der alten Architektur: Jeder weitere Aufruf von `make_shell_helpers` legte eine neue
Hülle um die bereits gepatchte Funktion – der Pro-Kopf stand dann doppelt im HTML und jedes Audit-Ereignis
landete doppelt in der Ereignistabelle (in Tests nachgewiesen, s. §5).

## 2. Geänderte Dateien

- `webserver.py` – Hook-Registry (`SHELL_HOOK_SLOTS`, `register_shell_hook`, `shell_hook`);
  `get_head_html` = `_base_head_html` + Hook, `get_sidebar_html` delegiert an den Hook, sonst eingebaute
  Sidebar; `log_audit` ruft nach dem Speichern den Audit-Listener.
- `pulse_pro.py` – Closures zu Modulfunktionen `pro_head_extra`, `pro_sidebar_html`,
  `pro_audit_mirror`, `_nav_item`; `make_shell_helpers` registriert nur noch (Name und Aufrufstelle bleiben).
- `tests/test_shell_hooks.py`, `tests/shell_html_snapshot.json` – neu.
- `tests/feature_smoke.py`, `tests/test_dashboard_redesign.py`, `tests/test_discord_log_regressions.py` –
  Quelltext-Prüfungen auf die neuen Funktionsnamen umgestellt (gleiche Aussagen).
- `docs/VERBESSERUNGSPLAN.md` – A-01a als erledigt vermerkt; dieser Bericht.

## 3. Neue Architektur

```
webserver.get_head_html(title)      = _base_head_html(title) + hook["head_extra"](title)
webserver.get_sidebar_html(...)     = hook["sidebar"](...)  oder eingebaute Sidebar
webserver.log_audit(...)            : Audit-Datei speichern → hook["audit_listener"](...)
pulse_pro.make_shell_helpers(ws)    : register_shell_hook × 3
```

- Ein Hook je Slot. Dieselbe Funktion erneut registrieren ist ein No-op (Registrierung idempotent);
  eine andere Funktion für einen belegten Slot wird mit `RuntimeError` abgewiesen und der erste Hook
  bleibt aktiv. Unbekannte Slots → `ValueError`, nicht aufrufbare Werte → `TypeError`.
- Initialisierung deterministisch: genau eine Stelle (`pulse_pro.register` beim Import von `webserver`).
- Fehlender Hook → eingebautes Verhalten (wie bisher, wenn `pulse_pro` nicht registriert werden konnte).
- Audit: Die Datei ist maßgeblich; ein fehlschlagender Listener bricht die aufrufende Aktion nicht ab
  (vorher fing `patched_log` nur den DB-Fehler ab – für den aktuellen Listener identisch).
- Tests, die `ws.log_audit` per `patch.object` ersetzen, verhalten sich unverändert.

## 4. Entfernte Laufzeitüberschreibungen

`ws.get_head_html = …`, `ws.get_sidebar_html = …`, `ws.log_audit = …` – alle drei entfernt. Nach dem
Import gehören die Funktionen wieder `webserver` (`__module__ == "webserver"`, im Test geprüft). Ein
statischer Test verhindert neue Zuweisungen/`setattr` auf diese Namen.

## 5. Tests und Ergebnisse

Neue Tests (`tests/test_shell_hooks.py`, 18 Tests):
- Snapshot: 4 Köpfe, 8 Sidebars (Gast, Teamler, ungelesene Inbox, Leitung, erweiterte Funktionen,
  Admin Übersicht/Verwaltung/Serververwaltung); SHA-256 + Länge; Version und CSS-Cache-Buster (`pulse.css?v=<Inhalts-Hash>`, unter Windows durch CRLF
  anders als auf dem Linux-Runner) normalisiert.
  **Der Snapshot wurde aus `origin/main` (alter Code, Worktree) erzeugt**; der neue Code liefert
  byte-identisches HTML.
- Ausgabe, `render_page`-Integration, Rechtefilter der Sidebar.
- Audit: genau ein Datei-Eintrag und ein Ereignis je Aufruf, „System“-Fallback, wiederholte
  Registrierung ohne Dopplung, Listener-/DB-Fehler, kein Listener.
- Registry: alle drei Slots belegt, Funktionen nicht ersetzt, Fallback ohne Hooks, No-op, Konflikt,
  unbekannter Slot/nicht aufrufbar, statische Zuweisungssperre.

Gegenprobe gegen die alte Architektur (neue Tests im Worktree auf `origin/main`):
`FAILED (failures=10, errors=8)` von 18 – die Tests erkennen die alte Architektur. Allein ausgeführt besteht der Snapshot
auf dem alten Code (gleiche Basis); nach zwei weiteren `make_shell_helpers`-Aufrufen erzeugt dort ein einziger
`log_audit` **3 Ereignisse statt 1** und der Pro-Kopf steht mehrfach im HTML.

Der Snapshot hat bei der Umsetzung einen echten Fehler gefunden: Beim Ausrücken der Sidebar-Funktion
wurden zunächst auch die Leerzeichen im f-String-Literal verschoben (168 Zeichen Abweichung je Sidebar);
korrigiert, danach identisch.

### 5a. Ergebnisse

Lokal (Windows, Python 3.14, Stand dieses Branches):

| Prüfung | Ergebnis |
|---|---|
| `python -m ruff check .` (ruff 0.6.9) | All checks passed |
| `python -m compileall -q .` | OK |
| `python tests/feature_smoke.py` | OK |
| `python tests/smoke_test.py` | OK |
| `python -m unittest discover -s tests -p 'test_*.py'` | 1275 Tests, OK (18 übersprungen) |
| `PULSE_E2E=1 python -m unittest tests.test_e2e_dashboard` (Playwright/Chromium) | 16 Tests, OK |
| `tests/test_shell_hooks.py` gegen `origin/main` (Gegenprobe) | FAILED (failures=10, errors=8) – erwartet |

Übersprungen lokal: 17 Tests ohne lauffähiges bash (Deploy-, Boot-, Crash-Restart-, Datenisolations-Skripte)
und 1 SIGTERM-Test (Windows) – unabhängig von dieser Änderung; die CI (Linux) führt sie aus.

## 6. Verbleibende Risiken

- Der Snapshot deckt die gewählten 12 Fälle ab, nicht jede Rechtekombination; die bestehenden Sidebar-
  und Rechtetests (`test_factions`, `test_server_control`, `test_pulse_admin_area`, Rechte-Sweep) laufen weiter.
- Ein künftiges Modul, das einen belegten Slot erneut belegen will, scheitert jetzt beim Import laut statt
  still zu überschreiben; `webserver` fängt Registrierungsfehler je Modul ab und meldet sie im Log.
- Inhalte von `PRO_CSS`/`PRO_JS`/Sidebar ändern sich künftig bewusst → Snapshot neu erzeugen
  (`python -m tests.test_shell_hooks --update`) und Diff prüfen.

## 7. Auswirkungen auf andere Module

Keine Änderung an URLs, Routen, Rechten, Datenstrukturen, JSON-Schlüsseln, Discord-IDs oder `custom_id`s.
Alle Aufrufer von `get_head_html`/`get_sidebar_html`/`log_audit` bleiben unverändert. Routen-Snapshot
unverändert (`tests/test_route_snapshot.py`).

## 8. Empfehlung für Phase 14.2

A-01b laut Phase-13.1-Plan: die 32 `remove_and_add`-Stellen bereichsweise abbauen, je Schritt hinter dem
Routen-Snapshot, der Rechte-Matrix und E2E; tote Handler erst nach Entscheidung E-6
(`docs/SEITEN_ALT_NEU.md`). Für Seiten-HTML, das dabei den Handler wechselt, dasselbe Snapshot-Muster
wie hier verwenden (Snapshot aus dem alten Stand erzeugen).
