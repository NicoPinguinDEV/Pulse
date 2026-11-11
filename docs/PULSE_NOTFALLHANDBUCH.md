# Pulse – Notfallhandbuch

**Geltungsbereich:** BOCHUM RP TeamOS – Discord-Bot, Dashboard, Datenhaltung und VPS  
**Stand:** 11.11.2026  
**Referenzen:** VPS_DEPLOYMENT.md · docs/BACKUP_RECOVERY_AND_IP_AUDIT.md · docs/PULSE_FEATURE_AUDIT_177.md

> Dieses Handbuch beschreibt das sichere Vorgehen, ist aber keine Live-Statusmeldung. Es ersetzt weder die Freigabe des Betreibers noch die Prüfung der tatsächlichen Serverumgebung. Änderungen an Produktivsystem, Discord, VPS oder Laufzeitdaten erfolgen nur durch eine ausdrücklich autorisierte Person.

## 1. Zweck und unverhandelbare Regeln

Dieses Handbuch führt von der ersten Meldung über Eindämmung und Wiederherstellung bis zum dokumentierten Abschluss. Es gilt bei Ausfall, Datenfehlern, Sicherheitsvorfällen, fehlgeschlagenen Updates und versehentlichen administrativen Änderungen.

- Ruhe bewahren, Auswirkungen eingrenzen und jede wichtige Entscheidung mit Zeitstempel dokumentieren.
- Keine ungezielten Neustarts, Datenbankänderungen, Bereinigungen oder Rollbacks auf Verdacht.
- Niemals Laufzeitdaten, Backups, /opt/pulse/data, .env oder .session_secret löschen oder überschreiben, um einen Startfehler zu umgehen.
- Niemals docker compose down -v, docker volume prune, git reset --hard oder spontane manuelle SQL-Reparaturen als Erstmaßnahme ausführen.
- Keine Tokens, Passwörter, OAuth-Codes, Session-Cookies, vollständigen personenbezogenen Datensätze oder ungefilterten privaten Logs in Discord, Issues oder Screenshots veröffentlichen.
- Keine Rollen, Sanktionen, Bewerbungen oder Teamdaten als Test verändern. Tests erfolgen mit synthetischen Daten und isolierter Umgebung.
- Ein Incident ist nicht abgeschlossen, nur weil der Prozess läuft: Healthcheck und wichtige Nutzerabläufe müssen geprüft sein.

## 2. Rollen und Verantwortlichkeiten

Bei jedem Incident werden die folgenden Rollen benannt. Eine Person darf mehrere Rollen übernehmen; die Zuordnung muss trotzdem eindeutig im Protokoll stehen.

| Rolle | Verantwortung |
|---|---|
| Incident Lead | Entscheidet Priorität, koordiniert Maßnahmen, holt Freigaben ein und erklärt den Incident für beendet. |
| Technische Einsatzkraft | Prüft Bot, Dashboard, Container, Datenbanken, Backups und Logs; führt nur freigegebene technische Maßnahmen aus. |
| Kommunikation | Informiert Team/Führung über Auswirkungen, Status und nächste Aktualisierung. Veröffentlicht keine Secrets oder unnötigen personenbezogenen Daten. |
| Datenschutz-/Datenverantwortung | Bewertet Datenverlust, unbefugte Offenlegung, Aufbewahrung und notwendige Schutzmaßnahmen. |
| Freigebende Person | Bestätigt risikoreiche Maßnahmen wie Restore, Credential-Rotation, Rollback oder produktive Konfigurationsänderungen. |

**Mindestbesetzung:** Bei P1/P2 muss eine Person als Incident Lead benannt sein. Bei möglichem Datenabfluss oder Datenverlust wird zusätzlich die Datenschutz-/Datenverantwortung einbezogen. Fehlt eine erreichbare Freigabe, werden nur lesende Diagnosen und reversible Eindämmungsschritte durchgeführt.

## 3. Schweregrade und Eskalation

