# Rechtliche Freigabe der öffentlichen Website

**Wichtiger Hinweis:** Die öffentlichen Rechtstexte sind technische Entwürfe auf Grundlage des Repository-Codes. Sie sind keine Rechtsberatung, keine anwaltlich geprüften AGB und keine Bestätigung der DSGVO-Konformität. Die Software kann weder die tatsächliche Betreiberstruktur noch Verträge, Interessenabwägungen, Einwilligungen oder die rechtliche Wirksamkeit einer Klausel überprüfen.

Die Veröffentlichung bleibt durch die bestehenden Freigabeschalter geschützt. Solange Angaben oder Freigaben fehlen, erhalten Seiten `noindex, nofollow`, `robots.txt` sperrt Suchmaschinen aus und `sitemap.xml` ist nicht abrufbar. **Das ist keine Zugangssperre:** Die URLs bleiben öffentlich erreichbar und gesetzliche Informationspflichten werden dadurch nicht aufgehoben.

## 1. Private Betreiberangaben auf dem VPS

Echte Namen, Anschriften und private Kontaktdaten gehören nicht in öffentliche GitHub-Dateien, Issues, Commits oder Chatnachrichten. Die Werte werden ausschließlich in der privaten VPS-Umgebung gepflegt.

| Variable | Inhalt | Einordnung |
|---|---|---|
| `PULSE_LEGAL_NAME` | Vollständiger Name oder Organisation des Anbieters | erforderlich für die Freigabe |
| `PULSE_LEGAL_ADDRESS` | Zustellfähige Anschrift; Zeilen mit `|` trennen | erforderlich für die Freigabe |
| `PULSE_LEGAL_EMAIL` | Erreichbare Kontakt-E-Mail | erforderlich für die Freigabe |
| `PULSE_LEGAL_HOSTER` | Name und Sitz des Hosting-Anbieters | erforderlich für die Freigabe |
| `PULSE_LEGAL_PHONE` | Telefonnummer | optional, soweit vorhanden/erforderlich |
| `PULSE_LEGAL_FORM` | Rechtsform | falls eine Organisation/juristische Person vorliegt |
| `PULSE_LEGAL_REPRESENTATIVE` | Name und Anschrift der vertretungsberechtigten Person | falls rechtlich erforderlich |
| `PULSE_LEGAL_REGISTER` | Registergericht und Registernummer | falls eingetragen |
| `PULSE_LEGAL_VAT_ID` | Umsatzsteuer-Identifikationsnummer | falls vorhanden |
| `PULSE_LEGAL_WIDNR` | Wirtschafts-Identifikationsnummer | falls vorhanden |
| `PULSE_LEGAL_AUTHORITY` | Zuständige Aufsichtsbehörde | nur falls die Tätigkeit einer behördlichen Zulassung unterliegt |
| `PULSE_LEGAL_PROFESSION` | Kammer, Berufsbezeichnung, Staat und Berufsrecht | nur falls eine reglementierte berufliche Tätigkeit ausgeübt wird |
| `PULSE_LEGAL_CONTENT_RESPONSIBLE` | Vollständiger Name und Anschrift der inhaltlich verantwortlichen Person | nur falls § 18 Abs. 2 MStV einschlägig ist |
| `PULSE_LEGAL_EXTRA` | Zusätzliche Angaben | nur falls tatsächlich erforderlich |

Die Felder für Rechtsform und vertretungsberechtigte Person dürfen nicht mit einem Team-/Projekt-Spitznamen gefüllt werden, wenn tatsächlich eine natürliche Person oder eine juristische Person Anbieter ist. Bei einer juristischen Person sind die einschlägigen Angaben zur Rechtsform und Vertretung zu prüfen.

### Impressum: Anwendbarkeit richtig einordnen

