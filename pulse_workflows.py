"""Workflow versions, safe retries and multi-stage approvals for /ultimate/automations (#144, #145, #148, #149).

pulse_ultimate stays the only automation engine. This module adds three things around it and shares its
database connection, permission helpers and step runner:

* Versions – every changed definition is stored as an immutable row (database triggers refuse UPDATE and DELETE).
  History, comparison and a controlled restore (restored automations start paused) build on those rows.
* Retries – failed steps of a real run are queued once per run. Only the failed steps are repeated, with
  exponential backoff and a bounded number of attempts. A step is marked "running" before it is executed, so an
  interrupted attempt is never repeated on its own: it goes to the dead-letter list and waits for a person.
* Approvals – an automation with approval stages does not run when it triggers. It creates one request; the
  stages are decided in order by the responsible group, every person decides at most once, nobody approves an
  own request. A rejection, an expired deadline or a changed/paused/deleted automation ends the request.

Nothing here decides about people: approvals are always given by a logged-in, authorised person.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import timedelta

from fastapi import Cookie, HTTPException, Request
from fastapi.responses import HTMLResponse

import pulse_automation_rules as rules

MAX_PENDING_RETRIES = 100
MAX_FINISHED_RETRIES = 300
MAX_PENDING_APPROVALS = 50
MAX_FINISHED_APPROVALS = 300
RETRIES_PER_CYCLE = 5
APPROVALS_PER_CYCLE = 5
MAX_NOTIFIED_APPROVERS = 25
INTERRUPTED = "Ausführung wurde unterbrochen (z. B. Neustart) – Ergebnis unklar, bitte prüfen und manuell fortsetzen."
RETRY_STATUS = {"pending": "⏳ geplant", "running": "▶ läuft", "done": "✅ erledigt", "dead": "☠️ Dead-Letter",
                "cancelled": "🚫 abgebrochen"}
APPROVAL_STATUS = {"pending": "⏳ wartet auf Freigabe", "approved": "✅ freigegeben", "executing": "▶ wird ausgeführt",
                   "executed": "✅ ausgeführt", "rejected": "❌ abgelehnt", "expired": "⌛ Frist abgelaufen",
                   "cancelled": "🚫 abgebrochen", "failed": "⚠ unterbrochen"}
_ACTIVE_RETRIES: set = set()
_ACTIVE_APPROVALS: set = set()


def _pu():
    import pulse_ultimate
    return pulse_ultimate


# ---------------------------------------------------------------------------------------------- schema
def ensure_schema(c) -> None:
    """Additive tables and columns (idempotent). Called by pulse_ultimate.init_db with its open connection."""
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS ultimate_automation_versions(
            id TEXT PRIMARY KEY,
            automation_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            definition_json TEXT NOT NULL,
            definition_hash TEXT NOT NULL,
            note TEXT DEFAULT '',
            created_by_id TEXT NOT NULL,
            created_by_name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(automation_id, version)
        );
        CREATE TRIGGER IF NOT EXISTS ultimate_automation_versions_no_update
            BEFORE UPDATE ON ultimate_automation_versions
            BEGIN SELECT RAISE(ABORT, 'automation versions are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS ultimate_automation_versions_no_delete
            BEFORE DELETE ON ultimate_automation_versions
            BEGIN SELECT RAISE(ABORT, 'automation versions are immutable'); END;
        CREATE TABLE IF NOT EXISTS ultimate_automation_retries(
            id TEXT PRIMARY KEY,
            automation_id TEXT NOT NULL,
            automation_name TEXT DEFAULT '',
            automation_version INTEGER DEFAULT 0,
            origin_run_id TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL,
            attempt INTEGER DEFAULT 0,
            max_attempts INTEGER NOT NULL,
            backoff_seconds INTEGER NOT NULL,
            next_attempt_at TEXT,
            steps_json TEXT NOT NULL,
            context_json TEXT DEFAULT '{}',
            trigger_info TEXT DEFAULT '{}',
            last_error TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ultimate_automation_retries_due
            ON ultimate_automation_retries(status, next_attempt_at);
        CREATE TABLE IF NOT EXISTS ultimate_automation_approvals(
            id TEXT PRIMARY KEY,
            automation_id TEXT NOT NULL,
            automation_name TEXT NOT NULL,
            automation_version INTEGER DEFAULT 0,
            status TEXT NOT NULL,
            current_stage INTEGER DEFAULT 0,
            stages_json TEXT NOT NULL,
            decisions_json TEXT DEFAULT '[]',
            trigger_info TEXT DEFAULT '{}',
            context_json TEXT DEFAULT '{}',
            requested_by_id TEXT DEFAULT '',
            requested_by_name TEXT DEFAULT '',
            requested_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            finished_at TEXT,
            finish_note TEXT DEFAULT '',
            run_id TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_ultimate_automation_approvals_status
            ON ultimate_automation_approvals(status, expires_at);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_ultimate_automation_approvals_one_pending
            ON ultimate_automation_approvals(automation_id) WHERE status='pending';
        """
    )
    for table, additions in (
        ("ultimate_automations", (("retry_json", "TEXT DEFAULT ''"), ("approval_json", "TEXT DEFAULT ''"),
                                  ("current_version", "INTEGER DEFAULT 0"))),
        ("ultimate_automation_runs", (("automation_version", "INTEGER DEFAULT 0"),)),
    ):
        columns = {row[1] for row in c.execute(f"PRAGMA table_info({table})").fetchall()}
        for column, kind in additions:
            if column not in columns:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")


# ---------------------------------------------------------------------------------------------- definitions
def _json(text, default):
    try:
        value = json.loads(text or "")
    except (TypeError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


def retry_config(automation: dict) -> dict:
    raw = _json(automation.get("retry_json"), {})
    try:
        return rules.normalize_retry(raw)
    except ValueError:
        return {"max_attempts": 0, "backoff_seconds": rules.RETRY_BACKOFF_DEFAULT}


def approval_config(automation: dict) -> dict:
    raw = _json(automation.get("approval_json"), {})
    try:
        return rules.normalize_approval(raw)
    except ValueError:  # a damaged row must never run unapproved: one administrator stage blocks it visibly
        return {"stages": [{"label": "Administrator", "role": "admin", "min_approvals": 1}],
                "timeout_minutes": rules.APPROVAL_TIMEOUT_DEFAULT}


def row_definition(row: dict) -> dict:
    """Definition snapshot of a stored automation (legacy single-action rows become one step, like the editor)."""
    pu = _pu()
    payload = _json(row.get("action_payload"), {})
    steps = pu.automation_steps(row)
    if not steps and row.get("action_type"):
        steps = [{"type": row["action_type"], **{k: v for k, v in payload.items() if k != "condition"}}]
    return rules.definition_snapshot({
        "name": row.get("name"), "trigger_type": row.get("trigger_type"), "interval_minutes": row.get("interval_minutes"),
        "trigger_config": pu.automation_trigger_config(row), "condition": payload.get("condition") or {"type": "always"},
        "steps": steps, "test_mode": bool(row.get("test_mode")), "retry": retry_config(row), "approval": approval_config(row),
    })


def record_version(c, automation_id: str, snapshot: dict, *, author_id: str, author_name: str, note: str = "") -> tuple[int, bool]:
    """Append a version when the definition differs from the newest one. Returns (version, created)."""
    pu = _pu()
    digest = rules.definition_hash(snapshot)
    last = c.execute("SELECT version,definition_hash FROM ultimate_automation_versions WHERE automation_id=? "
                     "ORDER BY version DESC LIMIT 1", (automation_id,)).fetchone()
    if last and last["definition_hash"] == digest:
        return int(last["version"]), False
    version = (int(last["version"]) if last else 0) + 1
    c.execute(
        "INSERT INTO ultimate_automation_versions(id,automation_id,version,definition_json,definition_hash,note,"
        "created_by_id,created_by_name,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (pu.uid("aver"), automation_id, version, json.dumps(snapshot, ensure_ascii=False, sort_keys=True), digest,
         str(note or "")[:300], str(author_id)[:40], str(author_name)[:80], pu.iso()))
    c.execute("UPDATE ultimate_automations SET current_version=? WHERE id=?", (version, automation_id))
    return version, True


