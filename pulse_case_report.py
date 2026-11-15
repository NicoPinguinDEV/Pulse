"""Audit #175: structured closing report and internal notes for team cases (``/ultimate/cases``).

* **Internal note** - a leadership member adds a note that lands in the case timeline (``ultimate_case_events``). Notes are only
  visible inside the case file (leadership), never to the people the case is about.
* **Closing report** - outcome, summary, measures, lessons and participants, stored once per case (``case_closing_reports``).
  Saving the report closes the case (status ``closed``) in one step, so a case is never closed without an explanation.
  A closed case can get its report corrected; every save is an event in the timeline and an audit entry.

The routes reuse the case file's own authorization (leadership only) and storage; nothing is deleted or migrated.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Cookie, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

import pulse_db

OUTCOMES = {"bestaetigt": "Sachverhalt bestätigt", "teilweise": "Teilweise bestätigt", "unbegruendet": "Unbegründet",
            "uebergeben": "An andere Stelle übergeben", "abgebrochen": "Ohne Ergebnis abgebrochen"}
LIMITS = {"summary": 3000, "measures": 2000, "lessons": 2000, "participants": 500}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get(case_id) -> dict | None:
    with pulse_db.connect() as db:
        row = db.execute("SELECT * FROM case_closing_reports WHERE case_id=?", (str(case_id),)).fetchone()
    return dict(row) if row else None


def validate(outcome: str, summary: str, measures: str = "", lessons: str = "", participants: str = "") -> dict:
    """Clean values or ``ValueError`` with a user-facing message. A summary is mandatory."""
    if outcome not in OUTCOMES:
        raise ValueError("Bitte ein Ergebnis auswählen.")
    values = {"summary": str(summary or "").strip(), "measures": str(measures or "").strip(),
              "lessons": str(lessons or "").strip(), "participants": str(participants or "").strip()}
    if len(values["summary"]) < 10:
        raise ValueError("Die Zusammenfassung braucht mindestens 10 Zeichen.")
    for key, limit in LIMITS.items():
        if len(values[key]) > limit:
            raise ValueError(f"„{key}“ ist zu lang (höchstens {limit} Zeichen).")
    return {"outcome": outcome, **values}


def save(case_id, outcome, summary, measures="", lessons="", participants="", actor_id="", actor_name="") -> dict:
    """Store (or replace) the report of one case. Raises ``ValueError`` for invalid input, ``LookupError`` for an unknown case."""
    clean = validate(outcome, summary, measures, lessons, participants)
    row = {"case_id": str(case_id), **clean, "author_id": str(actor_id)[:30], "author_name": str(actor_name)[:100], "at": _now()}
    import pulse_ultimate
    with pulse_ultimate.cx() as c:
        if c.execute("SELECT 1 FROM ultimate_cases WHERE id=?", (str(case_id),)).fetchone() is None:
            raise LookupError("Fall nicht gefunden.")
        c.execute("UPDATE ultimate_cases SET status='closed', closed_at=COALESCE(closed_at, ?), updated_at=? WHERE id=?", (row["at"], row["at"], str(case_id)))
    with pulse_db.connect() as db:
        db.execute("INSERT OR REPLACE INTO case_closing_reports (case_id,outcome,summary,measures,lessons,participants,author_id,author_name,at) "
                   "VALUES (:case_id,:outcome,:summary,:measures,:lessons,:participants,:author_id,:author_name,:at)", row)
    return row


def detail_card(case_id, case_status: str, esc) -> str:
    """HTML for the case detail page: report (or a link to write it) and the internal-note form. Never raises."""
    try:
        report = get(case_id)
        notes = ('<form method="post" action="/ultimate/cases/' + esc(case_id) + '/notiz" class="form"><textarea class="ta span2" name="text" maxlength="1500" required '
                 'placeholder="Interne Notiz (nur für die Leitung sichtbar)"></textarea><button class="btn primary span2">Notiz speichern</button></form>')
        if report:
            body = (f'<div class="row"><b>{esc(OUTCOMES.get(report["outcome"], report["outcome"]))}</b><span class="tiny">{esc(report["at"][:16].replace("T", " "))} · {esc(report["author_name"])}</span></div>'
                    f'<p class="tiny">{esc(report["summary"])}</p>'
                    + "".join(f'<div class="tiny"><b>{label}:</b> {esc(report[key])}</div>' for key, label in (("measures", "Maßnahmen"), ("lessons", "Erkenntnisse"), ("participants", "Beteiligte")) if report[key])
                    + f'<a class="btn" href="/ultimate/cases/{esc(case_id)}/abschluss">Bericht korrigieren</a>')
        else:
            body = ('<div class="tiny">Noch kein Abschlussbericht.</div>'
                    f'<a class="btn primary" href="/ultimate/cases/{esc(case_id)}/abschluss">{"Abschlussbericht schreiben" if case_status != "closed" else "Bericht nachtragen"}</a>')
        return f'<div class="grid g2" style="margin-top:13px"><div class="card"><h3>📝 Abschlussbericht</h3>{body}</div><div class="card"><h3>💬 Interne Notiz</h3>{notes}</div></div>'
    except Exception:  # noqa: BLE001 - the case file must keep working
        return ""


def register(app) -> None:
    import pulse_ultimate as ult
    esc = ult.esc
    app.state.case_report_registered = True

    def case_or_404(case_id):
        with ult.cx() as c:
            row = c.execute("SELECT * FROM ultimate_cases WHERE id=?", (case_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Fall nicht gefunden.")
        return row

    @app.get("/ultimate/cases/{case_id}/abschluss", response_class=HTMLResponse)
    async def closing_form(request: Request, case_id: str, user_session: str = Cookie(None)):
        ctx = ult.ctx_auth(request, user_session, manager=True)
        case = case_or_404(case_id)
        old = get(case_id) or {}
        msg = esc(request.query_params.get("msg", "")[:200])
        options = "".join(f'<option value="{k}"{" selected" if old.get("outcome") == k else ""}>{esc(v)}</option>' for k, v in OUTCOMES.items())
        body = (f'<div class="card"><h2>📝 Abschlussbericht {esc(case["case_no"])}</h2>{f"<p class=tiny>{msg}</p>" if msg else ""}'
                f'<p class="tiny">Mit dem Speichern wird der Fall abgeschlossen. Alle Angaben stehen in der Timeline und im Audit.</p>'
                f'<form method="post" action="/ultimate/cases/{esc(case_id)}/abschluss" class="form"><select class="select span2" name="outcome" required>{options}</select>'
                f'<textarea class="ta span2" name="summary" required minlength="10" maxlength="{LIMITS["summary"]}" placeholder="Zusammenfassung: was ist passiert, was wurde festgestellt?">{esc(old.get("summary", ""))}</textarea>'
                f'<textarea class="ta span2" name="measures" maxlength="{LIMITS["measures"]}" placeholder="Maßnahmen">{esc(old.get("measures", ""))}</textarea>'
                f'<textarea class="ta span2" name="lessons" maxlength="{LIMITS["lessons"]}" placeholder="Erkenntnisse / was ändern wir?">{esc(old.get("lessons", ""))}</textarea>'
                f'<input class="input span2" name="participants" maxlength="{LIMITS["participants"]}" placeholder="Beteiligte (Teammitglieder, Spieler)" value="{esc(old.get("participants", ""))}">'
                f'<button class="btn primary span2">Bericht speichern und Fall abschließen</button></form></div>')
        return ult.page(ctx, "cases", f"Abschlussbericht {case['case_no']}", "Strukturierter Bericht zum Fallabschluss.", body)

    @app.post("/ultimate/cases/{case_id}/abschluss")
    async def closing_save(request: Request, case_id: str, outcome: str = Form(""), summary: str = Form(""), measures: str = Form(""),
                           lessons: str = Form(""), participants: str = Form(""), user_session: str = Cookie(None)):
        ctx = ult.ctx_auth(request, user_session, manager=True)
        actor = ctx.user.get("global_name") or ctx.user.get("username") or "Team"
        case = case_or_404(case_id)
        try:
            save(case_id, outcome, summary, measures, lessons, participants, ctx.user["id"], actor)
        except ValueError as exc:
            return RedirectResponse(f"/ultimate/cases/{case_id}/abschluss?msg={str(exc)[:150]}", status_code=303)
        ult.add_case_event(case_id, ctx, "closing_report", f"{OUTCOMES[outcome]}: {summary.strip()[:300]}")
        ult.audit(ctx, "Abschlussbericht gespeichert", case_id, case["case_no"], outcome)
        return RedirectResponse(f"/ultimate/cases/{case_id}", status_code=303)

    @app.post("/ultimate/cases/{case_id}/notiz")
    async def internal_note(request: Request, case_id: str, text: str = Form(""), user_session: str = Cookie(None)):
        ctx = ult.ctx_auth(request, user_session, manager=True)
        case_or_404(case_id)
        text = text.strip()[:1500]
        if not text:
            raise HTTPException(status_code=400, detail="Die Notiz darf nicht leer sein.")
        ult.add_case_event(case_id, ctx, "internal_note", text)
        ult.audit(ctx, "Interne Fallnotiz", case_id, "", text[:120])
        return RedirectResponse(f"/ultimate/cases/{case_id}", status_code=303)
