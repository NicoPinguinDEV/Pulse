"""Audit #108: vollständige Quizverwaltung für Schulungen - Fragen bearbeiten, ergänzen, löschen, ordnen, Einstellungen ändern und
Auswertung je Frage (``/training/{id}/verwaltung``). Rechte wie die Schulungsverwaltung (``can_manage_training``, Führung, Admin).

* Antworten werden je Versuch gespeichert (``training_answers``: Versuch, Frage, gewählte Option, richtig/falsch), damit die
  Auswertung zeigt, welche Fragen oft falsch beantwortet werden. Alte Versuche ohne Antworten bleiben gültig und zählen nur in der Quote.
* Eine Schulung behält immer mindestens eine Frage; Fragen brauchen 2 bis 8 Optionen und eine gültige richtige Antwort.
* Änderungen an Fragen ändern keine bestehenden Versuche, Ergebnisse oder Zertifikate.
"""
from __future__ import annotations

import json

from fastapi import Cookie, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

import pulse_db

MIN_OPTIONS, MAX_OPTIONS, MAX_QUESTIONS = 2, 8, 100
MAX_QUESTION, MAX_OPTION = 500, 200


def can_manage(perms: dict) -> bool:
    perms = perms or {}
    return bool(perms.get("can_manage_training") or perms.get("can_promote") or perms.get("is_admin"))


def clean_question(question, options, answer_index) -> tuple[str, list[str], int]:
    """Validated ``(question, options, answer_index)``; raises ``ValueError`` with a user-facing message."""
    text = str(question or "").strip()
    if not text or len(text) > MAX_QUESTION:
        raise ValueError(f"Die Frage fehlt oder ist länger als {MAX_QUESTION} Zeichen.")
    opts = [str(o).strip()[:MAX_OPTION] for o in (options or ()) if str(o).strip()]
    if not MIN_OPTIONS <= len(opts) <= MAX_OPTIONS:
        raise ValueError(f"Bitte {MIN_OPTIONS} bis {MAX_OPTIONS} Antwortmöglichkeiten angeben.")
    if len({o.casefold() for o in opts}) != len(opts):
        raise ValueError("Antwortmöglichkeiten dürfen sich nicht wiederholen.")
    try:
        index = int(answer_index)
    except (TypeError, ValueError):
        raise ValueError("Die richtige Antwort ist ungültig.") from None
    if not 0 <= index < len(opts):
        raise ValueError("Die richtige Antwort ist ungültig.")
    return text, opts, index


def _questions(db, training_id):
    return [dict(r) for r in db.execute("SELECT * FROM training_questions WHERE training_id=? ORDER BY position, id", (str(training_id),)).fetchall()]


def _renumber(db, training_id):
    for pos, row in enumerate(_questions(db, training_id)):
        db.execute("UPDATE training_questions SET position=? WHERE id=?", (pos, row["id"]))


def add_question(training_id, question, options, answer_index) -> str:
    text, opts, index = clean_question(question, options, answer_index)
    with pulse_db.connect() as db:
        if not db.execute("SELECT 1 FROM trainings WHERE id=?", (str(training_id),)).fetchone():
            raise LookupError("Schulung nicht gefunden.")
        existing = _questions(db, training_id)
        if len(existing) >= MAX_QUESTIONS:
            raise ValueError(f"Höchstens {MAX_QUESTIONS} Fragen je Schulung.")
        qid = pulse_db.make_id("q")
        db.execute("INSERT INTO training_questions VALUES (?,?,?,?,?,?)", (qid, str(training_id), text, json.dumps(opts, ensure_ascii=False), index, len(existing)))
    return qid


def update_question(training_id, question_id, question, options, answer_index) -> None:
    text, opts, index = clean_question(question, options, answer_index)
    with pulse_db.connect() as db:
        changed = db.execute("UPDATE training_questions SET question=?, options_json=?, answer_index=? WHERE id=? AND training_id=?",
                             (text, json.dumps(opts, ensure_ascii=False), index, str(question_id), str(training_id))).rowcount
    if not changed:
        raise LookupError("Frage nicht gefunden.")