def versions(automation_id: str) -> list[dict]:
    pu = _pu()
    with pu.cx() as c:
        rows = c.execute("SELECT * FROM ultimate_automation_versions WHERE automation_id=? ORDER BY version DESC",
                         (automation_id,)).fetchall()
    return [{**dict(r), "definition": _json(r["definition_json"], {})} for r in rows]


def on_definition_changed(c, automation_id: str, reason: str, *, deleted: bool = False) -> None:
    """Open approvals and queued retries belong to the old definition – end them instead of running them."""
    now = _pu().iso()
    c.execute("UPDATE ultimate_automation_approvals SET status='cancelled',finished_at=?,finish_note=? "
              "WHERE automation_id=? AND status IN ('pending','approved')", (now, reason[:300], automation_id))
    c.execute("UPDATE ultimate_automation_retries SET status=?,last_error=?,updated_at=? "
              "WHERE automation_id=? AND status='pending'", ("cancelled" if deleted else "dead", reason[:300], now, automation_id))


# ---------------------------------------------------------------------------------------------- retries
def _plain_context(context: dict) -> dict:
    """Only simple values survive in the queue (no Discord objects); bounded in size."""
    out = {}
    for key, value in (context or {}).items():
        if isinstance(value, (str, int, float, bool)):
            out[str(key)[:60]] = value if not isinstance(value, str) else value[:300]
        elif isinstance(value, list) and all(isinstance(v, (str, int)) for v in value):
            out[str(key)[:60]] = value[:20]
    return out if len(json.dumps(out, ensure_ascii=False)) <= 4000 else {}


def _auto_retryable(step: dict, detail: str) -> bool:
    return step.get("type") not in rules.NO_AUTO_RETRY_STEP_TYPES and not rules.is_permanent_failure(detail)


def _prune(c, table: str, finished: tuple, keep: int, order: str) -> None:
    marks = ",".join("?" for _ in finished)
    c.execute(f"DELETE FROM {table} WHERE status IN ({marks}) AND id NOT IN "
              f"(SELECT id FROM {table} WHERE status IN ({marks}) ORDER BY {order} DESC LIMIT ?)",
              (*finished, *finished, keep))


def schedule_retry(automation: dict, run_id: str, steps: list, steps_done: list, context: dict, trigger_info: dict) -> str | None:
    """Queue the failed steps of one real run exactly once (origin_run_id is unique). Returns the queue id."""
    cfg = retry_config(automation)
    if cfg["max_attempts"] <= 0:
        return None
    entries = []
    for index, (step, done) in enumerate(zip(steps, steps_done)):
        if done.get("status") != "failed":
            continue
        detail = str(done.get("detail") or "")[:300]
        entries.append({"index": index, "step": step, "status": "failed", "detail": detail,
                        "auto": _auto_retryable(step, detail)})
    if not entries:
        return None
    pu = _pu()
    now = pu.utcnow()
    status, error = ("pending", "") if any(e["auto"] for e in entries) else \
        ("dead", "Fehler lässt sich nicht automatisch beheben – bitte Ursache prüfen und manuell fortsetzen.")
    retry_id = pu.uid("retry")
    with pu.cx() as c:
        if status == "pending" and c.execute(
                "SELECT COUNT(*) FROM ultimate_automation_retries WHERE status='pending'").fetchone()[0] >= MAX_PENDING_RETRIES:
            status, error = "dead", "Warteschlange für Wiederholungen ist voll – nicht automatisch wiederholt."
        cur = c.execute(
            "INSERT OR IGNORE INTO ultimate_automation_retries(id,automation_id,automation_name,automation_version,origin_run_id,"
            "status,attempt,max_attempts,backoff_seconds,next_attempt_at,steps_json,context_json,trigger_info,last_error,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (retry_id, automation["id"], str(automation.get("name") or "")[:160], int(automation.get("current_version") or 0),
             run_id, status, 0, cfg["max_attempts"], cfg["backoff_seconds"],
             pu.iso(now + timedelta(seconds=rules.backoff_delay(cfg["backoff_seconds"], 1))),
             json.dumps(entries, ensure_ascii=False), json.dumps(_plain_context(context), ensure_ascii=False),
             json.dumps(_plain_context(trigger_info), ensure_ascii=False), error, pu.iso(now), pu.iso(now)))
        if cur.rowcount != 1:
            return None
        _prune(c, "ultimate_automation_retries", ("done", "cancelled", "dead"), MAX_FINISHED_RETRIES, "updated_at")
    if status == "dead":
        _dead_letter_event(automation["id"], automation.get("name", ""), retry_id, error)
    return retry_id


def _dead_letter_event(automation_id: str, name: str, retry_id: str, reason: str) -> None:
    try:
        _pu().db.record_event("Automation: Dead-Letter", "automation", automation_id, "0", "Pulse Automation",
                              {"automation": name, "retry_id": retry_id, "reason": str(reason)[:300]})
    except Exception:
        pass


def _set_retry(retry_id: str, **fields) -> None:
    pu = _pu()
    fields["updated_at"] = pu.iso()
    with pu.cx() as c:
        c.execute(f"UPDATE ultimate_automation_retries SET {','.join(f'{k}=?' for k in fields)} WHERE id=?",
                  (*fields.values(), retry_id))


