"""Audit #124: one task update path for the bot and the dashboard.

Found divergence (existing code, unchanged here):

* web ``/tasks/update``: manager = ``can_manage_tasks or can_promote or is_admin``; assignee may change the
  status; **no event and no notification** is written.
* bot ``/taskdone``: manager = ``can_manage_tasks or is_admin`` (leadership with ``can_promote`` is refused);
  writes the ``task_completed`` event; no notification.

:func:`update` is the single, tested rule for both surfaces: the web rule decides who may do what (policy
action ``task.manage``), every change writes the same contract event (``pulse_events`` domain ``task``,
correlation ``task:<id>``, so the live stream and history show web and bot changes alike) and the people
concerned are notified through ``pulse_inbox_contract`` – never the person who made the change.

A person who is only the assignee may change the **status** of their own task and nothing else.
Not wired into the web route or the bot command yet; both keep working unchanged.
"""
from __future__ import annotations

import logging

import pulse_events as events
import pulse_inbox_contract as inbox
import pulse_policy as policy

log = logging.getLogger("pulse.tasks")

STATUSES = ("open", "in_progress", "done", "archived")
PRIORITIES = ("low", "normal", "high", "urgent")
MAX_NOTE = 500
MAX_DUE = 40


def may_manage(perms) -> bool:
    return policy.can(perms, "task.manage")


def _result(ok, code, message, task=None, changes=None):
    return {"ok": ok, "code": code, "message": message, "task": task, "changes": changes or {}}


MAX_TITLE = 160
MAX_DESCRIPTION = 2500


def create(perms, actor_id, actor_name, title, *, description="", assignee_id=None, assignee_name="", priority="normal",
           due_at=None, source="web", db=None, notifier=None) -> dict:
    """Create a task with the shared rule (``task.manage``), one contract event and a notification to the assignee
    (never to the creator). Same result shape as :func:`update`: ``{ok, code, message, task, changes}``
    (``code``: ``forbidden``, ``invalid``, ``ok``)."""
    if db is None:
        import pulse_db as db
    if not may_manage(perms):
        return _result(False, "forbidden", "Dafür fehlt dir die Berechtigung.")
    clean_title = str(title or "").strip()[:MAX_TITLE]
    if not clean_title:
        return _result(False, "invalid", "Der Titel fehlt.")
    if priority not in PRIORITIES:
        priority = "normal"
    if due_at is not None and len(str(due_at)) > MAX_DUE:
        return _result(False, "invalid", "Ungültige Deadline.")
    assignee = str(assignee_id) if assignee_id not in (None, "") else None
    if assignee is not None and not assignee.isdigit():
        return _result(False, "invalid", "Ungültige Zuständigkeit.")
    actor = str(actor_id)
    tid = db.create_task(clean_title, str(description or "").strip()[:MAX_DESCRIPTION], assignee, assignee_name or "", actor,
                         actor_name, priority, due_at or None)
    task = db.get_task(tid)
    events.emit_safe("task", "created", target_type="task", target_id=tid, actor_id=actor, actor_name=actor_name,
                     payload={"title": clean_title[:160], "source": source, "priority": priority, "assigned": bool(assignee)},
                     correlation_id=f"task:{tid}")
    if assignee and assignee != actor:
        try:
            inbox.deliver([assignee], "task", "created", "📋 Neue Aufgabe", f"{clean_title[:120]} wurde dir von {actor_name} zugewiesen.",
                          correlation_id=f"task:{tid}", target_id=tid, kind="warning" if priority == "urgent" else "info", notifier=notifier)
        except Exception:  # noqa: BLE001 - a notification problem must not undo the task
            log.warning("Aufgaben-Benachrichtigung fehlgeschlagen", exc_info=True)
    return _result(True, "ok", "Aufgabe erstellt.", task, {"created": (None, tid)})


