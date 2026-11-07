# BOCHUM RP / Pulse – rechtlicher Gesamtaudit und Restpunkte

**Prüfstand:** 7. November 2026
**Technische Basis:** `main` @ `378a166b5d4241908289dddaf6560a50c312e35a`
**Bearbeitungsbranch:** `legal/phase-1-privacy-accuracy`
**Status:** NICHT RECHTLICH FREIGEGEBEN

> Dieses Dokument ist ein technisches und organisatorisches Auditregister, keine Rechtsberatung und kein Rechtsgutachten. Eine Aufgabe gilt erst als erledigt, wenn die zugrunde liegende Tatsache/Vertragsunterlage vorliegt, die technische Änderung getestet ist und der Betreiber die rechtliche Einordnung geprüft hat. Es werden keine privaten Namen, Anschriften, Telefonnummern oder Vertragsdaten in Git gespeichert.

## Statusdefinition

- **BLOCKER:** Ohne tatsächliche Betreiberentscheidung, Vertragsnachweis oder rechtliche Einordnung keine abschließende Freigabe.
- **TECHNISCH OFFEN:** Code-/Prozessänderung und Regressionstest erforderlich.
- **BEDINGT:** Nur relevant, wenn die beschriebene Tätigkeit oder Funktion tatsächlich vorliegt.
- **UMGESETZT:** Konkrete technische Änderung nachgewiesen; nicht gleichbedeutend mit gesamtrechtlicher Freigabe.

## 1. Betreiber, Minderjährigkeit und Vertragsbefugnis – BLOCKER

- Den tatsächlichen Betreiber und datenschutzrechtlich Verantwortlichen anhand der tatsächlichen Entscheidungs- und Betriebsstruktur bestimmen.
- Bei minderjährigem Betreiber gesetzliche Vertreter einbeziehen und Hosting-, Domain-, OAuth-, KI- und sonstige Dauerschuldverhältnisse prüfen. Nicht unterstellen, dass ein Elternteil automatisch Betreiber oder Vertreter ist.
- § 112 BGB nur prüfen, wenn tatsächlich ein Erwerbsgeschäft selbständig betrieben werden soll; nicht pauschal auf ein kostenloses Hobbyprojekt übertragen.
- Keine falsche Firma, kein Verein und keine bloße Scheinvertretung eintragen.
- Privaten Namen und private Anschrift ausschließlich in geschützte Serverkonfiguration eintragen, nie in Commits, Issues, Logs oder Screenshots.

**Nachweise:** bestätigte Betreiberrolle, Einwilligung/Vertretung bei Verträgen soweit erforderlich, geprüfte Vertragskopien und Entscheidung über tatsächliche Monetarisierung.

## 2. Impressum und redaktionelle Inhalte – BLOCKER

- § 18 Abs. 1 MStV für das nicht ausschließlich persönliche/familiäre Onlineangebot prüfen.
- § 5 DDG gesondert anhand der tatsächlichen Geschäftsmäßigkeit und des Angebots in der Regel gegen Entgelt bewerten; bei Anwendbarkeit E-Mail plus weitere schnelle elektronische Kontaktmöglichkeit vorsehen.
- § 18 Abs. 2 MStV für echte journalistisch-redaktionelle News-/Blog-Inhalte prüfen; nicht allein aus dem Vorhandensein einer News-Seite eine Verantwortlichkeit ableiten.
- Eine c/o-/Impressumsservice-Adresse nur nach Prüfung der rechtlich zulässigen Empfangsbevollmächtigung, tatsächlichen Zustellbarkeit und Vollmacht verwenden. Eine Postfach- oder reine Weiterleitungsadresse nicht ohne Prüfung als ladungsfähige Anschrift behandeln.
- `PULSE_LEGAL_DDG_ASSESSMENT`, `PULSE_LEGAL_MSTV_18_2_ASSESSMENT` und `PULSE_LEGAL_REVIEWED` nicht als Ersatz für die tatsächliche Prüfung verwenden.