async def _attempt_retry(bot, row: dict) -> bool:
    pu = _pu()
    with pu.cx() as c:
        found = c.execute("SELECT * FROM ultimate_automations WHERE id=?", (row["automation_id"],)).fetchone()
    if not found:
        _set_retry(row["id"], status="cancelled", last_error="Automation wurde gelöscht.")
        return False
    automation = dict(found)
    blocker = ("Automation ist pausiert." if not automation["enabled"] else
               "Automation ist im Testmodus." if automation.get("test_mode") else
               "Definition wurde seit dem Lauf geändert." if int(automation.get("current_version") or 0) != int(row["automation_version"] or 0)
               else "")
    if blocker:
        _set_retry(row["id"], status="dead", last_error=blocker)
        _dead_letter_event(automation["id"], automation.get("name", ""), row["id"], blocker)
        return False
    if automation["id"] in pu._RUNNING:
        return False  # the automation itself is running – try again in the next cycle
    with pu.cx() as c:  # claim: exactly one worker moves pending → running
        claimed = c.execute("UPDATE ultimate_automation_retries SET status='running',attempt=attempt+1,updated_at=? "
                            "WHERE id=? AND status='pending'", (pu.iso(), row["id"])).rowcount == 1
    if not claimed:
        return False
    attempt = int(row["attempt"] or 0) + 1
    entries = _json(row["steps_json"], [])
    context = _json(row["context_json"], {})
    _ACTIVE_RETRIES.add(row["id"])
    pu._RUNNING.add(automation["id"])
    token = pu._DISPATCH_DEPTH.set(pu._DISPATCH_DEPTH.get() + 1)  # retried steps never trigger automations
    started, results = pu.iso(), []
    try:
        import webserver
        guild = bot.get_guild(webserver.GUILD_ID) if bot else None
        for entry in entries:
            if entry.get("status") == "ok" or not entry.get("auto", True):
                continue
            entry["status"] = "running"  # stored first: an interrupted step is never repeated automatically
            await asyncio.to_thread(_set_retry, row["id"], steps_json=json.dumps(entries, ensure_ascii=False))
            try:
                state, detail = await pu._run_step(bot, guild, automation, entry.get("step") or {}, context)
            except Exception as exc:
                state, detail = "failed", f"Fehler: {type(exc).__name__}"
            detail = str(detail)[:300]
            entry["status"] = "ok" if state in ("ok", "skipped") else "failed"
            entry["detail"] = detail
            entry["auto"] = entry["status"] != "ok" and _auto_retryable(entry.get("step") or {}, detail)
            await asyncio.to_thread(_set_retry, row["id"], steps_json=json.dumps(entries, ensure_ascii=False))
            results.append({"type": (entry.get("step") or {}).get("type"), "status": state, "detail": detail})
        remaining = [e for e in entries if e.get("status") != "ok"]
        error = " · ".join(f"Aktion {int(e.get('index', 0)) + 1}: {e.get('detail', '')}" for e in remaining)[:1000]
        if not remaining:
            status, next_at = "done", None
        elif attempt >= int(row["max_attempts"]) or not any(e.get("auto") for e in remaining):
            status, next_at = "dead", None
        else:
            status = "pending"
            next_at = pu.iso(pu.utcnow() + timedelta(seconds=rules.backoff_delay(row["backoff_seconds"], attempt + 1)))
        await asyncio.to_thread(_set_retry, row["id"], status=status, next_attempt_at=next_at, last_error=error,
                                steps_json=json.dumps(entries, ensure_ascii=False))
        summary = f"Wiederholung {attempt}/{row['max_attempts']}: " + (
            " · ".join(f"{rules.STEP_TYPES.get(r['type'], r['type'])}: {r['detail']}" for r in results) or "keine Aktion ausgeführt")
        await asyncio.to_thread(
            pu._store_run, automation, "success" if status == "done" else "error", summary, started=started, duration_ms=0,
            trigger_info={"trigger": "retry", "attempt": attempt, "origin_run": row["origin_run_id"], "retry_id": row["id"]},
            steps=results, actor_name="Pulse Wiederholung")
        if status == "dead":
            _dead_letter_event(automation["id"], automation.get("name", ""), row["id"], error)
        return True
    finally:
        pu._DISPATCH_DEPTH.reset(token)
        pu._RUNNING.discard(automation["id"])
        _ACTIVE_RETRIES.discard(row["id"])


async def process_retries(bot, limit: int = RETRIES_PER_CYCLE) -> int:
    """Run the due retries (bounded per cycle). Interrupted attempts move to the dead-letter list first."""
    pu = _pu()
    now = pu.iso()
    with pu.cx() as c:
        for stale in c.execute("SELECT id,automation_id,automation_name FROM ultimate_automation_retries WHERE status='running'").fetchall():
            if stale["id"] in _ACTIVE_RETRIES:
                continue
            c.execute("UPDATE ultimate_automation_retries SET status='dead',last_error=?,updated_at=? WHERE id=? AND status='running'",
                      (INTERRUPTED, now, stale["id"]))
        due = [dict(r) for r in c.execute(
            "SELECT * FROM ultimate_automation_retries WHERE status='pending' AND next_attempt_at<=? "
            "ORDER BY next_attempt_at LIMIT ?", (now, max(1, int(limit)))).fetchall()]
    ran = 0
    for row in due:
        try:
            ran += 1 if await _attempt_retry(bot, row) else 0
        except Exception as exc:  # one broken queue entry must not stop the others
            print(f"⚠️ Wiederholung {row.get('id')} fehlgeschlagen: {type(exc).__name__}")
    return ran


def resume_retry(retry_id: str) -> dict:
    """A person puts a dead-letter entry back into the queue for exactly one more attempt of every open step."""
    pu = _pu()
    with pu.cx() as c:
        found = c.execute("SELECT * FROM ultimate_automation_retries WHERE id=?", (retry_id,)).fetchone()
        if not found:
            raise LookupError("Eintrag nicht gefunden.")
        row = dict(found)
        if row["status"] != "dead":
            raise ValueError("Nur Dead-Letter-Einträge können manuell fortgesetzt werden.")
        entries = _json(row["steps_json"], [])
        for entry in entries:
            if entry.get("status") != "ok":
                entry["auto"] = True
        automation = c.execute("SELECT current_version FROM ultimate_automations WHERE id=?", (row["automation_id"],)).fetchone()
        if not automation:
            raise ValueError("Die Automation existiert nicht mehr.")
        changed = c.execute(
            "UPDATE ultimate_automation_retries SET status='pending',max_attempts=?,next_attempt_at=?,steps_json=?,"
            "automation_version=?,updated_at=? WHERE id=? AND status='dead'",
            (int(row["attempt"] or 0) + 1, pu.iso(), json.dumps(entries, ensure_ascii=False),
             int(automation["current_version"] or 0), pu.iso(), retry_id)).rowcount
    if changed != 1:
        raise ValueError("Eintrag wurde gleichzeitig geändert.")
    return row


def cancel_retry(retry_id: str) -> dict:
    pu = _pu()
    with pu.cx() as c:
        found = c.execute("SELECT * FROM ultimate_automation_retries WHERE id=?", (retry_id,)).fetchone()
        if not found:
            raise LookupError("Eintrag nicht gefunden.")
        if c.execute("UPDATE ultimate_automation_retries SET status='cancelled',last_error=?,updated_at=? "
                     "WHERE id=? AND status IN ('pending','dead')", ("Manuell abgebrochen.", pu.iso(), retry_id)).rowcount != 1:
            raise ValueError("Dieser Eintrag kann nicht mehr abgebrochen werden.")
    return dict(found)


