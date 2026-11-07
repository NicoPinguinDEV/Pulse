# BOCHUM RP / Pulse – Compliance-Baseline

**Stand:** 7. November 2026  
**Repository-Basis:** `main` @ `64907a3a280366c9453827c81963b8fdfdc04c29` (zum Zeitpunkt dieser Baseline)  
**Betreiberstruktur:** natürliche Person / Privatbetreiber (vom Betreiber im Gespräch bestätigt)  
**Freigabestatus:** NICHT RECHTLICH FREIGEGEBEN

> Diese Datei ist eine technische und organisatorische Arbeitsgrundlage, keine Rechtsberatung und kein Rechtsgutachten. Sie bestätigt weder die Rechtmäßigkeit einzelner Verarbeitungen noch eine anwaltliche Prüfung. Tatsächliche Betreiberangaben, Verträge, Konfigurationen und Live-Datenflüsse müssen separat belegt werden.

## 1. Harte Freigabegrenze

Die öffentlichen Rechtsseiten dürfen nicht als final freigegeben behandelt werden, solange die tatsächlichen Betreiberangaben, Datenflüsse, Rechtsgrundlagen, Empfänger, Verträge und Speicher-/Löschprozesse nicht geprüft wurden.

- Keine erfundenen Namen, Anschriften, Rechtsformen, Behörden, Vertragsbeziehungen oder Transfermechanismen veröffentlichen.
- Keine privaten Betreiberangaben in Git, Issues, Pull Requests, Commits oder öffentlichen Screenshots speichern. Sie gehören ausschließlich in die geschützte Serverkonfiguration.
- `PULSE_LEGAL_REVIEWED`, `PULSE_TERMS_APPROVED` und das Gültigkeitsdatum nicht setzen, solange die zugehörige Prüfung tatsächlich nicht erfolgt ist.
- `noindex` und eine nicht ausgelieferte Sitemap sind keine Zugangssperre und ersetzen keine gesetzlichen Informationspflichten.
- Automatische Löschfristen nur dann als aktiv beschreiben, wenn sie technisch ausgeführt, getestet und anhand der Produktionskonfiguration bestätigt wurden.

## 2. Bestätigte technische Prüfhinweise aus dem Repository

Der folgende Stand stammt aus dem Quellcode und der technischen Dokumentation. Er ersetzt keinen Zugriff auf Produktionsdaten oder Provider-Verträge.

### 2.1 Öffentliche Teamvorstellung

Vor dieser Änderung wurden Teamprofilbilder direkt über Discord-CDN-Adressen eingebunden. Das führte beim Besuch der Teamvorstellung zu einem zusätzlichen Drittanbieterabruf im Browser.

**Umsetzung in Phase 1:** öffentliche Teamkarten verwenden lokale Initialen statt Discord-Profilbilder. Die Content-Security-Policy erlaubt keine Discord-CDN-Bildhosts mehr. Tests sollen sicherstellen, dass öffentliche Seiten keine externen `script`-, `link`- oder `img`-Ressourcen einbinden.

Die Teamvorstellung verwendet weiterhin Discord-Mitglieds-/Rollendaten und einen internen Einwilligungsnachweis. Die Offenlegungsfassung wurde auf Version 2 angehoben; alte Einwilligungen aus Version 1 reichen nicht mehr aus, sodass Mitglieder die aktualisierte Freigabe erneut erteilen müssen. Rechtsgrundlage, Art.-14-Information, Freiwilligkeit der Einwilligung und die Aufbewahrung des Nachweises bleiben gesondert zu prüfen.

### 2.2 Bewerbungen, Retention und Tickets

- In `pulse_retention.py` ist `RETENTION_MUTATIONS_ENABLED = False`.
- Die zentrale Datenbereinigung bleibt damit fail-closed. Eine konfigurierte Ziel-Frist ist kein Beleg für eine ausgeführte Löschung.
- Der Code dokumentiert Bewerbungsdaten mit Identität, Antworten, Testergebnissen, Praxisaufgaben und Bearbeitungsstatus.
- Ticket-Transkripte werden laut technischem Stand ohne automatische Löschfrist aufbewahrt.
- Für Teamdaten besteht keine einheitlich technisch durchgesetzte Löschfrist über alle Teilbestände.
- Entschiedene KI-Moderationsfälle werden laut Code nach 30 Tagen bereinigt; offene Fälle können länger bestehen.
- Backups können gelöschte Daten bis zum Ende ihrer jeweiligen Rotation enthalten. Löschkonzept und Restore-Prozess müssen zusammenpassen.