- **§ 18 Abs. 1 MStV** erfasst Telemedien, die nicht ausschließlich persönlichen oder familiären Zwecken dienen. Er verlangt leicht erkennbare, unmittelbar erreichbare und ständig verfügbare Angaben zu Name und Anschrift sowie bei juristischen Personen zusätzlich Name und Anschrift der vertretungsberechtigten Person.
- **§ 5 DDG** gilt für geschäftsmäßige, in der Regel gegen Entgelt angebotene digitale Dienste und enthält weitere Angaben, z. B. elektronische Kontaktmöglichkeit, gegebenenfalls Aufsichtsbehörde, Register, Berufsangaben und Identifikationsnummern.
- **§ 18 Abs. 2 MStV** betrifft zusätzlich Telemedien mit journalistisch-redaktionell gestalteten Angeboten. Ob die konkreten BOCHUM-RP-Inhalte diese Schwelle erreichen, muss anhand der tatsächlich veröffentlichten Inhalte geprüft werden. Falls ja, muss eine verantwortliche Person mit vollständigem Namen und Anschrift benannt werden und die gesetzlichen persönlichen Voraussetzungen erfüllen.

Die Seiten sollen die Normen deshalb nicht pauschal so darstellen, als seien sämtliche Zusatzangaben für jedes Hobbyprojekt automatisch erforderlich. Die konkrete Anbieterstruktur und die tatsächlichen Inhalte müssen rechtlich eingeordnet werden.

Eine c/o- oder Empfangsadresse darf nicht einfach als Briefkastenersatz verwendet werden. Vor ihrer Nutzung muss geklärt sein, dass dort rechtserhebliche Zustellungen tatsächlich und zuverlässig entgegengenommen werden dürfen. Eine Postfach-/Packstationsadresse oder reine virtuelle Weiterleitung ist nicht ohne Weiteres eine zustellfähige Anschrift. Eine zulässige Empfangsbevollmächtigung ist eine individuelle Rechtsfrage; die private Wohnanschrift darf nicht durch erfundene Angaben ersetzt werden.

## 2. Verantwortlicher und Datenschutzkontakt

Standardmäßig verwendet die Datenschutzerklärung die Anbieterangaben aus `PULSE_LEGAL_*`. Nur wenn der datenschutzrechtlich Verantwortliche tatsächlich eine andere Person/Organisation ist, können die folgenden privaten VPS-Werte abweichen:

| Variable | Inhalt |
|---|---|
| `PULSE_PRIVACY_CONTROLLER_NAME` | Abweichender Name des Verantwortlichen |
| `PULSE_PRIVACY_CONTROLLER_ADDRESS` | Abweichende Anschrift des Verantwortlichen; Zeilen mit `|` trennen |
| `PULSE_PRIVACY_CONTROLLER_EMAIL` | Abweichende Kontakt-E-Mail |
| `PULSE_PRIVACY_DPO_CONTACT` | Kontaktdaten eines Datenschutzbeauftragten, falls einer benannt werden muss |
| `PULSE_PRIVACY_SUPERVISORY_AUTHORITY` | Konkrete zuständige Datenschutzaufsichtsbehörde |
| `PULSE_PRIVACY_TRANSFER_INFO` | Verifizierte Angaben zu Drittland, Empfängerrolle und Übermittlungsmechanismus |

Die Identität des Verantwortlichen darf nicht mit dem Hosting-Anbieter, Discord oder dem Betreiber einer KI-API verwechselt werden. Ob ein Datenschutzbeauftragter bestellt werden muss, ist anhand der gesetzlichen Kriterien und der tatsächlichen Verarbeitung zu prüfen. Die zuständige Aufsichtsbehörde richtet sich nach dem Verantwortlichen und den gesetzlichen Zuständigkeitsregeln, nicht automatisch nach dem Wohnort eines Website-Besuchers.

## 3. Manuelle Freigabe bleibt gesperrt

Eine Suchmaschinenfreigabe ist nur möglich, wenn alle vier Pflichtangaben vorhanden sind und die Betreiberfreigaben bewusst gesetzt wurden:

