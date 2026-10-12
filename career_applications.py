"""Online-Eignungstests für die Pulse-Team-Bewerbung.

Die Antworten werden ausschließlich serverseitig bewertet. Der Bewerber erhält
beim Absenden keine Möglichkeit, einen Score oder den Bestehensstatus vorzugeben.
"""

from __future__ import annotations

from html import escape
from typing import Iterable


APPLICATION_CAREERS = {
    "low_team": {
        "label": "Low Team",
        "description": "Einstieg ins Team mit Fokus auf Support, Kommunikation und sichere Grundlagen.",
        "pass_percent": 50,
        "questions": [
            {
                "question": "Du kennst die Antwort auf eine Supportfrage nicht sicher. Was tust du?",
                "options": {
                    "a": "Du gibst eine plausible Antwort, damit das Ticket schnell geschlossen wird.",
                    "b": "Du ignorierst die Frage, bis jemand anderes antwortet.",
                    "c": "Du sagst transparent Bescheid und fragst bei einer zuständigen Person nach.",
                    "d": "Du bittest den Nutzer, die Frage nicht weiter zu stellen.",
                },
                "correct": "c",
            },
            {
                "question": "Wie gehst du mit internen Team- und Personaldaten um?",
                "options": {
                    "a": "Du teilst sie mit Freunden, wenn diese danach fragen.",
                    "b": "Du greifst nur bei dienstlichem Bedarf darauf zu und gibst sie nicht unbefugt weiter.",
                    "c": "Du veröffentlichst sie, wenn dadurch ein Konflikt schneller gelöst wird.",
                    "d": "Du speicherst sie privat, damit du sie später leichter findest.",
                },
                "correct": "b",
            },
            {
                "question": "Du beobachtest einen möglichen Regelverstoß. Was ist der richtige erste Schritt?",
                "options": {
                    "a": "Du hältst relevante Fakten fest und folgst dem vorgesehenen Meldeweg.",
                    "b": "Du veröffentlichst sofort eine Anschuldigung im allgemeinen Chat.",
                    "c": "Du verhängst eigenständig jede Sanktion, die dir passend erscheint.",
                    "d": "Du löschst Nachrichten, damit es keinen Streit gibt.",
                },
                "correct": "a",
            },
            {
                "question": "Ein Nutzer wird im Support unfreundlich. Wie reagierst du?",
                "options": {
                    "a": "Du antwortest genauso unfreundlich.",
                    "b": "Du drohst direkt mit einem Team-Warn.",
                    "c": "Du beendest das Gespräch ohne Erklärung.",
                    "d": "Du bleibst ruhig, erklärst sachlich die Regeln und holst bei Bedarf Hilfe.",
                },
                "correct": "d",
            },
            {
                "question": "Du bemerkst, dass du selbst eine falsche Auskunft gegeben hast. Was tust du?",
                "options": {
                    "a": "Du hoffst, dass es niemand bemerkt.",
                    "b": "Du meldest und korrigierst den Fehler zeitnah und transparent.",
                    "c": "Du löschst den Verlauf ohne Rücksprache.",
                    "d": "Du gibst einem anderen Teamler die Schuld.",
                },
                "correct": "b",
            },
            {
                "question": "Ein Teamkollege bittet dich um eine Berechtigung, die du nicht verwalten darfst.",
                "options": {
                    "a": "Du verweist an die zuständige Person und prüfst keine Rechte eigenmächtig frei.",
                    "b": "Du gibst den Zugang aus Kollegialität frei.",
                    "c": "Du teilst dein eigenes Konto.",
                    "d": "Du gibst ihm vorübergehend Admin-Rechte.",
                },
                "correct": "a",
            },
        ],
    },
    "high_team": {
        "label": "High Team",
        "description": "Erweiterte Verantwortung für Moderation, schwierige Fälle und die Qualität von Entscheidungen.",
        "pass_percent": 67,
        "questions": [
            {
                "question": "Gegen einen befreundeten Teamler liegt eine Beschwerde vor. Wie gehst du vor?",
                "options": {
                    "a": "Du weist die Beschwerde ab, um deinen Freund zu schützen.",
                    "b": "Du prüfst die Fakten neutral und legst einen Interessenkonflikt offen bzw. gibst den Fall ab.",
                    "c": "Du veröffentlichst die Beschwerde im Teamchat.",
                    "d": "Du bittest den Beschwerdeführer, den Fall zurückzuziehen.",
                },
                "correct": "b",
            },
            {
                "question": "Was gehört zu einer nachvollziehbaren Team-Warnung?",
                "options": {
                    "a": "Nur die Aussage, dass die Person schwierig ist.",
                    "b": "Eine Einschätzung ohne konkrete Situation.",
                    "c": "Ein konkreter Grund, die relevanten Fakten bzw. Belege und eine sachliche Dokumentation.",
                    "d": "Die Anzahl der Personen, die sich beschwert haben, ohne Prüfung der Inhalte.",
                },
                "correct": "c",
            },
            {
                "question": "Ein Fall enthält vertrauliche Informationen. Was ist angemessen?",
                "options": {
                    "a": "Den vollständigen Fall an alle Teamler senden.",
                    "b": "Screenshots privat an Freunde weiterleiten.",
                    "c": "Die Angaben in einem öffentlichen Kanal diskutieren.",
                    "d": "Den Zugriff auf die Personen begrenzen, die den Fall bearbeiten müssen.",
                },
                "correct": "d",
            },
            {
                "question": "Der Sachverhalt eines Regelverstoßes ist noch unklar. Was ist der beste nächste Schritt?",
                "options": {
                    "a": "Sofort die strengste Sanktion vergeben.",
                    "b": "Fehlende Informationen gezielt einholen und erst danach nach den Regeln entscheiden.",
                    "c": "Den Fall ohne Dokumentation schließen.",
                    "d": "Nach persönlicher Sympathie entscheiden.",
                },
                "correct": "b",
            },
            {
                "question": "Ein Teamler bittet dich, eine bestehende Sanktion zurückzunehmen. Wie reagierst du?",
                "options": {
                    "a": "Du nimmst sie ohne Prüfung zurück.",
                    "b": "Du prüfst Begründung und Fakten nach denselben Standards und dokumentierst die Entscheidung.",
                    "c": "Du versprichst die Rücknahme, bevor du den Fall ansiehst.",
                    "d": "Du ignorierst jeden Antrag auf Überprüfung.",
                },
                "correct": "b",
            },
            {
                "question": "Du entdeckst einen möglichen Missbrauch von Team-Berechtigungen. Was tust du?",
                "options": {
                    "a": "Du entfernst ohne Prüfung alle Rollen der beteiligten Personen.",
                    "b": "Du sprichst den Verdacht öffentlich an, damit alle Bescheid wissen.",
                    "c": "Du sicherst relevante Informationen und meldest den Fall über den vorgesehenen vertraulichen Weg.",
                    "d": "Du ignorierst es, sofern kein Nutzer sich beschwert.",
                },
                "correct": "c",
            },
        ],
    },
    "leadership": {
        "label": "Leitungsebene",
        "description": "Fokus auf Personalverantwortung, nachvollziehbare Entscheidungen, vertrauliche Daten und Vier-Augen-Prinzip.",
        "pass_percent": 83,
        "questions": [
            {
                "question": "Ein enger Freund ist Gegenstand einer schwerwiegenden Personalbeschwerde. Was ist richtig?",
                "options": {
                    "a": "Du entscheidest allein, da du die Person gut kennst.",
                    "b": "Du schließt den Fall zum Schutz der Team-Moral.",
                    "c": "Du legst den Interessenkonflikt offen und veranlasst eine unabhängige Prüfung.",
                    "d": "Du informierst zuerst alle Teamler über den Verdacht.",
                },
                "correct": "c",
            },
            {
                "question": "Ein Bewerber besteht den Online-Test für die Leitungsebene. Was bedeutet das?",
                "options": {
                    "a": "Er erhält automatisch eine Leitungsrolle.",
                    "b": "Das Testergebnis ist ein Entscheidungskriterium; Eignung, Gespräch und die vorgesehenen Freigaben bleiben erforderlich.",
                    "c": "Andere Teamler dürfen keine Rückfragen mehr stellen.",
                    "d": "Der Bewerber darf ab sofort alle Personalakten einsehen.",
                },
                "correct": "b",
            },
            {
                "question": "Wie sollten Berechtigungen auf sensible Personalinformationen vergeben werden?",
                "options": {
                    "a": "Nach dem Prinzip der erforderlichen Mindestberechtigung und mit nachvollziehbarer Kontrolle.",
                    "b": "Alle Mitglieder des Teams erhalten Zugriff.",
                    "c": "Zugriff ist dauerhaft und ohne Protokollierung zu vergeben.",
                    "d": "Jeder darf die Informationen weitergeben, solange ein Name entfernt wird.",
                },
                "correct": "a",
            },
            {
                "question": "Bei einem schwerwiegenden Vorfall besteht Gefahr, dass Belege verloren gehen. Was hat Priorität?",
                "options": {
                    "a": "Sofort eine öffentliche Schuldzuweisung veröffentlichen.",
                    "b": "Die beteiligten Personen im allgemeinen Chat befragen.",
                    "c": "Die Angelegenheit ignorieren, bis alle Führungskräfte online sind.",
                    "d": "Relevante Belege sichern, den vertraulichen Meldeweg nutzen und nur notwendige Personen einbeziehen.",
                },
                "correct": "d",
            },
            {
                "question": "Wie sollte eine wichtige Personalentscheidung dokumentiert werden?",
                "options": {
                    "a": "Nur mündlich, damit keine Historie entsteht.",
                    "b": "Mit sachlicher Begründung, verantwortlichen Personen und den vorgeschriebenen Freigaben.",
                    "c": "Mit privaten Meinungen über die betroffene Person.",
                    "d": "Erst dann, wenn jemand die Entscheidung infrage stellt.",
                },
                "correct": "b",
            },
            {
                "question": "Du möchtest eine große Personaländerung in Team-Updates bekannt geben. Was gehört in die Mitteilung?",
                "options": {
                    "a": "Die vollständige Personalakte als Begründung.",
                    "b": "Private Nachrichten und sämtliche Belege.",
                    "c": "Nur die erforderlichen Informationen; vertrauliche Details bleiben in der internen Akte.",
                    "d": "Unbestätigte Informationen, damit das Team frühzeitig gewarnt ist.",
                },
                "correct": "c",
            },
        ],
    },
}