# ---------------------------------------------------------------------------------------------- approvals
def request_approval(automation: dict, trigger_info: dict, context: dict, actor_id: str = "", actor_name: str = "") -> dict | None:
    """Create the approval request for one triggered run. None = this automation needs no approval."""
    stages = approval_config(automation)["stages"]
    if not stages:
        return None
    pu = _pu()
    now = pu.utcnow()
    request_id = pu.uid("appr")
    with pu.cx() as c:
        existing = c.execute("SELECT * FROM ultimate_automation_approvals WHERE automation_id=? AND status='pending'",
                             (automation["id"],)).fetchone()
        if existing:
            return {"created": False, "request": dict(existing), "summary": "Freigabe steht bereits aus – kein zweiter Antrag."}
        if c.execute("SELECT COUNT(*) FROM ultimate_automation_approvals WHERE status='pending'").fetchone()[0] >= MAX_PENDING_APPROVALS:
            return {"created": False, "request": None, "summary": "Zu viele offene Freigaben – Lauf nicht angefordert."}
        try:
            c.execute(
                "INSERT INTO ultimate_automation_approvals(id,automation_id,automation_name,automation_version,status,current_stage,"
                "stages_json,decisions_json,trigger_info,context_json,requested_by_id,requested_by_name,requested_at,expires_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (request_id, automation["id"], str(automation.get("name") or "")[:160], int(automation.get("current_version") or 0),
                 "pending", 0, json.dumps(stages, ensure_ascii=False), "[]",
                 json.dumps(_plain_context(trigger_info), ensure_ascii=False), json.dumps(_plain_context(context), ensure_ascii=False),
                 str(actor_id or "")[:40], str(actor_name or "")[:80], pu.iso(now),
                 pu.iso(now + timedelta(minutes=approval_config(automation)["timeout_minutes"]))))
        except sqlite3.IntegrityError:  # a parallel trigger created the request first
            return {"created": False, "request": None, "summary": "Freigabe steht bereits aus – kein zweiter Antrag."}
        _prune(c, "ultimate_automation_approvals", ("executed", "rejected", "expired", "cancelled", "failed"),
               MAX_FINISHED_APPROVALS, "requested_at")
        row = dict(c.execute("SELECT * FROM ultimate_automation_approvals WHERE id=?", (request_id,)).fetchone())
    return {"created": True, "request": row,
            "summary": f"Freigabe angefordert – Stufe 1/{len(stages)}: {stages[0]['label']}."}


def approval(request_id: str) -> dict | None:
    with _pu().cx() as c:
        row = c.execute("SELECT * FROM ultimate_automation_approvals WHERE id=?", (request_id,)).fetchone()
    return dict(row) if row else None


def may_decide(stage: dict, *, is_admin: bool, is_manager: bool) -> bool:
    return is_admin if stage.get("role") == "admin" else bool(is_admin or is_manager)


def decide(request_id: str, *, user_id: str, user_name: str, is_admin: bool, is_manager: bool, decision: str,
           comment: str = "") -> dict:
    """One decision of one person. Raises LookupError / PermissionError / ValueError with a German message."""
    pu = _pu()
    if decision not in ("approve", "reject"):
        raise ValueError("Unbekannte Entscheidung.")
    comment = str(comment or "").strip()[:500]
    user_id = str(user_id)
    with pu.cx() as c:
        found = c.execute("SELECT * FROM ultimate_automation_approvals WHERE id=?", (request_id,)).fetchone()
        if not found:
            raise LookupError("Freigabe nicht gefunden.")
        row = dict(found)
        if row["status"] != "pending":
            raise ValueError("Diese Freigabe ist bereits abgeschlossen.")
        now = pu.iso()
        if row["expires_at"] <= now:
            c.execute("UPDATE ultimate_automation_approvals SET status='expired',finished_at=?,finish_note=? WHERE id=? AND status='pending'",
                      (now, "Frist abgelaufen.", request_id))
            c.commit()
            raise ValueError("Die Frist für diese Freigabe ist abgelaufen.")
        stages, decisions = _json(row["stages_json"], []), _json(row["decisions_json"], [])
        index = int(row["current_stage"] or 0)
        if index >= len(stages):
            raise ValueError("Diese Freigabe ist bereits abgeschlossen.")
        stage = stages[index]
        if not may_decide(stage, is_admin=is_admin, is_manager=is_manager):
            raise PermissionError(f"Stufe „{stage.get('label')}“ darf nur die Gruppe {rules.APPROVER_ROLES.get(stage.get('role'), '?')} entscheiden.")
        if any(str(d.get("user_id")) == user_id for d in decisions):
            raise ValueError("Du hast bei dieser Freigabe bereits entschieden – jede Person entscheidet höchstens einmal.")
        if decision == "approve" and row["requested_by_id"] and row["requested_by_id"] == user_id:
            raise PermissionError("Den selbst angeforderten Lauf darfst du nicht freigeben (Vier-Augen-Prinzip).")
        if decision == "reject" and not comment:
            raise ValueError("Bitte die Ablehnung kurz begründen.")
        decisions.append({"stage": index, "user_id": user_id, "user_name": str(user_name)[:80], "decision": decision,
                          "comment": comment, "at": now})
        status, finished, note, next_index = "pending", None, "", index
        if decision == "reject":
            status, finished, note = "rejected", now, f"Abgelehnt in Stufe {index + 1} von {str(user_name)[:80]}."
        elif sum(d["stage"] == index and d["decision"] == "approve" for d in decisions) >= int(stage.get("min_approvals", 1)):
            next_index = index + 1
            if next_index >= len(stages):
                status = "approved"
        changed = c.execute(
            "UPDATE ultimate_automation_approvals SET status=?,current_stage=?,decisions_json=?,finished_at=?,finish_note=? "
            "WHERE id=? AND status='pending' AND decisions_json=?",
            (status, next_index, json.dumps(decisions, ensure_ascii=False), finished, note, request_id, row["decisions_json"])).rowcount
        if changed != 1:
            raise ValueError("Die Freigabe wurde gleichzeitig geändert – bitte erneut versuchen.")
        return dict(c.execute("SELECT * FROM ultimate_automation_approvals WHERE id=?", (request_id,)).fetchone())


def cancel_approval(request_id: str, *, user_id: str, is_admin: bool, note: str = "") -> dict:
    pu = _pu()
    with pu.cx() as c:
        found = c.execute("SELECT * FROM ultimate_automation_approvals WHERE id=?", (request_id,)).fetchone()
        if not found:
            raise LookupError("Freigabe nicht gefunden.")
        row = dict(found)
        if not (is_admin or (row["requested_by_id"] and row["requested_by_id"] == str(user_id))):
            raise PermissionError("Nur die anfordernde Person oder Administratoren können abbrechen.")
        if c.execute("UPDATE ultimate_automation_approvals SET status='cancelled',finished_at=?,finish_note=? "
                     "WHERE id=? AND status IN ('pending','approved')",
                     (pu.iso(), (note or "Manuell abgebrochen.")[:300], request_id)).rowcount != 1:
            raise ValueError("Diese Freigabe kann nicht mehr abgebrochen werden.")
    return row


def _finish_approval(request_id: str, status: str, note: str, run_id: str = "") -> None:
    pu = _pu()
    with pu.cx() as c:
        c.execute("UPDATE ultimate_automation_approvals SET status=?,finished_at=?,finish_note=?,run_id=? WHERE id=?",
                  (status, pu.iso(), note[:300], run_id, request_id))