| Variable | Wert | Bedeutung |
|---|---|---|
| `PULSE_LEGAL_REVIEWED` | `1` | Verantwortlicher hat Anbieterangaben, Datenflüsse, Empfänger, Speicherpraxis, Rechtsgrundlagen, Betroffenenrechte und konkrete Verträge geprüft |
| `PULSE_TERMS_APPROVED` | `1` | Verantwortlicher hat die Nutzungsbedingungen ausdrücklich geprüft und freigegeben |
| `PULSE_TERMS_EFFECTIVE_DATE` | `YYYY-MM-DD` | Tatsächliches Gültigkeitsdatum der freigegebenen Fassung |

Die Werte wurden in diesem PR **nicht gesetzt, nicht geändert und nicht auf dem VPS geprüft**. Auch ein gesetzter Schalter ist nur eine bewusste Betreiberbestätigung, keine automatische Rechtsprüfung und keine Zertifizierung.

## 4. Datenverarbeitung: Code-Stand und bekannte Grenzen

Die Datenschutzerklärung unterscheidet nun stärker zwischen dem, was der Code tatsächlich tut, und dem, was der Betreiber rechtlich noch belegen muss.

### Website und Team-Login

- Bei Seitenaufrufen können IP-Adresse, Zeitpunkt, URL und Browserkennung für Auslieferung, Rate-Limiting und Sicherheit verarbeitet werden.
- Der Team-Login nutzt Discord OAuth mit dem Scope `identify`; Pulse erhält Discord-ID, Namen und Avatar.
- Login-IP-Auditdaten haben im aktuellen Code eine 30-Tage-Aufbewahrung; Dashboard-Sitzungen enden nach 7 Tagen bzw. 24 Stunden Inaktivität.
- Die öffentlichen Teamprofile werden seit dem Merge von PR #164 live aus den konfigurierten Discord-Teamrollen erzeugt. Sichtbar sind Anzeigename, Teamrolle und gegebenenfalls Profilbild; Präsenzstatus, Abwesenheiten, interne Notizen, Verwarnungen und private Kontaktdaten sind nicht Teil der öffentlichen Karten.
- Bei Profilbildern fordert der Browser Ressourcen von Discord-CDN-Domains an. Eine CDN-Bildadresse kann technische Discord-Kennungen enthalten, auch wenn keine Discord-ID als sichtbares Profilfeld ausgegeben wird.

**Technische Schutzmaßnahme in diesem PR:** Die öffentliche Teamseite zeigt nur noch Personen mit einer ausdrücklich erteilten, versionsgebundenen Freigabe. Die Person muss in Discord den Befehl `/teamseite_freigabe` aufrufen, die ausführliche Information lesen und eine separate Bestätigungsschaltfläche anklicken. Die Freigabe speichert Discord-ID, Textversion, Teamrollen-Scope und Zeitstempel in `pulse.db`. Ohne Nachweis wird kein Profil ausgegeben. Widerruf ist über `/teamseite_freigabe_widerrufen` möglich; Rollenänderungen und Serveraustritt beenden die aktuelle Freigabe. Die Freigabe hat keine Auswirkung auf Teammitgliedschaft oder Berechtigungen.

**Weiterhin offen:** Der Betreiber muss sicherstellen, dass die Informationspflichten nach Art. 13 DSGVO vor der Freigabe erfüllt werden und – soweit Daten aus Discord übernommen werden – die Information nach Art. 14 DSGVO im konkreten Aufnahmeprozess erfolgt. Die tatsächliche Identität des Verantwortlichen, der Kontaktweg und eine zuständige Aufsichtsbehörde müssen korrekt sein. Bei Minderjährigen ist vor der Freigabe zu klären, ob zusätzliche Schutz- oder Zustimmungserfordernisse greifen. Die Löschfrist des minimalen Freigabenachweises (einschließlich Widerrufszeitpunkt) ist noch festzulegen und technisch umzusetzen.