def render_career_test(career_path: str) -> str:
    """Rendert nur die Fragen/Optionen, niemals den Lösungsschlüssel."""
    career = APPLICATION_CAREERS.get(career_path)
    if not career:
        return ""

    groups: list[str] = []
    for index, question in enumerate(career["questions"], start=1):
        options = "".join(
            (
                '<label class="flex items-start gap-2.5 rounded-xl border border-slate-200 '
                'dark:border-slate-700 p-3 cursor-pointer hover:border-indigo-400">'
                f'<input type="radio" name="answer_{index}" value="{escape(option_key)}" '
                'required class="mt-0.5 accent-indigo-600 shrink-0">'
                f'<span>{escape(option_text)}</span></label>'
            )
            for option_key, option_text in question["options"].items()
        )
        groups.append(
            '<fieldset class="rounded-2xl border border-slate-200 dark:border-slate-800 p-4 space-y-3">'
            f'<legend class="px-1 font-semibold text-sm">{index}. {escape(question["question"])}</legend>'
            f'<div class="grid gap-2">{options}</div></fieldset>'
        )
    return "".join(groups)


def grade_career_test(career_path: str, answers: Iterable[str]) -> dict:
    """Validiert und bewertet einen vollständigen Test serverseitig."""
    career = APPLICATION_CAREERS.get(career_path)
    if not career:
        raise ValueError("Bitte eine gültige Laufbahn auswählen.")

    submitted = list(answers)
    questions = career["questions"]
    if len(submitted) != len(questions):
        raise ValueError("Bitte alle Testfragen beantworten.")

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
