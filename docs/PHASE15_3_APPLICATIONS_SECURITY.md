# Phase 15.3 – Bewerbungsdaten: Bestandsaufnahme und Schutzmaßnahmen

Stand: 02.11.2026

## Ziel

Bewerbungsdaten enthalten personenbezogene Angaben, Freitext, Test- und Praxisantworten sowie interne Bewertungen. Diese Daten werden getrennt von allgemeinen Team-Dashboard-Rechten behandelt. Diese Bestandsaufnahme hält die aktuellen Datenflüsse, bereits umgesetzte Schutzmaßnahmen und noch offene Produktentscheidungen fest.

## 1. Öffentlicher Bewerbungsablauf

1. Bewerber melden sich über den Discord-OAuth-Ablauf an.
2. Pulse stellt ein signiertes, typisiertes `apply`-Session-Cookie aus. Es ist `HttpOnly`, `SameSite=Lax`, auf HTTPS `Secure`, gilt drei Stunden und wird durch eine absolute Laufzeit von zwölf Stunden begrenzt.
3. Die Seite prüft die erforderliche Discord-Rolle. Die Einreichung prüft die signierte Sitzung und die Mitgliedschaft erneut serverseitig; API-/Discord-Prüfprobleme führen zu einem geschlossenen Fehlerzustand statt zu einer Freigabe.
4. Laufbahn, Modulstatus, Pflichtfelder und die Anzahl der Fragen werden serverseitig geprüft. Test- und Praxisbewertungen werden im Backend berechnet. Vom Browser übermittelte Zusatzfelder können den Testwert nicht erhöhen.
5. Pro IP existiert eine Einreichungsbegrenzung. Der Check auf eine bereits offene Bewerbung und das Speichern des neuen Datensatzes erfolgen gemeinsam unter `_io_lock`, damit gleichzeitige Requests keine doppelten offenen Bewerbungen anlegen.

## 2. Gespeicherte Daten

`applications.json` enthält – je nach Verfahrensstand und Altbestand – unter anderem:

- Discord-ID, Discord-Name/Profilbild und Bewerbungszeitpunkt
- Motivationstext und ausgewählte Laufbahn
- Testscore, Bestehensstatus, Fragen, Antworten und richtige Antworten
- Praxisaufgaben, Antworten, Prüfhilfen und manuelle Bewertungsstände
- Up-/Downvotes, strukturierte Reviews und interne Review-Notizen
- Status, Bearbeiter, Entscheidungsgründe, Gesprächsnotizen und Historie
- Gesprächstermin, Kalenderreferenz und Erinnerungs-/Kommunikationsstatus

Weitere Gesprächs- und Bewertungsinformationen können zusätzlich in `suite_interviews` in der Suite-Datenbank liegen.

## 3. Berechtigungsmodell

Vollständige Bewerbungsdaten dürfen nur gelesen werden, wenn mindestens eines dieser Rechte vorliegt:

- `can_manage_applications`
- `can_promote`
- `is_admin`

Die vorhandene Bewerbungsübersicht bleibt für Teammitglieder mit Dashboard-Zugriff und Abstimmungsrecht erreichbar. Diese Ansicht zeigt nur die für die bestehende Abstimmung nötige reduzierte Übersicht; Motivation, Antworten, interne Reviews und Bewerber-ID werden ausgeblendet.

Die Recruiting-Berechtigung wird zusätzlich serverseitig an diesen Stellen geprüft:

- direkte Suite-Routen für Bewerbungslisten, Bewerbungsakten und Gesprächslisten
- globale Suite-Suche für Bewerbungs- und Gesprächsdaten
- vollständige Details in der klassischen Bewerbungsübersicht

Das Verstecken eines Navigationspunktes allein ist ausdrücklich keine Zugriffskontrolle.

## 4. Speicherung und Backup

- `applications.json` bleibt in dieser Phase das aktive Datenformat.
- Auf POSIX-Systemen setzt Pulse beim Start und bei jedem Schreiben die Dateirechte auf `0600`.
- Voll-Backup-Ordner werden auf POSIX-Systemen auf `0700`, erzeugte ZIP-Archive auf `0600` gesetzt. Die Archive enthalten dieselben sensiblen Daten und müssen wie Produktivdaten behandelt werden.
- Die ZIP-Dateien sind nicht zusätzlich verschlüsselt. Der Schutz beruht derzeit auf Dateisystem-/Volume-Rechten und dem Zugriff auf den Server. Für externe/off-site Backups ist verschlüsselte Ablage gesondert vorzusehen.
- Der Privacy-Export durch die Administration enthält die zur Discord-ID passenden Bewerbungsdaten.

## 5. Aufbewahrung und Löschung – offene Produktentscheidung

Die Retention-Funktion ist standardmäßig deaktiviert. Der konfigurierbare Standardwert für abgeschlossene Bewerbungen beträgt 183 Tage, sobald die Funktion aktiviert wird.

Wichtig: `hired` und `accepted` sind derzeit von der automatischen Löschung ausgenommen. Diese Datensätze werden als Quelle für `Team seit` verwendet. Dadurch können vollständige Antworten und interne Notizen erfolgreicher Bewerber deutlich länger gespeichert bleiben als abgelehnte Bewerbungen.

Diese Ausnahme wird in Phase 15.3 nicht stillschweigend verändert, weil sie bestehende Teamzeit-/Historienfunktionen beeinflussen kann. Vor einer automatischen Bereinigung erfolgreicher Fälle ist ein separates, minimiertes Personal-/Einstellungsereignis erforderlich (z. B. Discord-ID, Einstellungsdatum, Laufbahn und benötigte Audit-Referenz). Danach können Test-/Praxisantworten und interne Review-Notizen nach einer festgelegten Frist entfernt werden, ohne `Team seit` zu verlieren.

## 6. Umgesetzte Schutzmaßnahmen in PR #126

- Zentrale Funktion `can_review_applications(ctx)` als einheitliche Sichtbarkeitsentscheidung.
- Redaction der privaten Felder für Dashboard-Nutzer ohne Recruiting-Recht.
- Serverseitige Gates für Suite-Listen, Akten, Gesprächsübersichten und globale Suche.
- Atomare Prüfung und Anlage einer Bewerbung.
- POSIX-Modus `0600` für `applications.json`.
- POSIX-Modi `0700` für den Full-Backup-Ordner und `0600` für erzeugte Full-Backup-Archive.
- Regressionstests für die Rechteprüfung, redigierte Ansicht, Suche und Dateirechte.

## 7. Noch nicht umgesetzt

1. Datenminimierung für alte `hired`/`accepted`-Fälle nach Einführung eines separaten Einstellungsereignisses.
2. Entscheidung und Dokumentation einer verbindlichen Aufbewahrungsdauer je Status und Datentyp.
3. Verschlüsselte Off-site-Backups und ein dokumentierter Schlüssel-/Wiederherstellungsprozess.
4. Versioniertes Bewerbungsdaten-Schema und ein späterer Repository-/SQLite-Schritt mit Migrations- und Restore-Tests.
5. Prüfung, ob alle zusätzlichen Interview-, Kalender- und Kommunikationsreferenzen bei Löschung einer Bewerbung mit entfernt bzw. anonymisiert werden.

Bis diese Punkte entschieden sind, bleiben bestehende Aufbewahrungswerte und JSON-Daten unverändert; es erfolgt keine automatische Bereinigung oder Produktionsmigration durch diesen PR.
