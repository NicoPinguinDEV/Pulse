"""Local, rule-based summaries for tickets and applications (#36, #134, #135).

One implementation for Discord and the dashboard. Everything here is extractive and deterministic: sentences are
taken from the source, facts are counted, nothing is sent to an external service and no AI is involved.

* Ticket handover: current state (key statements), open questions, steps already taken, people involved and the
  next sensible step – each with its source so the reader can check it.
* Application overview: the answers arranged for reviewers. It never rates, ranks, recommends or decides;
  the existing test result is only repeated as the fact it already is.
"""
from __future__ import annotations

import html
import re

import pulse_ticket_extras as tx

MAX_MESSAGES = 100
MAX_LIST = 5
SHORT_ANSWER_WORDS = 15
TICKET_NOTICE = "Lokal und regelbasiert aus dem Verlauf erstellt · keine externe KI · vor Weitergabe bitte prüfen"
APPLICATION_NOTICE = ("Strukturierte Aufbereitung der Angaben – keine Bewertung, keine Empfehlung. "
                      "Über Einladung, Annahme oder Ablehnung entscheidet ausschließlich das Recruiting-Team.")
_TRANSCRIPT_LINE = re.compile(r"^\[(\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}:\d{2})\] (.{1,120}?) \((\d{5,22})\): (.*)$")
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
_DONE = re.compile(r"\b(erledigt|geprüft|geklärt|weitergeleitet|zurückerstattet|erstattet|behoben|korrigiert|entsperrt|"
                   r"gesperrt|eingetragen|angepasst|gelöscht|wiederhergestellt|freigeschaltet|habe\s+\w+|haben\s+\w+|wurde\s+\w+)\b",
                   re.IGNORECASE)
STATUS_LABELS = {"pending": "Eingegangen", "interview": "Bewerbungsgespräch", "hired": "Eingestellt", "accepted": "Eingestellt",
                 "rejected": "Abgelehnt"}


def _clean_messages(messages) -> list[dict]:
    out = []
    for item in list(messages or [])[-MAX_MESSAGES:]:
        if not isinstance(item, dict) or item.get("is_bot"):
            continue
        text = str(item.get("content") or "").strip()[:2000]
        if not text:
            continue
        out.append({"author": str(item.get("author") or "Unbekannt").strip()[:80] or "Unbekannt",
                    "author_id": str(item.get("author_id") or "").strip()[:25],
                    "created_at": str(item.get("created_at") or "").strip()[:40],
                    "content": text, "is_staff": item.get("is_staff")})
    return out


def parse_transcript(text: str, limit: int = MAX_MESSAGES) -> list[dict]:
    """Messages from an archived ticket transcript ("[dd.mm.yyyy hh:mm:ss] Name (id): text"), newest `limit`."""
    rows: list[dict] = []
    for line in str(text or "").splitlines():
        match = _TRANSCRIPT_LINE.match(line)
        if match:
            content = match.group(4)
            for marker in (" | Anhänge: ", " | Embeds: ", " | Inhalt: "):
                content = content.split(marker)[0]
            rows.append({"created_at": match.group(1), "author": match.group(2), "author_id": match.group(3),
                         "content": "" if content.strip() == "[kein Text]" else content})
        elif rows and line.strip() and not line.startswith("---"):
            rows[-1]["content"] += "\n" + line
    return [r for r in rows if r["content"].strip()][-max(1, int(limit)):]


async def channel_messages(channel, limit: int = MAX_MESSAGES, max_chars: int = 16_000) -> list[dict]:
    """The newest human text messages of a ticket channel, oldest first, bounded in number and size.

    Discord errors (403/404/5xx) propagate to the caller, which decides how to tell the user.
    """
    rows, total = [], 0
    async for message in channel.history(limit=max(1, min(int(limit), MAX_MESSAGES)), oldest_first=False):
        if getattr(message.author, "bot", False):
            continue
        remaining = max_chars - total
        if remaining <= 0:
            break
        content = str(getattr(message, "content", "") or "").strip()[:min(1200, remaining)]
        if not content:
            continue
        rows.append({"author": getattr(message.author, "display_name", "Unbekannt"),
                     "author_id": str(getattr(message.author, "id", "") or ""),
                     "created_at": str(getattr(message, "created_at", "") or ""), "content": content,
                     "attachment_count": len(getattr(message, "attachments", []) or []), "is_bot": False})
        total += len(content)
    rows.reverse()  # the API returns newest first; summaries are built chronologically
    return rows