**Folgerung:** Die Retention-Sperre nicht einfach entfernen. Vor einer Aktivierung sind Dateninventar, Kategorien, Ereignisbezüge, konkrete Fristen, Ausnahmen, Backup-/Export-Verhalten, Mehrspeicher-Transaktionen, Auditierbarkeit und Wiederherstellungstests abzuschließen.

### 2.3 KI-Dienste

Die technische Dokumentation nennt Google Gemini und OpenAI als mögliche Anbieter. Je nach aktivierter Funktion können Supportnachrichten bzw. Moderationsmerkmale an den tatsächlich konfigurierten Anbieter übermittelt werden.

Vor Freigabe sind für jedes aktive Konto/Produkt/Tarif zu belegen:
- Vertragspartner und konkrete Produkt-/API-Variante;
- Anbieterrolle und ggf. Art.-28-Vertrag;
- Datenfelder und Zweck je Funktion;
- Speicher-/Löschbedingungen und Trainingsverwendung;
- Unterauftragnehmer und Verarbeitungsregionen;
- Drittlandübermittlung und konkreter Mechanismus nach Art. 44 ff. DSGVO;
- dokumentierte Rechtsgrundlage und Interessenabwägung, soweit erforderlich;
- verständliche Information der betroffenen Personen.

`/ki_vergessen` löscht keinen bereits beim Anbieter gespeicherten Inhalt und darf nicht als externe Löschbestätigung beschrieben werden.

### 2.4 Hosting, OAuth und Plattformen

Der Betreiber nutzt einen KVM-vServer über das Kunden-/Verwaltungsportal Vionity. Die Vionity-Oberfläche weist im Footer Henrik Kramer e.K. aus; die offizielle Anbieterkennzeichnung von Prepaid-Hoster.de nennt **Henrik Kramer e.K., Kurpromenade 48, 23743 Grömitz, Deutschland** als Anbieter. Vionity ist das Verwaltungsportal, nicht eine eigene Anbieteranschrift für BOCHUM RP. Im Kundenbereich des konkreten VPS wird als Standort **Offenbach am Main** angezeigt; dies ist vor endgültiger Freigabe mit der konkreten Vertrags-/Rechenzentrumsdokumentation abzugleichen.

Die Anbieter-FAQ sagt, dass eine Auftragsdatenverarbeitung auf Anfrage im Einzelfall geprüft und bei Bedarf ein Formular bereitgestellt wird; dazu seien vollständige Kundendaten erforderlich. Ein für diesen VPS abgeschlossener und unterzeichneter AVV ist aktuell **nicht nachgewiesen**. Er muss über den Support angefordert werden. Zu prüfen und zu dokumentieren sind insbesondere Anbieterrolle, technische und organisatorische Maßnahmen, Support-/Administrationszugriffe, Unterauftragnehmer, tatsächliche Betriebs- und Backup-Standorte sowie Lösch-/Rückgaberegelungen. Bis diese Unterlagen vorliegen, darf die Datenschutzerklärung keinen abgeschlossenen AVV oder bestätigte Unterauftragnehmer behaupten.

Für Discord OAuth, Roblox-Abfragen, KI-Anbieter und sonstige Integrationen sind ebenfalls tatsächliche Konten, Tarife, Rollen, Unterauftragnehmer, Regionen und Vertragsunterlagen zu prüfen. Der Domainname oder der allgemeine Markenname eines Providers belegt weder den physischen Serverstandort noch die vertragliche Verantwortlichkeit.

## 3. Betreiber- und Rechtstexte – offene Angaben

Da BOCHUM RP laut Betreiber als natürliche Person geführt wird:

