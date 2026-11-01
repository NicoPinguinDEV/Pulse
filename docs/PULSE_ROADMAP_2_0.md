# Pulse TeamOS – Roadmap 2.0

**Strategische Entwicklungsplanung für Discord-Bot und Web-Dashboard · © BOCHUM RP | V1**  
**Stand:** 01.11.2026  
**Verbindliche Ausgangsbasis:** main @ 690b14bbca79f694c103a5d8d4f0a19e450f84df (Merge von PR #112)  
**Produktionsumgebung:** Ubuntu 24.04 · 2 vCores · 2 GB RAM · 50 GB SSD

> Diese Roadmap beschreibt die langfristige Richtung und die Reihenfolge der nächsten Entwicklungsblöcke. Sie ersetzt nicht die technischen Einzelnachweise in den Phasenberichten und nicht die verbindlichen Sicherheitsregeln aus CLAUDE.md. Der detaillierte Alt-Aufgabenbestand bleibt in VERBESSERUNGSPLAN.md; vorhandene Funktionen werden vor jeder Umsetzung im aktuellen Code geprüft.

---

## 1. Zielbild

Pulse soll ein vollständiges, verlässliches TeamOS werden, das die Verwaltung von BOCHUM RP zentral zusammenführt:

- **Discord-Bot:** Interaktionen, Moderation, Tickets, Voice-Support, Recruiting und Teamprozesse.
- **Web-Dashboard:** die zentrale Arbeitsoberfläche für Teammitglieder, Führungskräfte und Administration.
- **Daten- und Sicherheitsplattform:** nachvollziehbare Aktionen, robuste Speicherung, sichere Rechte und geprüfte Backups.
- **Command Center:** verständliche Live-Übersicht über Team, Support, Systemzustand und wichtige Ereignisse.
- **Automatisierung:** wiederkehrende Abläufe vereinfachen, ohne unkontrollierte oder nicht nachvollziehbare Aktionen.
- **Analytics:** belastbare Auswertungen für Führungskräfte, ohne unnötige personenbezogene Daten offenzulegen.

Erfolg bedeutet nicht möglichst viele Menüpunkte oder Commands. Erfolg bedeutet, dass jede Funktion einen klaren Zweck hat, sicher bedienbar ist, zuverlässig funktioniert und keine bestehenden Daten oder Abläufe beschädigt.

## 2. Entwicklungsgrundsätze

1. **Bestehendes zuerst verstehen.** Vor jeder Funktion prüfen wir, ob sie bereits existiert, teilweise existiert oder durch einen anderen Handler aktiv bereitgestellt wird.
2. **Kein Funktionsverlust.** URLs, HTTP-Methoden, Slash-Commands, custom_id-Werte, Discord-IDs, JSON-Schlüssel und Datenbankspalten bleiben kompatibel. Änderungen brauchen Übergänge und Regressionstests.
3. **Kleine, abgeschlossene Phasen.** Ein Branch und ein PR pro zusammenhängender Phase. Keine Big-Bang-Rewrites und keine Massenformatierung.
4. **Test-first bei Fehlern.** Fehler möglichst zuerst reproduzieren und mit einem Regressionstest festhalten, danach gezielt beheben.
5. **Sicherheit vor Komfort.** Serverrechte werden serverseitig geprüft. Unbekannte Rechte werden verweigert. Kritische Aktionen benötigen eine klare Bestätigung und ein Audit-Ereignis.
6. **Keine stillen KI-Aktionen.** KI darf unterstützen und Fälle vorbereiten, aber nicht allein Sanktionen verhängen. Bei fehlender KI-API muss Pulse weiterarbeiten.
7. **Keine Pflichtkosten.** Es werden keine verpflichtenden kostenpflichtigen KI- oder Drittanbieter-APIs eingeführt.
8. **Begrenzte Ressourcen.** Container-Zielbudget: höchstens 768 MiB RAM plus bis zu 256 MiB Swap. Interne Schwellen: 500 / 620 / 700 MB. Caches und Queues müssen begrenzt sein; vorhandene Scheduler und Task-Verwaltung werden wiederverwendet.
9. **Datenmigrationen sind eigene Vorhaben.** Vorher Backup, Schema-/Formatprüfung, idempotenter Import, Vergleich, Rückweg und dokumentierter Betriebsablauf.
10. **Keine Produktionsänderung ohne Auftrag.** Kein VPS-Deployment, keine Änderung an Live-Rollen oder echten Discord-Daten und keine Umschaltung von Datenbank-Backends ohne ausdrückliche Freigabe.
11. **Merge und Release bleiben getrennt.** Ein grüner PR ist noch keine Produktionsfreigabe. Vor Merge/Deployment wird der tatsächlich geprüfte Commit kontrolliert.

## 3. Verifizierter Ausgangsstand

### 3.1 Zuletzt abgeschlossene Arbeit

| Bereich | Nachgewiesener Stand |
|---|---|
| Phase 9 – Start, Import und Lifecycle | Import-Seiteneffekte sowie zentrale Shutdown-/Task-Probleme wurden bearbeitet; Bericht: PHASE9_BERICHT.md. |
| Phase 10 – Performance | N+1-Abfragen, blockierende Statistikpfade und Messwerkzeuge wurden bearbeitet; Bericht: PHASE10_BERICHT.md. Messungen stammen teilweise von einem Windows-Entwicklungsrechner und sind nicht als VPS-Messwerte zu verstehen. |
| Phase 11 – Dashboard | Ein Dashboard-Redesign wurde dokumentiert; weitere Vereinheitlichung und Navigation bleiben gesondert zu planen. |
| Phase 14.1 | Explizite Shell-Hooks statt drei Laufzeit-Monkeypatches. |
| Phase 14.4a / 14.4b | Nachweislich verdrängte Legacy-Routen und tote Handler wurden teilweise entfernt; aktive Routen blieben erhalten. |
| Phase 14.5a / 14.5b | Fehlerbeobachtung für Housekeeping und zentrale Verwaltung einmaliger Hintergrundtasks. |
| Phase 14.6 | Wiederkehrende Hintergrundjobs sollen nach nicht-netzwerkbedingten Fehlern nicht dauerhaft ausfallen. |
| Phase 14.7 / 14.7b | Schichthistorie kann verlustgeprüft in SQLite gespeichert werden; Crash-Recovery und Rückweg sind umgesetzt. |
| Phase 14.8 | Indexierte Schichthistorien-Abfragen und Regressionstests sind über PR #111 und PR #112 in main. PR #112 ist Teil des Ausgangs-HEADs dieser Roadmap. |
| Phase 15.0 | Read-only-Inventur von `logs.json` ist in PR #114 dokumentiert; der Melonly-Lesezugriff wurde mit PR #119 abgesichert. Das unabhängige Review des Inventurberichts bleibt als Nachweis offen. |

### 3.2 Wichtige offene Punkte aus dem bestehenden Plan

Diese Punkte sind nicht als erledigt zu behandeln:

- **D-07 / A-05 – wachsende Datenbestände:** logs.json und applications.json sind noch nicht vollständig migriert. Die Schichthistorie ist im Code vorbereitet, aber die produktive Backend-Umschaltung wurde nicht ausgeführt.
- **D-09 – Zeitstempel:** Altformate werden tolerant gelesen; neue Schreibstellen sind noch nicht vollständig vereinheitlicht.
- **P-10 – Logging:** zentraler Logger und Fehler-Ringpuffer existieren. Die schrittweise Umstellung alter print-Aufrufe und stiller Fehlerbehandlung bleibt offen.
- **A-01b – Handler-Architektur:** nicht alle alten remove_and_add-Ersetzungen sind abgebaut. Frühere Inventare müssen vor dem nächsten Paket gegen den aktuellen Code geprüft werden.
- **A-02 – webserver.py:** die Zerlegung und die schrittweise Ablösung der großen Sammelaktion POST /action bleiben offen.
- **A-06 – Template-Autoescape:** für neue oder grundlegend überarbeitete Seiten vorgesehen, keine Komplettumstellung.
- **U-01 – Navigation:** doppelte Funktionsseiten und alte/neue Routen müssen anhand der bestehenden Entscheidungsvorlage SEITEN_ALT_NEU.md geordnet werden. Die Entscheidung E-6 verlangt zuerst eine Vorlage alt → neu.
- **Dokumentation:** README, CHANGELOG, Modulübersichten und Versionsangaben werden fortlaufend gegen den echten Stand geprüft.

### 3.3 Vorhandene Funktionen, die nicht neu gebaut werden sollen

Pulse enthält bereits umfangreiche Bereiche wie Verifikation, Willkommen/Abschied, AutoMod, Anti-Nuke, Teamwarnungen, Schichtverwaltung, Activity Check, Bewerbungen, Tickets, Voice-Support, Backups, Wiederherstellung, Serverkontrolle, Job-Monitoring und Dashboard-Rechte.

Die Funktionslandkarte in PULSE_INNOVATION_ROADMAP.md und die aktuellen Tests dienen als Ausgangspunkt. Ein Ausbau bedeutet zunächst Lücken schließen und Bedienung/Integration verbessern – nicht vorhandene Systeme ein zweites Mal danebenstellen.

### 3.4 Noch zu bestätigende Betriebs-/Repository-Einstellungen

Der aktuelle GitHub-Branch-Endpunkt meldet `main` am 02.11.2026 als **nicht geschützt** (`protected: false`, Branch-Protection deaktiviert). Reviewer für die Deploy-Umgebung und Dependabot-Sicherheitsupdates sind in diesem Arbeitsschritt nicht erneut verifiziert worden und bleiben separat zu prüfen.

---

## 4. Priorisierte Roadmap

Die Nummerierung beginnt nach Phase 14.8. Phasenstatus werden erst dann auf erledigt gesetzt, wenn Umsetzung, Review und die geforderten Prüfungen abgeschlossen sind. Ein angelegter Branch oder offener PR zählt nicht als abgeschlossen.

### Phase 15 – Datenhaltung, Datenintegrität und Wachstum

**Priorität:** P0 · **Status:** in Arbeit (Phase 15.1 – Speichervertrag) · **Abhängigkeit:** keine

**Ziel:** Wachsende Dateien sicher behandeln und Datenmigrationen nachvollziehbar, messbar und rückgängig machbar durchführen.

| ID | Arbeitspaket | Ergebnis / Abnahme |
|---|---|---|
| 15.0 | Read-only-Inventur von logs.json | Alle aktuellen Leser, Schreiber, Datenformen, Aufbewahrungsregeln, Locks und Backup-Pfade sind im Code verifiziert. Die alte Datenlandkarte wird bei Abweichungen korrigiert. |
| 15.1 | logs.json: Speicher- und Abfragekonzept | Entwurf für Schema, Repository-Vertrag, Transaktionsgrenzen, Migration, Rollback, Backup und Regressionstests: [PHASE15_1_LOGS_STORAGE_DESIGN.md](PHASE15_1_LOGS_STORAGE_DESIGN.md). Abnahme und Review stehen noch aus. |
| 15.2 | logs.json: inkrementelle SQLite-Umsetzung | Indexe passen zu echten Abfragen; Dashboard-/Melonly-Ausgaben bleiben fachlich identisch. JSON-Kompatibilität und Rückweg sind getestet. |
| 15.3 | applications.json: Datenschutz- und Migrationsprüfung | Alle Verbraucher und Schreibpfade sind bekannt. Die beschlossene Aufbewahrung von sechs Monaten und der noch offene verantwortliche Kontakt für den Bewerbungshinweis werden berücksichtigt. |
| 15.4 | applications.json: Migration in einer eigenen Teilphase | Daten, Status, Anhänge/Verweise und Altformate bleiben erhalten. Aufbewahrung ist nachvollziehbar, protokolliert und standardmäßig nicht überraschend aktiviert. |
| 15.5 | Backup-/Restore-Probelauf | Vorschau des Restore-Diffs, Sicherheitskopie, Validierung und Wiederherstellung werden mit synthetischen Daten und isolierten Pfaden getestet. |
| 15.6 | Weitere JSON-/SQLite-Dateien bewerten | Kleine Datenbanken werden einzeln bewertet. Eine Zusammenführung erfolgt nur, wenn Eigentümerschaft, Tabellenkollisionen, Backup und Rückweg geklärt sind. |

**Separater Betriebsschritt – Schichthistorie aktivieren:** Die Umschaltung von PULSE_SHIFT_HISTORY_BACKEND auf sqlite ist kein automatischer Teil dieser Codephase. Erst nach geprüftem Backup, dokumentiertem Status und ausdrücklicher Freigabe darf sie auf dem VPS erfolgen. Der Rückweg auf JSON muss genauso dokumentiert bleiben.

**Abnahmekriterien für Phase 15**
- Vorher-/Nachher-Vergleich bestätigt Anzahl, Reihenfolge, Inhalte und fachliche Aggregationen.
- Tests für leere, große, alte, ungültige und unvollständige Daten sowie doppelte und fehlende optionale Felder.
- Tests für gleichzeitige Schreibvorgänge, Prozessabbruch, Wiederholung, Backup, Restore und Rollback.
- Index- und Abfragepläne belegen, dass die vorgesehenen Filter tatsächlich verwendet werden.
- Benchmark mit mindestens 20.000 synthetischen Einträgen; bei Bedarf zusätzlich 100.000. Ergebnisse und Gleichheit der Resultate werden dokumentiert.
- Keine Laufzeitdaten des VPS werden für Tests verwendet oder verändert.

### Phase 16 – Architektur und Wartbarkeit

**Priorität:** P1 · **Status:** geplant · **Abhängigkeit:** Phase 15 nur dort, wo Persistenz-Schnittstellen betroffen sind

**Ziel:** Pulse intern modularer machen, ohne die sichtbaren Schnittstellen zu brechen.

| ID | Arbeitspaket | Ergebnis / Abnahme |
|---|---|---|
| 16.0 | Aktuelle Architektur-Baseline | Routen, Handler-Ersetzungen, Importe, Persistenzzugriffe und Modulabhängigkeiten werden neu erfasst. Alte Zahlen aus früheren Berichten gelten nicht ungeprüft weiter. |
| 16.1 | Handler-Registrierung schrittweise vereinheitlichen | remove_and_add-Pakete werden einzeln abgebaut. Es werden nur nachweislich tote Handler entfernt; die Entscheidung E-6 wird vor jeder Seitenzusammenführung eingeholt. |
| 16.2 | webserver.py modularisieren | Auth/Session, Persistenz, UI-Helfer und Fachrouten werden schrittweise ausgelagert. Jede Teiländerung hat einen unveränderten Routen- und Rechte-Snapshot. |
| 16.3 | POST /action sicher aufteilen | Neue fachliche Endpunkte werden eingeführt; bestehende Formulare und der alte Endpunkt delegieren während einer Übergangszeit kompatibel. |
| 16.4 | Zeitstempel-Schreibformate vereinheitlichen | Neue Daten werden in einem festgelegten Format geschrieben. Altformate bleiben lesbar; Migration und Anzeige sind getrennt getestet. |
| 16.5 | Logging modulweise vervollständigen | print und stille except/pass-Stellen werden nur in kleinen, getesteten Bereichen ersetzt. Keine Tokens, Secrets oder unnötigen Nachrichteninhalte protokollieren. |
| 16.6 | Templates sicher weiterentwickeln | Neue und wesentlich überarbeitete Seiten verwenden Autoescape. Bestehende Seiten werden nicht in einem Big-Bang umgebaut. |

**Abnahmekriterien für Phase 16**
- Routen-Snapshot: gleiche beabsichtigte Methoden, Pfade, Statuscodes und Zugriffskontrollen.
- Kein zusätzlicher Import-Seiteneffekt, keine doppelten Hintergrundjobs und sauberer Shutdown.
- Regressionstests prüfen alte Formulare, custom_id-Werte und Datenformate.
- Geänderte Modulbereiche bestehen gezielte Tests sowie den vollen CI-Satz, sobald mehrere Module oder gemeinsame Infrastruktur betroffen sind.
- Der Diff enthält keine unverbundenen Umformatierungen oder pauschalen Löschungen.

### Phase 17 – Sicherheit, Rechte und Administration

**Priorität:** P0/P1 · **Status:** geplant · **Abhängigkeit:** laufend; vor neuen Admin-Funktionen

**Ziel:** Alle Verwaltungsaktionen nachvollziehbar, serverseitig autorisiert und kontrollierbar machen.

| ID | Arbeitspaket | Ergebnis / Abnahme |
|---|---|---|
| 17.0 | Aktueller Rechte-Audit | Aktive Routen und Discord-Aktionen werden gegen die aktuelle Rollen-/Rechtematrix geprüft. Alte Befunde werden reproduziert oder ausdrücklich geschlossen. |
| 17.1 | Einheitliche Rechte-Simulation | Eine rein lesende Vorschau zeigt, was eine ausgewählte Rolle bzw. Person im Dashboard sehen und ausführen dürfte. Die Simulation ersetzt keine echte Serverautorisierung. |
| 17.2 | Teamakte 2.0 | Warnungen, interne Notizen, Anhänge und Historie folgen den bereits getroffenen Datenschutz- und Rechteentscheidungen. Jede Einsicht und Änderung ist angemessen protokolliert. |
| 17.3 | Aktionsprotokoll vervollständigen | Personal-, Moderations-, Rollen-, Ticket- und Adminaktionen besitzen einheitliche Akteurs-, Ziel-, Grund-, Zeit- und Ergebnisfelder, soweit fachlich verfügbar. |
| 17.4 | Sicherheits-/Incident-Center verbessern | Vorfälle, Anti-Nuke-Ereignisse, AutoMod-Meldungen, fehlgeschlagene Aktionen und Wiederherstellungszustände werden verständlich dargestellt. |
| 17.5 | Anti-Nuke und Moderation bedienbar machen | Schwellen, Ausnahmen und Alarmzustände sind erklärbar und testbar. Schutzfunktionen bleiben standardmäßig deaktiviert, bis ein Administrator sie bewusst aktiviert. |

**Abnahmekriterien für Phase 17**
- Jede schreibende Route und jede administrative Discord-Aktion hat einen Verhaltenstest für erlaubt und verweigert.
- Keine Änderung vor erfolgreicher serverseitiger Autorisierung.
- Nutzer können keine fremden Teamakten, Anhänge, Exporte oder Protokolle über IDs erraten oder abrufen.
- Kritische Aktionen sind idempotent, gegen Doppelklick/Rennen geschützt und mit Ergebnis protokolliert.
- Keine automatische Sanktion allein aufgrund einer KI-Einschätzung.

### Phase 18 – Dashboard 2.0 und Bedienung

**Priorität:** P1 · **Status:** geplant · **Abhängigkeit:** aktive Routen und Rechte aus Phase 16/17

**Ziel:** Eine einheitliche, moderne und schnelle Bedienoberfläche ohne doppelte oder schwer auffindbare Funktionen.

| ID | Arbeitspaket | Ergebnis / Abnahme |
|---|---|---|
| 18.0 | Informationsarchitektur festlegen | Die Tabelle alt → neu aus SEITEN_ALT_NEU.md wird aktualisiert und zur Entscheidung vorgelegt. Keine Seite wird ohne Freigabe zusammengelegt oder entfernt. |
| 18.1 | Navigation vereinheitlichen | Klare Hauptbereiche, rollenabhängige Einträge und eine eindeutige kanonische Seite je Funktion. Alte URLs erhalten bei Bedarf kompatible Weiterleitungen. |
| 18.2 | Globale Suche | Suche über freigegebene Team-, Ticket-, Bewerbungs-, Wiki- und Protokolldaten; Treffer werden nach tatsächlichen Leserechten gefiltert. |
| 18.3 | Command Center | Eine konfigurierbare Startseite mit persönlichen Aufgaben, Teamstatus, offenen Tickets, wichtigen Meldungen und Systemzustand. |
| 18.4 | Designsystem vereinheitlichen | Einheitliche Komponenten, Abstände, Farben, Formulare, Tabellen, Dialoge und Statusanzeigen; bestehende Dashboard-Designvorgaben werden berücksichtigt. |
| 18.5 | Mobil- und Fehlerzustände | Desktop und Mobilgerät, Lade-, Leer-, Fehler- und Erfolgszustände, Tastaturbedienung und Scrollposition werden geprüft. |

**Abnahmekriterien für Phase 18**
- Kein Funktionsverlust bei Navigation oder Weiterleitungen; Route-Snapshot und Berechtigungstests bleiben grün.
- Suche gibt keine unberechtigten Daten preis und besitzt begrenzte Abfragekosten.
- Visuelle Prüfung auf Desktop und Mobilgerät mit versionierten E2E-Tests für zentrale Abläufe.
- Keine neue Seite ohne definierten Zweck, Verantwortlichkeit und Rechte.

### Phase 19 – Discord-Bot und Teamprozesse 2.0

**Priorität:** P1 · **Status:** geplant · **Abhängigkeit:** Phase 17 für Rechte und Audit

**Ziel:** Bot und Dashboard arbeiten als ein zusammenhängendes System statt als getrennte Funktionssammlungen.

| ID | Arbeitspaket | Ergebnis / Abnahme |
|---|---|---|
| 19.0 | Bestehende Bot-Funktionen inventarisieren | Slash-Commands, Panels, Cogs und aktive Listener werden mit der aktuellen Funktionslandkarte abgeglichen. |
| 19.1 | Team-Onboarding und Offboarding | Verifikation, Bewerbung, Einstellung, Rollenwechsel und Team-Austritt folgen klaren, getesteten Abläufen. Team-Kick entfernt Teamrollen, aber kickt nicht vom Discord-Server. |
| 19.2 | Ticket-Workflow verbessern | Übergabe mit Pflichtnotiz, Zuständigkeit, nächstem Schritt und nachvollziehbarem Abschluss. |
| 19.3 | Schnellantworten und Ticket-Triage | Freigegebene Schnellantworten je Kategorie, Filter, Priorität und Zuständigkeit ohne doppelte Ticketsysteme. |
| 19.4 | Slash-Commands und Hilfesystem | Commands werden verständlich gruppiert, Eingaben validiert und Berechtigungen vor Ausführung geprüft. Bestehende Namen bleiben als vereinbarte Aliase erhalten. |
| 19.5 | Voice-Support und Benachrichtigungen | Warteschlange, Bereitschaft, Übergabe und Statusmeldungen werden verbessert, ohne bei Beitritt neue Kanäle zu erstellen oder Nutzer unerwartet zu verschieben. |

**Abnahmekriterien für Phase 19**
- Discord-Tests verwenden Fakes und lösen keine echten Serveraktionen aus.
- 403/404/429, Timeouts, fehlende Rollen/Kanäle und Bot-Rollenhierarchie werden berücksichtigt.
- Doppelte Interaktionen erzeugen keine doppelten Teamaktionen, Tickets oder Protokolle.
- Alle sicherheitsrelevanten Änderungen besitzen einen Audit-Eintrag.

### Phase 20 – Automatisierung und KI-Unterstützung

**Priorität:** P2 · **Status:** geplant · **Abhängigkeit:** Phase 17 und die stabilen Team-/Ticket-Workflows aus Phase 19

**Ziel:** Wiederkehrende Aufgaben automatisieren, ohne unkontrollierbare Hintergrundlogik aufzubauen.

| ID | Arbeitspaket | Ergebnis / Abnahme |
|---|---|---|
| 20.0 | Automationsinventur | Vorhandene Ultimate-Automation, Housekeeping und Scheduler werden bewertet; keine parallele zweite Engine. |
| 20.1 | Workflow-Regeln | Ereignis, Bedingung, Aktion, Ausnahmen, Cooldown und Verantwortlichkeit sind klar definiert. |
| 20.2 | Vorschau und Testmodus | Workflows können vor Aktivierung simuliert werden. Neue Regeln sind standardmäßig aus und werden erst nach bewusster Freigabe aktiv. |
| 20.3 | Ausführungsschutz | Idempotenz, Deduplizierung, Rate-Limits, begrenzte Queues, Abbruch und Audit sind vorhanden. Vorhandene Task-/Job-Verwaltung wird wiederverwendet. |
| 20.4 | KI-Moderationsunterstützung 2.0 | KI kann Inhalte priorisieren, zusammenfassen und Fälle vorbereiten. Eine menschliche Prüfung bleibt bei Sanktionen erforderlich; bei API-Ausfall bleibt die übrige Moderation funktionsfähig. |
| 20.5 | Team-Erinnerungen und Routineabläufe | Abwesenheiten, Activity Checks, Probezeiten, Schulungen und Fristen können anhand klarer Regeln erinnert werden; Ruhezeiten und Deduplizierung verhindern Spam. |

**Abnahmekriterien für Phase 20**
- Keine neue Endlosschleife oder ungebundene Task-Erzeugung.
- Jede Automation lässt sich deaktivieren, nachvollziehen und sicher erneut ausführen.
- KI-Ausfall, leere Ergebnisse, ungültige Antworten und Rate-Limits führen zu einem kontrollierten Fallback.
- Keine kostenpflichtige KI-Abhängigkeit und keine automatische Sanktion allein durch KI.

### Phase 21 – Analytics, Berichte und Führungskräfte-Cockpit

**Priorität:** P2 · **Status:** geplant · **Abhängigkeit:** stabile Datenquellen und Rechte aus Phase 15/17

**Ziel:** Führungskräfte bekommen belastbare Informationen, ohne mehrfach dieselben Daten teuer oder inkonsistent zu berechnen.

| ID | Arbeitspaket | Ergebnis / Abnahme |
|---|---|---|
| 21.0 | Kennzahlenkatalog | Jede Kennzahl hat Definition, Zeitraum, Datenquelle, Berechtigung und Umgang mit fehlenden Daten. |
| 21.1 | Team- und Personalentwicklung | Schichtzeiten, Aktivität, Beförderungen, Probezeiten und Warnverläufe werden korrekt und rollenbasiert ausgewertet. |
| 21.2 | Ticket- und Supportanalysen | Volumen, Wartezeiten, Zuständigkeiten, Übergaben und Abschlussquoten werden nachvollziehbar dargestellt. |
| 21.3 | Service- und Systemmetriken | Job-Fehler, API-Ausfälle, Queue-Auslastung, Speicher und Backup-Zustand ergänzen die bestehenden Health-Informationen. |
| 21.4 | Exporte und Reports | CSV/JSON-Exporte verwenden dieselben geprüften Berechnungen und Rechte wie die Dashboard-Ansichten. |
| 21.5 | Trends und Ziele | Wochen-/Monatsvergleiche und Ziele werden nur auf Basis dokumentierter Kennzahlen berechnet. |

**Abnahmekriterien für Phase 21**
- Backend-Abfragen vermeiden N+1-Muster und werden mit realistischen synthetischen Daten gemessen.
- Dashboard, API und Export liefern für dieselben Filter dieselben fachlichen Werte.
- Zeiträume verwenden konsistente Zeitzonen und klar dokumentierte Grenzen.
- Personenbezogene Kennzahlen sind nur für dafür berechtigte Rollen sichtbar.

### Phase 22 – Betrieb, Qualitätssicherung und Releases

**Priorität:** P1 · **Status:** geplant · **Abhängigkeit:** laufend, mit jeder Phase mitzupflegen

**Ziel:** Jede freigegebene Version ist reproduzierbar geprüft, sicher ausrollbar und wiederherstellbar.

| ID | Arbeitspaket | Ergebnis / Abnahme |
|---|---|---|
| 22.0 | Repository-Schutz prüfen | Branch-Schutz, erforderliche Checks, Review-Regeln, Deploy-Environment und Dependabot-Einstellungen mit dem Repository-Inhaber prüfen und dokumentieren. |
| 22.1 | CI-Gates pflegen | Python 3.14/3.15, Ruff, compileall, Smokes, Unittests, Sicherheitsprüfung, Docker-Build und Compose-Integration bleiben nachvollziehbar. |
| 22.2 | E2E-Qualität | Playwright prüft kritische Login-, Team-, Ticket-, Bewerbungs- und Admin-Abläufe ohne echte Produktionsdaten. |
| 22.3 | Backup und Recovery üben | Nicht nur Backup-Erstellung, sondern regelmäßige isolierte Wiederherstellung und Integritätsprüfung. |
| 22.4 | Monitoring und Ressourcen | Health, Jobs, Container-RAM, CPU, Speicherplatz, Datenbankzustand und Backup-Alter werden verständlich bewertet. |
| 22.5 | Release-Dokumentation | CHANGELOG, Versionsnummer, Phasenbericht und Betriebshinweise spiegeln den tatsächlich gemergten Stand wider. |

**Abnahmekriterien für Phase 22**
- Nur der tatsächlich getestete Commit darf als Release-/Deploy-Ziel dienen.
- Deployment ist separat freigegeben; ein CI-Erfolg löst nicht automatisch eine manuelle Produktionsfreigabe aus.
- Vor Deployment sind Sicherung, Healthcheck, Rollback und Nachkontrolle dokumentiert.
- Versionsnummern werden erst nach Freigabe geändert.

---

## 5. Reihenfolge und Abhängigkeiten

| Reihenfolge | Phase | Warum jetzt? | Voraussetzung für Folgendes |
|---|---|---|---|
| 1 | 15 – Datenhaltung | Wachstum und Datenintegrität sind technische Grundlage für alle späteren Auswertungen. | stabile Datenzugriffe und geprüfte Restore-Pfade |
| 2 | 16 – Architektur | Verhindert, dass neue Funktionen weiter auf schwer wartbaren Doppelstrukturen aufbauen. | klare aktive Handler, Persistenz- und UI-Grenzen |
| 3 | 17 – Sicherheit | Rechte, Datenschutz und Audit müssen vor zusätzlichen Admin-Funktionen belastbar sein. | geprüfte Autorisierung und Aktionsprotokolle |
| 4 | 18 – Dashboard | Navigation und Command Center bauen auf stabilen Routen und Rechten auf. | freigegebene Informationsarchitektur |
| 5 | 19 – Discord-Bot | Bot-Abläufe werden mit dem Dashboard vereinheitlicht. | sichere Rechte-/Audit-Basis |
| 6 | 20 – Automatisierung | Automationen benötigen stabile Prozesse und Abschalt-/Simulationsmechanismen. | Bot-Workflows und Sicherheitskontrollen |
| 7 | 21 – Analytics | Kennzahlen hängen von stabilen und konsistenten Datenquellen ab. | Phase 15 und Rechte aus Phase 17 |
| 8 | 22 – Betrieb/Releases | Qualität und Betrieb laufen parallel; der Reifegrad wird mit jeder Phase erweitert. | ab Tag 1 laufende CI-/Backup-/Monitoring-Pflege |

Sicherheitsfixes mit nachgewiesener hoher Schwere werden unabhängig von dieser Reihenfolge sofort vorgezogen. Eine Phase darf in kleine Teilphasen zerlegt werden; sie wird nicht durch eine große Sammeländerung erzwungen.

## 6. Verbindlicher Ablauf für jede Phase

1. **Baseline:** Branch, HEAD, Arbeitsbaum, relevante Dokumente und offene PRs prüfen.
2. **Ist-Analyse:** aktive Codepfade und tatsächliche Aufrufer gezielt untersuchen. Vorhandene Funktionen und frühere Fixes nicht erneut bauen.
3. **Teilplan:** konkrete Dateien, Risiken, Abhängigkeiten, Tests und Nicht-Ziele festlegen.
4. **Regression zuerst:** nach Möglichkeit einen Test schreiben, der den Fehler oder die fehlende Eigenschaft nachweist.
5. **Kleine Umsetzung:** nur fachlich zusammenhängende Änderungen; keine nebenbei vorgenommenen Refactorings.
6. **Prüfung:** gezielte Tests zuerst, danach je nach Risikoklasse kompletter CI-Satz und Review.
7. **Dokumentation:** Phasenbericht, Testnachweise, offene Risiken und Roadmap-Status aktualisieren.
8. **PR und Stopp:** PR zur Prüfung bereitstellen. Nach Abschluss der Phase stoppen und auf die nächste Freigabe warten.

### Mindestprüfungen

- Gezielte Regressionstests für jede geänderte Funktion.
- Ruff, compileall, feature_smoke und smoke_test.
- Vollständige Unittest-Suite, sobald gemeinsame Infrastruktur, mehrere Module, Rechte, Daten oder Lifecycle betroffen sind.
- Python 3.14 und Produktions-Python-Version 3.15 in CI.
- Docker-Build und Docker-Compose-Integration bei relevanten Änderungen.
- Playwright für zentrale Dashboard-Flows, wenn UI oder Navigation betroffen ist.
- Security-Scans bei Änderungen an Abhängigkeiten, Authentifizierung, Uploads, Datenzugriff oder Deploy.
- Performance- und Datenäquivalenzmessung bei Datenbank-/Abfrageänderungen.

Nicht lokal ausführbare Prüfungen werden nicht als bestanden ausgegeben; die zuständigen CI-Ergebnisse müssen im PR sichtbar sein.

## 7. Definition of Done

Eine Phase gilt erst als abgeschlossen, wenn alle zutreffenden Punkte erfüllt sind:

- [ ] Das Ziel und die Nicht-Ziele sind dokumentiert.
- [ ] Der aktive Codepfad wurde vor der Änderung verifiziert.
- [ ] Regressionstests decken Erfolg, Ablehnung, Fehlerfälle und relevante Grenzfälle ab.
- [ ] Daten, Berechtigungen, URLs, Commands und Interaktions-IDs bleiben kompatibel oder besitzen einen getesteten Übergang.
- [ ] Gezielte Tests und der erforderliche CI-Satz sind grün.
- [ ] Der Diff wurde auf Funktionsverlust, Secrets, Laufzeitdaten und unverbundene Änderungen geprüft.
- [ ] Ein unabhängiges Review hat Korrektheit, Berechtigungen, Datenintegrität und Testabdeckung geprüft.
- [ ] Bericht und Roadmap nennen Commit/PR, echte Testergebnisse und verbleibende Risiken.
- [ ] Keine Produktionsmigration und kein Deployment ohne gesonderte Freigabe.

## 8. Offene Entscheidungen / Freigabepunkte

| Thema | Aktueller Stand | Benötigter nächster Schritt |
|---|---|---|
| Schichthistorie auf SQLite umschalten | Code und Rückweg vorhanden; produktiv nicht aktiviert. | separater, ausdrücklich freigegebener VPS-Betriebsschritt |
| Navigation / doppelte Seiten (E-6) | Alt-neu-Entscheidungsvorlage existiert. | aktualisierte Zuordnung vor dem Zusammenführen vorlegen |
| Bewerbungsdatenschutz | Aufbewahrung 6 Monate beschlossen; verantwortlicher Kontakt für den Hinweis ist offen. | Kontakt vor Änderung des Bewerbungshinweises festlegen |
| Repository-Schutz | Audit vom 24.10. meldete fehlenden Branch-Schutz/Deploy-Reviewer; aktueller Status nicht erneut verifiziert. | Repository-Einstellungen mit Inhaber prüfen |
| Neue Automationen / Sanktionen | sichere Defaults und menschliche Prüfung sind verbindlich. | jede neue Aktion einzeln beschreiben und ausdrücklich freigeben |

## 9. Startpunkt nach Annahme der Roadmap

Der nächste technische Schritt ist **Phase 15.0: eine rein lesende, aktuelle Inventur von logs.json**. Sie liefert:

- alle aktiven Leser und Schreiber, nicht nur ältere Dokumentationsstellen;
- Datenformate, optionale Felder, Zeitstempel und Sortierregeln;
- alle Dashboard-, Bot-, Export-, Backup- und Retention-Aufrufer;
- Locking, parallele Schreibvorgänge und Fehlerverhalten;
- reale Abfrageprofile und passende Indexkandidaten;
- notwendige Tests und einen sicheren Migrations-/Rollback-Vorschlag.

Erst nach dieser Inventur wird entschieden, welche Implementierung wirklich erforderlich ist. Phase 15.0 verändert keine Daten, keine Routen und keinen Produktivbetrieb.

---

**Leitlinie:** Pulse wird nicht durch möglichst viele parallele Baustellen professionell, sondern durch eine klare Reihenfolge, sichere Daten, verständliche Bedienung, konsequente Tests und kontrollierte Releases.