def _side(message: dict, requester_id: str) -> str:
    """requester | team | unknown – from the explicit flag, else from the ticket owner's id."""
    if message.get("is_staff") is True:
        return "team"
    if message.get("is_staff") is False:
        return "requester"
    if requester_id and message.get("author_id"):
        return "requester" if message["author_id"] == requester_id else "team"
    return "unknown"


def _answered(question: dict, later: list[dict], requester_id: str) -> bool:
    side = _side(question, requester_id)
    for message in later:
        other = _side(message, requester_id)
        if side == "unknown" or other == "unknown":
            if message["author"] != question["author"]:
                return True
        elif other != side:
            return True
    return False


def summarize_ticket(messages, *, ticket: dict | None = None, events=None, source: str = "Discord-Verlauf",
                     limit: int = MAX_LIST) -> dict:
    """Structured handover summary. `messages` are chronological dicts (author, author_id, created_at, content,
    optional is_staff / is_bot / attachment_count); `events` are rows of pulse_db.ticket_events."""
    ticket = ticket or {}
    requester_id = str(ticket.get("user_id") or "")
    clean = _clean_messages(messages)
    base = tx.build_ticket_summary(list(messages or [])[-MAX_MESSAGES:], limit=limit)

    people: dict[str, dict] = {}
    for message in clean:
        entry = people.setdefault(message["author"], {"name": message["author"], "messages": 0,
                                                      "role": {"requester": "Ersteller", "team": "Team"}.get(_side(message, requester_id), "Beteiligt")})
        entry["messages"] += 1

    questions = []
    for index, message in enumerate(clean):
        for part in _SENTENCE.split(message["content"]):
            text = part.strip()
            if text.endswith("?") and len(text) >= 8 and not _answered(message, clean[index + 1:], requester_id):
                questions.append({"author": message["author"], "created_at": message["created_at"], "content": text[:300],
                                  "side": _side(message, requester_id)})
    questions = questions[-limit:]

    steps = []
    for event in sorted([e for e in (events or []) if isinstance(e, dict)], key=lambda e: str(e.get("created_at") or ""))[-8:]:
        label = str(event.get("event_type") or "").strip()
        if not label:
            continue
        detail = str(event.get("details") or "").strip()
        steps.append({"author": str(event.get("actor_name") or "Pulse")[:80], "created_at": str(event.get("created_at") or "")[:40],
                      "content": (label + (f": {detail}" if detail else ""))[:300], "source": "Ticket-Ereignis"})
    said = []
    for message in clean:
        if _side(message, requester_id) != "team":
            continue
        for part in _SENTENCE.split(message["content"]):
            text = part.strip()
            if len(text) >= 15 and not text.endswith("?") and _DONE.search(text):
                said.append({"author": message["author"], "created_at": message["created_at"], "content": text[:300],
                             "source": "Nachricht"})
    steps.extend(said[-limit:])

    status = str(ticket.get("status") or "")
    last = clean[-1] if clean else None
    open_to_requester = [q for q in questions if q["side"] == "team"]
    if status == "closed":
        next_step = {"text": "Ticket ist geschlossen – keine weitere Aktion nötig.",
                     "reason": f"Schließgrund: {ticket.get('close_reason') or 'nicht angegeben'}"}
    elif not clean:
        next_step = {"text": "Es gibt noch keine auswertbaren Nachrichten – zuerst das Anliegen erfragen.", "reason": "Verlauf ist leer."}
    elif ticket and not ticket.get("claimed_by_id") and not ticket.get("claimed_by_name"):
        next_step = {"text": "Ticket ist noch nicht übernommen – zuerst die Zuständigkeit klären und das Ticket übernehmen.",
                     "reason": "Kein Bearbeiter eingetragen."}
    elif _side(last, requester_id) == "requester":
        next_step = {"text": f"Antwort an {last['author']} steht aus.",
                     "reason": f"Die letzte Nachricht ({last['created_at'][:19].replace('T', ' ')}) stammt vom Ersteller."}
    elif open_to_requester:
        next_step = {"text": f"Rückmeldung des Erstellers abwarten: „{open_to_requester[-1]['content'][:160]}“",
                     "reason": f"Offene Rückfrage von {open_to_requester[-1]['author']}."}
    elif questions:
        next_step = {"text": f"Offene Frage klären: „{questions[-1]['content'][:160]}“", "reason": f"Gestellt von {questions[-1]['author']}."}
    else:
        next_step = {"text": "Sachstand prüfen und dem Ersteller das Ergebnis mitteilen oder das Ticket abschließen.",
                     "reason": "Keine offene Frage und keine unbeantwortete Nachricht erkannt."}
    return {
        "ticket_id": str(ticket.get("id") or ""), "category": str(ticket.get("category") or ""), "status": status,
        "message_count": len(clean), "attachment_count": base["attachment_count"],
        "first_at": clean[0]["created_at"] if clean else "", "last_at": clean[-1]["created_at"] if clean else "",
        "key_points": base["key_points"], "open_questions": questions, "steps_done": steps,
        "participants": list(people.values())[:10], "next_step": next_step, "source": source, "notice": TICKET_NOTICE,
    }