def delete_question(training_id, question_id) -> None:
    with pulse_db.connect() as db:
        rows = _questions(db, training_id)
        if not any(r["id"] == str(question_id) for r in rows):
            raise LookupError("Frage nicht gefunden.")
        if len(rows) <= 1:
            raise ValueError("Eine Schulung braucht mindestens eine Frage.")
        db.execute("DELETE FROM training_questions WHERE id=? AND training_id=?", (str(question_id), str(training_id)))
        _renumber(db, training_id)


def move_question(training_id, question_id, direction) -> None:
    if direction not in ("up", "down"):
        raise ValueError("Ungültige Richtung.")
    with pulse_db.connect() as db:
        rows = _questions(db, training_id)
        ids = [r["id"] for r in rows]
        if str(question_id) not in ids:
            raise LookupError("Frage nicht gefunden.")
        i = ids.index(str(question_id))
        j = i - 1 if direction == "up" else i + 1
        if 0 <= j < len(ids):
            ids[i], ids[j] = ids[j], ids[i]
        for pos, qid in enumerate(ids):
            db.execute("UPDATE training_questions SET position=? WHERE id=?", (pos, qid))


def update_settings(training_id, title, description, passing_score, time_limit) -> None:
    head = str(title or "").strip()
    if not head or len(head) > 120:
        raise ValueError("Der Titel fehlt oder ist länger als 120 Zeichen.")
    try:
        passing, limit = int(passing_score), int(time_limit)
    except (TypeError, ValueError):
        raise ValueError("Bestehensgrenze und Zeitlimit müssen Zahlen sein.") from None
    if not 1 <= passing <= 100 or not 1 <= limit <= 180:
        raise ValueError("Bestehensgrenze 1-100 %, Zeitlimit 1-180 Minuten.")
    with pulse_db.connect() as db:
        changed = db.execute("UPDATE trainings SET title=?, description=?, passing_score=?, time_limit_minutes=? WHERE id=?",
                             (head, str(description or "").strip()[:500], passing, limit, str(training_id))).rowcount
    if not changed:
        raise LookupError("Schulung nicht gefunden.")


def record_answers(attempt_id, questions, chosen: dict) -> int:
    """Store one row per question of an attempt (``chosen``: ``{question_id: option index or None}``). Never raises."""
    try:
        rows = []
        for q in questions:
            raw = chosen.get(q["id"])
            try:
                pick = int(raw) if raw is not None else None
            except (TypeError, ValueError):
                pick = None
            rows.append((str(attempt_id), q["id"], -1 if pick is None else pick, 1 if pick is not None and pick == int(q["answer_index"]) else 0))
        with pulse_db.connect() as db:
            db.executemany("INSERT OR REPLACE INTO training_answers (attempt_id,question_id,chosen_index,correct) VALUES (?,?,?,?)", rows)
        return len(rows)
    except Exception:  # noqa: BLE001 - the quiz result must never fail because of statistics
        return 0


def stats(training_id) -> dict:
    """Attempts, pass rate, average score and correct rate per question (``None`` while no answers were recorded)."""
    with pulse_db.connect() as db:
        attempts = [dict(r) for r in db.execute("SELECT id,score,passed FROM training_attempts WHERE training_id=?", (str(training_id),)).fetchall()]
        questions = _questions(db, training_id)
        per_q = {r["question_id"]: (r["total"], r["right"]) for r in db.execute(
            "SELECT question_id, COUNT(*) AS total, SUM(correct) AS right FROM training_answers WHERE question_id IN "
            "(SELECT id FROM training_questions WHERE training_id=?) GROUP BY question_id", (str(training_id),)).fetchall()}
    total = len(attempts)
    out_q = []
    for q in questions:
        n, right = per_q.get(q["id"], (0, 0))
        out_q.append({"id": q["id"], "question": q["question"], "answers": n, "correct_rate": round(100 * (right or 0) / n) if n else None})
    return {"attempts": total, "pass_rate": round(100 * sum(1 for a in attempts if a["passed"]) / total) if total else None,
            "average_score": round(sum(a["score"] for a in attempts) / total) if total else None, "questions": out_q}


