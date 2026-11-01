# Phase 14.4b – Verdrängte Feature-Handler entfernt

**Basis:** `8fd9176d87afa870e5068fd1a4f17f132a0e4d60`  
**Branch:** `phase/14-4b-remove-displaced-feature-handlers`

## Entfernt

Aus `pulse_features.register` wurden die 13 bereits in der Laufzeit-Inventur als verdrängt bestätigten Handler entfernt:
- Inbox: `inbox_page`, `inbox_read`
- Suche: `search_page`
- Aufgaben: `tasks_page`, `task_update`
- Tickets: `tickets_page`, `ticket_update`
- Kalender: `calendar_page`
- System: `system_page`
- Auswertung: `achievements_page`, `stats_page`, `meetings_history_page`
- Backup: `restore_backup` (der aktive Handler bleibt `webserver.restore_backup`)

Zusätzlich wurde `pulse_features.housekeeping` entfernt. Diese Schleife wurde nie gestartet; die produktive Housekeeping-Implementierung befindet sich in `cogs/pulse_pro.py`.

Nur durch diese toten Handler bzw. die nie gestartete Schleife benutzte lokale UI-Helfer und Imports wurden bereinigt.

## Bewusst erhalten

Die aktiven Routen für Aufgaben-Erstellung, Ticket-Transkript/Bewertung, Wiki und Schulungen bleiben im Modul. Der aktive `POST /backup/restore`-Handler in `webserver` bleibt unverändert. Ebenso bleiben `/tickets/panels` und sämtliche aktiven Ticket-Center-Funktionen unverändert.

## Qualitätssicherung

- Neuer Regressionstest prüft die Abwesenheit aller 13 verdrängten Handler.
- Der Test bestätigt die zehn weiterhin aktiven Feature-Routen.
- Der Test bestätigt, dass die alte Housekeeping-Schleife nicht mehr im Modul existiert.
- Effektiver Routensnapshot und URLs bleiben unverändert.
- Die Routenlandkarte weist fünf statt 18 verdrängte Registrierungen aus.
- Keine Datenmigration, keine Schema-/ID-/custom_id-Änderung, keine Laufzeitdatenänderung.