### Bewerbungen, Teamdaten und Löschung

- Bewerbungen enthalten Identität, Antworten, Testergebnisse, Praxisaufgaben und Bearbeitungsstatus.
- Die konfigurierbaren Zielwerte der Retention-Funktion sind kein Beleg für eine ausgeführte Löschung: `RETENTION_MUTATIONS_ENABLED = False`; die zentrale Bereinigung bleibt fail-closed.
- Daher dürfen Bewerbungs- oder Auditdaten nicht als automatisch fristgerecht gelöscht beschrieben werden.
- Für Teamdaten besteht derzeit keine einheitlich technisch durchgesetzte Löschfrist für alle Teilbestände. Der Verantwortliche muss pro Datenkategorie tatsächliche Fristen und Verantwortlichkeiten festlegen.
- Ticket-Transkripte werden laut aktuellem Code ohne automatische Löschfrist aufbewahrt.
- Entschiedene KI-Moderationsfälle werden derzeit nach 30 Tagen bereinigt; offene Fälle können länger bestehen bleiben.
- Backups rotieren nach aktuellem Code täglich 7 Tage, wöchentlich 4 Wochen und monatlich 6 Monate. Eine Löschung aus dem Live-System entfernt Daten nicht sofort aus vorhandenen Sicherungen.

### KI-Unterstützung und Moderation

- Der Support-Assistent übermittelt bei aktiver Konfiguration die aktuelle Frage und bis zu acht vorherige gespeicherte Gesprächsnachrichten an den konfigurierten Anbieter (Google Gemini oder OpenAI).
- Die KI-Moderation kann Nachrichtentext, Discord-Nutzerkennung/-Name, Kanal, Rollen, Erwähnungen, Zeitstempel und technische Fallmerkmale an den KI-Anbieter übertragen.
- `/ki_vergessen` entfernt den Pulse-intern gespeicherten Gesprächsverlauf. Es löscht nicht automatisch bereits übermittelte Daten oder Protokolle beim externen Anbieter.
- Anbieter, Region, Auftragsverarbeitung, Unterauftragnehmer, Speicherfristen, Trainingsverwendung und Drittlandtransfer hängen vom konkret verwendeten Konto, Tarif und Vertrag ab. Diese Tatsachen müssen vor Aktivierung bzw. endgültiger Freigabe anhand der realen Anbieter-Unterlagen geprüft werden.

### Cookies und Browser-Speicher

Die Cookie-Seite listet Login-Cookies, OAuth-State, Bewerbungsentwürfe sowie Dashboard-Präferenzen. Die technische Verwendung allein beweist nicht automatisch, dass jeder Zugriff/Speichervorgang unter § 25 Abs. 2 TDDDG fällt. Insbesondere Präferenzen, Formularentwürfe und optionale Funktionen müssen einzeln nach Zweck und Erforderlichkeit geprüft werden. Bei späteren Analyse-, Marketing- oder Einbettungsdiensten muss die Consent-Implementierung vor der ersten optionalen Anfrage funktionieren.

## 5. Datenschutzprüfung vor Freigabe