- Anbietername muss der tatsächliche vollständige bürgerliche Name sein.
- Anschrift muss – soweit die anwendbare Anbieterkennzeichnung sie verlangt – tatsächlich zustellfähig sein.
- Keine Gesellschaft, kein Verein und keine Vertretungsberechtigung erfinden.
- Ob § 5 DDG zusätzlich gilt, ist anhand Geschäftsmäßigkeit und Entgeltlichkeit des konkreten Dienstes zu prüfen.
- Ob § 18 Abs. 2 MStV für tatsächliche News-/Blog-Inhalte gilt, ist anhand der redaktionellen Gestaltung zu prüfen.
- Datenschutzrechtlich Verantwortlicher und Website-Anbieter können nur dann unterschiedlich benannt werden, wenn die tatsächlichen Rollen das tragen.
- Hostinganbieter ist zunächst als Henrik Kramer e.K. (Prepaid-Hoster.de; Verwaltung über Vionity) dokumentiert; Vertragspartner, AVV, Rechenzentrum, Unterauftragnehmer und Backup-Orte bleiben anhand der Kundenunterlagen zu verifizieren.
- Kontaktadresse muss überwacht und tatsächlich erreichbar sein.
- Datenschutzbeauftragtenpflicht und zuständige Aufsichtsbehörde müssen anhand der tatsächlichen Tätigkeit und Niederlassung beurteilt werden.

**Nicht in dieser Baseline eintragen:** private Wohnanschrift, private Telefonnummern, Zugangsdaten, API-Schlüssel, OAuth-Secrets oder vollständige personenbezogene Produktionsdatensätze.

## 4. Datenschutz-Freigabecheckliste

- [ ] Dateninventar aller Website-, Discord-Bot-, Dashboard-, Bewerbungs-, Ticket-, Moderations-, Kalender-, Backup- und Analyseprozesse ist vollständig.
- [ ] Für jeden Verarbeitungsvorgang sind Zweck, Datenkategorien, betroffene Personen, Empfänger, Rechtsgrundlage und Aufbewahrung schriftlich zugeordnet.
- [ ] Art.-13- und Art.-14-Informationen werden zum richtigen Zeitpunkt und über einen wirksamen Informationsweg bereitgestellt.
- [ ] Berechtigtes Interesse ist pro Verarbeitungsvorgang dokumentiert und abgewogen.
- [ ] Einwilligungen sind informiert, freiwillig, spezifisch, nachweisbar, widerrufbar und nicht mit Teamrechten verknüpft.
- [ ] Minderjährige und gegebenenfalls erforderliche Schutz-/Zustimmungsmaßnahmen werden pro Funktion beurteilt.
- [ ] Hostingvertrag, Auftragsverarbeitung, TOMs und Unterauftragnehmer sind geprüft.
- [ ] Discord-, Roblox- und KI-Anbieterrollen sowie EWR-/Drittlandübermittlungen sind anhand der tatsächlichen Konten und Verträge geprüft.
- [ ] Cookie-, Local-Storage- und Session-Storage-Zugriffe sind einzeln nach § 25 TDDDG eingeordnet.
- [ ] Optionale Drittanbieterdienste bleiben vor erforderlicher Einwilligung technisch blockiert.
- [ ] Bewerbungs-, Team-, Ticket-, Moderations-, Audit- und Einwilligungsnachweise haben sachlich begründete Fristen und funktionierende Lösch-/Sperrprozesse.
- [ ] Backups, Exporte, Anhänge und verbundene Datenbanken sind im Löschkonzept berücksichtigt.
- [ ] Auskunft, Berichtigung, Löschung, Einschränkung, Widerspruch und Datenübertragbarkeit können fristgerecht bearbeitet werden.
- [ ] Verfahren für Datenschutzverletzungen einschließlich Bewertung, Dokumentation und ggf. 72-Stunden-Meldung bestehen.
- [ ] Verzeichnis der Verarbeitungstätigkeiten und – sofern erforderlich – Datenschutz-Folgenabschätzung sind geprüft.

## 5. Weitere Rechtsgebiete – Anwendbarkeit prüfen