**Nachweise:** bestätigte Anbieteridentität, zustellfähige Adresse oder belastbare Empfangsvollmacht, erreichbare Kontaktmöglichkeit, schriftlich begründete DDG-/MStV-Einstufung.

## 3. Hosting und Auftragsverarbeitung – BLOCKER

- Vionity ist das Kunden-/Verwaltungsportal; die Anbieterkennzeichnung nennt Henrik Kramer e.K. / Prepaid-Hoster.de. Vertragspartner für den konkreten VPS anhand Bestellung/Rechnung/Vertrag verifizieren.
- Im Kundenportal wird Offenbach am Main als Standort angezeigt. Tatsächlichen Betriebsstandort, Backupstandorte, Supportzugriffe und Unterauftragnehmer anhand Providerunterlagen bestätigen.
- Prüfen, ob und in welchem Umfang der Hoster personenbezogene Daten im Auftrag verarbeitet. Soweit Art. 28 DSGVO greift, AVV und TOMs abschließen und dauerhaft aufbewahren.
- Keine AVV-Unterzeichnung, keine Region und keinen Unterauftragnehmer behaupten, solange kein Nachweis vorliegt.

**Nachweise:** Vertrag/Bestellbestätigung, AVV, TOMs, Unterauftragnehmerliste, Datenstandorte, Support-/Administrationszugriffe, Lösch-/Rückgabevereinbarung.

## 4. Dateninventar und Rechtsgrundlagen – TECHNISCH OFFEN / BLOCKER

- Alle Website-, Discord-Bot-, Dashboard-, Bewerbungs-, Team-, Ticket-, Sprachsupport-, Roblox-, KI-, Logging-, Backup- und Wiederherstellungsprozesse in einem Verzeichnis der Verarbeitungstätigkeiten erfassen.
- Pro Vorgang Zweck, Datenkategorien, betroffene Personen, Empfänger, Rechtsgrundlage, Speicherdauer, Zugriffsrollen und Drittlandbezug festlegen.
- Art. 13-Informationen zum Erhebungszeitpunkt, Art. 14-Informationen bei Daten aus anderen Quellen und die Ausnahmen jeweils prüfen.
- Berechtigtes Interesse pro Verarbeitung dokumentieren und abwägen; Vertragserfüllung nicht pauschal auf jede Bewerbung oder Community-Verarbeitung anwenden.

**Nachweise:** vollständiges Datenflussdiagramm, Verzeichnis der Verarbeitungstätigkeiten, Rechtsgrundlagen-/LIA-Matrix, Empfänger- und Transferregister.

## 5. Betroffenenrechte und Datenexport – TECHNISCH OFFEN

- Zentralen Eingang für Auskunft, Berichtigung, Löschung, Einschränkung, Widerspruch, Datenübertragbarkeit und Widerruf vorsehen.
- Identität angemessen prüfen, Anfragen protokollieren, Fristkontrolle (grundsätzlich ein Monat) und nachvollziehbare Antwort ermöglichen.
- Der bestehende `export_person`-Export hat eine Obergrenze von 5.000 Zeilen pro SQLite-Tabelle und dokumentierte Lücken bei alten Freitext-Auditdaten. Er darf daher nicht als garantiert vollständiger Gesamtexport dargestellt werden.
- Alle SQLite-/JSON-Stores, Transkripte, Anhänge, Uploads, öffentliche Zustimmungsnachweise und Backup-Auswirkungen in den Export-/Löschprozess aufnehmen.
- Gesetzliche Aufbewahrungsausnahmen und Rechte Dritter je Fall berücksichtigen; keine pauschale Komplettlöschung unabhängig von Rechtsgrundlagen.

**Abnahmekriterien:** Vollständiger Testdatensatz über alle Stores, Exportvergleich mit bekannten Person-IDs/Referenzen, Löschsimulation, Frist-/Auditprotokoll, keine Daten anderer Personen im Export.