async def execute_approved(bot, request_id: str, actor_name: str = "") -> dict:
    """Run a fully approved request exactly once (approved → executing is claimed atomically)."""
    pu = _pu()
    with pu.cx() as c:
        claimed = c.execute("UPDATE ultimate_automation_approvals SET status='executing' WHERE id=? AND status='approved'",
                            (request_id,)).rowcount == 1
        row = c.execute("SELECT * FROM ultimate_automation_approvals WHERE id=?", (request_id,)).fetchone()
        found = c.execute("SELECT * FROM ultimate_automations WHERE id=?", (row["automation_id"],)).fetchone() if row else None
    if not claimed:
        return {"status": "skipped", "summary": "Freigabe ist nicht (mehr) ausführbar."}
    row = dict(row)
    _ACTIVE_APPROVALS.add(request_id)
    try:
        automation = dict(found) if found else None
        blocker = ("Automation wurde gelöscht." if automation is None else
                   "Automation ist pausiert." if not automation["enabled"] else
                   "Definition wurde seit der Anforderung geändert." if int(automation.get("current_version") or 0) != int(row["automation_version"] or 0)
                   else "")
        if blocker:
            await asyncio.to_thread(_finish_approval, request_id, "cancelled", blocker)
            return {"status": "skipped", "summary": blocker}
        if automation["id"] in pu._RUNNING:  # busy right now: keep it approved, the automation loop picks it up
            with pu.cx() as c:
                c.execute("UPDATE ultimate_automation_approvals SET status='approved' WHERE id=? AND status='executing'", (request_id,))
            return {"status": "skipped", "summary": "Automation läuft gerade – Ausführung folgt automatisch."}
        info = {**_json(row["trigger_info"], {}), "approval_id": request_id}
        result = await pu.execute_automation(bot, automation, trigger_info=info, context=_json(row["context_json"], {}),
                                             actor_name=actor_name or "Freigabe", approved=True)
        await asyncio.to_thread(_finish_approval, request_id, "executed", f"Ausgeführt: {result.get('status')}", result.get("run_id") or "")
        return result
    finally:
        _ACTIVE_APPROVALS.discard(request_id)


async def process_approvals(bot, limit: int = APPROVALS_PER_CYCLE) -> int:
    """Automation loop: expire overdue requests, mark interrupted executions, run approved leftovers."""
    pu = _pu()
    now = pu.iso()
    with pu.cx() as c:
        expired = [dict(r) for r in c.execute(
            "SELECT * FROM ultimate_automation_approvals WHERE status='pending' AND expires_at<=?", (now,)).fetchall()]
        c.execute("UPDATE ultimate_automation_approvals SET status='expired',finished_at=?,finish_note=? "
                  "WHERE status='pending' AND expires_at<=?", (now, "Frist abgelaufen.", now))
        for stale in c.execute("SELECT id FROM ultimate_automation_approvals WHERE status='executing'").fetchall():
            if stale["id"] not in _ACTIVE_APPROVALS:
                c.execute("UPDATE ultimate_automation_approvals SET status='failed',finished_at=?,finish_note=? "
                          "WHERE id=? AND status='executing'", (now, INTERRUPTED, stale["id"]))
        ready = [r["id"] for r in c.execute(
            "SELECT id FROM ultimate_automation_approvals WHERE status='approved' ORDER BY requested_at LIMIT ?",
            (max(1, int(limit)),)).fetchall()]
    for row in expired:
        notify_outcome({**row, "status": "expired"})
    ran = 0
    for request_id in ready:
        try:
            result = await execute_approved(bot, request_id)
            ran += 0 if result.get("status") == "skipped" else 1
        except Exception as exc:
            print(f"⚠️ Freigegebene Automation {request_id} fehlgeschlagen: {type(exc).__name__}")
    return ran


async def notify_stage(bot, request: dict) -> int:
    """Inbox notice for the people who may decide the current stage (bounded, best effort)."""
    pu = _pu()
    try:
        import webserver
        guild = bot.get_guild(webserver.GUILD_ID) if bot else None
        stages = _json(request.get("stages_json"), [])
        index = int(request.get("current_stage") or 0)
        if guild is None or index >= len(stages):
            return 0
        decided = {str(d.get("user_id")) for d in _json(request.get("decisions_json"), [])}
        config, sent = webserver.load_config(), 0
        for member in pu.team_members(guild):
            if sent >= MAX_NOTIFIED_APPROVERS:
                break
            if str(member.id) in decided or str(member.id) == str(request.get("requested_by_id") or ""):
                continue
            perms, _ = webserver.compute_perms(guild, member.id, config)
            if not may_decide(stages[index], is_admin=bool(perms.get("is_admin")), is_manager=bool(perms.get("can_promote"))):
                continue
            pu.db.notify(member.id, f"Freigabe erforderlich: {request.get('automation_name', '')}"[:160],
                         f"Stufe {index + 1}/{len(stages)} „{stages[index].get('label', '')}“ wartet auf deine Entscheidung "
                         f"(Frist: {request.get('expires_at', '')}).", "warning", "/ultimate/automations#approvals",
                         dedupe_key=f"automation-approval:{request.get('id')}:{index}")
            sent += 1
        return sent
    except Exception as exc:
        print(f"⚠️ Freigabe-Benachrichtigung fehlgeschlagen: {type(exc).__name__}")
        return 0


def notify_outcome(request: dict) -> None:
    """Tell the requesting person how the request ended (inbox only, best effort)."""
    target = str(request.get("requested_by_id") or "")
    if not target.isdigit():
        return
    try:
        _pu().db.notify(int(target), f"Freigabe {APPROVAL_STATUS.get(request.get('status'), request.get('status'))}: "
                        f"{request.get('automation_name', '')}"[:160],
                        str(request.get("finish_note") or "Die Freigabe wurde abgeschlossen.")[:500],
                        "info" if request.get("status") in ("approved", "executed") else "warning",
                        "/ultimate/automations#approvals", dedupe_key=f"automation-approval-end:{request.get('id')}")
    except Exception:
        pass


