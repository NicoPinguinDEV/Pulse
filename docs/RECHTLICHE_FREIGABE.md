# Rechtliche Freigabe der öffentlichen Website

Die Seiten `/impressum`, `/datenschutz` und `/cookies` beschreiben das tatsächliche Verhalten des Codes (geprüft am 2026-11-06). Sie ersetzen keine Rechtsberatung. Nichts davon ist als rechtssicher freigegeben.

Solange eine Pflichtangabe fehlt, zeigt die Seite einen Hinweis, `robots.txt` sperrt alles, und alle öffentlichen Seiten senden `noindex`.

## Vom Betreiber zu ergänzen

| Variable in `.env` | Inhalt | Pflicht |
|---|---|---|
| `PULSE_LEGAL_NAME` | Vollständiger Name oder Organisation | ja |
| `PULSE_LEGAL_ADDRESS` | Zustellfähige Anschrift, Zeilen mit `\|` trennen | ja |
| `PULSE_LEGAL_EMAIL` | Erreichbare E-Mail-Adresse | ja |
| `PULSE_LEGAL_HOSTER` | Name und Sitz des Hosting-Anbieters | ja |
| `PULSE_LEGAL_PHONE` | Telefon, falls angeboten | nein |
| `PULSE_LEGAL_CONTENT_RESPONSIBLE` | Inhaltlich Verantwortlicher, falls nötig | nein |
| `PULSE_LEGAL_EXTRA` | Weitere Pflichtangaben (z. B. Register, USt-ID) | nein |
| `PULSE_PUBLIC_DISCORD_URL` | Dauerhafte Discord-Einladung | nein, ohne sie fehlt der Beitritts-Button |

Der Stand ist im Dashboard unter Verwaltung → Website sichtbar.

## Checkliste für die juristische Prüfung

1. **Impressum:** Gilt § 5 DDG (geschäftsmäßig, in der Regel gegen Entgelt) oder § 18 MStV? Gibt es Einnahmen, Spenden oder Vorteile? Reicht die Kontaktangabe für eine schnelle Kontaktaufnahme?
2. **Verantwortlicher:** Wer genau ist Verantwortlicher (Einzelperson, Team, Verein)? Muss ein Datenschutzbeauftragter benannt werden?
3. **Hosting:** Liegt ein Vertrag zur Auftragsverarbeitung nach Art. 28 DSGVO vor?
4. **Discord:** Rollenverteilung zwischen Betreiber und Discord prüfen, Grundlage für die Übermittlung in die USA bestätigen.
5. **KI-Anbieter:** Vor Aktivierung Vertrag und Nutzungsbedingungen prüfen: Auftragsverarbeitung, Nutzung für Training, Speicherdauer, Drittlandübermittlung. Kostenlose Stufen können abweichende Bedingungen haben. Die Prüfung aller Servernachrichten durch die KI-Moderation braucht eine belastbare Interessenabwägung, ein Hinweis an die Mitglieder ist zu empfehlen.
6. **Minderjährige:** Die Community läuft auf Roblox/Discord, es ist mit Minderjährigen zu rechnen. Art. 8 DSGVO und die Verständlichkeit der Texte prüfen. In den Regelwerken ist kein Mindestalter hinterlegt.
7. **Speicherfristen festlegen:**
   - Bewerbungen: Die automatische Bereinigung ist gesperrt (`RETENTION_MUTATIONS_ENABLED = False`). Die Konfiguration nennt 183 Tage, sie wird derzeit nicht angewendet.
   - Ticket-Transkripte werden ohne Frist aufbewahrt.
   - Teamdaten und Moderationsfälle: Frist festlegen.
   Danach die Tabelle `SECTIONS` in `pulse_public_legal.py` anpassen. Wer Fristen zusichert, muss sie auch umsetzen.
8. **Datenschutzabläufe:** Wer beantwortet Auskunfts- und Löschanfragen, und in welcher Frist? `pulse_retention.export_person()` hilft beim Export.
9. **Rechtsgrundlagen:** Die genannten Grundlagen sind ein Vorschlag aus der technischen Analyse. Besonders Art. 6 Abs. 1 lit. f braucht eine dokumentierte Abwägung.
10. **Regelwerke:** Die Veröffentlichung auf `/regelwerk` ist mit der Serverleitung abzustimmen, die Rechte liegen dort.
11. **Marken:** Nutzung von „Bochum“, „Discord“, „Roblox“ sowie die Nennung realer Organisationen (etwa „ADAC“ im Regelwerk) prüfen. Bochum RP sollte nicht wie ein Angebot der Stadt wirken.
12. **Cookies:** Einstufung der Einträge unter `/cookies` als unbedingt erforderlich bestätigen, vor allem die Darstellungs-Einstellungen im Dashboard (§ 25 Abs. 2 Nr. 2 TDDDG).
13. **Gesetzesstand:** Die Fundstellen (DDG, TDDDG, DSGVO) vor Veröffentlichung gegen die amtliche Fassung auf gesetze-im-internet.de prüfen.

## Optionale Dienste

Aktuell lädt die Website nichts von Dritten und setzt keine Cookies, deshalb gibt es keinen Einwilligungsdialog. Soll ein optionaler Dienst dazukommen, gehört er in `OPTIONAL_SERVICES` in `pulse_public_site.py`, und sein Code in ein `<template data-consent-service="...">`. Der Dialog erscheint dann automatisch, Akzeptieren und Ablehnen sind gleichwertig, nichts ist vorausgewählt, und die Auswahl liegt versioniert im Browser. Die Content-Security-Policy muss dafür bewusst erweitert und die Datenschutzerklärung ergänzt werden.