- DDG und MStV (Anbieterkennzeichnung, redaktionelle Verantwortung);
- DSGVO und BDSG (personenbezogene Daten);
- TDDDG (Speichern/Auslesen von Endgeräteinformationen);
- DSA (nur soweit die konkreten Dienste unter die jeweiligen Vermittlungs-/Hostingpflichten fallen);
- BGB/AGB- und Verbraucherrecht (Vertragsschluss, Einbeziehung, Transparenz und Klauselkontrolle);
- Urheber-, Marken-, Design-, Bildnis- und Persönlichkeitsrechte;
- JMStV/JuSchG (konkrete Inhalte und Zielgruppen);
- BFSG (konkrete Dienstleistung und mögliche Kleinstunternehmensausnahme);
- Gewerbe-, Einkommen- und Umsatzsteuerrecht (tatsächliche Einnahmen und Geschäftsmodell);
- EU AI Act (Rolle, Einsatzgebiet, Risikoklasse und jeweilige Anwendungszeitpunkte).

Nicht jede Rechtsnorm ist allein aufgrund einer Website automatisch einschlägig. Die Einstufung muss anhand der tatsächlichen Angebote, Inhalte, Nutzergruppen und Verträge erfolgen.

## 6. Tests und Deployment

- Änderungen laufen über einen separaten Branch und Pull Request; `main` wird nicht direkt umgangen.
- Die einschlägigen Tests müssen auf dem konkreten PR-Commit erfolgreich sein.
- Der VPS darf ausschließlich über den vorgesehenen Deployment-Workflow aktualisiert werden.
- Der Deploy-Workflow verlangt einen geschützten `main`-Branch und erfolgreiche Pflicht-CI-Läufe. Ein als „success“ markierter Workflow kann übersprungene Deploy-Schritte enthalten; Job-Ergebnisse müssen geprüft werden.
- Am Prüfzeitpunkt meldete GitHub für `main` `protected=false`. Der Deploy-Lauf #431 hatte die Schritte „Deployment-Secrets prüfen“, „SSH vorbereiten“ und „Backup, Deploy, Health-Check, ggf. Rollback“ übersprungen. Daraus folgt **kein Nachweis**, dass der VPS auf Commit `64907a3a280366c9453827c81963b8fdfdc04c29` aktualisiert wurde.
- Keine SSH-/VPS-Änderung, kein Branch-Protection-Bypass und kein manuelles Setzen von Freigabeschaltern ohne getrennte Prüfung und ausdrückliche Freigabe.

## 7. Rechtsgrundlagen – amtliche Startpunkte

- [DSGVO – Verordnung (EU) 2016/679](https://eur-lex.europa.eu/eli/reg/2016/679/oj)
- [§ 5 DDG](https://www.gesetze-im-internet.de/ddg/__5.html)
- [§ 18 MStV](https://www.gesetze-bayern.de/Content/Document/MStV-18)
- [§ 25 TDDDG](https://www.gesetze-im-internet.de/tdddg/__25.html)
- [Digital Services Act – Verordnung (EU) 2022/2065](https://eur-lex.europa.eu/eli/reg/2022/2065/oj)
- [BGB](https://www.gesetze-im-internet.de/bgb/)
- [BFSG](https://www.gesetze-im-internet.de/bfsg/)

Diese Liste ist ein Startpunkt und keine vollständige oder fallabschließende Rechtsprüfung.

## 8. Phase 1 – technische Datenschutzkorrekturen

Branch: `legal/phase-1-privacy-accuracy` (von Commit `378a166b5d4241908289dddaf6560a50c312e35a`).

- Bewerbungsportal ohne Discord-CDN-Profilbilder; signierte Bewerbungs-Sitzung auf ID und Namensfelder minimiert.
- Browserseitige Bewerbungsentwürfe: 30 Tage seit letzter Änderung, automatische Bereinigung abgelaufener/fehlerhafter Werte und sichtbare manuelle Löschmöglichkeit.
- `apply_session`: 3 Stunden, bei aktiver Nutzung erneuert, absolute Obergrenze 12 Stunden.
- Datenschutz- und Sicherheitsseiten unterscheiden öffentliche Website (keine externen Bilder) und interne Dashboard-/Teamansichten (Discord-CDN-Avatarabrufe möglich).

Die serverseitige Datenbereinigung bleibt absichtlich gesperrt. Weder diese Phase noch ein grüner CI-Lauf belegen vollständige Datenschutz-Compliance. Offene Punkte sind im `docs/RECHTLICHER_GESAMTAUDIT_BOCHUM_RP.md` als Nachweis- oder Implementierungsaufgaben erfasst.
