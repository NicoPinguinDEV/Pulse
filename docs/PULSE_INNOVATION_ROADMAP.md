# Pulse Innovation Roadmap

Stand: Oktober 2026 · Version laut `pulse_version.py`: 7.9.7.
Diese Datei trennt strikt zwischen **implementiert**, **teilweise implementiert**, **geprüft/offen** und **nur vorgeschlagen**.
Alle Punkte unter „implementiert“ sind durch Tests abgesichert (`tests/feature_smoke.py` plus die genannten Unit-Tests).

## Implementiert

| Bereich | Problem vorher | Lösung | Tests |
|---|---|---|---|
| Ticket-Zentrale (`/tickets`) | Liste zeigte nur Priorität; Übernehmen/Schließen/Wiedereröffnen waren im Dashboard nicht erreichbar, obwohl `/tickets/action` existierte. Keine Filter, keine Suche, bis zu 500 Tickets gemischt. Wer auf wen wartet, war unsichtbar. | `pulse_ticket_center.py` + `pulse_ticket_triage.py`: Status „Neu / wartet auf Team / wartet auf Ersteller“ mit Wartezeit, Begründung und nächstem Schritt; Sortierung überfällig → Priorität → Wartezeit; Tabs (inkl. „Meine“, „Überfällig“), Suche, Kategorie-Filter, Pagination (25); alle Aktionen über die bestehende, serverseitig geprüfte Route. | `tests/test_pulse_ticket_triage.py` |
| Mein Pulse | Nur Anzahl eigener Tickets. | Karte „Meine Tickets“ mit derselben Triage und Direktlink in den Discord-Kanal. | feature_smoke |
| Schichtende | Keine Übergabe-Hinweise. | Beim Beenden zeigt die Rückmeldung offene übernommene Tickets und überfällige Aufgaben (blockiert nie). | feature_smoke |
| Ticket-Panels & Automatik | – | Visueller Panel-Builder, Bilder, Begrüßungen, Formulare, Erinnerungen/Auto-Close (siehe CHANGELOG/Commits). | `tests/test_pulse_ticket_design.py` |
| Leitungs-Cockpit, Mein Pulse, Rollenverwaltung, Social Media | – | Siehe jeweilige Module (`pulse_cockpit.py`, `pulse_home.py`, `pulse_role_manager.py`, `pulse_social_dashboard.py`). | jeweilige `tests/test_*_utils.py` |

### Wichtige Regeln, die dabei eingehalten wurden
- Keine neue Hintergrundschleife: Triage wird beim Seitenaufruf berechnet; Schwellen kommen aus den bestehenden Ticket-Automatik-Einstellungen, damit Dashboard und Erinnerungen dieselbe Definition von „überfällig“ nutzen.
- Fehlende Aktivitätsdaten (ältere Tickets) werden als „Keine Zeitangabe vorhanden“ angezeigt, nicht geraten.
- Rechte: Aktionen nur für `can_manage_tickets`/Führung sichtbar; die Route prüft serverseitig erneut.

## Teilweise implementiert
- **Wartezeiten** basieren auf `last_user_message_at`/`last_staff_message_at`, die erst seit dem Ticket-Automatik-Update erfasst werden. Ältere offene Tickets zeigen deshalb anfangs keine Wartezeit.

## Geprüft / offen (priorisiert)
1. **P1 – Restore-Probelauf:** `/backup/restore` erstellt vorher eine Sicherheitskopie und validiert, zeigt aber keine Vorschau („was würde sich ändern“). Ein Dry-Run mit Rollen-/Kanal-/Mitglieder-Diff vor der Bestätigung wäre ein deutlicher Sicherheitsgewinn. Zurückgestellt, weil die Strukturwiederherstellung Discord-seitig komplex ist und eine eigene, sorgfältig getestete Änderung braucht.
2. **P2 – Ticket-Übergabe mit Zusammenfassung:** Freigeben eines Tickets mit Pflicht-Notiz („geprüft / offen / nächster Schritt“), die im Ticket und in der Liste erscheint.
3. **P2 – Schnellantworten/Vorlagen pro Kategorie** im Ticket (Discord-Select), nur aus freigegebenen Texten.
4. **P2 – Rollen-Simulation:** „Welche Dashboard-Aktionen dürfte diese Person?“ rein lesend in den Einstellungen.
5. **P3 – Bewerbungen:** Rubriken mit mehreren Prüfern und sichtbaren Abweichungen.

## Nur vorgeschlagen
- Ruhezeiten für Erinnerungen (z. B. keine DMs nachts).
- Last-/Fallverteilung pro Teamler im Cockpit (Tickets + Voice-Fälle der letzten 7 Tage).
