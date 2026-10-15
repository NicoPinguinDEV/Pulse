"""Anspruchsvolle, serverseitig bewertete Eignungstests für Pulse-Recruiting.

Die Fragen prüfen konkrete Regeln von Bochum RP V1 im IC-/RP- und Discord-Regelwerk.
Antwortoptionen werden beim Rendern gemischt; Lösungsschlüssel bleiben serverseitig.
"""
from __future__ import annotations

from html import escape
from random import SystemRandom
from typing import Iterable


def _q(rule_refs: str, topic: str, question: str, options: dict[str, str], correct: str) -> dict:
    if set(options) != {"a", "b", "c", "d"} or correct not in options:
        raise ValueError("Jede Testfrage benötigt vier Optionen und einen gültigen Lösungsschlüssel.")
    return {
        "rule_refs": rule_refs,
        "topic": topic,
        "question": question,
        "options": options,
        "correct": correct,
    }


APPLICATION_CAREERS = {
    "low_team": {
        "label": "Low Team",
        "description": "Anspruchsvoller Einstiegstest zu RP-Grundregeln, Datenschutz, Support und sicherem Teamverhalten.",
        "pass_percent": 75,
        "questions": [
            _q("§1.5, §16.1", "Support und Regelkenntnis",
               "Ein Spieler verlangt sofort eine verbindliche Auskunft zu einer Regel, bei der du dir nicht sicher bist. Welche Reaktion ist fachlich korrekt?",
               {
                   "a": "Du entscheidest nach deiner bisherigen Erfahrung, weil eine schnelle Antwort wichtiger ist als eine spätere Korrektur.",
                   "b": "Du schließt das Anliegen und verweist allgemein auf das Regelwerk, ohne die konkrete Frage zu klären.",
                   "c": "Du sagst transparent, dass du die Stelle prüfen musst, sicherst den Gesprächskontext und holst bei Bedarf eine zuständige Person hinzu.",
                   "d": "Du lässt den Spieler abstimmen, welche Auslegung fairer wäre, und übernimmst das Ergebnis.",
               }, "c"),
            _q("§4.3–§4.4, §18.2–§18.4", "IC/OOC-Trennung",
               "Während einer laufenden Verfolgung ruft ein Beteiligter mehrfach OOC, dass die Gegenseite gegen eine Regel verstoße. Es liegt kein technischer oder realer Notfall vor. Was ist richtig?",
               {
                   "a": "Du hältst die Verfolgung an und klärst den Regelstreit direkt im Voice, damit niemand benachteiligt wird.",
                   "b": "Du trennst den Regelstreit vom laufenden RP, führst die Situation regelkonform weiter und sicherst danach relevante Belege für den Support.",
                   "c": "Du ignorierst ab jetzt auch regelwidrige Handlungen, weil das RP nicht unterbrochen werden darf.",
                   "d": "Du drohst beiden Seiten mit einer Sanktion, bevor du den Sachverhalt kennst.",
               }, "b"),
            _q("§8.2", "NLR",
               "Dein Charakter stirbt bei einem Einsatz. Acht Minuten später kehrst du an denselben Ort zurück, erinnerst dich an den Angreifer und willst die Verfolgung fortsetzen. Welche Bewertung trifft zu?",
               {
                   "a": "Das ist erlaubt, solange du den Angriff nicht zuerst erwähnst.",
                   "b": "Nur die Erinnerung an den Namen ist verboten; der Ort darf sofort wieder betreten werden.",
                   "c": "Nach fünf Minuten darfst du zurück, wenn ein Teamler nicht ausdrücklich widerspricht.",
                   "d": "Du darfst dich an den Tod nicht erinnern und musst die 15-minütige Sperre am Ereignisort einhalten.",
               }, "d"),
            _q("§7.1–§7.2", "VDM und RDM",
               "Welche Einordnung ist anhand des Regelwerks am präzisesten?",
               {
                   "a": "VDM bezeichnet das absichtliche Überfahren einer Person mit einem Fahrzeug; RDM das Töten ohne RP-Hintergrund.",
                   "b": "VDM liegt nur bei mehreren Fahrzeugtreffern vor; RDM nur dann, wenn eine Waffe verwendet wurde.",
                   "c": "Beides ist dasselbe und setzt immer voraus, dass die betroffene Person vorher beleidigt hat.",
                   "d": "VDM ist jede Verkehrsregelverletzung; RDM ist jede Tötung während eines laufenden Einsatzes.",
               }, "a"),
            _q("§8.1, §20.1–§20.2", "Fear-RP und NVL",
               "Eine glaubhaft bewaffnete Person bedroht deinen Charakter aus nächster Nähe. Du könntest ohne nachvollziehbare Erfolgschance fliehen oder angreifen. Was verlangt das Regelwerk?",
               {
                   "a": "Du darfst angreifen, weil ein Sieg im RP wichtiger ist als die dargestellte Gefahr.",
                   "b": "Du musst sofort OOC um eine Freigabe bitten und bis dahin jede Handlung einstellen.",
                   "c": "Du spielst realistische Angst und den hohen Wert des eigenen Lebens aus, statt unrealistischen Heldenmut zu erzwingen.",
                   "d": "Du darfst nur dann Angst zeigen, wenn die Gegenseite ausdrücklich eine Schusswaffe nennt.",
               }, "c"),
            _q("§12.1–§12.3", "Safezones",
               "In einer Safezone provoziert ein Spieler deinen Charakter und beleidigt ihn IC. Ein Teamkollege meint, eine Gegenreaktion sei deswegen erlaubt. Was gilt?",
               {
                   "a": "Gewalt ist zulässig, wenn sie als Reaktion und nicht als erster Angriff erfolgt.",
                   "b": "Gewalt bleibt untersagt; eine Ausnahme erfordert eine ausdrückliche Supportfreigabe, nicht bloß die Zustimmung eines Teamkollegen.",
                   "c": "Die Safezone-Regel gilt nur für Zivilisten, nicht für Fraktionen.",
                   "d": "Die Regel gilt nur, solange sich kein Supporter in der Nähe befindet.",
               }, "b"),
            _q("§13.1–§13.2", "Peacetime",
               "Es sind genau zwei Polizeibeamte im Dienst. Eine Gruppe plant einen Raub und bezeichnet ihn als kleines illegales Geschäft. Wie ist zu entscheiden?",
               {
                   "a": "Der Raub ist zulässig, wenn alle Beteiligten vorher zustimmen.",
                   "b": "Der Raub ist zulässig, solange keine Geisel beteiligt ist.",
                   "c": "Schwere Verbrechen bleiben erlaubt, sofern die Polizei nicht ausdrücklich interveniert.",
                   "d": "Bei weniger als drei Cops sind Raub, Mord und Geiselnahmen verboten; kleinere illegale Aktivitäten können nur mit realistischem RP stattfinden.",
               }, "d"),
            _q("§11.1–§11.5", "Geiselnahmen",
               "Welche Kombination entspricht den ausdrücklich genannten Mindest- und Höchstgrenzen?",
               {
                   "a": "Mindestens drei Cops online, maximal eine Forderung je Geisel, höchstens 5.000 € pro Cop beziehungsweise 2.500 € pro Zivilist und keine Wiederholung an derselben Location.",
                   "b": "Zwei Cops reichen aus; pro Geisel dürfen zwei Forderungen gestellt werden, sofern die Summe niedrig bleibt.",
                   "c": "Drei Cops sind nur bei einer Polizeigeisel nötig; für Zivilisten gelten weder Forderungsgrenze noch Wiederholungsverbot.",
                   "d": "Die Grenzen gelten pro Täter statt pro Geisel; eine Location darf am selben Tag erneut verwendet werden.",
               }, "a"),
            _q("§5.1–§5.3 Discord-Regelwerk", "Accountschutz",
               "Ein Kollege bittet dich, ihm deine Zugangsdaten zu überlassen, damit er während deiner Abwesenheit ein Ticket bearbeiten kann. Was ist korrekt?",
               {
                   "a": "Du darfst sie teilen, wenn du den Zugang anschließend änderst und der Kollege Teammitglied ist.",
                   "b": "Du darfst nur das Passwort teilen; persönliche Daten bleiben weiterhin geschützt.",
                   "c": "Du teilst keine Zugangsdaten und nutzt keine Account-Weitergabe als Arbeitslösung; jeder ist selbst für die Sicherheit des eigenen Accounts verantwortlich.",
                   "d": "Du erlaubst den Zugriff, wenn der Kollege verspricht, keine Nachrichten zu löschen.",
               }, "c"),
            _q("§5.1 Discord-Regelwerk", "Doxing und persönliche Daten",
               "Ein Nutzer veröffentlicht die private Adresse eines anderen Mitglieds und bittet dich, den Screenshot zur Warnung weiterzuverbreiten. Welche Reaktion ist richtig?",
               {
                   "a": "Du verbreitest ihn nur in Teamkanälen, damit jeder gewarnt ist.",
                   "b": "Du entfernst die persönlichen Daten aus dem Screenshot und veröffentlichst den Rest öffentlich.",
                   "c": "Du wartest ab, bis die betroffene Person selbst einen Antrag stellt.",
                   "d": "Du verbreitest die Daten nicht weiter, sicherst den Vorfall vertraulich und leitest ihn an die zuständigen Verantwortlichen weiter; Doxing ist laut Regelwerk besonders schwerwiegend.",
               }, "d"),
            _q("§3.3 Discord-Regelwerk", "Voice-Aufnahmen",
               "Du möchtest einen Streit in einem Sprachkanal als Beweis aufnehmen. Zwei Personen stimmen zu, eine dritte anwesende Person äußert sich nicht. Was gilt?",
               {
                   "a": "Die Aufnahme ist erlaubt, wenn du sie später nur dem Serverteam zeigst.",
                   "b": "Du nimmst nicht auf und streamst das Gespräch nicht, solange nicht alle anwesenden Teilnehmer ausdrücklich zugestimmt haben.",
                   "c": "Die Zustimmung der Mehrheit reicht aus, sofern keine privaten Nachrichten aufgenommen werden.",
                   "d": "Eine Aufnahme ist immer erlaubt, wenn sie einen mutmaßlichen Regelverstoß dokumentieren soll.",
               }, "b"),
            _q("§16.2–§16.3, §18.3, §23.1–§23.3", "Beweise und Beschwerdeweg",
               "Du vermutest einen Regelverstoß, hast aber nur eine unvollständige Aussage aus zweiter Hand. Was ist der sauberste nächste Schritt?",
               {
                   "a": "Du vermeidest öffentliche Schuldzuweisungen, sammelst soweit möglich überprüfbare Belege und nutzt den vorgesehenen Supportweg; Selbstjustiz ist untersagt.",
                   "b": "Du veröffentlichst den Verdacht und bittest die Community, weitere Anschuldigungen zu sammeln.",
                   "c": "Du verhängst vorsorglich die strengste Sanktion, damit keine Beweise verschwinden.",
                   "d": "Du schließt den Fall endgültig, weil Beschwerden ohne vollständige Videoaufnahme grundsätzlich unmöglich sind.",
               }, "a"),
        ],
    },
    "high_team": {
        "label": "High Team",
        "description": "Schwerer Fachtest zu RP-Regeln, Supportentscheidungen, Moderation, Beweissicherung und Discord-Sicherheit.",
        "pass_percent": 82,
        "questions": [
            _q("§7.4, §3.3, §4.1–§4.4", "Metagaming",
               "Ein Teamler liest in einem Discord-Ticket den Fluchtplan eines Charakters und nutzt diese Information anschließend IC, obwohl sein Charakter sie nicht erfahren konnte. Welche Bewertung trifft zu?",
               {
                   "a": "Das ist Powergaming, weil der Teamler eine Entscheidung eines anderen Charakters beeinflusst.",
                   "b": "Es ist zulässig, wenn der Fluchtplan tatsächlich stimmt.",
                   "c": "Es ist nur dann verboten, wenn die Information öffentlich im Discord stand.",
                   "d": "Das ist Metagaming: OOC-Wissen wird ohne zulässigen IC-Weg in das Rollenspiel übertragen.",
               }, "d"),
            _q("§7.3, §21.1–§21.3", "Powergaming und Zwangs-RP",
               "Ein Spieler schreibt: „Ich fessele dich, du kannst dich nicht wehren, bekommst Angst und verrätst mir alles.“ Welche Beurteilung ist am präzisesten?",
               {
                   "a": "Zulässig, weil der erste Spieler seine Handlung vollständig beschrieben hat.",
                   "b": "Problematisch: Er legt Reaktion, Gefühle und Entscheidung des anderen Charakters fest, statt eine ausspielbare Handlung mit Reaktionsmöglichkeit anzubieten.",
                   "c": "Nur dann verboten, wenn die andere Person ausdrücklich OOC widerspricht.",
                   "d": "Ausschließlich Fail-RP; die Grundsätze zu Powergaming und Zwangs-RP betreffen nur Kämpfe.",
               }, "b"),
            _q("§2.1–§2.3, §7.5, §9.1", "Charaktertreue und Fail-RP",
               "Ein zuvor vorsichtiger Charakter wird ohne RP-Ereignis plötzlich furchtlos, allwissend und aggressiv, nur damit der Spieler eine Auseinandersetzung gewinnt. Was ist entscheidend?",
               {
                   "a": "Der Wechsel ist zulässig, weil Spieler die Persönlichkeit jederzeit frei ändern können.",
                   "b": "Es ist nur ein Problem, wenn andere Spieler sich darüber beschweren.",
                   "c": "Ohne nachvollziehbaren RP-Grund ist der abrupte Wechsel mit glaubwürdiger Charakterentwicklung unvereinbar und kann Fail-RP sein.",
                   "d": "Die Änderung ist erlaubt, solange der Charakter dieselbe Kleidung trägt.",
               }, "c"),
            _q("§8.2, §19.1–§19.2", "NLR und RP-Flucht",
               "Nach einem Tod loggt sich ein Spieler aus, kehrt später zurück und nutzt die Erinnerung an den Kampf, um direkt Rache zu nehmen. Welche Aussage ist richtig?",
               {
                   "a": "Ausloggen setzt die NLR zurück, weil eine neue Sitzung begonnen hat.",
                   "b": "Die NLR betrifft nur die Rückkehr zum Ort, nicht die Erinnerung an den Tod.",
                   "c": "Die Handlung ist erlaubt, sofern mindestens 15 echte Minuten vergangen sind.",
                   "d": "Der Logout macht die Regel nicht unwirksam: keine Erinnerung an den Tod und 15 Minuten Sperre am Ereignisort; RP-Flucht bleibt verboten.",
               }, "d"),
            _q("§11.1–§11.5", "Geiselnahme: Grenzen und Ablauf",
               "Drei Polizeibeamte sind online. Eine Gruppe nimmt einen Cop und einen Zivilisten als Geiseln, verlangt 5.000 € für den Cop und 2.500 € für den Zivilisten, jeweils mit nur einer Forderung, und plant dieselbe Location nächste Runde erneut. Welche Bewertung trifft zu?",
               {
                   "a": "Die genannten Forderungsgrenzen und die Mindestzahl sind eingehalten; die geplante Wiederholung an derselben Location ist trotzdem untersagt.",
                   "b": "Die gesamte Geiselnahme ist automatisch unzulässig, da Cop-Geiseln nicht erlaubt sind.",
                   "c": "Die Geiselnahme ist vollständig regelkonform, weil die Forderungsgrenzen wichtiger als die Ortsregel sind.",
                   "d": "Nur die Forderung für den Zivilisten ist zulässig; für einen Cop gilt keine feste Grenze.",
               }, "a"),
            _q("§13.1–§13.2", "Peacetime und Deliktsschwere",
               "Bei zwei Cops im Dienst will eine Gruppe eine Geisel nehmen, behauptet aber, es sei nur ein kleiner illegaler Rollenspielmoment. Wie entscheidest du?",
               {
                   "a": "Die Gruppe darf beginnen und muss nur abbrechen, falls ein dritter Cop online kommt.",
                   "b": "Die Bezeichnung der Gruppe ist ausschlaggebend; eine Geiselnahme zählt damit als kleine Aktivität.",
                   "c": "Die Geiselnahme ist bei weniger als drei Cops als schweres Verbrechen verboten; die Einstufung richtet sich nach der tatsächlichen Handlung, nicht nach dem Etikett.",
                   "d": "Das Verbot gilt nur für Raub und Mord, Geiselnahmen sind ausgenommen.",
               }, "c"),
            _q("§12.1–§12.3", "Safezone-Ausnahme",
               "Ein ranghoher Teamler stimmt im Teamchat einem Angriff auf eine Safezone zu. Es gibt keine Freigabe durch Support. Was ist die richtige Bewertung?",
               {
                   "a": "Die Zustimmung reicht wegen des Rangs aus.",
                   "b": "Die Gewalt bleibt verboten; für die genannte Ausnahme verlangt das Regelwerk eine ausdrückliche Supportfreigabe.",
                   "c": "Die Freigabe kann nach dem Angriff durch einen Supporter rückwirkend ersetzt werden.",
                   "d": "Safezones schützen nur vor Waffen, nicht vor Festnahmen oder anderen Gewaltformen.",
               }, "b"),
            _q("§10.4, §11.4, §18.1", "Überfälle und Ausspielpflicht",
               "Eine Gruppe möchte einen Überfall starten, hat aber keine Absprache getroffen und plant, den Ablauf nach dem ersten Schuss direkt abzukürzen. Was muss vor dem Start geklärt sein?",
               {
                   "a": "Nur die maximale Beute; die Ausspielweise ist Privatsache der Gruppe.",
                   "b": "Ein Supporter muss jeden einzelnen Dialog vorab freigeben.",
                   "c": "Eine Absprache ist nur bei Geiseln erforderlich, nie bei Überfällen.",
                   "d": "Überfälle brauchen Absprache und realistisches RP; Situationen müssen vollständig ausgespielt werden.",
               }, "d"),
            _q("§14.1–§14.4", "Medic-RP",
               "Während ein Gefecht noch aktiv ist, heilt ein Medic denselben Verletzten zum zweiten Mal, damit dieser sofort zurückkämpfen kann. Welche Regelkombination greift?",
               {
                   "a": "Doppelte Heilung ist nur verboten, wenn der Verletzte sie ablehnt.",
                   "b": "Medic-RP hat Vorrang, deshalb ist Heilung in laufenden Gefechten ausdrücklich erlaubt.",
                   "c": "Die Heilung während des aktiven Gefechts und die doppelte Heilung verstoßen gegen die genannten Medic-Regeln; Verletzungen müssen realistisch ausgespielt werden.",
                   "d": "Es gibt keine Einschränkung, sobald der Medic die Behandlung IC ankündigt.",
               }, "c"),
            _q("§25.1–§25.3", "Verkehr und Unfälle",
               "Es kommt zu einem hörbaren Crash, während mindestens ein ADACler im Dienst ist. Der Fahrer möchte sofort weiterfahren, weil er den Unfall als Zeitverlust betrachtet. Was ist richtig?",
               {
                   "a": "Die normale StVO und angepasste Fahrweise gelten; die Unfallsituation muss bei hörbarem Crashsound unter der genannten ADAC-Bedingung ausgespielt werden.",
                   "b": "Ein Crash muss nur ausgespielt werden, wenn ein Spieler sichtbar verletzt ist.",
                   "c": "Nur der ADAC entscheidet, ob die StVO im RP gilt.",
                   "d": "Die Ausspielpflicht gilt ausschließlich bei absichtlich verursachten Unfällen.",
               }, "a"),
            _q("§18.1–§18.5, §23.1", "Beweissicherung und RP-Fortsetzung",
               "Du beobachtest während einer laufenden Szene einen wahrscheinlichen Regelverstoß. Es besteht kein technischer oder realer Notfall. Wie gehst du vor?",
               {
                   "a": "Du beendest die Szene sofort und verliest die betroffene Regel.",
                   "b": "Du verhängst sofort eine endgültige Strafe, um die Szene nicht fortführen zu müssen.",
                   "c": "Du lässt alle Beweise ungesichert, weil das RP Vorrang hat.",
                   "d": "Du führst die Situation weiter aus, sicherst danach Beweise und kontaktierst Support; ein RP-Abbruch ist nur bei technischen oder realen Notfällen zulässig.",
               }, "d"),
            _q("§6.1–§6.3, §16.2", "Sanktionen und Beweise",
               "Zwei Beschwerden widersprechen sich und es existiert nur ein kurzer, aus dem Kontext geschnittener Clip. Was ist vor einer Sanktion am wichtigsten?",
               {
                   "a": "Der ersten Beschwerde glauben, wenn sie von mehreren Personen unterstützt wird.",
                   "b": "Den Sachverhalt weiter prüfen, verfügbare Beweise im Kontext bewerten und eine verhältnismäßige Entscheidung nach dem Regelwerk treffen.",
                   "c": "Immer die niedrigste Sanktion vergeben und den Fall anschließend schließen.",
                   "d": "Eine Sanktion nach Sympathie erteilen, solange die Entscheidung im Ermessensspielraum liegt.",
               }, "b"),
            _q("§5.1–§5.3 Discord-Regelwerk, §23.2", "Interessenkonflikte",
               "Eine Beschwerde betrifft einen engen Freund von dir. Der Freund fordert, dass du den Fall allein bearbeitest, weil du seine Sicht kennst. Was ist professionell?",
               {
                   "a": "Den Fall allein übernehmen, weil persönliche Kenntnis die Prüfung beschleunigt.",
                   "b": "Die Beschwerde direkt zurückweisen, falls der Freund seinen Fehler zugibt.",
                   "c": "Den Konflikt offenlegen und eine unabhängige Prüfung ermöglichen, statt die Entscheidung durch persönliche Bindung zu beeinflussen.",
                   "d": "Die Angelegenheit im allgemeinen Teamchat zur Abstimmung stellen und den Mehrheitsentscheid übernehmen.",
               }, "c"),
            _q("§18.3, §23.2–§23.3, Discord-Regelwerk §6.3", "Unvollständiger Sachverhalt",
               "Ein Nutzer verlangt eine harte Strafe; seine Meldung enthält jedoch weder genaue Zeitangabe noch vollständige Belege. Was ist der beste nächste Schritt?",
               {
                   "a": "Die fehlenden Angaben zielgerichtet anfordern, die Entscheidung bis zur ausreichenden Sachverhaltsklärung zurückstellen und nicht aufgrund von Druck sanktionieren.",
                   "b": "Die härteste Sanktion vorläufig vergeben und die Beweislage später prüfen.",
                   "c": "Den Nutzer zur öffentlichen Diskussion auffordern, damit die Community den Fall entscheidet.",
                   "d": "Den Fall automatisch ablehnen, da nur vollständige Videoaufnahmen als Beweis gelten.",
               }, "a"),
            _q("§6.1–§6.3, §18.3", "Team-Warn und Dokumentation",
               "Du hältst eine Team-Warn für notwendig. Welche Grundlage erlaubt eine sachliche und nachvollziehbare Entscheidung am ehesten?",
               {
                   "a": "Eine allgemeine Einschätzung, dass die Person häufig schwierig ist.",
                   "b": "Eine private Unterhaltung ohne Bezug zum konkreten Vorfall.",
                   "c": "Die Anzahl der Personen, die dieselbe Sanktion fordern.",
                   "d": "Ein konkreter Sachverhalt mit relevanten Belegen und einer nachvollziehbaren Begründung; die Maßnahme muss zum Verstoß passen.",
               }, "d"),
            _q("§5.1 Discord-Regelwerk", "Doxing: Reaktion",
               "Ein Mitglied veröffentlicht die reale Adresse eines anderen Nutzers. Der Inhalt verbreitet sich bereits in mehreren Kanälen. Wie gehst du als Teamler vor?",
               {
                   "a": "Die Adresse in einem privaten Teamkanal nochmals posten, damit alle sie erkennen.",
                   "b": "Die Verbreitung stoppen, Daten nicht erneut teilen, Belege vertraulich sichern und den zuständigen Verantwortlichen melden; das Regelwerk nennt dafür einen permanenten Bann und eine Meldung an Discord.",
                   "c": "Nur den ursprünglichen Beitrag löschen und den Vorfall nicht dokumentieren.",
                   "d": "Die betroffene Person selbst entscheiden lassen, ob die Regel gelten soll.",
               }, "b"),
            _q("§3.3 Discord-Regelwerk", "Aufnahme und Einwilligung",
               "Du möchtest eine Sprachunterhaltung dokumentieren. Alle außer einer anwesenden Person haben ausdrücklich zugestimmt. Darf die Aufnahme erfolgen?",
               {
                   "a": "Ja, sofern das Material nicht öffentlich gepostet wird.",
                   "b": "Ja, wenn ein Teamler die Aufnahme später auswertet.",
                   "c": "Nein. Die Regel verlangt die ausdrückliche Einwilligung aller anwesenden Gesprächsteilnehmer.",
                   "d": "Ja, wenn die nicht einwilligende Person nur zuhört.",
               }, "c"),
            _q("§2.5, §5 Discord-Regelwerk", "Vertrauliche Inhalte",
               "Ein Teammitglied erhält einen internen, noch unveröffentlichten Konzeptentwurf und möchte ihn einem befreundeten Creator für Feedback schicken. Was ist richtig?",
               {
                   "a": "Nicht ohne Erlaubnis weitergeben: Leaks interner Updates, Konzepte und vertraulicher Inhalte sind untersagt.",
                   "b": "Weitergeben ist zulässig, solange der Creator verspricht, nichts zu veröffentlichen.",
                   "c": "Es ist erlaubt, wenn der Entwurf keine vollständigen Spielernamen enthält.",
                   "d": "Interne Inhalte dürfen extern geteilt werden, sobald ein Teammitglied zugestimmt hat, auch ohne Berechtigung.",
               }, "a"),
            _q("§2.1–§2.5 Discord-Regelwerk", "Kanalnutzung und Werbung",
               "Ein Spieler veröffentlicht dieselbe Eigenwerbung mehrfach in Supportkanälen und verschickt sie zusätzlich ungefragt per Direktnachricht. Welche Einordnung ist korrekt?",
               {
                   "a": "Nur die Nachrichten im Supportkanal sind problematisch; DMs sind privat und vom Regelwerk ausgenommen.",
                   "b": "Es ist erlaubt, wenn jede Nachricht eine andere Formulierung besitzt.",
                   "c": "Werbung ist nur dann verboten, wenn Links auf kommerzielle Produkte führen.",
                   "d": "Die vorgesehene Kanalnutzung ist einzuhalten; Spam und unaufgeforderte Eigenwerbung sind auch per DM untersagt.",
               }, "d"),
            _q("§4.3 Discord-Regelwerk", "Pings und Ghost-Pings",
               "Ein Teamler erwähnt ohne sachlichen Anlass mehrfach Leitungspersonen, löscht die Nachrichten direkt wieder und behauptet danach, es habe keinen Ping gegeben. Wie ist das zu bewerten?",
               {
                   "a": "Zulässig, weil gelöschte Nachrichten grundsätzlich nicht mehr als Ping zählen.",
                   "b": "Grundlose Erwähnungen und Ghost-Pings sind untersagt; der Rang des Absenders ändert daran nichts.",
                   "c": "Nur @everyone ist untersagt, gezielte Personen-Pings sind immer erlaubt.",
                   "d": "Es zählt nur, ob die Leitungsperson online war.",
               }, "b"),
            _q("§6.1–§6.3 Discord-Regelwerk", "Ticketmissbrauch",
               "Ein Nutzer öffnet mehrere Tickets mit absichtlich falschen Angaben, um schneller an eine Antwort zu kommen. Zusätzlich ignoriert er die sachliche Anweisung des Supporters. Was trifft zu?",
               {
                   "a": "Falschangaben sind zulässig, wenn der eigentliche Wunsch berechtigt ist.",
                   "b": "Supportanweisungen gelten nur dann, wenn der Nutzer ihnen zustimmt.",
                   "c": "Ticket-Spam, Falschangaben und Missbrauch können sanktioniert werden; Supportanweisungen sind zu befolgen.",
                   "d": "Der Supporter muss alle Tickets offen halten, bis die Community entschieden hat.",
               }, "c"),
            _q("§17.2, §23.2 Discord-Regelwerk §5.3", "Cheats, Exploits und Plattformregeln",
               "Ein Spieler meldet, einen Exploit gefunden zu haben, und demonstriert ihn aktiv, um Vorteile zu erhalten. Welche Aussage ist korrekt?",
               {
                   "a": "Das ist nur dann relevant, wenn andere Spieler bereits Schaden gemeldet haben.",
                   "b": "Ein Exploit ist erlaubt, solange er nur einmal verwendet wird.",
                   "c": "Zuerst muss die Community abstimmen, ob der Vorteil zu groß ist.",
                   "d": "Hacking, Cheating und Exploits führen laut RP-Regelwerk zum permanenten Bann; die Maßnahme ist durch das Serverteam umzusetzen und zu dokumentieren.",
               }, "d"),
            _q("§1.2, §1.3, §5.2 Discord-Regelwerk", "Toxisches Verhalten und DMs",
               "Ein Nutzer beleidigt ein Mitglied wiederholt in Direktnachrichten und droht ihm wegen eines serverbezogenen Konflikts. Er behauptet, DMs seien grundsätzlich privat und könnten daher nie sanktioniert werden. Was gilt?",
               {
                   "a": "Serverbezogene Belästigungen oder Drohungen per DM können nach dem Discord-Regelwerk wie Serververstöße behandelt werden.",
                   "b": "Nur Nachrichten in öffentlichen Serverkanälen können jemals berücksichtigt werden.",
                   "c": "Das Team darf ausschließlich dann reagieren, wenn der Absender die DM selbst veröffentlicht.",
                   "d": "Private Nachrichten sind stets außerhalb der Serverregeln, auch wenn sie direkt mit einem Serverkonflikt zusammenhängen.",
               }, "a"),
        ],
    },
    "leadership": {
        "label": "Leitungsebene",
        "description": "Sehr anspruchsvoller Führungstest mit komplexen Fallabwägungen, Regelwerkskenntnis, Datenschutz, Personalverantwortung und nachvollziehbarer Entscheidungsführung.",
        "pass_percent": 90,
        "questions": [
            _q("Bewerbungsablauf, §6.1–§6.3", "Einstellungsentscheidung",
               "Ein Bewerber erzielt im Online-Test 97 %, zeigt im Gespräch aber, dass er sensible Daten aus Neugier abfragen und eigenständig Strafen ohne Prüfung vergeben würde. Was ist die richtige Entscheidung?",
               {
                   "a": "Einstellen, weil ein hoher Testwert fachliche Zweifel vollständig aufhebt.",
                   "b": "Automatisch die höchste Rolle vergeben, damit die Leistung belohnt wird.",
                   "c": "Das Testergebnis als ein Kriterium behandeln; Eignung, Gespräch und vorgesehene Freigaben weiter prüfen und bei diesen Defiziten nicht automatisch einstellen.",
                   "d": "Dem Bewerber Zugriff auf echte Personalakten geben, um seine Eignung praktisch zu testen.",
               }, "c"),
            _q("§23.2–§23.3, Discord-Regelwerk §6.3", "Interessenkonflikte",
               "Gegen einen engen Freund gibt es eine schwere Beschwerde. Du bist die ranghöchste gerade verfügbare Person und könntest technisch allein entscheiden. Was ist am belastbarsten?",
               {
                   "a": "Du legst die persönliche Verbindung offen, sicherst den Fall und organisierst soweit möglich eine unabhängige Prüfung; Dringlichkeit rechtfertigt keine voreingenommene Entscheidung.",
                   "b": "Du entscheidest allein, weil deine hohe Stellung jeden Interessenkonflikt ausgleicht.",
                   "c": "Du lehnst die Beschwerde ab, bis die betroffene Person online ist.",
                   "d": "Du veröffentlichst alle Belege, damit die Community die Unabhängigkeit der Entscheidung kontrollieren kann.",
               }, "a"),
            _q("§5.1 Discord-Regelwerk, §18.3", "Datenschutzvorfall",
               "Ein privater Screenshot mit Realname und Telefonnummer eines Mitglieds wurde versehentlich in einem öffentlichen Kanal gepostet. Welche Reihenfolge ist am sinnvollsten?",
               {
                   "a": "Den Screenshot in einen anderen Kanal kopieren und dort nach weiteren Informationen fragen.",
                   "b": "Zuerst eine öffentliche Stellungnahme mit allen Details veröffentlichen, danach den Beitrag entfernen.",
                   "c": "Nur die betroffene Person bitten, nicht weiter darüber zu sprechen.",
                   "d": "Die weitere Verbreitung stoppen, den Zugriff begrenzen, nötige Belege vertraulich sichern und den Vorfall über den zuständigen Weg bearbeiten, ohne die Daten erneut zu verbreiten.",
               }, "d"),
            _q("Discord-Regelwerk §2.5, §5.1", "Interne Daten und Weitergabe",
               "Eine externe Person bietet an, die Serverentwicklung kostenlos zu unterstützen, verlangt dafür aber die vollständigen internen Personalakten und unveröffentlichten Konzepte. Was ist korrekt?",
               {
                   "a": "Die Daten teilen, wenn der externe Helfer eine Verschwiegenheitserklärung verspricht.",
                   "b": "Nicht weitergeben, solange keine ausdrücklich autorisierte, notwendige und eng begrenzte Freigabe besteht; vertrauliche Konzepte und persönliche Daten sind zu schützen.",
                   "c": "Nur die Personalakten senden, falls die Discord-IDs entfernt wurden.",
                   "d": "Den Zugriff dauerhaft freigeben, wenn dadurch Arbeit der Leitung eingespart wird.",
               }, "b"),
            _q("§5.1–§5.3 Discord-Regelwerk", "Mindestberechtigung",
               "Mehrere Teamler benötigen für unterschiedliche Aufgaben Zugriff auf das Dashboard. Welche Rechtevergabe ist am sichersten?",
               {
                   "a": "Allen Teamlern vorsorglich Administratorrechte geben, um spätere Verzögerungen zu vermeiden.",
                   "b": "Die Rechte nach Rang vergeben und einen einmal gewährten Vollzugriff nicht mehr überprüfen.",
                   "c": "Nur die für die Aufgabe erforderlichen Rechte vergeben, Berechtigungen nachvollziehbar prüfen und unnötige Zugriffe vermeiden.",
                   "d": "Zugangsdaten gemeinsam nutzen, damit die Zugriffsverwaltung einfacher wird.",
               }, "c"),
            _q("§6.1–§6.3, §18.3", "Dokumentation einer Personalentscheidung",
               "Du bestätigst eine wichtige Degradierung mündlich im Voice. Es existiert ein konkreter Vorfall, aber kein schriftlicher Grund und keine nachvollziehbare Aktennotiz. Wie sollte die Entscheidung behandelt werden?",
               {
                   "a": "Die Begründung, relevanten Fakten und verantwortlichen Entscheider zeitnah sachlich dokumentieren; private Werturteile ersetzen keine Belege.",
                   "b": "Die Entscheidung nicht dokumentieren, damit die Leitung flexibel bleibt.",
                   "c": "Die Personalakte mit privaten Meinungen im öffentlichen Team-Updates-Kanal ersetzen.",
                   "d": "Die Entscheidung nur dokumentieren, wenn die betroffene Person sie später anficht.",
               }, "a"),
            _q("§6.2–§6.3, §23.2–§23.3", "Verhältnismäßigkeit und Druck",
               "Eine bekannte Person fordert öffentlich einen permanenten Bann gegen einen Gegner. Die Beweislage zeigt bisher nur einen möglichen, geringfügigen Erstverstoß. Wie entscheidest du?",
               {
                   "a": "Permanent bannen, damit die Leitung Autorität zeigt.",
                   "b": "Die Forderung übernehmen, wenn mehrere Zuschauer zustimmen.",
                   "c": "Den Gegner ebenfalls sanktionieren, um unparteiisch zu wirken.",
                   "d": "Den Sachverhalt unabhängig prüfen und eine verhältnismäßige Maßnahme wählen; öffentliche Forderungen sind kein Ersatz für Belege.",
               }, "d"),
            _q("§23.1–§23.3, Discord-Regelwerk §6.3", "Selbstjustiz und Eskalation",
               "Ein Teamler wird im RP provoziert und will den Spieler privat bestrafen, indem er absichtlich dessen RP-Situation zerstört. Was musst du als Leitung priorisieren?",
               {
                   "a": "Die Handlung dulden, wenn der Spieler vorher selbst provoziert hat.",
                   "b": "Selbstjustiz unterbinden, IC/OOC-Konflikt trennen und den Vorfall mit Belegen über den vorgesehenen Weg prüfen lassen.",
                   "c": "Die betroffene Person öffentlich bloßstellen, damit andere daraus lernen.",
                   "d": "Den Teamler automatisch dauerhaft entlassen, bevor irgendein Sachverhalt geprüft wurde.",
               }, "b"),
            _q("§12.1–§12.3", "Safezone: keine Rang-Ausnahme",
               "Die Leitung genehmigt im privaten Teamchat einen Angriff auf das Krankenhaus; eine ausdrückliche Freigabe durch Support liegt nicht vor. Welcher Grundsatz gilt?",
               {
                   "a": "Die Leitung kann jede Safezone-Ausnahme selbst erteilen.",
                   "b": "Die Regel betrifft nur öffentliche Kämpfe; ein geheimer Angriff ist ausgenommen.",
                   "c": "Die genannte Ausnahme setzt eine ausdrückliche Supportfreigabe voraus; interne Zustimmung allein hebt das Gewaltverbot nicht auf.",
                   "d": "Die Freigabe ist nicht erforderlich, falls die Beteiligten eine gute RP-Story versprechen.",
               }, "c"),
            _q("§13.1–§13.2", "Peacetime: tatsächliche Einordnung",
               "Zwei Cops sind im Dienst. Eine Führungskraft bezeichnet eine geplante Geiselnahme als kleines illegales RP, um die Peacetime-Regel zu umgehen. Welche Entscheidung ist regelkonform?",
               {
                   "a": "Die tatsächliche Handlung zählt: Eine Geiselnahme bleibt bei weniger als drei Cops verboten, auch wenn sie anders benannt wird.",
                   "b": "Die Geiselnahme ist zulässig, wenn die Führungskraft den Ablauf überwacht.",
                   "c": "Die Regel gilt nur für öffentliche Raube und nicht für geplante Geiselnahmen.",
                   "d": "Der Fall ist erlaubt, sobald der Täter keine Geldforderung stellt.",
               }, "a"),
            _q("§11.1–§11.5", "Geiselnahme: Mehrere Bedingungen",
               "Drei Polizeibeamte sind online. Für eine Cop-Geisel werden 5.000 €, für eine zivile Geisel 2.500 € und jeweils genau eine Forderung angesetzt; dieselbe Location soll in der nächsten Geiselnahme wieder benutzt werden. Welche Aussage ist korrekt?",
               {
                   "a": "Alle Vorgaben sind eingehalten, weil Mindestzahl und Geldgrenzen stimmen.",
                   "b": "Die Forderungen sind unzulässig, weil pro Geisel gar keine Forderung erlaubt ist.",
                   "c": "Die Location darf wiederverwendet werden, wenn andere Täter beteiligt sind.",
                   "d": "Mindestzahl, genannte Höchstbeträge und eine Forderung pro Geisel sind eingehalten; eine Wiederholung an derselben Location bleibt untersagt.",
               }, "d"),
            _q("§8.2, §19.1–§19.2", "NLR nach Crash oder Disconnect",
               "Ein Spieler stirbt, hat einen technischen Crash und kehrt vier Minuten später zurück. Er behauptet, der Crash lösche seine RP-Verpflichtungen und er dürfe den Täter nun aufgrund seiner Erinnerung verfolgen. Was ist richtig?",
               {
                   "a": "Der Crash gestattet jede Fortsetzung mit vollständigem Vorwissen.",
                   "b": "Die Situation ist nach dem Crash realistisch fortzusetzen; NLR bleibt bestehen und verlangt keine Erinnerung an den Tod sowie 15 Minuten Sperre am Ereignisort.",
                   "c": "NLR gilt nur, wenn der Spieler freiwillig ausloggt.",
                   "d": "Eine technische Unterbrechung verwandelt sämtliche OOC-Informationen in IC-Wissen.",
               }, "b"),
            _q("§8.1, §20.1–§20.2, §21.1–§21.3", "NVL und Zwangs-RP",
               "Ein bewaffneter Täter bedroht deinen Charakter. Du willst aus Trotz angreifen und gleichzeitig festlegen, dass ein anderer Charakter sofort aufgibt. Welche Bewertung verbindet die Regeln korrekt?",
               {
                   "a": "Beides ist zulässig, wenn die Szene dadurch schneller endet.",
                   "b": "Nur die festgelegte Reaktion ist problematisch; NVL gilt ausschließlich bei tatsächlichem Tod.",
                   "c": "Unrealistischer Heldenmut kann gegen NVL/Fear-RP verstoßen; außerdem darfst du die Gefühle und Entscheidungen anderer Charaktere nicht bestimmen.",
                   "d": "Solange beide Parteien sprechen, gelten Fear-RP und Zwangs-RP nicht.",
               }, "c"),
            _q("§7.4, §4.1–§4.4", "Informationsgrenzen",
               "Eine Leitungsperson sieht OOC einen privaten Plan, der im RP noch nicht bekannt ist. Sie möchte eine Fraktion IC vorsorglich warnen und begründet dies damit, den Server vor einem Regelverstoß zu schützen. Was ist korrekt?",
               {
                   "a": "OOC-Wissen darf nur dann in IC überführt werden, wenn es nützlich ist.",
                   "b": "Führungspersonen sind von Metagaming ausgenommen, wenn es um Sicherheit geht.",
                   "c": "Die Warnung ist erlaubt, wenn die betroffene Fraktion ohnehin verdächtig ist.",
                   "d": "Die Trennung bleibt bestehen: OOC-Information darf nicht ohne zulässigen IC-Weg genutzt werden; ein Verdacht ist über den vorgesehenen OOC-/Supportweg zu klären.",
               }, "d"),
            _q("§21.2–§21.3, §22.1–§22.3", "Zwangs-RP und RP-Qualität",
               "In einer Verhandlung beschreibt ein Spieler seine eigene Handlung und schreibt zusätzlich, dass der Gegencharakter dadurch Angst bekommt und alle Forderungen akzeptiert. Wie sollte die Szene korrigiert werden?",
               {
                   "a": "Die Handlung so formulieren, dass sie ausspielbar ist und dem anderen Charakter eine tatsächliche Reaktionsmöglichkeit lässt; Gefühle und Entscheidungen nicht vorgeben.",
                   "b": "Die Szene automatisch beenden, da Verhandlungen keine zulässigen RP-Situationen sind.",
                   "c": "Die Beschreibung akzeptieren, wenn die geforderte Reaktion realistisch wäre.",
                   "d": "Die Reaktion des Gegencharakters durch eine Teamentscheidung im IC erzwingen.",
               }, "a"),
            _q("§14.1–§14.4", "Medic und aktives Gefecht",
               "Ein verletzter Spieler wird im aktiven Gefecht von einem Medic geheilt und kurz darauf nochmals von einem zweiten Medic behandelt. Eine Führungskraft fordert die Heilungen, um die Einsatzdauer zu verkürzen. Wie ist das zu bewerten?",
               {
                   "a": "Die Führungskraft darf Medic-Regeln bei dienstlichen Einsätzen aufheben.",
                   "b": "Medic-RP hat Vorrang und macht jede Heilung innerhalb des Gefechts zulässig.",
                   "c": "Heilung während aktiver Gefechte und doppelte Heilung sind untersagt; dienstlicher Zeitdruck hebt die Regeln nicht auf.",
                   "d": "Nur die zweite Heilung ist problematisch, wenn der erste Medic die Behandlung beendet hat.",
               }, "c"),
            _q("§25.1–§25.3, §17.3", "Verkehr und Blitzer",
               "Bei mindestens einem ADACler im Dienst kommt es zu einem hörbaren Crash. Außerdem wurde ein Blitzer so platziert, dass Fahrzeuge ohne realistische Möglichkeit ausweichen zu können in Gefahr geraten. Welche Vorgehensweise passt zu den Regeln?",
               {
                   "a": "Nur der Blitzer muss geprüft werden; ein Crashsound hat keine Bedeutung, wenn niemand sofort aussteigt.",
                   "b": "Unfallsituation realistisch ausspielen, StVO und situationsangepasste Fahrweise beachten sowie die unrealistische Blitzerplatzierung korrigieren lassen.",
                   "c": "Blitzer dürfen überall stehen, wenn der Fahrer zu schnell fährt.",
                   "d": "Der Fahrer kann den Unfall abbrechen, sofern er die Kosten übernimmt.",
               }, "b"),
            _q("§18.2–§18.5, §16.2", "Beschwerdeweg nach dem RP",
               "Während RP laufen mehrere offensichtliche Regelvorwürfe auf. Es besteht kein Notfall. Eine Partei fordert, die Situation zu stoppen und den Streit im OOC-Voice auszutragen. Welche Reihenfolge ist korrekt?",
               {
                   "a": "Sofort stoppen; OOC-Diskussionen sind der bevorzugte Weg, um Missverständnisse zu vermeiden.",
                   "b": "Die lautere Partei entscheiden lassen und Beweise erst bei einer Ablehnung suchen.",
                   "c": "Die Szene ignorieren und später ohne Begründung irgendeine Sanktion erteilen.",
                   "d": "RP vollständig fortführen, relevante Beweise sichern und danach Support kontaktieren; Regelklärungen und Beschwerden nicht IC oder mitten in der Szene austragen.",
               }, "d"),
            _q("§26.1–§26.2, §1.5", "Regeländerungen",
               "Eine Regel wurde nachweislich geändert. Ein Spieler sagt, er habe die frühere Fassung gelernt und müsse deshalb die aktuelle Regel nicht befolgen. Welche Aussage trifft zu?",
               {
                   "a": "Die Serverleitung kann das Regelwerk anpassen; Unwissenheit schützt nicht und mit dem Beitritt werden die geltenden Regeln anerkannt.",
                   "b": "Es zählt immer ausschließlich die Fassung, die der Spieler persönlich gelesen hat.",
                   "c": "Änderungen gelten nur für neue Mitglieder.",
                   "d": "Eine Regeländerung wird erst bindend, wenn jeder einzelne Spieler sie schriftlich bestätigt.",
               }, "a"),
            _q("§16.1–§16.3, Discord-Regelwerk §6.1–§6.3", "Support und Tickets",
               "Eine Führungskraft hält eine Support-Anweisung für falsch und möchte sie öffentlich im Ticket ignorieren, bis die Nutzer abstimmen. Was ist angemessen?",
               {
                   "a": "Die Anweisung darf im öffentlichen Streit ignoriert werden, wenn die Führungskraft höher steht.",
                   "b": "Das Ticket muss offengehalten werden, bis alle Discord-Mitglieder einverstanden sind.",
                   "c": "Support-Anweisungen sind bindend; Beschwerden über die Entscheidung gehören in den vorgesehenen Weg, und das Ticketsystem darf nicht missbraucht werden.",
                   "d": "Die Führungskraft darf das Ticket ohne Antwort löschen, um die Diskussion zu beenden.",
               }, "c"),
            _q("§5.1 Discord-Regelwerk", "Doxing und schwerer Schutzverstoß",
               "Nach einem Doxing-Vorfall schlägt ein Teammitglied vor, die Adresse zu spiegeln, um die betroffene Person zu schützen und Beweise zu sichern. Wie reagierst du?",
               {
                   "a": "Du spiegelst die Information in einem privaten Kanal, damit ein vollständiger Beleg vorliegt.",
                   "b": "Du verhinderst jede weitere Verbreitung, sicherst nur erforderliche Nachweise vertraulich und veranlasst die im Regelwerk vorgesehene Eskalation; Doxing ist dort mit permanentem Bann und Meldung an Discord verknüpft.",
                   "c": "Du wartest auf die Zustimmung des Täters, bevor du handelst.",
                   "d": "Du erlaubst es, wenn die Adresse nach einer Stunde gelöscht wird.",
               }, "b"),
            _q("§3.3 Discord-Regelwerk", "Einwilligung bei Aufnahmen",
               "Bei einem internen Gespräch stimmen fünf von sechs anwesenden Personen einer Aufnahme zu. Die sechste schweigt, und eine Leitungsperson will die Datei nur für ein internes Verfahren nutzen. Was ist zulässig?",
               {
                   "a": "Die Aufnahme ist wegen der internen Nutzung automatisch erlaubt.",
                   "b": "Eine einfache Mehrheit reicht, wenn die Gesprächsleitung zustimmt.",
                   "c": "Die Aufnahme ist nur verboten, falls sie später öffentlich veröffentlicht wird.",
                   "d": "Ohne ausdrückliche Einwilligung aller Anwesenden darf das Gespräch nicht aufgenommen oder gestreamt werden.",
               }, "d"),
            _q("§2.5 Discord-Regelwerk", "Leak interner Informationen",
               "Eine Leitungsperson will ein unveröffentlichtes Update an einen befreundeten Creator senden, damit dieser bereits ein Werbevideo vorbereiten kann. Eine ausdrücklich autorisierte Freigabe besteht nicht. Was gilt?",
               {
                   "a": "Nicht weitergeben: unveröffentlichte Updates, Konzepte und vertrauliche Informationen dürfen ohne Erlaubnis nicht geleakt werden.",
                   "b": "Weitergabe ist unproblematisch, wenn der Creator zur Verschwiegenheit aufgefordert wird.",
                   "c": "Nur vollständige Dokumente gelten als Leaks, einzelne Ausschnitte sind immer erlaubt.",
                   "d": "Die Leitung darf interne Inhalte ohne Einschränkung an externe Kontakte geben.",
               }, "a"),
            _q("§2.3 Discord-Regelwerk", "Werbung und Direktnachrichten",
               "Ein Creator bewirbt sein externes Projekt nicht nur im Chat, sondern versendet die Werbung zudem unaufgefordert an Mitglieder per DM. Er behauptet, private Nachrichten seien keine Eigenwerbung auf dem Server. Welche Aussage ist korrekt?",
               {
                   "a": "DM-Werbung ist erlaubt, wenn sie nur an wenige Personen geschickt wird.",
                   "b": "Es ist nur verboten, wenn die Werbung einen anderen Discord-Server nennt.",
                   "c": "Unaufgeforderte Eigenwerbung ist sowohl öffentlich als auch per Direktnachricht untersagt.",
                   "d": "Mitglieder dürfen für externe Projekte werben, wenn sie gleichzeitig Bochum RP erwähnen.",
               }, "c"),
            _q("§2.1–§2.2, §4.3 Discord-Regelwerk", "Spam und Erwähnungen",
               "Ein Teamler erzeugt absichtlich einen öffentlichen Konflikt, flutet themenfremde Kanäle, pingt Leitungspersonen ohne Anlass und löscht die Erwähnungen wieder. Welche Bewertung ist am korrektesten?",
               {
                   "a": "Alles ist zulässig, weil Teamler Konflikte öffentlich klären dürfen.",
                   "b": "Kanalnutzung, Spam, Provokation/Drama sowie grundlose und gelöschte Erwähnungen können mehrere Discord-Regeln gleichzeitig verletzen.",
                   "c": "Nur @everyone-Pings sind relevant; einzelne Erwähnungen sind nicht geregelt.",
                   "d": "Gelöschte Nachrichten schließen eine Prüfung grundsätzlich aus.",
               }, "b"),
            _q("§5.3 Discord-Regelwerk", "Discord-ToS und Community-Richtlinien",
               "Ein Mitglied behauptet, lokale Serverregeln dürften offizielle Discord-Nutzungsbedingungen außer Kraft setzen. Wie lautet die korrekte Einordnung?",
               {
                   "a": "Lokale Regeln haben immer Vorrang vor Discord-Richtlinien.",
                   "b": "Discord-Richtlinien gelten nur für Personen mit Teamrollen.",
                   "c": "Die Discord-Regeln sind nur bei Beschwerden anwendbar.",
                   "d": "Offizielle Discord-ToS und Community-Richtlinien gelten uneingeschränkt zusätzlich zum Serverregelwerk.",
               }, "d"),
            _q("§17.2, Discord-Regelwerk §5.3", "Cheating und Exploits",
               "Ein Spieler demonstriert einen Exploit, um seine Fraktion zu bevorteilen, und sagt, die Leitung solle erst abwarten, ob sich jemand beschwert. Was ist die richtige Reaktion?",
               {
                   "a": "Den Exploit dulden, wenn kein Geld im Spiel betroffen ist.",
                   "b": "Den Spieler nur mündlich erinnern, da ein permanenter Bann unverhältnismäßig wäre.",
                   "c": "Relevante Fakten sichern und die Regel konsequent anwenden: Hacking, Cheating und Exploits führen laut RP-Regelwerk zum permanenten Bann; offizielle Plattformregeln gelten zusätzlich.",
                   "d": "Den Exploit selbst nachnutzen, um seine Auswirkungen besser zu verstehen.",
               }, "c"),
            _q("§17.4", "Scamming beim Handel",
               "Eine Fraktion tauscht Gegenstände aus und verschweigt absichtlich einen vereinbarten Teil der Gegenleistung, nachdem der Handel abgeschlossen ist. Welche Regel ist einschlägig?",
               {
                   "a": "Scamming beim Handel ist verboten; der Vorfall sollte anhand des tatsächlichen Handelsverlaufs geprüft und nicht durch Selbstjustiz beantwortet werden.",
                   "b": "Es ist erlaubt, wenn beide Parteien nicht schriftlich unterschrieben haben.",
                   "c": "Scamming ist nur verboten, wenn echtes Geld verwendet wird.",
                   "d": "Die betroffene Fraktion darf sofort im Gegenzug Eigentum der Gegenseite einbehalten.",
               }, "a"),
            _q("§15.1 RP-Regelwerk, Discord-Regelwerk §3.1", "Soundboards und Voicechanger",
               "Ein Mitglied nutzt einen Soundboard-Spam und einen extrem verzerrenden Voicechanger, um eine Support-Unterhaltung zu stören. Es behauptet, nur die Kommunikation im RP sei geregelt. Was gilt?",
               {
                   "a": "Die Nutzung ist immer erlaubt, sobald kein Musikstück abgespielt wird.",
                   "b": "Die Regelwerke untersagen Soundboards bzw. störende Audio-Manipulationen; das Verhalten kann sowohl RP- als auch Discord-Kommunikationsregeln berühren.",
                   "c": "Nur andere Nutzer dürfen Audioverhalten melden, Teamler dürfen nicht eingreifen.",
                   "d": "Die Regel gilt nur für öffentliche Textkanäle.",
               }, "b"),
            _q("§17.3, §25.1–§25.2", "Realistische Verkehrssituation",
               "Ein Spieler platziert einen Blitzer an einer offensichtlich unrealistischen Stelle und begründet dies damit, dass die StVO nur im echten Straßenverkehr gelte. Was ist korrekt?",
               {
                   "a": "Blitzerplatzierung ist frei, solange eine Geschwindigkeitsüberschreitung vorliegt.",
                   "b": "Die StVO gilt nur für Polizei und ADAC.",
                   "c": "Blitzer müssen realistisch platziert werden; die StVO und eine an die RP-Situation angepasste Fahrweise gelten auf dem Server.",
                   "d": "Der Spieler darf die Platzierung selbst entscheiden, wenn der Blitzer keine Geldstrafe auslöst.",
               }, "c"),
            _q("§10.1–§10.4, §18.1", "Fraktions- und Berufs-RP",
               "Eine nicht genehmigte Organisation beginnt berufliches Einsatz-RP, führt einen Überfall ohne Absprache durch und verkürzt die Szene, sobald ein Ergebnis feststeht. Welche Gesamtbewertung ist richtig?",
               {
                   "a": "Das ist zulässig, wenn alle Beteiligten das Ergebnis akzeptieren.",
                   "b": "Nur die abgekürzte Szene ist relevant; Genehmigungen und Absprache sind freiwillig.",
                   "c": "Fraktionsregeln gelten nur für Gangs, nicht für andere Organisationen.",
                   "d": "Fraktionen/Organisationen benötigen offizielle Genehmigung, berufliches RP muss ernsthaft sein, Überfälle benötigen Absprache und jede Situation ist vollständig auszuspielen.",
               }, "d"),
            _q("§2.1–§2.3, §7.5, §22.1–§22.3", "Charakterentwicklung und RP-Qualität",
               "Ein Charakter besitzt laut Hintergrundgeschichte klare Schwächen und eine vorsichtige Persönlichkeit. Um eine Belohnung zu erhalten, ignoriert er plötzlich Verletzungen und handelt ohne RP-Grund völlig anders. Was ist die beste Beurteilung?",
               {
                   "a": "RP steht über Gewinn; Charaktere benötigen glaubwürdige Entwicklung, und ein unbegründeter Persönlichkeitswechsel oder unrealistisches Verhalten kann Fail-RP sein.",
                   "b": "Spieler dürfen ihre Charaktere in jeder Situation beliebig verändern, solange sie eine Belohnung erhalten.",
                   "c": "Nur die Belohnung ist relevant; Verletzungen haben keine RP-Auswirkungen.",
                   "d": "Eine Charaktergeschichte ist optional, wenn ein Teamler die Handlung genehmigt.",
               }, "a"),
            _q("§26.1–§26.2, §1.5 Discord-Regelwerk", "Verantwortung nach Regeländerung",
               "Die Serverleitung aktualisiert das Regelwerk. Ein langjähriges Teammitglied beruft sich auf seine frühere Praxis und erklärt, die neue Regel gelte für ihn bis zu einer persönlichen Unterweisung nicht. Was ist richtig?",
               {
                   "a": "Erfahrene Teammitglieder dürfen frühere Auslegungen vorübergehend weiter anwenden.",
                   "b": "Neue Regeln gelten nur bei ausdrücklicher Zustimmung aller Teamler.",
                   "c": "Das Teammitglied muss die jeweils geltenden Regeln einhalten; Änderungen sind vorbehalten und Unwissenheit schützt nicht vor Konsequenzen.",
                   "d": "Die Regeländerung betrifft nur Bewerber, nicht bestehende Teammitglieder.",
               }, "c"),
        ],
    },
}


