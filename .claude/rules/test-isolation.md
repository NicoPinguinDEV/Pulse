---
paths:
  - tests/**/*.py
---

# Regeln für Tests

- Keine echten Discord-API-Aufrufe, OAuth-Logins, Teamrollenänderungen, Nachrichten oder Produktionsdaten.
- Negative Tests prüfen nicht nur den HTTP-/Rückgabestatus, sondern auch, dass keine schreibende Aktion ausgelöst wurde.
- Bug zuerst durch einen reproduzierbaren Regressionstest festhalten, dann möglichst klein korrigieren.
- Bei Datenformaten Migrationen, Altdaten, ungültige Werte, wiederholte Ausführung und Stabilität nach einem Fehlschlag abdecken.
- Nutze für die Aufgabe die kleinste passende Testsuite; vollständige Tests bleiben Pflicht bei Cross-Cutting-, Sicherheits-, Daten- oder Release-Änderungen.