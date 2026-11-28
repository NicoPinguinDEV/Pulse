# Web-Architektur von Pulse

## Einstiegspunkt

`webserver.py` bleibt der stabile Import- und Kompositionspunkt für `main.py`,
Tests und bestehende Integrationen. Dort liegen weiterhin die zentralen
Laufzeitdienste: Konfiguration, Datenzugriff, Sessions, Autorisierung, Schichten
und Team-Schutzlogik.

Die vorher in einer einzelnen Datei gebündelten Web-Bereiche sind in
`pulse_web/` nach fachlicher Verantwortung getrennt:

| Modul | Verantwortungsbereich |
|---|---|
| `ui.py` | Layout, gemeinsame Seitenbausteine, Health-/Live-Konsole |
| `dashboard.py` | Einstieg, OAuth, Dashboard, Schichten und Aktivitätsübersicht |
| `tickets.py` | Ticket-Verwaltung, Moderationsfälle und Checklisten |
| `team.py` | Teamliste, Mitgliederdetails, Besprechungen, Abmeldungen |
| `applications.py` | Öffentliches Bewerbungsportal, Bewerbungen, Interviews |
| `backups.py` | Discord-Backups, Komplett-Backups und Wiederherstellung |
| `administration.py` | Einrichtungsassistent, Sicherheit und Einstellungen |

## Kompatibilität während der Migration

`pulse_web._compat` hält vorübergehend die bisherige gemeinsame Modul-Namespace
verfügbar. Dadurch bleiben existierende Handler, interne Aufrufe und der Import
`from webserver import ...` funktionsfähig, ohne die Handler bei diesem Schritt
fachlich umzuschreiben.

Die Domain-Module werden in ihrer bisherigen Registrierungsreihenfolge geladen.
Das ist wichtig, weil FastAPI bei überlappenden Routen den zuerst registrierten
Handler verwenden kann. Der Routensnapshot dokumentiert deshalb weiterhin die
aktiven Handler und wird mit `python tools/route_map.py` aktualisiert.

Die Brücke ist ausdrücklich kein Muster für neue Features: Neue Funktionen
sollen benötigte Services möglichst explizit importieren oder über klar definierte
Abhängigkeiten erhalten. Die verbleibende globale Kopplung kann anschließend
schrittweise und separat abgebaut werden.

## Prüfung

- `python -m compileall -q .`
- `python -m unittest tests.test_webserver_domain_modules`
- `python -m unittest tests.test_route_snapshot`
- `python tools/route_map.py` und anschließender Review von
  `docs/routen_snapshot.json` und `docs/ROUTEN.md`

Die Aufteilung ändert keine Datenformate, Datenbanktabellen, Rollen,
Berechtigungen, URLs oder fachlichen Abläufe.