# ---------------------------------------------------------------------------------------------- HTML
def editor_fields(record: dict | None) -> str:
    """Retry and approval inputs of the automation editor."""
    esc = _pu().esc
    retry = retry_config(record or {})
    approval_cfg = approval_config(record or {}) if record else {"stages": [], "timeout_minutes": rules.APPROVAL_TIMEOUT_DEFAULT}
    stage_rows = []
    for i in range(1, rules.MAX_APPROVAL_STAGES + 1):
        stage = approval_cfg["stages"][i - 1] if i <= len(approval_cfg["stages"]) else {}
        roles = "<option value=''>— keine Stufe —</option>" + "".join(
            f"<option value='{k}'{' selected' if stage.get('role') == k else ''}>{esc(v)}</option>" for k, v in rules.APPROVER_ROLES.items())
        stage_rows.append(
            f"<div style='display:flex;flex-wrap:wrap;gap:6px;margin-top:6px'><b class='tiny' style='min-width:60px'>Stufe {i}</b>"
            f"<select class='select' style='max-width:210px' name='approval{i}_role' aria-label='Zuständigkeit Stufe {i}'>{roles}</select>"
            f"<input class='input' style='max-width:260px' name='approval{i}_label' maxlength='80' placeholder='Bezeichnung, z. B. Teamleitung' value='{esc(stage.get('label', ''))}'>"
            f"<input class='input' style='max-width:120px' type='number' name='approval{i}_min' min='1' max='{rules.MAX_STAGE_APPROVALS}' "
            f"value='{esc(stage.get('min_approvals', 1))}' aria-label='Nötige Freigaben Stufe {i}'></div>")
    return f"""<label class="tiny span2"><b>FREIGABE</b> – optional: Stufen werden in dieser Reihenfolge entschieden, erst danach laufen die Aktionen</label>
 <div class="span2">{''.join(stage_rows)}
  <div class="tiny" style="margin-top:6px">Frist <input class="input" style="max-width:110px;display:inline-block" type="number" name="approval_timeout" min="{rules.APPROVAL_TIMEOUT_MIN}" max="{rules.APPROVAL_TIMEOUT_MAX}" value="{esc(approval_cfg['timeout_minutes'])}" aria-label="Frist für Freigaben in Minuten"> Minuten ·
  jede Person entscheidet höchstens einmal, niemand gibt den eigenen Lauf frei. Ablehnung, Fristablauf oder eine geänderte Automation beenden den Antrag.</div></div>
 <label class="tiny span2"><b>WIEDERHOLUNG</b> – fehlgeschlagene Aktionen automatisch erneut versuchen (0 = aus)</label>
 <input class="input" type="number" name="retry_max_attempts" min="0" max="{rules.RETRY_MAX_ATTEMPTS}" value="{esc(retry['max_attempts'])}" aria-label="Anzahl automatischer Wiederholungen">
 <input class="input" type="number" name="retry_backoff_seconds" min="{rules.RETRY_BACKOFF_MIN}" max="{rules.RETRY_BACKOFF_MAX}" value="{esc(retry['backoff_seconds'])}" aria-label="Wartezeit vor der ersten Wiederholung in Sekunden">
 <div class="tiny span2">Die Wartezeit verdoppelt sich je Versuch. Wiederholt werden nur fehlgeschlagene Aktionen – nie bereits erfolgreiche. Ein Notfall-Lockdown wird nie automatisch wiederholt.</div>"""


def editor_payload(form) -> dict:
    stages = [{"role": str(form.get(f"approval{i}_role") or "").strip(), "label": form.get(f"approval{i}_label") or "",
               "min_approvals": form.get(f"approval{i}_min")} for i in range(1, rules.MAX_APPROVAL_STAGES + 1)]
    return {"retry": {"max_attempts": form.get("retry_max_attempts"), "backoff_seconds": form.get("retry_backoff_seconds")},
            "approval": {"stages": stages, "timeout_minutes": form.get("approval_timeout")}}


def _decisions_html(row: dict) -> str:
    esc = _pu().esc
    stages = _json(row.get("stages_json"), [])
    out = []
    for i, stage in enumerate(stages):
        given = [d for d in _json(row.get("decisions_json"), []) if d.get("stage") == i]
        mark = "✅" if sum(d.get("decision") == "approve" for d in given) >= int(stage.get("min_approvals", 1)) else (
            "❌" if any(d.get("decision") == "reject" for d in given) else ("⏳" if i == int(row.get("current_stage") or 0) and row.get("status") == "pending" else "•"))
        who = ", ".join(f"{esc(d.get('user_name', ''))} ({'Freigabe' if d.get('decision') == 'approve' else 'Ablehnung'}"
                        + (f": {esc(d.get('comment'))}" if d.get("comment") else "") + ")" for d in given) or "noch keine Entscheidung"
        out.append(f"<li>{mark} Stufe {i + 1} „{esc(stage.get('label', ''))}“ · {esc(rules.APPROVER_ROLES.get(stage.get('role'), '?'))} · "
                   f"{int(stage.get('min_approvals', 1))} nötig · {who}</li>")
    return "<ul class='tiny' style='margin:4px 0 0 16px'>" + "".join(out) + "</ul>"


def panels(*, admin: bool, manager: bool, user_id: str, visible_ids: set) -> str:
    """Approval inbox and retry/dead-letter list for the automations page (only automations the viewer may see)."""
    pu = _pu()
    esc = pu.esc
    with pu.cx() as c:
        approvals = [dict(r) for r in c.execute(
            "SELECT * FROM ultimate_automation_approvals ORDER BY (status='pending') DESC, requested_at DESC LIMIT 40").fetchall()]
        retries = [dict(r) for r in c.execute(
            "SELECT * FROM ultimate_automation_retries ORDER BY (status IN ('pending','running','dead')) DESC, updated_at DESC LIMIT 40").fetchall()]
        known = {r[0] for r in c.execute("SELECT id FROM ultimate_automations").fetchall()}

    def visible(row):
        return row["automation_id"] in visible_ids or (admin and row["automation_id"] not in known)

    approval_html = []
    for row in [r for r in approvals if visible(r)][:20]:
        stages = _json(row["stages_json"], [])
        index = int(row["current_stage"] or 0)
        actions = ""
        if row["status"] == "pending" and index < len(stages):
            decided = any(str(d.get("user_id")) == str(user_id) for d in _json(row["decisions_json"], []))
            if may_decide(stages[index], is_admin=admin, is_manager=manager) and not decided:
                own = row["requested_by_id"] and row["requested_by_id"] == str(user_id)
                actions += (f"<form class='form' method='post' action='/ultimate/automations/approvals/{esc(row['id'])}/decide' style='margin-top:6px'>"
                            "<input class='input span2' name='comment' maxlength='500' placeholder='Kommentar (bei Ablehnung Pflicht)'>"
                            + ("" if own else "<button class='btn primary' name='decision' value='approve'>✅ Freigeben</button>")
                            + "<button class='btn danger' name='decision' value='reject'>❌ Ablehnen</button></form>")
            if admin or (row["requested_by_id"] and row["requested_by_id"] == str(user_id)):
                actions += (f"<form method='post' action='/ultimate/automations/approvals/{esc(row['id'])}/cancel' style='margin-top:6px'>"
                            "<button class='btn'>🚫 Antrag abbrechen</button></form>")
        approval_html.append(
            f"<div class='event'><b>{esc(row['automation_name'])}</b> · Version {int(row['automation_version'] or 0)} · "
            f"{APPROVAL_STATUS.get(row['status'], esc(row['status']))}"
            f"<div class='tiny'>angefordert {esc(row['requested_at'])}"
            + (f" von {esc(row['requested_by_name'])}" if row.get("requested_by_name") else " durch Auslöser")
            + (f" · Frist {esc(row['expires_at'])}" if row["status"] == "pending" else "")
            + (f" · {esc(row.get('finish_note') or '')}" if row.get("finish_note") else "") + "</div>"
            + _decisions_html(row) + actions + "</div>")

    retry_html = []
    for row in [r for r in retries if visible(r)][:20]:
        entries = _json(row["steps_json"], [])
        steps = "".join(
            f"<li>{'✅' if e.get('status') == 'ok' else ('❓' if e.get('status') == 'running' else '❌')} Aktion {int(e.get('index', 0)) + 1} "
            f"({esc(rules.STEP_TYPES.get((e.get('step') or {}).get('type'), (e.get('step') or {}).get('type')))}): {esc(e.get('detail', ''))}"
            + ("" if e.get("status") == "ok" or e.get("auto") else " · nur manuell") + "</li>" for e in entries if isinstance(e, dict))
        actions = ""
        if row["status"] == "dead":
            actions += (f"<form method='post' action='/ultimate/automations/retries/{esc(row['id'])}/resume' style='display:inline-flex;gap:6px;align-items:center;margin-top:6px'>"
                        "<label class='tiny'><input type='checkbox' name='confirm' value='1' required> offene Aktionen einmal erneut ausführen</label>"
                        "<button class='btn primary'>▶ Fortsetzen</button></form> ")
        if row["status"] in ("dead", "pending"):
            actions += (f"<form method='post' action='/ultimate/automations/retries/{esc(row['id'])}/cancel' style='display:inline-block;margin-top:6px'>"
                        "<button class='btn'>🚫 Verwerfen</button></form>")
        retry_html.append(
            f"<div class='event'><b>{esc(row.get('automation_name') or row['automation_id'])}</b> · {RETRY_STATUS.get(row['status'], esc(row['status']))}"
            f" · Versuch {int(row['attempt'] or 0)}/{int(row['max_attempts'] or 0)}"
            + (f" · nächster Versuch {esc(row['next_attempt_at'])}" if row["status"] == "pending" and row.get("next_attempt_at") else "")
            + (f"<div class='tiny'>{esc(row['last_error'])}</div>" if row.get("last_error") else "")
            + f"<ul class='tiny' style='margin:4px 0 0 16px'>{steps}</ul>{actions}</div>")
    return (f"<div class='card' id='approvals' style='margin-top:13px'><h3>🖊️ Freigaben</h3>"
            f"<div class='timeline'>{''.join(approval_html) or '<div class=tiny>Keine Freigaben offen oder protokolliert.</div>'}</div></div>"
            f"<div class='card' id='retries' style='margin-top:13px'><h3>🔁 Wiederholungen &amp; Dead-Letter</h3>"
            f"<div class='tiny'>Höchstens {MAX_PENDING_RETRIES} geplante Wiederholungen; abgeschlossene Einträge werden auf {MAX_FINISHED_RETRIES} begrenzt.</div>"
            f"<div class='timeline'>{''.join(retry_html) or '<div class=tiny>Keine Wiederholungen geplant, kein Dead-Letter-Eintrag.</div>'}</div></div>")