## 6. Aufbewahrung und Löschung – BLOCKER

- `pulse_retention.py` hat `RETENTION_MUTATIONS_ENABLED = False`. Diese Sperre bleibt aktiv, bis referenzsichere Bereinigung über alle verknüpften Stores, atomare/rollbackfähige Verarbeitung und Restore-Verhalten getestet sind.
- Bewerbungen, Teamdaten, Schicht-/Kalenderdaten, Verwarnungen, Moderationsfälle, Auditlogs, Consent-Nachweise, Tickets, Transkripte und Anhänge benötigen je einen sachlich begründeten Zweck und eine Frist.
- Ticket-Transkripte und die gespeicherten Anhänge haben derzeit keine automatische Löschfrist.
- Backups können aus Live-Stores gelöschte Daten bis zur Rotation enthalten. Restore darf gelöschte Daten nicht ohne erneute Prüfung dauerhaft zurückbringen.
- Browserseitige, nicht abgesendete Antragsentwürfe erhalten mit Phase 1 eine 30-Tage-Ablauffrist seit letzter Änderung. Abgelaufene Werte sind nicht wiederherstellbar und werden beim nächsten Formularaufruf entfernt; dies betrifft keine bereits eingereichten Bewerbungen.

**Abnahmekriterien:** Dry-run mit Referenzgraph, Backup vor Bereinigung, sichere Wiederholung nach Fehler, vollständige Transaktions-/Journal-Strategie, Tests gegen verwaiste Bezüge, Löschprotokoll ohne Klartextdaten, getestete Backup-Rotation/Restore.

## 7. Minderjährige, Einwilligung und Jugendschutz – BLOCKER / BEDINGT

- Alter der betroffenen Personen nicht pauschal aus Art. 8 DSGVO ableiten. Art. 8 betrifft die Einwilligung bei direkt angebotenen Diensten der Informationsgesellschaft, wenn die Verarbeitung auf Einwilligung beruht.
- Freiwillige öffentliche Teamprofile, Fotos, Namensdarstellung und andere Einwilligungen separat nach Zweck, Freiwilligkeit, Widerruf und Alter prüfen.
- Tatsächliche Roblox-Erfahrung, Roblox Content Maturity, Inhalte, Zielgruppe und Rolle von BOCHUM RP als Plattform-/Erfahrungsanbieter feststellen.
- Eine pauschale USK-18- oder 16+-Grenze nicht erfinden. Einstufung und Hinweise aus tatsächlich verfügbaren Spiel-/Inhaltseigenschaften ableiten.
- Community-Schutzregeln für private Nachrichten, Doxing, Grooming-Verdacht, Belästigung, persönliche Kontaktdaten, vertrauliche Meldungen und Eskalation definieren.

**Nachweise:** Inhaltsinventar, dokumentierte Alters-/Zielgruppenbewertung, gültige Freigabetexte, Minderjährigenschutz-Konzept, Beschwerde-/Notfallprozess.

## 8. Community-Regeln, Meldungen und DSA – BEDINGT / TECHNISCH OFFEN

- Prüfen, ob BOCHUM RP neben Discord tatsächlich eigene Hosting-/Plattformfunktionen anbietet, auf denen Nutzer Inhalte für andere Nutzer speichern oder veröffentlichen. Die Verwaltung eines Discord-Servers macht den Betreiber nicht automatisch zum DSA-Plattformanbieter.
- Unabhängig davon einen gut erreichbaren Meldeweg für konkrete Rechtsverletzungen, private Daten, Bedrohungen, Belästigung, Urheberrechtsverletzungen und Sicherheitslücken vorsehen.
- Meldungen mit Vorgangsnummer, Inhalt/URL, Eingang, Prüfung, Entscheidung, Begründung, Bearbeiter und Beschwerdemöglichkeit dokumentieren.
- Nicht jede Behauptung automatisch als bewiesenen Verstoß behandeln; bei akuter Gefahr geeignete Sofortmaßnahmen und anschließende Prüfung vorsehen.
- Moderationsordnung um nachvollziehbare Kriterien, Begründung schwerer Maßnahmen und menschliche erneute Überprüfung ergänzen.