| Stufe | Beispiele | Reaktion und Eskalation |
|---|---|---|
| **P1 – kritisch** | Bot und Dashboard nicht verfügbar; bestätigter Datenverlust; kompromittierter Bot-/Adminzugang; unautorisierte kritische Änderungen. | Sofort Incident Lead und Technik informieren. Nicht notwendige Änderungen stoppen. Bei Sicherheits-/Datenschutzbezug Datenschutzverantwortung hinzuziehen. |
| **P2 – hoch** | Zentrale Funktion gestört, Datenbankprüfung auffällig, wiederholte Container-/Healthcheck-Fehler, Update schlägt fehl und kein gesunder Stand startet. | Incident Lead und Technik zeitnah hinzuziehen. Betroffene Funktion begrenzen, Ursache prüfen und Wiederanlauf planen. |
| **P3 – mittel** | Einzelner Befehl, Dashboardbereich oder Hintergrundjob fehlerhaft, während Kernbetrieb weiterläuft. | Zuständiges Modul prüfen, Fehler dokumentieren und mit Regressionstest beheben. |
| **P4 – niedrig** | Darstellungsfehler, einzelne unkritische Meldung oder Dokumentationsabweichung ohne Betriebswirkung. | Als Wartungsaufgabe erfassen und priorisieren. |

**Eskalationsregel:** Wird die Auswirkung größer, ein Datenintegritätsproblem entdeckt oder ein weiterer Dienst beeinträchtigt, wird die Stufe sofort erhöht. Bei Unsicherheit zunächst die höhere plausible Stufe wählen. Es gibt keine automatische Sanktion gegen Nutzer oder Teammitglieder als Incident-Maßnahme.

## 4. Erste 15 Minuten – Checkliste

- [ ] Zeitpunkt des ersten Hinweises und Zeitpunkt der Erkennung in Europe/Berlin erfassen.
- [ ] Incident-ID vergeben (z. B. PULSE-INC-2026-001) und P1–P4 festlegen.
- [ ] Betroffene Komponenten notieren: Discord-Bot, Dashboard/OAuth, API, Datenbank, Backup, VPS/Container oder Drittanbieter.
- [ ] Auswirkungen beschreiben: Wer ist betroffen? Seit wann? Welche Funktionen funktionieren noch?
- [ ] Incident Lead, technische Einsatzkraft und Kommunikationsverantwortung benennen.
- [ ] Letzte bekannte Änderung und letzten nachweislich gesunden Commit ermitteln; nicht aus Vermutung ableiten.
- [ ] Lesende Diagnose durchführen und Zeitstempel/Fehlerarten protokollieren.
- [ ] Beweise sichern, aber Secrets und personenbezogene Inhalte schwärzen.
- [ ] Parallele Deployments und nicht notwendige Konfigurationsänderungen pausieren.
- [ ] Bei möglichem Sicherheitsvorfall Zugriff begrenzen und Datenschutzverantwortung informieren.
- [ ] Nächsten Prüfzeitpunkt und verantwortliche Person festlegen.

**Erstmeldungsvorlage:**

```text
Incident-ID:
Schweregrad:
Erkannt am (Europe/Berlin):
Betroffene Komponenten:
Beobachtete Auswirkung:
Letzter bekannter gesunder Stand:
Incident Lead:
Technische Einsatzkraft:
Datenschutzbezug: ja / nein / unklar
Bereits ausgeführte, nur lesende Prüfungen:
Nächster Schritt und Verantwortlicher:
```

## 5. Sichere Diagnose

Diagnosen zuerst lesend ausführen. Befehle mit Serverpfaden gehören in eine autorisierte SSH-Sitzung auf dem VPS, nicht in Windows PowerShell.

**Discord und Anwendung**

- Im Server als berechtigte Person /pulse-diagnose ausführen. Der Befehl prüft unter anderem Gateway, fehlgeschlagene Cog-Ladevorgänge, Zeitzone, erforderliche IDs, Rollen-Hierarchie, aktivierte Moderationsschutz-Berechtigungen und Datenbanken.
- Dashboard-/API-Erreichbarkeit lokal auf dem VPS prüfen:

```bash
curl -fsS --max-time 5 http://127.0.0.1:25095/healthz
```

Ein gesunder Healthcheck muss ok: true, bot_ready: true, guild_ready: true und database_ok: true enthalten. Ein HTTP-Erfolg allein reicht nicht, wenn einer dieser Zustände falsch ist.

**Container und Update-Dienst**

```bash
cd /opt/pulse/Pulse
docker compose ps
docker compose logs --tail=150 pulse
systemctl status pulse-github-update.service
journalctl -u pulse-github-update.service -n 100 --no-pager
```

Logs vor Weitergabe auf Secrets, Session-/OAuth-Daten und personenbezogene Inhalte prüfen und schwärzen. Niemals .env ausgeben oder anhängen.

**Datenbanken und Backup**

Pulse-Systemdiagnosen führen für vorhandene SQLite-Datenbanken einen lesenden PRAGMA quick_check(1) aus. Bei Auffälligkeiten nicht direkt auf der laufenden Datei reparieren. Schreibzugriffe, Kopieren aktiver SQLite-Dateien und Restore sind getrennte, freigabepflichtige Maßnahmen. Vor jeder Wiederherstellung ist der aktuelle Stand zu sichern.

