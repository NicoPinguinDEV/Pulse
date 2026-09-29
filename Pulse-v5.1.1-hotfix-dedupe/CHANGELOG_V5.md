# Pulse v5.1 – Professional TeamOS Upgrade

## Oberfläche
- neues Operations Center mit Live-Kennzahlen, Aufmerksamkeit, Inbox und Schnellaktionen
- neues responsives Sidebar-Layout mit Gruppen und Status-Badges
- Dark/Light Theme, Command Palette (Ctrl+K), Mobile Navigation
- PWA Manifest + Service Worker
- kompakte Status-Pills, Karten, Tabellen und responsive Layouts

## Team-Workflow
- Teamstatus: Verfügbar / Beschäftigt / Abwesend / Nicht stören
- Schicht-Übergaben mit Priorität und Benachrichtigungen
- Team-Ankündigungen mit Discord-Embed und Audit
- Beförderungsanträge mit Rollenprüfung und Audit
- gemeinsames Freigabecenter für Bewerbungen und Beförderungen
- Aufgaben-Kanban mit Verantwortlichen, Fristen, Prioritäten und Historie
- globale Suche
- Team-Analytics + CSV Export
- Team-Kalender

## Tickets
- persistente Ticket-Views
- atomarer Claim gegen Race Conditions
- Freigeben / Übernehmen
- Prioritätswechsel direkt im Ticket
- Abschluss mit Schließgrund-Modal
- Transcript-Erstellung
- Abschluss-DM
- detailliertere Ticketstatistik

## Daten / Sicherheit
- zentrale SQLite-Struktur für Pulse-Module
- WAL, Foreign Keys und Busy Timeout
- deduplizierte Inbox-Benachrichtigungen
- Event-/Audit-Historie
- Session Signierung und Security Headers
- Same-Origin Guard für state-changing Requests
- separate konfigurierbare Bewerbungs-OAuth-Redirect-URI
- Backups inklusive zentraler Pulse-Daten

## Discord
- `/pulse`
- `/teamstatus`
- `/mytasks`
- `/pulsehealth`
- automatisches Housekeeping für überfällige Aufgaben, alte Tickets und LOA-Erinnerungen
- professionelle Bot-Presence

## Bekannte Hosting-Voraussetzung
Discord OAuth muss exakt auf die konfigurierte Callback-URL zeigen. Für Bewerbungen ist `DISCORD_APPLICATION_REDIRECT_URI` die bevorzugte Variable.


## 5.1 Quality / UX
- Live-Dashboard-Zahlen für Dienst, Tickets und überfällige Aufgaben
- sichererer Service Worker ohne Cache privater Dashboard-Inhalte
- Systemstatus erkennt nicht geladene Cogs
- zusätzliche Discord-Befehle für Ankündigungen, Aufgaben und Teamakten
- Slash-Command-Fehler werden nutzerfreundlich behandelt und geloggt
- Bot-Presence aktualisiert die aktuelle Dienstanzahl automatisch
- Routing-Dubletten zwischen Pulse-v4 und Pulse-v5 entfernt
- Datenbank-CRUD nochmals gegen reale Tabellen getestet; Trainings- und Achievement-APIs korrigiert


## 5.1.1 Final Quality Pass
- individuelle Inbox-Nachrichten können direkt als gelesen markiert werden
- Live-Dashboard aktualisiert nun Dienst, Tickets, Freigaben, Bewerbungen, überfällige Aufgaben und LOAs
- Command Palette enthält jetzt auch Achievements
- zentrale DB ist über `PULSE_DB_PATH` konfigurierbar
- finaler Runtime-Cleanup: keine lokalen DB-/Session-/Cache-Dateien im Release
- zusätzlicher Smoke-Test für DB- und Workflow-Kernfunktionen