## 9. Nutzungsbedingungen und Vertragsschluss – TECHNISCH OFFEN

- Die öffentliche Nutzungsbedingungen-Seite ist allein noch kein Nachweis der wirksamen AGB-Einbeziehung.
- Bei tatsächlichen Vertragsschlüssen Hinweis, zumutbare Kenntnisnahme und Einverständnis nach § 305 Abs. 2 BGB sicherstellen.
- Regelwerkversion und Zeitpunkt einer tatsächlichen Zustimmung nachweisbar zuordnen, ohne die Zustimmung mit Discord-Rollen oder unnötigen Daten zu koppeln.
- Transparente Regeln für Maßnahmen, Beschwerden, Inhalte/Rechte Dritter, Verfügbarkeit und Haftung prüfen; keine pauschalen Haftungsausschlüsse oder automatische Geldstrafen einbauen.

## 10. KI und EU AI Act – TECHNISCH OFFEN / BEDINGT

- Tatsächliche KI-Funktionen, Anbieter, Produkt-/API-Variante, Modell, Datenfelder, Empfänger und Drittlandbezug feststellen.
- Direkte Interaktionen mit KI klar als KI kennzeichnen, sofern dies nicht offensichtlich ist.
- Menschliche Prüfung vor Moderationssanktionen sicherstellen; keine alleinige automatische Entscheidung über Bewerbungen oder Sanktionen behaupten.
- Teammitglieder angemessen zu KI-Kompetenz, Fehlerbildern, Bias, Halluzinationen und Eskalation schulen.
- Risikoklassifizierung und Pflichten für den konkreten KI-Einsatz einzelfallbezogen prüfen; keine Hochrisiko-Einstufung allein aus der Verwendung von KI ableiten.

## 11. TDDDG, Cookies und externe Ressourcen – TECHNISCH OFFEN

- Cookies, Local Storage und Session Storage jeweils nach § 25 TDDDG und tatsächlicher Erforderlichkeit prüfen.
- Öffentliche Informationsseiten laden nach aktuellem Teststand nur First-Party-Bilder, Skripte und Stylesheets.
- Interne Dashboard-/Teamansichten können Discord-CDN-Profilbilder laden; die Roblox-Spielervorschau kann Roblox-Thumbnailbilder direkt im Browser laden. Beide Datenflüsse sind in der Datenschutzerklärung offenzulegen. Das Bewerbungsportal lädt keine Avatarbilder mehr und speichert kein Avatarfeld in seiner signierten Sitzung.
- Optionalen Analytics-/Marketing-/Embed-Dienst nur nach wirksamer Einwilligung starten, falls erforderlich.
- Cookie-Tabelle mit realen Laufzeiten und Löschverhalten synchron zu den Code-Konstanten halten.

## 12. Backups, Sicherheitsvorfälle und Zugriffsschutz – TECHNISCH OFFEN

- Full-Backup-Archive enthalten sensible Bewerbungs-, Team- und Ticketdaten. POSIX-Dateirechte `0700`/`0600` sind ein Basisschutz, ersetzen aber keine Verschlüsselung.
- Archivverschlüsselung, externe Schlüsselverwaltung, Schlüsselrotation, Backupzugriff und Wiederherstellung prüfen.
- Datenschutzverletzungsprozess für Erkennen, Bewertung, Dokumentation, ggf. Aufsichtsbehördenmeldung innerhalb der gesetzlichen Frist und Benachrichtigung betroffener Personen festlegen.
- Incident-Runbook, Verantwortliche, Erreichbarkeit und regelmäßige Übung dokumentieren.