def register(app) -> None:
    import webserver as ws
    from pulse_pro import render_pro_page
    e = ws.esc

    def manager(request, user_session):
        ctx = ws.auth(request, user_session, perm=None)
        if not can_manage(ctx.perms):
            raise HTTPException(status_code=403, detail="Dafür fehlt dir die Berechtigung.")
        return ctx

    @app.get("/training/{training_id}/verwaltung", response_class=HTMLResponse)
    async def quiz_admin_page(request: Request, training_id: str, user_session: str = Cookie(None)):
        ctx = manager(request, user_session)
        t = pulse_db.get_training(training_id)
        if not t:
            raise HTTPException(status_code=404, detail="Schulung nicht gefunden.")
        st = stats(training_id)
        rate = {q["id"]: q for q in st["questions"]}
        cards = ""
        last = len(t["questions"]) - 1
        for i, q in enumerate(t["questions"]):
            r = rate[q["id"]]
            quote = "noch keine Antworten" if r["correct_rate"] is None else f'{r["correct_rate"]} % richtig ({r["answers"]} Antworten)'
            options = "\n".join(q["options"])
            cards += (f'<form method="post" action="/training/{e(training_id)}/verwaltung/frage" class="{ws.CARD} p-4 mb-3 space-y-2">'
                      f'<input type="hidden" name="question_id" value="{e(q["id"])}"><div class="text-[11px] text-slate-400">Frage {i + 1} · {e(quote)}</div>'
                      f'<input class="{ws.INPUT} w-full" name="question" maxlength="{MAX_QUESTION}" required value="{e(q["question"])}">'
                      f'<textarea class="{ws.INPUT} w-full" name="options" rows="4" required>{e(options)}</textarea>'
                      f'<label class="text-xs">Richtig ist Nr. <input class="{ws.INPUT}" type="number" name="answer_no" min="1" max="{MAX_OPTIONS}" value="{int(q["answer_index"]) + 1}" style="width:5rem"></label>'
                      f'<div class="flex flex-wrap gap-2"><button name="aktion" value="speichern" class="{ws.BTN} py-1.5 px-3">Speichern</button>'
                      f'<button name="aktion" value="hoch" class="pulse-btn ghost"{" disabled" if i == 0 else ""}>↑</button>'
                      f'<button name="aktion" value="runter" class="pulse-btn ghost"{" disabled" if i == last else ""}>↓</button>'
                      f'<button name="aktion" value="loeschen" class="pulse-btn ghost" onclick="return confirm(\'Frage wirklich löschen?\')">Löschen</button></div></form>')
        summary = (f'{st["attempts"]} Versuche · Bestehensquote {"–" if st["pass_rate"] is None else str(st["pass_rate"]) + " %"} · Durchschnitt {"–" if st["average_score"] is None else str(st["average_score"]) + " %"}')
        settings = (f'<form method="post" action="/training/{e(training_id)}/verwaltung/einstellungen" class="{ws.CARD} p-4 mb-4 space-y-2">'
                    f'<input class="{ws.INPUT} w-full" name="title" required maxlength="120" value="{e(t["title"])}"><textarea class="{ws.INPUT} w-full" name="description" rows="2" maxlength="500">{e(t["description"])}</textarea>'
                    f'<label class="text-xs">Bestehensgrenze % <input class="{ws.INPUT}" type="number" name="passing_score" min="1" max="100" value="{int(t["passing_score"])}" style="width:6rem"></label> '
                    f'<label class="text-xs">Zeitlimit Min. <input class="{ws.INPUT}" type="number" name="time_limit" min="1" max="180" value="{int(t["time_limit_minutes"])}" style="width:6rem"></label> '
                    f'<button class="{ws.BTN} py-1.5 px-3">Einstellungen speichern</button></form>')
        new = (f'<form method="post" action="/training/{e(training_id)}/verwaltung/frage" class="{ws.CARD} p-4 space-y-2"><input type="hidden" name="aktion" value="neu">'
               f'<div class="text-xs font-bold">Neue Frage</div><input class="{ws.INPUT} w-full" name="question" maxlength="{MAX_QUESTION}" required placeholder="Frage">'
               f'<textarea class="{ws.INPUT} w-full" name="options" rows="4" required placeholder="Eine Antwortmöglichkeit pro Zeile (2-{MAX_OPTIONS})"></textarea>'
               f'<label class="text-xs">Richtig ist Nr. <input class="{ws.INPUT}" type="number" name="answer_no" min="1" max="{MAX_OPTIONS}" value="1" style="width:5rem"></label> '
               f'<button class="{ws.BTN} py-1.5 px-3">Frage hinzufügen</button></form>')
        body = (f'<div class="pulse-crumb"><a href="/training">Schulungen</a> › <a href="/training/{e(training_id)}">{e(t["title"])}</a> › Quizverwaltung</div>'
                f'<h1 class="text-lg font-bold mb-1">Quizverwaltung{__import__("pulse_help").tip("quiz")}</h1><p class="text-xs text-slate-400 mb-3">{e(summary)}. Änderungen betreffen nur künftige Versuche.</p>{settings}{cards}{new}')
        return HTMLResponse(render_pro_page(ws, "Quizverwaltung", ctx, "training", body))

    @app.post("/training/{training_id}/verwaltung/einstellungen")
    async def quiz_admin_settings(request: Request, training_id: str, title: str = Form(...), description: str = Form(""), passing_score: str = Form(...),
                                  time_limit: str = Form(...), user_session: str = Cookie(None)):
        ctx = manager(request, user_session)
        try:
            update_settings(training_id, title, description, passing_score, time_limit)
        except LookupError:
            raise HTTPException(status_code=404, detail="Schulung nicht gefunden.")
        except ValueError as exc:
            return ws.back(f"/training/{training_id}/verwaltung", str(exc), False)
        ws.log_audit(ctx.user.get("global_name"), ctx.user["id"], "Schulung geändert", f"{title.strip()[:80]} · {training_id}")
        return ws.back(f"/training/{training_id}/verwaltung", "Einstellungen gespeichert.")

    @app.post("/training/{training_id}/verwaltung/frage")
    async def quiz_admin_question(request: Request, training_id: str, aktion: str = Form(...), question_id: str = Form(""), question: str = Form(""),
                                  options: str = Form(""), answer_no: str = Form("1"), user_session: str = Cookie(None)):
        ctx = manager(request, user_session)
        back = f"/training/{training_id}/verwaltung"
        try:
            index = int(answer_no) - 1 if str(answer_no).strip().lstrip("-").isdigit() else -1
            if aktion == "neu":
                add_question(training_id, question, options.splitlines(), index)
            elif aktion == "speichern":
                update_question(training_id, question_id, question, options.splitlines(), index)
            elif aktion == "loeschen":
                delete_question(training_id, question_id)
            elif aktion in ("hoch", "runter"):
                move_question(training_id, question_id, "up" if aktion == "hoch" else "down")
            else:
                raise HTTPException(status_code=400, detail="Unbekannte Aktion.")
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except ValueError as exc:
            return ws.back(back, str(exc), False)
        ws.log_audit(ctx.user.get("global_name"), ctx.user["id"], "Quizfrage geändert", f"{training_id} · {aktion}")
        return ws.back(back, "Gespeichert.")