def render_career_test(career_path: str) -> str:
    """Rendert Fragen und gemischte Antwortoptionen, niemals den Lösungsschlüssel."""
    career = APPLICATION_CAREERS.get(career_path)
    if not career:
        return ""

    groups: list[str] = []
    rng = SystemRandom()
    for index, question in enumerate(career["questions"], start=1):
        option_items = list(question["options"].items())
        rng.shuffle(option_items)
        options = "".join(
            (
                '<label class="flex items-start gap-2.5 rounded-xl border border-slate-200 '
                'dark:border-slate-700 p-3 cursor-pointer hover:border-indigo-400">'
                f'<input type="radio" name="answer_{index}" value="{escape(option_key)}" '
                'required class="mt-0.5 accent-indigo-600 shrink-0">'
                f'<span>{escape(option_text)}</span></label>'
            )
            for option_key, option_text in option_items
        )
        groups.append(
            '<fieldset class="rounded-2xl border border-slate-200 dark:border-slate-800 p-4 space-y-3">'
            f'<legend class="px-1 font-semibold text-sm">{index}. {escape(question["question"])}</legend>'
            f'<div class="grid gap-2">{options}</div></fieldset>'
        )
    return "".join(groups)


def grade_career_test(career_path: str, answers: Iterable[str]) -> dict:
    """Validiert und bewertet den vollständigen, serverseitig definierten Test."""
    career = APPLICATION_CAREERS.get(career_path)
    if not career:
        raise ValueError("Bitte eine gültige Laufbahn auswählen.")

    submitted = list(answers)
    questions = career["questions"]
    if len(submitted) != len(questions):
        raise ValueError(f"Bitte alle {len(questions)} Testfragen beantworten.")

    score = 0
    details = []
    for question, answer in zip(questions, submitted):
        answer = str(answer or "").strip().lower()
        if answer not in question["options"]:
            raise ValueError("Eine Testantwort ist ungültig oder fehlt.")
        correct = answer == question["correct"]
        score += int(correct)
        details.append(
            {
                "question": question["question"],
                "topic": question["topic"],
                "rule_refs": question["rule_refs"],
                "selected_answer": question["options"][answer],
                "correct_answer": question["options"][question["correct"]],
                "correct": correct,
            }
        )

    total = len(questions)
    percent = round((score / total) * 100) if total else 0
    return {
        "career_path": career_path,
        "career_label": career["label"],
        "score": score,
        "total": total,
        "percent": percent,
        "pass_percent": career["pass_percent"],
        "passed": percent >= career["pass_percent"],
        "details": details,
    }