- [ ] Verantwortlichen und gegebenenfalls Datenschutzbeauftragten eindeutig bestimmen.
- [ ] Für jeden Verarbeitungsvorgang Zweck, Rechtsgrundlage und – bei Art. 6 Abs. 1 lit. f DSGVO – dokumentiertes berechtigtes Interesse samt Abwägung festlegen.
- [ ] Bei Daten aus Discord/anderen Quellen die Informationspflicht nach Art. 14 DSGVO prüfen und rechtzeitig erfüllen.
- [ ] Vor Freigabe der öffentlichen Teamliste die individuelle Einwilligungs-UI und deren Textversion fachlich prüfen; Art. 13/14-Information, Minderjährigenschutz und Kontaktwege sicherstellen.
- [ ] Aufbewahrungsfrist für den minimalen Einwilligungs-/Widerrufsnachweis festlegen und automatisiert durchsetzen.
- [ ] Hosting-Vertrag, Rechenzentrumsort, Unterauftragnehmer und Art.-28-Vertrag prüfen.
- [ ] Für Discord, Roblox, Gemini/OpenAI Empfängerrollen und mögliche internationale Übermittlungen mit konkretem Zielstaat und konkretem Transfermechanismus prüfen.
- [ ] Tatsächliche KI-Konfiguration, Provider-Auswahl, Modell, Datenfelder, Konto-/Tarifbedingungen, Aufbewahrung und Trainingsverwendung prüfen.
- [ ] Bewerbungs-, Team-, Moderations-, Audit- und Ticketdaten mit festen und technisch umgesetzten Speicher-/Löschfristen versehen.
- [ ] Löschung einschließlich Backups, Exporte und verlinkte Datenspeicher organisatorisch und technisch umsetzen.
- [ ] Betroffenenrechte mit Identitätsprüfung, Fristen, Zuständigkeit und Dokumentation praktisch ausführbar machen.
- [ ] Zuständige Datenschutzaufsichtsbehörde korrekt benennen.
- [ ] Cookie-/Storage-Einträge einzeln nach § 25 TDDDG einordnen; optionale Dienste vor Zustimmung blockieren.
- [ ] Prüfen, ob Kinder/Jugendliche die Angebote nutzen und ob zusätzliche Anforderungen für diesen konkreten Dienst gelten.

## 6. Nutzungsbedingungen und Einbeziehung

Die Nutzungsbedingungen sind ausführlicher strukturiert und enthalten unter anderem Zugang, Community-Verhalten, Inhalte/Rechte Dritter, Bewerbungen, Teamfunktionen, Moderation, KI, externe Plattformen, Verfügbarkeit, Datenschutz und eine zurückhaltende Haftungsregelung.

**Wichtig:** Eine verlinkte oder veröffentlichte Seite allein bedeutet nicht, dass AGB wirksam Bestandteil eines Vertrags geworden sind. § 305 Abs. 2 BGB verlangt bei Vertragsschluss einen deutlichen Hinweis, eine zumutbare Möglichkeit zur Kenntnisnahme und das Einverständnis der anderen Vertragspartei. Die tatsächlichen Registrierungs-, Bewerbungs- und Community-Abläufe müssen daraufhin geprüft werden. Klauseln müssen außerdem transparent und angemessen sein (§ 307 BGB); zwingende Haftungsgrenzen dürfen nicht umgangen werden (§ 309 BGB). Deshalb bleiben die Bedingungen bis zur Betreiberprüfung ein Entwurf.

## 7. Amtliche Rechtsgrundlagen für die abschließende Prüfung

- [§ 5 DDG – Allgemeine Informationspflichten](https://www.gesetze-im-internet.de/ddg/__5.html)
- [§ 18 MStV – Informationspflichten und Auskunftsrechte](https://www.landesrecht-bw.de/bsbw/document/jlr-NNLBW00007E9DNN00000000043)
- [DSGVO – Verordnung (EU) 2016/679, insbesondere Art. 13, 14, 28 und 44 ff.](https://eur-lex.europa.eu/eli/reg/2016/679/oj)
- [§ 25 TDDDG](https://www.gesetze-im-internet.de/tdddg/__25.html)
- [§ 305 BGB – Einbeziehung Allgemeiner Geschäftsbedingungen](https://www.gesetze-im-internet.de/bgb/__305.html)
- [§ 307 BGB – Inhaltskontrolle](https://www.gesetze-im-internet.de/bgb/__307.html)
- [§ 309 BGB – Klauselverbote](https://www.gesetze-im-internet.de/bgb/__309.html)

Diese Quellen ersetzen keine Prüfung des konkreten Betreibers, der tatsächlichen Website-Inhalte, der Anbieter-Verträge und der betrieblichen Abläufe.
