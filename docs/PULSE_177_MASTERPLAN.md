# Pulse TeamOS – Masterplan 177/177

Stand: 2026-11-14, `origin/main` @ `2c02788`. Zählung nie von Hand, sondern `python tools/audit_counts.py`
(Quelle: Einzelzeilen in `docs/PULSE_FEATURE_AUDIT_177.md`). Mit #99: 102 fertig / 75 teilweise / 0 offen.

## Bestandsaufnahme (verifiziert)

- Hauptrepo `Documents/Pulse` liegt auf `phase/14-8-indexed-shift-reads` mit 25 nicht committeten Änderungen – **nicht anfassen**.
- Weitere Worktrees mit Änderungen: `Pulse-177-b4` (8), `Pulse-177-b5` (7). Alle anderen sauber.
- Keine offenen PRs; zuletzt gemergt: #247, #248, #249 (Bewerbungs-Board).
- Branch-Protection von `main` ist nicht aktiviert (manuell in GitHub einzurichten); ein Merge kann den VPS-Deploy auslösen.

## Blöcke und Abhängigkeiten

Die Reihenfolge folgt den Abhängigkeiten: Block F (Integrationsverträge) liefert Fallnummern, Events, Inbox, Audit und
Vorlagen, auf denen die übrigen Blöcke aufsetzen. Deshalb wird F früh begonnen, sobald A abgeschlossen ist.

| Block | Inhalt | Audit-IDs | Abhängig von |
|---|---|---|---|
| A | Recruiting abschließen | 99 (fertig im Branch), 100, 101, 102, 103 | – (103 → F/#123 Vorlagen) |
| F | Zentrale Integration | 116–125, 11 | A (Vorlagen-Muster) |
| B | Discord, Moderation, Community | 4, 6, 10, 41, 44, 47, 50, 51, 53, 54 | F (#116, #120, #121) |
| C | Dashboard 2.0 | 57, 58, 62, 64–68, 70 | F (#117 Events für Live-Feed) |
| D | Teamverwaltung/Personal | 71, 73–76, 81, 87–89, 92, 94 | F, C |
| E | Academy | 106, 108, 111, 113 | F (#123), D (#74) |
| G | Intelligence/Automatisierung | 129–133, 136, 137, 139, 141, 151, 152 | F (#125), D |
| H | System/Sicherheit/Betrieb | 153–155, 157, 158, 160, 165, 168, 169, 172 | F (#122) |
| I | BOCHUM-RP-TeamOS | 173–177 | D, E, F |

## Arbeitsregeln je Block

Ein Feature-Branch und eine prüfbare PR pro Block (bei Block F mehrere, weil zentral). Pro Funktion: Audit-Text prüfen,
aktiven Codepfad belegen, Lücke bestimmen, implementieren, Verhaltens-/Rechte-/Fehlertests, Regression, Dokumentation.
„Fertig" nur mit Code- und Testnachweis. Keine Sanktionen durch KI, keine bezahlte externe KI-API, keine Produktivdaten,
kein VPS, kein Live-Discord, keine Releases, keine Versionsänderung, PRs werden nicht selbst gemergt.

## Ehrliche Einordnung

75 teilweise Funktionen sind mehrere Wochen Entwicklung und mehrere Sitzungen. Pro Sitzung wird ein Block geschlossen,
mit `HANDOFF.md` und aktualisierten Audit-Zahlen. Epic #185 wird erst geschlossen, wenn alle 177 Zeilen „Fertig" sind.
