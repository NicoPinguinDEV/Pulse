# Versionierte Moderations-Begründungsvorlagen

Pulse bietet serverbezogene Begründungsvorlagen für Bürger-Verwarnungen, Timeouts, Kicks und Bans. Die Vorlagen können nicht zwischen Discord-Servern aufgerufen werden.

## Vorlagen verwalten

Alle Verwaltungsbefehle sind auf Pulse-Administratoren beschränkt:

- `/mod vorlagen` – aktive Vorlagen auflisten; optional kann nach Aktion gefiltert werden.
- `/mod vorlage_erstellen` – eine Vorlage für warn, timeout, kick oder ban anlegen.
- `/mod vorlage_aktualisieren` – eine neue Version für eine vorhandene Vorlagen-ID speichern.
- `/mod vorlage_archivieren` – die Vorlage aus der Auswahl nehmen; ihre Historie bleibt erhalten.
- `/mod vorlage_versionen` – die gespeicherten Versionen samt Titel, Autor und Zeitstempel ansehen.

Ein neuer Eintrag startet mit Version 1. Bei jeder Aktualisierung werden Titel und Text als neue Version gespeichert. Frühere Versionen werden nicht überschrieben.

## Bei einer Moderationsaktion auswählen

Bei `/mod warn`, `/mod timeout`, `/mod kick` und `/mod ban` bleibt das Feld `grund` bestehen. Beginne dort den Vorlagentitel einzugeben und wähle die angezeigte Option mit Versionsnummer aus. Die Auswahl übermittelt intern die Vorlagen-ID und die exakte Version. Der Fall speichert außerdem eine Kopie des angewendeten Begründungstextes.

Freitext bleibt weiterhin möglich. Eine veraltete, archivierte, fremde oder zur Aktion unpassende Vorlagenauswahl wird abgewiesen. Die vorhandenen Pulse- und Discord-Rechte sowie die Zielhierarchieprüfungen werden vor der Maßnahme weiterhin ausgeführt. Kick und Ban behalten ihre Bestätigungsabfrage.

## Speicherung, Audit und Datenschutz

Vorlagen sind pro Discord-Guild und Moderationsaktion getrennt. Erstellen, Aktualisieren und Archivieren werden im Pulse-Audit protokolliert. Die Vorlagenliste ist privat und zeigt nur Aktionen, für die die Person sowohl das erforderliche Pulse-Recht als auch das passende Discord-Recht besitzt.

Vorlagen-Metadaten, unveränderliche Versionen und Moderationsfallakten liegen in `pulse.db`. Alle drei Tabellen sind Bestandteil von `pulse_db.export_state()` und `pulse_db.import_state()`. Das Schema wird idempotent erstellt; bei bestehenden Moderationsfällen werden nur zwei optionale Referenzspalten ergänzt. Alte Fallgründe und Fall-IDs werden nicht umgeschrieben.