def _parse_version(text) -> int | None:
    text = str(text or "").strip()
    return int(text) if text.isdigit() and len(text) <= 6 else None


# ---------------------------------------------------------------------------------------------- routes
def register(app, *, load_automation, is_admin, automation_view, save_definition) -> None:
    """Routes for versions, approvals and retries. Permission helpers come from pulse_ultimate.register."""
    pu = _pu()
    esc = pu.esc

    def actor(ctx) -> tuple[str, str]:
        return str(ctx.user["id"]), ctx.user.get("global_name") or ctx.user.get("username") or "Team"

    def back(msg: str, ok: bool, anchor: str = "", path: str = "/ultimate/automations"):
        import webserver
        return webserver.back(path + anchor, msg, ok)

    def guarded_automation(automation_id: str, ctx) -> dict | None:
        """The automation behind a request/queue entry; 403 for security automations, None when it was deleted."""
        try:
            return load_automation(automation_id, ctx)
        except HTTPException as exc:
            if exc.status_code == 404:
                if not is_admin(ctx):
                    raise HTTPException(403, "Nur Administratoren dürfen Einträge gelöschter Automationen bearbeiten.")
                return None
            raise

    @app.get("/ultimate/automations/{automation_id}/versions", response_class=HTMLResponse)
    async def ultimate_automation_versions(request: Request, automation_id: str, user_session: str = Cookie(None)):
        ctx = pu.ctx_auth(request, user_session, manager=True)
        record = load_automation(automation_id, ctx)
        history = versions(automation_id)
        current = row_definition(record)
        by_number = {v["version"]: v for v in history}
        a, b = _parse_version(request.query_params.get("a")), _parse_version(request.query_params.get("b"))
        compare = ""
        if a in by_number:
            right = by_number[b]["definition"] if b in by_number else current
            right_label = f"Version {b}" if b in by_number else "aktueller Stand"
            changes = rules.diff_definitions(by_number[a]["definition"], right)
            rows = "".join(f"<tr><td>{esc(ch['label'])}</td><td>{esc(ch['before'])}</td><td>{esc(ch['after'])}</td></tr>" for ch in changes)
            compare = pu.card(
                f"<h3 style='margin-top:0'>🔍 Vergleich: Version {a} → {esc(right_label)}</h3>"
                + (f"<div style='overflow-x:auto'><table class='table'><thead><tr><th>Feld</th><th>Version {a}</th><th>{esc(right_label)}</th></tr></thead>"
                   f"<tbody>{rows}</tbody></table></div>" if changes else "<div class='tiny'>Keine Unterschiede.</div>"))
        items = []
        for v in history:
            is_current = v["version"] == int(record.get("current_version") or 0)
            links = (f"<a class='btn' href='/ultimate/automations/{esc(automation_id)}/versions?a={v['version']}'>🔍 mit aktuellem Stand vergleichen</a>"
                     + (f"<a class='btn' href='/ultimate/automations/{esc(automation_id)}/versions?a={v['version'] - 1}&amp;b={v['version']}'>↔ mit Vorversion vergleichen</a>"
                        if v["version"] - 1 in by_number else ""))
            restore = "" if is_current else (
                f"<form method='post' action='/ultimate/automations/{esc(automation_id)}/versions/{v['version']}/restore' "
                "style='display:inline-flex;gap:6px;align-items:center'>"
                "<label class='tiny'><input type='checkbox' name='confirm' value='1' required> Wiederherstellen bestätigen</label>"
                "<button class='btn danger'>↩ Wiederherstellen (pausiert)</button></form>")
            items.append(
                f"<div class='event'><b>Version {v['version']}</b>{' · <span class=pill>aktuell</span>' if is_current else ''}"
                f"<div class='tiny'>{esc(v['created_at'])} · {esc(v['created_by_name'])}" + (f" · {esc(v['note'])}" if v.get("note") else "") + "</div>"
                f"<div class='tiny'>{esc(rules.describe(v['definition']))}</div>"
                f"<div style='display:flex;flex-wrap:wrap;gap:6px;margin-top:6px'>{links}{restore}</div></div>")
        body = (f"<div class='card'><a class='btn' href='/ultimate/automations'>← Zurück zu den Automationen</a>"
                f"<div class='tiny' style='margin-top:8px'>Versionen sind unveränderlich. Wiederherstellen legt eine neue Version an und pausiert die "
                f"Automation, bis sie bewusst wieder aktiviert wird. Offene Freigaben und geplante Wiederholungen der alten Definition enden dabei.</div></div>"
                f"{compare}<div class='card' style='margin-top:13px'><h3>🕘 Versionsverlauf „{esc(record['name'])}“</h3><div class='timeline'>"
                + ("".join(items) or "<div class='tiny'>Noch keine Version gespeichert – sie entsteht beim nächsten Speichern im Editor.</div>")
                + "</div></div>")
        return pu.page(ctx, "automations", "Automation – Versionen", "Verlauf, Vergleich und kontrolliertes Wiederherstellen.", body)

    @app.post("/ultimate/automations/{automation_id}/versions/{version}/restore")
    async def ultimate_automation_version_restore(request: Request, automation_id: str, version: str, user_session: str = Cookie(None)):
        ctx = pu.ctx_auth(request, user_session, manager=True)
        record = load_automation(automation_id, ctx)
        form = await request.form()
        path = f"/ultimate/automations/{automation_id}/versions"
        if form.get("confirm") != "1":
            return back("Wiederherstellen bitte bestätigen.", False, path=path)
        number = _parse_version(version)
        target = next((v for v in versions(automation_id) if v["version"] == number), None)
        if target is None:
            raise HTTPException(404, "Version nicht gefunden.")
        try:  # the old definition passes today's validation and today's rights – nothing is restored blindly
            value = rules.normalize_automation({**target["definition"], "enabled": False}, is_admin=is_admin(ctx))
        except PermissionError as exc:
            raise HTTPException(403, str(exc))
        except ValueError as exc:
            return back(f"Version {number} ist nicht mehr gültig: {exc}", False, path=path)
        user_id, user_name = actor(ctx)
        _, new_version, changed = save_definition(value, existing=record, user_id=user_id, user_name=user_name,
                                                  note=f"Wiederhergestellt aus Version {number}", force_disabled=True)
        if not changed:
            return back(f"Version {number} entspricht bereits dem aktuellen Stand.", False, path=path)
        pu.audit(ctx, "Automation-Version wiederhergestellt", automation_id, record["name"],
                 f"Version {number} → neue Version {new_version}, Automation pausiert")
        return back(f"Version {number} als Version {new_version} wiederhergestellt. Die Automation ist pausiert – bitte prüfen und aktivieren.",
                    True, path=path)

    @app.post("/ultimate/automations/approvals/{request_id}/decide")
    async def ultimate_automation_approval_decide(request: Request, request_id: str, user_session: str = Cookie(None)):
        ctx = pu.ctx_auth(request, user_session, manager=True)
        row = approval(request_id)
        if row is None:
            raise HTTPException(404, "Freigabe nicht gefunden.")
        guarded_automation(row["automation_id"], ctx)
        form = await request.form()
        user_id, user_name = actor(ctx)
        try:
            updated = decide(request_id, user_id=user_id, user_name=user_name, is_admin=is_admin(ctx),
                             is_manager=bool(ctx.perms.get("can_promote")), decision=str(form.get("decision") or ""),
                             comment=str(form.get("comment") or ""))
        except PermissionError as exc:
            raise HTTPException(403, str(exc))
        except LookupError as exc:
            raise HTTPException(404, str(exc))
        except ValueError as exc:
            return back(str(exc), False, "#approvals")
        pu.audit(ctx, "Automation-Freigabe entschieden", row["automation_id"], row["automation_name"],
                 f"{form.get('decision')} · Stufe {int(row['current_stage'] or 0) + 1} · Status {updated['status']}")
        bot = getattr(request.app.state, "bot", None)
        if updated["status"] == "rejected":
            notify_outcome(updated)
            return back("Freigabe abgelehnt – die Automation wird nicht ausgeführt.", True, "#approvals")
        if updated["status"] == "approved":
            result = await execute_approved(bot, request_id, actor_name=f"Freigabe durch {user_name}"[:80])
            notify_outcome({**updated, "status": "executed", "finish_note": f"Alle Stufen freigegeben. Ergebnis: {result.get('summary', '')}"})
            return back(f"Alle Stufen freigegeben. Ausführung: {str(result.get('summary') or result.get('status'))[:200]}",
                        result.get("status") in ("success", "test", "skipped"), "#approvals")
        await notify_stage(bot, updated)
        return back("Freigabe gespeichert – die nächste Entscheidung steht aus.", True, "#approvals")

    @app.post("/ultimate/automations/approvals/{request_id}/cancel")
    async def ultimate_automation_approval_cancel(request: Request, request_id: str, user_session: str = Cookie(None)):
        ctx = pu.ctx_auth(request, user_session, manager=True)
        row = approval(request_id)
        if row is None:
            raise HTTPException(404, "Freigabe nicht gefunden.")
        guarded_automation(row["automation_id"], ctx)
        try:
            cancel_approval(request_id, user_id=actor(ctx)[0], is_admin=is_admin(ctx))
        except PermissionError as exc:
            raise HTTPException(403, str(exc))
        except ValueError as exc:
            return back(str(exc), False, "#approvals")
        pu.audit(ctx, "Automation-Freigabe abgebrochen", row["automation_id"], row["automation_name"], request_id)
        return back("Antrag abgebrochen – die Automation wird nicht ausgeführt.", True, "#approvals")

    def retry_row(retry_id: str, ctx) -> dict:
        with pu.cx() as c:
            found = c.execute("SELECT * FROM ultimate_automation_retries WHERE id=?", (retry_id,)).fetchone()
        if not found:
            raise HTTPException(404, "Eintrag nicht gefunden.")
        guarded_automation(found["automation_id"], ctx)
        return dict(found)

    @app.post("/ultimate/automations/retries/{retry_id}/resume")
    async def ultimate_automation_retry_resume(request: Request, retry_id: str, user_session: str = Cookie(None)):
        ctx = pu.ctx_auth(request, user_session, manager=True)
        row = retry_row(retry_id, ctx)
        form = await request.form()
        if form.get("confirm") != "1":
            return back("Fortsetzen bitte bestätigen.", False, "#retries")
        try:
            resume_retry(retry_id)
        except ValueError as exc:
            return back(str(exc), False, "#retries")
        pu.audit(ctx, "Automation-Wiederholung manuell fortgesetzt", row["automation_id"], row.get("automation_name") or "", retry_id)
        return back("Eintrag wieder eingeplant – die offenen Aktionen laufen im nächsten Automationslauf genau einmal.", True, "#retries")

    @app.post("/ultimate/automations/retries/{retry_id}/cancel")
    async def ultimate_automation_retry_cancel(request: Request, retry_id: str, user_session: str = Cookie(None)):
        ctx = pu.ctx_auth(request, user_session, manager=True)
        row = retry_row(retry_id, ctx)
        try:
            cancel_retry(retry_id)
        except ValueError as exc:
            return back(str(exc), False, "#retries")
        pu.audit(ctx, "Automation-Wiederholung verworfen", row["automation_id"], row.get("automation_name") or "", retry_id)
        return back("Eintrag verworfen.", True, "#retries")
