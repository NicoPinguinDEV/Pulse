# Bewerbungs-Dublettenprüfung

Die Dublettenprüfung ist ein rein lesender Recruiting-Hinweis. Sie vergleicht eine Bewerbung nur mit anderen Datensätzen, deren validierte Discord-ID exakt übereinstimmt.

## Matching-Regeln
- Unterstützte Legacy-Felder: `user_id`, `discord_id`, `applicant_id`.
- Akzeptiert werden ausschließlich Ziffernfolgen mit 15 bis 22 Stellen.
- Anzeigenamen, Motivationen, Antworten, E-Mail-Adressen oder Ähnlichkeitswerte werden nicht zum Matching verwendet.
- Die aktuelle Bewerbung wird ausgeschlossen; maximal fünf Treffer werden angezeigt.

## Anzeige und Datenschutz
- Die Hinweise erscheinen nur in Ansichten, die bereits `can_review_applications(ctx)` verlangen bzw. `can_review` wahr ist.
- Die Karte zeigt nur weitere Bewerbungs-ID, Status und Einreichungsdatum.
- Namen, Discord-IDs, Motivationen, Testantworten, Reviews und interne Notizen der anderen Bewerbung werden nicht in die Hinweis-Komponente übernommen.
- Ausgabe von Datenfeldern wird HTML-escaped.

## Keine automatische Entscheidung
Ein Treffer ist kein Betrugsnachweis. Die Karte ist ausdrücklich als Prüfhinweis gekennzeichnet. Pulse ändert dadurch weder Bewerbungsstatus noch Bewertung, Rollen, Stimmen, Fristen oder sonstige Daten. Die Entscheidung verbleibt vollständig bei berechtigten Recruitern.

Die Prüfung liest ausschließlich den bestehenden `applications.json`-Datenbestand; es gibt keine Migration oder neue persistente Tabelle.