def ticket_sections(summary: dict) -> list[tuple[str, list[str]]]:
    """The summary as titled plain-text sections – the single layout used by Discord, notes and the dashboard."""
    def line(item):
        stamp = str(item.get("created_at") or "")[:19].replace("T", " ")
        return f"{item.get('author', '')}{' · ' + stamp if stamp else ''}: {item.get('content', '')}"

    return [
        ("Bisheriger Sachstand", [line(p) for p in summary.get("key_points") or []] or ["Keine Kernaussagen erkannt."]),
        ("Offene Fragen", [line(q) for q in summary.get("open_questions") or []] or ["Keine offene Frage erkannt."]),
        ("Bereits durchgeführte Schritte", [line(s) for s in summary.get("steps_done") or []] or ["Keine Schritte protokolliert."]),
        ("Beteiligte Personen", [f"{p['name']} ({p['role']}, {p['messages']} Nachrichten)" for p in summary.get("participants") or []]
         or ["Niemand."]),
        ("Nächster sinnvoller Schritt", [summary["next_step"]["text"], "Grund: " + summary["next_step"]["reason"]]),
    ]


def ticket_summary_text(summary: dict, limit: int = 3500) -> str:
    out = []
    for title, lines in ticket_sections(summary):
        out.append(f"{title}:")
        out.extend(f"- {text}" for text in lines)
    return "\n".join(out)[:limit]


def ticket_summary_html(summary: dict) -> str:
    """Escaped HTML for dashboard cards (no markup from the source survives)."""
    blocks = []
    for title, lines in ticket_sections(summary):
        items = "".join(f"<li>{html.escape(text)}</li>" for text in lines)
        blocks.append(f"<h3 class='font-bold mt-3'>{html.escape(title)}</h3><ul class='list-disc ml-5 text-sm'>{items}</ul>")
    span = f"{summary.get('first_at', '')[:19]} – {summary.get('last_at', '')[:19]}" if summary.get("message_count") else "—"
    blocks.append(f"<p class='text-xs text-slate-500 mt-3'>Grundlage: {int(summary.get('message_count') or 0)} Nachrichten · "
                  f"{int(summary.get('attachment_count') or 0)} Anhänge · {html.escape(span.replace('T', ' '))} · "
                  f"Quelle: {html.escape(str(summary.get('source') or ''))}<br>{html.escape(str(summary.get('notice') or ''))}</p>")
    return "".join(blocks)


# ---------------------------------------------------------------------------------------------- applications
def _words(text) -> int:
    return len(re.findall(r"\w+", str(text or "")))


def _key_sentences(text: str, limit: int = 2) -> list[str]:
    points = tx.build_ticket_summary([{"author": "", "content": str(text or "")}], limit=limit)["key_points"]
    return [p["content"] for p in points]