## 13. Urheber-, Marken-, Persönlichkeits- und Nutzungsrechte – TECHNISCH OFFEN

- Für Logos, Schriftarten, Musik, Sounds, Videos, Bilder, Plugins und Templates Herkunft, Lizenz, Umfang, Laufzeit, Bearbeitungsrechte und Namensnennung dokumentieren.
- Bei Nutzerinhalten nur die konkret erforderlichen Nutzungsrechte für Speicherung/Anzeige einholen; Urheber- und Persönlichkeitsrechte Dritter beachten.
- BOCHUM RP darf nicht irreführend als offizieller Dienst der Stadt Bochum, Roblox oder Discord erscheinen. Keine fremden Marken/Logos ohne passende Grundlage verwenden.

## 14. Einnahmen, Werbung und Verbraucherrecht – BEDINGT

- Bis zur Klärung der Betreiber-/Vertragsbefugnis keine Zahlungen, VIP-Verkäufe, Ingame-Vorteile, Werbevergütung oder Sponsoring einführen.
- Falls später Einnahmen/Entgelte auftreten: Gewerbe-, Einkommensteuer-, Umsatzsteuer-, Preisangaben-, Verbraucher-, Widerrufs-, Zahlungsdienst- und ggf. Schlichtungspflichten anhand des konkreten Geschäftsmodells einzeln beurteilen.
- Spenden ohne Gegenleistung sind nicht automatisch mit Verkäufen gleichzusetzen; die tatsächliche Ausgestaltung zählt.

## 15. Barrierefreiheit – BEDINGT

- BFSG-Anwendbarkeit nur für konkrete Dienstleistungen des elektronischen Geschäftsverkehrs und unter Beachtung möglicher Kleinstunternehmensausnahmen prüfen.
- Unabhängig davon Tastaturbedienung, Fokus, Kontrast, Fehlermeldungen, Alternativtexte und Screenreader-Verhalten testen.

## 16. Freigabegate

Die folgenden Zustände dürfen erst nach Nachweis gesetzt werden:

- `PULSE_LEGAL_REVIEWED=1`: Betreiber hat tatsächliche Anbieterangaben, Prozesse, Empfänger, Rechtsgrundlagen, Fristen und Verträge geprüft.
- `PULSE_TERMS_APPROVED=1` plus Gültigkeitsdatum: Bedingungen und tatsächlicher Einbeziehungsprozess sind geprüft und freigegeben.
- `PULSE_LEGAL_DDG_ASSESSMENT` und `PULSE_LEGAL_MSTV_18_2_ASSESSMENT`: schriftliche, auf das wirkliche Angebot bezogene Bewertung liegt vor.
- noindex-/Sitemap-Gate bleibt geschlossen, solange verpflichtende Betreiberangaben oder Prüfungen fehlen.

## 17. Amtliche Rechtsgrundlagen – Startpunkte

- [DSGVO – Verordnung (EU) 2016/679](https://eur-lex.europa.eu/eli/reg/2016/679/oj)
- [§§ 106–112 BGB – Geschäftsfähigkeit und selbständiger Betrieb eines Erwerbsgeschäfts](https://www.gesetze-im-internet.de/bgb/)
- [§ 5 DDG](https://www.gesetze-im-internet.de/ddg/__5.html)
- [§ 18 MStV](https://www.gesetze-bayern.de/Content/Document/MStV-18)
- [§ 25 TDDDG](https://www.gesetze-im-internet.de/tdddg/__25.html)
- [Digital Services Act – Verordnung (EU) 2022/2065](https://eur-lex.europa.eu/eli/reg/2022/2065/oj)
- [EU AI Act – Verordnung (EU) 2024/1689](https://eur-lex.europa.eu/eli/reg/2024/1689/oj)
- [BFSG](https://www.gesetze-im-internet.de/bfsg/)

Diese Quellenliste ist nicht abschließend und ersetzt keine rechtliche Prüfung des konkreten Betriebs.