def update(perms, actor_id, actor_name, task_id, *, status=None, priority=None, due_at=None, assignee_id=None,
           assignee_name=None, note="", source="web", db=None, notifier=None) -> dict:
    """Apply a task change with the shared rule. Returns ``{ok, code, message, task, changes}``; never raises
    for rule violations (``code``: ``not_found``, ``forbidden``, ``invalid``, ``unchanged``, ``ok``)."""
    if db is None:
        import pulse_db as db
    task = db.get_task(str(task_id or "").strip())
    if not task:
        return _result(False, "not_found", "Aufgabe nicht gefunden.")
    actor = str(actor_id)
    manager = may_manage(perms)
    is_assignee = str(task.get("assignee_id") or "") == actor and actor != ""
    if not (manager or is_assignee):
        return _result(False, "forbidden", "Du bist nicht für diese Aufgabe zuständig.", task)
    if not manager and any(v is not None for v in (priority, due_at, assignee_id, assignee_name)):
        return _result(False, "forbidden", "Als Zuständige/r darfst du nur den Status ändern.", task)
    if status is not None and status not in STATUSES:
        return _result(False, "invalid", "Ungültiger Status.", task)
    if priority is not None and priority not in PRIORITIES:
        return _result(False, "invalid", "Ungültige Priorität.", task)
    if due_at is not None and len(str(due_at)) > MAX_DUE:
        return _result(False, "invalid", "Ungültige Deadline.", task)
    if assignee_id is not None and assignee_id != "" and not str(assignee_id).isdigit():
        return _result(False, "invalid", "Ungültige Zuständigkeit.", task)

    changes: dict = {}
    if status is not None and status != task.get("status"):
        changes["status"] = (task.get("status"), status)
    if priority is not None and priority != task.get("priority"):
        changes["priority"] = (task.get("priority"), priority)
    if due_at is not None and str(due_at) != str(task.get("due_at") or ""):
        changes["due_at"] = (task.get("due_at"), str(due_at))
    new_assignee = None if assignee_id is None else str(assignee_id)
    if new_assignee is not None and new_assignee != str(task.get("assignee_id") or ""):
        changes["assignee"] = (task.get("assignee_id"), new_assignee)
    if not changes:
        return _result(False, "unchanged", "Nichts zu ändern.", task)

    clean_note = str(note or f"Per {'Discord' if source == 'discord' else 'Dashboard'} geändert")[:MAX_NOTE]
    db.update_task(task["id"], status=status if "status" in changes else None,
                   priority=priority if "priority" in changes else None,
                   due_at=due_at if "due_at" in changes else None,
                   assignee_id=new_assignee if "assignee" in changes else None,
                   assignee_name=assignee_name if "assignee" in changes else None,
                   actor_id=actor, actor_name=actor_name, note=clean_note)
    updated = db.get_task(task["id"])

    if changes.get("status", (None, None))[1] == "done":
        action = "completed"
    elif "assignee" in changes:
        action = "reassigned"
    else:
        action = "updated"
    events.emit_safe("task", action, target_type="task", target_id=task["id"], actor_id=actor, actor_name=actor_name,
                     payload={"title": str(task.get("title") or "")[:160], "source": source,
                              "changes": sorted(changes)},
                     correlation_id=f"task:{task['id']}")
    _notify(task, changes, action, actor, actor_name, notifier)
    return _result(True, "ok", "Aufgabe aktualisiert.", updated, changes)


def _notify(task, changes, action, actor, actor_name, notifier) -> None:
    """New assignee on reassignment; the creator when someone else completes. Never the actor, never twice."""
    title = str(task.get("title") or "Aufgabe")[:120]
    targets: dict[str, tuple[str, str]] = {}
    new_assignee = changes.get("assignee", (None, None))[1]
    if new_assignee:
        targets[str(new_assignee)] = ("📋 Aufgabe zugewiesen", f"{title} wurde dir von {actor_name} zugewiesen.")
    if action == "completed" and task.get("creator_id"):
        targets.setdefault(str(task["creator_id"]), ("✅ Aufgabe erledigt", f"{title} wurde von {actor_name} erledigt."))
    for uid, (head, body) in targets.items():
        if uid == actor:
            continue
        try:
            inbox.deliver([uid], "task", action, head, body, correlation_id=f"task:{task['id']}",
                          target_id=task["id"], notifier=notifier)
        except Exception:  # noqa: BLE001 - a notification problem must not undo the change
            log.warning("Aufgaben-Benachrichtigung fehlgeschlagen", exc_info=True)