def summarize_application(application: dict) -> dict:
    """Arrange one application for reviewers. Contains facts and quotes only – no score, no recommendation."""
    app = application if isinstance(application, dict) else {}
    text = str(app.get("text") or "")
    practical = []
    for index, item in enumerate(app.get("practical_answers") or [], start=1):
        if not isinstance(item, dict):
            continue
        answer = str(item.get("answer") or "").strip()
        practical.append({"number": index, "title": str(item.get("title") or "Praxisaufgabe")[:160], "words": _words(answer),
                          "key_sentences": _key_sentences(answer, 1) if answer else [], "empty": not answer})
    details = [d for d in (app.get("test_details") or []) if isinstance(d, dict)]
    test = None
    if app.get("test_percent") is not None:
        test = {"score": int(app.get("test_score") or 0), "total": int(app.get("test_total") or 0),
                "percent": int(app.get("test_percent") or 0), "pass_percent": app.get("test_pass_percent"),
                "wrong_questions": [str(d.get("question") or "")[:200] for d in details if not d.get("correct")][:10]}
    notes = []
    if not text.strip():
        notes.append("Der Bewerbungstext ist leer.")
    elif _words(text) < SHORT_ANSWER_WORDS:
        notes.append(f"Der Bewerbungstext ist sehr kurz ({_words(text)} Wörter).")
    for item in practical:
        if item["empty"]:
            notes.append(f"Praxisaufgabe {item['number']} „{item['title']}“ wurde nicht beantwortet.")
        elif item["words"] < SHORT_ANSWER_WORDS:
            notes.append(f"Praxisaufgabe {item['number']} „{item['title']}“ ist sehr kurz beantwortet ({item['words']} Wörter).")
    if practical and str(app.get("practical_review_status") or "pending") == "pending":
        notes.append("Die Praxisaufgaben sind noch nicht manuell geprüft.")
    reviews = app.get("reviews") if isinstance(app.get("reviews"), dict) else {}
    if not reviews:
        notes.append("Es liegt noch keine Prüferbewertung vor.")
    return {
        "name": str(app.get("name") or "")[:120], "career": str(app.get("career_label") or "Nicht angegeben")[:120],
        "created_at": str(app.get("created_at") or "")[:40],
        "status": STATUS_LABELS.get(str(app.get("status") or ""), str(app.get("status") or "unbekannt")),
        "text_words": _words(text), "text_key_sentences": _key_sentences(text, 3) if text.strip() else [],
        "practical": practical, "test": test, "review_count": len(reviews),
        "votes": {"up": len(app.get("upvotes") or []), "down": len(app.get("downvotes") or [])},
        "check_notes": notes, "notice": APPLICATION_NOTICE,
    }


def application_summary_html(application: dict) -> str:
    """Collapsible, fully escaped overview for the reviewer view of /applications."""
    s = summarize_application(application)
    e = html.escape
    rows = [f"<li><b>Laufbahn:</b> {e(s['career'])} · <b>Status:</b> {e(s['status'])} · <b>Eingang:</b> {e(s['created_at'] or '—')}</li>",
            f"<li><b>Bewerbungstext:</b> {s['text_words']} Wörter"
            + ("".join(f"<br>„{e(x)}“" for x in s["text_key_sentences"])) + "</li>"]
    for item in s["practical"]:
        rows.append(f"<li><b>Praxisaufgabe {item['number']} – {e(item['title'])}:</b> "
                    + ("nicht beantwortet" if item["empty"] else f"{item['words']} Wörter"
                       + "".join(f"<br>„{e(x)}“" for x in item["key_sentences"])) + "</li>")
    if s["test"]:
        t = s["test"]
        rows.append(f"<li><b>Online-Test (vorhandenes Ergebnis):</b> {t['score']}/{t['total']} richtig ({t['percent']} %)"
                    + ("".join(f"<br>Falsch beantwortet: {e(q)}" for q in t["wrong_questions"])) + "</li>")
    rows.append(f"<li><b>Prüferstand:</b> {s['review_count']} Bewertungen · 👍 {s['votes']['up']} · 👎 {s['votes']['down']}</li>")
    notes = "".join(f"<li>{e(n)}</li>" for n in s["check_notes"]) or "<li>Keine Auffälligkeiten bei Vollständigkeit und Umfang.</li>"
    return ("<details class='mt-3'><summary class='cursor-pointer text-xs font-semibold text-sky-600 dark:text-sky-300'>"
            "📋 Strukturierte Zusammenfassung für Prüfer</summary>"
            f"<ul class='list-disc ml-5 mt-2 text-xs space-y-1'>{''.join(rows)}</ul>"
            f"<p class='text-xs font-semibold mt-2'>Hinweise zu Vollständigkeit und Umfang</p><ul class='list-disc ml-5 text-xs'>{notes}</ul>"
            f"<p class='text-[10px] text-slate-500 mt-2'>{e(s['notice'])}</p></details>")