## 6. Wiederherstellung – Reihenfolge und Freigaben

### 6.1 Anwendungsausfall oder fehlerhafter Start

1. Incident Lead und technische Einsatzkraft bestätigen betroffene Komponenten und letzte gesunde Version.
2. Containerstatus und Update-Protokoll lesen. Bei einem fehlgeschlagenen Boot-Update prüfen, ob der Helper den bestehenden Stand gestartet oder den Rollback ausgelöst hat.
3. Die Start-/Update-Logik sichert Laufzeitdaten vor einem Update und prüft danach den Healthcheck. Den Ausgang nicht durch manuelles Überschreiben des Checkouts umgehen.
4. Ein erneuter Start oder eine erneute Update-Ausführung ist eine produktive Änderung und erfordert Freigabe durch Incident Lead/Betreiber.
5. Nicht parallel manuell deployen, solange der Deploy-Lock belegt ist. Niemals einen Branch, ungeprüften Commit oder lokalen Arbeitsstand live schalten.
6. Nach Wiederanlauf Healthcheck und die unter Abschnitt 8 aufgeführten Kernabläufe verifizieren.

### 6.2 Fehler nach einem Code-Update

- Installierten Commit, Update-Log und letzten erfolgreichen CI-geprüften main-Commit feststellen.
- Prüfen, ob der vorhandene Boot-/Deploy-Helper bereits auf den vorherigen kompatiblen Code zurückgekehrt ist.
- Ein manueller Rollback darf nur nach Prüfung der Datenlayout-Kompatibilität und gemäß VPS_DEPLOYMENT.md erfolgen. Ein alter Code-Stand darf niemals auf ein inkompatibles neues Datenlayout losgelassen werden.
- Wenn der Helper einen kritischen Datenlayout-Wechsel ausdrücklich nicht zurückrollt, Pulse nicht durch manuelles Checkout auf einen alten Stand starten. Incident eskalieren und den dokumentierten Migrations-/Recovery-Pfad verwenden.

### 6.3 Datenfehler oder vermuteter Datenverlust

1. Schreibende Aktionen im betroffenen Bereich stoppen; bei laufender Beschädigung entscheidet Incident Lead über kontrolliertes Stoppen des Dienstes.
2. Aktuelle Dateien und Backups nicht löschen. Vor Restore einen vollständigen Sicherungsstand des aktuellen Zustands erstellen.
3. Backup-Integrität und Manifest prüfen; Restore-Vorschau nutzen. Die Vorschau muss vor dem Restore zeigen, welche Dateien/Tabellen geändert würden.
4. Restore zuerst in einer isolierten Testumgebung mit synthetischen Daten üben. Bei laufendem Produktivbetrieb niemals Test-Restore in das Live-Datenverzeichnis schreiben.
5. Produktiv-Restore nur mit ausdrücklicher Freigabe. Die ausdrückliche Freigabe muss durch Incident Lead/Betreiber erfolgen; Quellbackup, Zielverzeichnis und Rückfallplan sind zu dokumentieren.
6. Nach Restore Datenintegritätsprüfungen, Healthcheck und fachliche Stichproben durch berechtigte Personen durchführen. Keine personenbezogenen Inhalte in das Incident-Protokoll kopieren.

### 6.4 Sicherheits- oder Credential-Vorfall

- Als P1 behandeln, bis Reichweite und Folgen eingegrenzt sind. Zugangsdaten ausschließlich über den jeweiligen offiziellen Anbieter und durch berechtigte Betreiberpersonen widerrufen/rotieren.
- Den Discord-Bot-Token bei bestätigter Kompromittierung über das Discord Developer Portal erneuern und sicher in der Hosting-Konfiguration hinterlegen. Token niemals in Chat, Issue oder Log posten.
- SESSION_SECRET bzw. .session_secret nicht blind austauschen: davon können verschlüsselte Recovery-Zugänge abhängen. Rotation nur nach Prüfung der Folgen und mit gesichertem Wiederherstellungsplan.
- Betroffene Sessions/Integrationen nach einem geprüften Plan widerrufen, Zugriff auf Laufzeitdaten und Backups kontrollieren und Beweismittel geschützt erhalten.
- Datenschutzverantwortung prüft, ob personenbezogene Daten betroffen sein könnten und welche weiteren Schritte erforderlich sind.

## 7. Betriebsfreigabe und Kommunikation

Vor jeder produktiven Wiederherstellungsmaßnahme müssen im Incident-Protokoll stehen:

