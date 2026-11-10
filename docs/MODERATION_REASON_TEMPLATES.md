# Versionierte Moderations-Begründungsvorlagen

Pulse verwaltet Begründungsvorlagen pro Discord-Server und Moderationsaktion. Unterstützt werden Verwarnung, Timeout, Kick und Ban.

## Befehle

- `/mod vorlagen` – aktive Vorlagen anzeigen, die zu den eigenen Pulse- und Discord-Moderationsrechten passen.
- `/mod vorlage_erstellen` – neue Vorlage anlegen (nur Administratoren).
- `/mod vorlage_bearbeiten` – eine neue, unveränderliche Version speichern (nur Administratoren).
- `/mod vorlage_historie` – die letzten zehn Versionen samt Autor und Zeitstempel einsehen (nur Administratoren).
- `/mod vorlage_archivieren` – eine Vorlage für neue Maßnahmen deaktivieren, ohne ihre Historie zu löschen (nur Administratoren).

Bei `/mod warn`, `/mod timeout`, `/mod kick` und `/mod ban` kann das optionale Feld `vorlage` den exakten Vorlagennamen enthalten. Der Pflichtgrund bleibt als zusätzliche Begründung erhalten. Verwendet der Vorlagentext den optionalen Platzhalter `{details}`, wird der Pflichtgrund dort eingesetzt; andernfalls wird er als Zusatzdetail angehängt.

## Versionen und Fallakten

Eine neue Vorlage beginnt bei Version 1. Jede Bearbeitung legt eine weitere unveränderliche Revision an und schaltet nur die aktive Versionsnummer um. Frühere Versionen werden nicht überschrieben. Archivierte Vorlagen können nicht mehr verwendet oder bearbeitet werden; ihre Historie bleibt lesbar.

Eine Moderationsfallakte speichert die verwendete Vorlagen-ID und Versionsnummer zusammen mit dem tatsächlich angewendeten Grund. Ein späteres Bearbeiten der Vorlage verändert keine bereits angelegte Fallakte.

## Rechte und Sichtbarkeit

Die Verwaltung (Erstellen, Bearbeiten, Archivieren und Historie) ist ausschließlich Administratoren gestattet. Die Vorlagenliste ist privat und zeigt nur Aktionen, für die das Mitglied sowohl das erforderliche Pulse-Recht als auch das passende Discord-Recht besitzt. Die Auswahl einer Vorlage ersetzt niemals die vorhandene Rechte-, Zielhierarchie- oder Bestätigungsprüfung einer Moderationsaktion.

Vorlagen sind nach Guild und Aktion getrennt. Namen sind innerhalb einer Guild und Aktion eindeutig (Groß-/Kleinschreibung wird ignoriert). Es gibt keine automatische Sanktion und keine externe KI-Abhängigkeit.

## Speicherung und Sicherung

Die Katalog-Metadaten und unveränderlichen Versionen liegen in `mod_reason_templates` und `mod_reason_template_versions`; die Tabellen sind Teil von `pulse_db.TABLES` und werden über `pulse_db.export_state()` gesichert und wiederhergestellt. Die Fallakten-Tabelle `mod_cases` ist ebenfalls in der JSON-Backup-Abdeckung enthalten. Das Schema wird idempotent erstellt und bestehende Fallakten erhalten die beiden optionalen Referenzspalten ohne Änderung vorhandener Gründe.