- konkrete Maßnahme und erwartete Wirkung;
- ausführende Person und freigebende Person;
- betroffene Dienste und Daten;
- vorhandenes Vorher-Backup bzw. begründete Ausnahme;
- erwartete Unterbrechung und Rückfallmöglichkeit;
- Prüfschritte für den Erfolg.

Statusmeldungen enthalten nur bestätigte Fakten, betroffene Funktionen, bekannte Einschränkungen und den nächsten Aktualisierungszeitpunkt. Keine Schuldzuweisungen und keine Spekulation als Tatsache darstellen. Bei Sicherheitsvorfällen Kommunikation mit Datenschutz-/Betreiberverantwortung abstimmen.

## 8. Wiederanlauf-Checkliste

Ein Incident darf erst geschlossen werden, wenn die zutreffenden Punkte geprüft und protokolliert sind:

- [ ] Container läuft und docker compose ps zeigt den erwarteten Dienststatus.
- [ ] /healthz liefert ok: true, bot_ready: true, guild_ready: true und database_ok: true.
- [ ] Discord-Gateway ist verbunden; keine neuen kritischen Cog-Ladefehler.
- [ ] Dashboard-Login und eine berechtigte interne Dashboard-Seite funktionieren.
- [ ] Ticket-/Support-Kernablauf ist mit einem sicheren Testfall oder einer geeigneten nicht-destruktiven Prüfung erreichbar.
- [ ] Datenbank-Quick-Checks und erforderliche Konsistenzprüfungen sind erfolgreich.
- [ ] Backup ist vorhanden, lesbar und gemäß Verfahren verifiziert.
- [ ] Moderationsschutz, Wartungsmodus und Feature-Schalter stehen auf dem beabsichtigten Zustand.
- [ ] Hintergrundjobs/Queues laufen ohne neue Rückstau- oder Fehleranzeichen.
- [ ] Incident Lead bestätigt die Wiederaufnahme des Normalbetriebs und Kommunikation informiert Betroffene.

Nur zutreffende Kernabläufe prüfen; bei sicherheitskritischen Maßnahmen keine echte Sanktion oder riskante Discord-Aktion als Test auslösen.

## 9. Abschluss und Nachbereitung

Im Incident-Protokoll dokumentieren:

- Beginn, Erkennung, Schweregrad, Auswirkungen und Ende;
- bestätigte Ursache oder ausdrücklich „Ursache ungeklärt“;
- betroffene Daten/Komponenten und Zeitraum;
- Maßnahmen, Zeitpunkte, Ausführende und Freigaben;
- Backup-/Restore-Referenzen und verifizierte Prüfergebnisse (keine geheimen Inhalte);
- Kommunikation und noch offene Risiken;
- Folgeaufgaben mit Verantwortlichkeit und Priorität.

Nach P1/P2 wird eine kurze Nachbesprechung durchgeführt: Was hat funktioniert? Wo fehlten Diagnose, Alarmierung, Dokumentation oder Tests? Welche Regressionstests, Monitoring-Regeln oder Runbook-Korrekturen sind nötig? Auditstatus erst nach Code-/Testnachweis ändern. Deploymentstatus separat prüfen; ein GitHub-Merge beweist nicht, dass der VPS bereits aktualisiert ist.

## 10. Maßgebliche technische Referenzen

- VPS_DEPLOYMENT.md: Aufbau, Datenverzeichnis, Deploy-/Rollback- und Healthcheck-Verfahren.
- scripts/pulse-vps-boot-update.sh: tatsächliche Boot-Update-Schutzlogik; nicht durch spontane manuelle Befehle umgehen.
- pulse_system_checks.py: lesende System-, Rollen-, Umgebungs- und Datenbankdiagnosen.
- pulse_backup.py und tests/test_full_backup.py: Backup, Integritätsprüfung, Restore-Vorschau, isolierte und fehlerbehaftete Restore-Tests.
- docs/BACKUP_RECOVERY_AND_IP_AUDIT.md: Recovery-Opt-in, Tokenverschlüsselung und Grenzen.
- docs/CLAUDE_CODE_HANDOFF.md: historische Architekturhinweise; bei Abweichungen gelten aktueller Code und aktuelle Betriebsdokumentation.

**Wichtig:** Vor einem realen Incident müssen Kontaktwege, Erreichbarkeit und konkrete Freigabepersonen außerhalb dieses Repositories verfügbar sein. Dieses Dokument enthält absichtlich keine privaten Telefonnummern, Passwörter oder Zugangsdaten.
