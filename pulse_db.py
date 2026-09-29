import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

DB_PATH = os.getenv("PULSE_DB_PATH", "pulse.db")
_LOCK = threading.RLock()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    with _LOCK:
        conn = sqlite3.connect(DB_PATH, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=15000")
            conn.execute("PRAGMA journal_mode=WAL")
            yield conn
            conn.commit()
        finally:
            conn.close()


def _ensure_column(db, table: str, column: str, definition: str):
    columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db():
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS notifications (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                kind TEXT DEFAULT 'info',
                url TEXT DEFAULT '/pulse-inbox',
                read_at TEXT,
                created_at TEXT NOT NULL,
                dedupe_key TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, read_at, created_at);

            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                assignee_id TEXT,
                assignee_name TEXT,
                creator_id TEXT NOT NULL,
                creator_name TEXT NOT NULL,
                status TEXT DEFAULT 'open',
                priority TEXT DEFAULT 'normal',
                due_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                archived_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, due_at);
            CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(assignee_id, status);
            CREATE INDEX IF NOT EXISTS idx_tasks_creator ON tasks(creator_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS task_history (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                actor_name TEXT NOT NULL,
                old_status TEXT,
                new_status TEXT,
                old_priority TEXT,
                new_priority TEXT,
                note TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_task_history_task ON task_history(task_id, created_at);

            CREATE TABLE IF NOT EXISTS tickets (
                id TEXT PRIMARY KEY,
                channel_id TEXT UNIQUE,
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                user_name TEXT NOT NULL,
                category TEXT NOT NULL,
                status TEXT DEFAULT 'open',
                priority TEXT DEFAULT 'normal',
                claimed_by_id TEXT,
                claimed_by_name TEXT,
                opened_at TEXT NOT NULL,
                claimed_at TEXT,
                closed_at TEXT,
                close_reason TEXT DEFAULT '',
                rating INTEGER,
                rating_comment TEXT DEFAULT '',
                transcript_path TEXT DEFAULT '',
                closed_by_id TEXT,
                closed_by_name TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status, priority, opened_at);
            CREATE INDEX IF NOT EXISTS idx_tickets_claimed ON tickets(claimed_by_id, status);
            CREATE INDEX IF NOT EXISTS idx_tickets_user ON tickets(user_id, opened_at DESC);

            CREATE TABLE IF NOT EXISTS ticket_events (
                id TEXT PRIMARY KEY,
                ticket_id TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                actor_name TEXT NOT NULL,
                event_type TEXT NOT NULL,
                details TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ticket_events_ticket ON ticket_events(ticket_id, created_at);

            CREATE TABLE IF NOT EXISTS wiki_pages (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                category TEXT NOT NULL,
                content TEXT NOT NULL,
                author_id TEXT NOT NULL,
                author_name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS achievements (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                icon TEXT DEFAULT '🏅',
                metric TEXT NOT NULL,
                target INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS achievement_progress (
                achievement_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                progress INTEGER DEFAULT 0,
                unlocked_at TEXT,
                PRIMARY KEY (achievement_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS trainings (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                passing_score INTEGER DEFAULT 80,
                time_limit_minutes INTEGER DEFAULT 20,
                published INTEGER DEFAULT 0,
                created_by_id TEXT NOT NULL,
                created_by_name TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS training_questions (
                id TEXT PRIMARY KEY,
                training_id TEXT NOT NULL,
                question TEXT NOT NULL,
                options_json TEXT NOT NULL,
                answer_index INTEGER NOT NULL,
                position INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS training_attempts (
                id TEXT PRIMARY KEY,
                training_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                user_name TEXT NOT NULL,
                score INTEGER NOT NULL,
                passed INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_training_attempts_user ON training_attempts(user_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_training_attempts_training ON training_attempts(training_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS meetings_history (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                date_time TEXT NOT NULL,
                description TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                attendees_json TEXT DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_meeting_history_date ON meetings_history(date_time);

            CREATE TABLE IF NOT EXISTS team_status (
                user_id TEXT PRIMARY KEY,
                user_name TEXT NOT NULL,
                status TEXT DEFAULT 'available',
                message TEXT DEFAULT '',
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS handover_notes (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                author_id TEXT NOT NULL,
                author_name TEXT NOT NULL,
                priority TEXT DEFAULT 'normal',
                created_at TEXT NOT NULL,
                archived_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_handover_created ON handover_notes(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_handover_author ON handover_notes(author_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_handover_active ON handover_notes(archived_at, created_at DESC);

            CREATE TABLE IF NOT EXISTS announcements (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                author_id TEXT NOT NULL,
                author_name TEXT NOT NULL,
                kind TEXT DEFAULT 'info',
                discord_message_id TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_announcements_created ON announcements(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_announcements_kind ON announcements(kind, created_at DESC);

            CREATE TABLE IF NOT EXISTS promotion_requests (
                id TEXT PRIMARY KEY,
                target_user_id TEXT NOT NULL,
                target_user_name TEXT NOT NULL,
                current_role TEXT DEFAULT '',
                requested_role TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_by_id TEXT NOT NULL,
                created_by_name TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                decided_by_id TEXT,
                decided_by_name TEXT,
                decision_note TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                decided_at TEXT,
                requested_role_id TEXT,
                current_role_id TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_promotion_status ON promotion_requests(status, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_promotion_target ON promotion_requests(target_user_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS checkins (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                user_name TEXT NOT NULL,
                kind TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_checkins_user ON checkins(user_id, kind, created_at);

            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                target_type TEXT DEFAULT '',
                target_id TEXT DEFAULT '',
                actor_id TEXT DEFAULT '',
                actor_name TEXT DEFAULT '',
                payload_json TEXT DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_events_target ON events(target_type, target_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_events_actor ON events(actor_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL
            );
            """
        )
        # Migrations for v4 installations.
        _ensure_column(db, "notifications", "dedupe_key", "TEXT")
        _ensure_column(db, "tasks", "archived_at", "TEXT")
        _ensure_column(db, "tickets", "closed_by_id", "TEXT")
        _ensure_column(db, "tickets", "closed_by_name", "TEXT")
        _ensure_column(db, "promotion_requests", "requested_role_id", "TEXT")
        _ensure_column(db, "promotion_requests", "current_role_id", "TEXT")
        db.execute("CREATE INDEX IF NOT EXISTS idx_notifications_dedupe ON notifications(user_id, dedupe_key, created_at)")
        _seed(db)


def _seed(db):
    defaults = [
        ("ach_first_shift", "Erste Schicht", "Schließe deine erste Schicht ab.", "⏱️", "shifts", 1),
        ("ach_ticket_50", "Support-Profi", "Bearbeite 50 Tickets.", "🎫", "tickets_closed", 50),
        ("ach_hours_10", "Dauerbrenner", "Sammle 10 Dienststunden.", "🔥", "hours", 10),
        ("ach_meeting_10", "Besprechungsprofi", "Nimm an 10 Meetings teil.", "🎙️", "meetings", 10),
        ("ach_flag_100", "Flaggenmeister", "Erreiche 100 richtige Flaggenantworten.", "🚩", "flag_correct", 100),
        ("ach_handover_10", "Saubere Übergabe", "Erstelle 10 Übergaben für das Team.", "🧭", "handovers", 10),
        ("ach_task_25", "Organisationstalent", "Erledige 25 Aufgaben.", "✅", "tasks_done", 25),
    ]
    for row in defaults:
        db.execute("INSERT OR IGNORE INTO achievements VALUES (?, ?, ?, ?, ?, ?)", row)


def make_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def notify(user_id, title, body, kind="info", url="/pulse-inbox", dedupe_key=None, dedupe_window_seconds=21600):
    with connect() as db:
        uid = str(user_id)
        if dedupe_key:
            existing = db.execute(
                "SELECT id FROM notifications WHERE user_id=? AND dedupe_key=? AND julianday(created_at) >= julianday('now', ?) ORDER BY created_at DESC LIMIT 1",
                (uid, str(dedupe_key), f"-{int(dedupe_window_seconds)} seconds"),
            ).fetchone()
            if existing:
                return existing[0]
        nid = make_id("notif")
        db.execute(
            "INSERT INTO notifications(id,user_id,title,body,kind,url,read_at,created_at,dedupe_key) VALUES (?,?,?,?,?,?,NULL,?,?)",
            (nid, uid, title, body, kind, url, _now(), str(dedupe_key) if dedupe_key else None),
        )
        return nid


def notifications(user_id, limit=50):
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (str(user_id), max(1, min(int(limit), 500))),
        ).fetchall()
        return [dict(r) for r in rows]


def unread_count(user_id):
    with connect() as db:
        return db.execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND read_at IS NULL", (str(user_id),)).fetchone()[0]


def mark_notifications_read(user_id, nid=None):
    with connect() as db:
        now = _now()
        if nid:
            db.execute("UPDATE notifications SET read_at=? WHERE id=? AND user_id=?", (now, nid, str(user_id)))
        else:
            db.execute("UPDATE notifications SET read_at=? WHERE user_id=? AND read_at IS NULL", (now, str(user_id)))


def create_task(title, description, assignee_id, assignee_name, creator_id, creator_name, priority="normal", due_at=None):
    with connect() as db:
        tid = make_id("task")
        now = _now()
        db.execute(
            "INSERT INTO tasks(id,title,description,assignee_id,assignee_name,creator_id,creator_name,status,priority,due_at,created_at,updated_at,archived_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
            (tid, title, description or "", str(assignee_id) if assignee_id else None, assignee_name or "", str(creator_id), creator_name, "open", priority, due_at, now, now),
        )
        return tid


def list_tasks(status=None, assignee_id=None, limit=200, include_archived=False):
    with connect() as db:
        sql = "SELECT * FROM tasks WHERE 1=1"
        args = []
        if not include_archived:
            sql += " AND (status!='archived' OR status IS NULL)"
        if status:
            sql += " AND status=?"; args.append(status)
        if assignee_id:
            sql += " AND assignee_id=?"; args.append(str(assignee_id))
        sql += " ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END, due_at IS NULL, due_at, created_at DESC LIMIT ?"
        args.append(max(1, min(int(limit), 1000)))
        return [dict(r) for r in db.execute(sql, args).fetchall()]


def get_task(tid):
    with connect() as db:
        row = db.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
        return dict(row) if row else None


def update_task(tid, status=None, priority=None, due_at=None, assignee_id=None, assignee_name=None, actor_id=None, actor_name=None, note=""):
    with connect() as db:
        current = db.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
        if not current:
            return False
        fields, args = [], []
        old_status, old_priority = current["status"], current["priority"]
        if status is not None:
            fields.append("status=?"); args.append(status)
            if status == "done":
                fields.append("archived_at=?"); args.append(_now())
            elif status != "archived":
                fields.append("archived_at=NULL")
        if priority is not None:
            fields.append("priority=?"); args.append(priority)
        if due_at is not None:
            fields.append("due_at=?"); args.append(due_at)
        if assignee_id is not None:
            fields.append("assignee_id=?"); args.append(str(assignee_id) if assignee_id else None)
        if assignee_name is not None:
            fields.append("assignee_name=?"); args.append(assignee_name)
        if not fields:
            return True
        fields.append("updated_at=?"); args.append(_now()); args.append(tid)
        db.execute(f"UPDATE tasks SET {', '.join(fields)} WHERE id=?", args)
        if actor_id:
            db.execute(
                "INSERT INTO task_history VALUES (?,?,?,?,?,?,?,?,?,?)",
                (make_id("th"), tid, str(actor_id), actor_name or "System", old_status, status if status is not None else old_status,
                 old_priority, priority if priority is not None else old_priority, note or "", _now()),
            )
        return True


def task_history(task_id, limit=100):
    with connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM task_history WHERE task_id=? ORDER BY created_at DESC LIMIT ?", (task_id, limit)).fetchall()]


def create_ticket(channel_id, guild_id, user_id, user_name, category, priority="normal"):
    with connect() as db:
        tid = make_id("ticket")
        now = _now()
        db.execute(
            "INSERT INTO tickets(id,channel_id,guild_id,user_id,user_name,category,status,priority,opened_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (tid, str(channel_id), str(guild_id), str(user_id), user_name, category, "open", priority, now),
        )
        db.execute("INSERT INTO ticket_events VALUES (?,?,?,?,?,?,?)", (make_id("te"), tid, str(user_id), user_name, "opened", category, now))
        return tid


def get_ticket(ticket_id=None, channel_id=None):
    with connect() as db:
        row = db.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone() if ticket_id else db.execute("SELECT * FROM tickets WHERE channel_id=?", (str(channel_id),)).fetchone()
        return dict(row) if row else None


def list_tickets(status=None, limit=200, claimed_by_id=None):
    with connect() as db:
        sql = "SELECT * FROM tickets WHERE 1=1"; args = []
        if status:
            sql += " AND status=?"; args.append(status)
        if claimed_by_id:
            sql += " AND claimed_by_id=?"; args.append(str(claimed_by_id))
        sql += " ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END, opened_at DESC LIMIT ?"
        args.append(max(1, min(int(limit), 2000)))
        return [dict(r) for r in db.execute(sql, args).fetchall()]


def claim_ticket(tid, user_id, user_name):
    with connect() as db:
        now = _now()
        cur = db.execute(
            "UPDATE tickets SET claimed_by_id=?,claimed_by_name=?,claimed_at=?,status='in_progress' WHERE id=? AND status!='closed' AND (claimed_by_id IS NULL OR claimed_by_id=?)",
            (str(user_id), user_name, now, tid, str(user_id)),
        )
        if cur.rowcount:
            db.execute("INSERT INTO ticket_events VALUES (?,?,?,?,?,?,?)", (make_id("te"), tid, str(user_id), user_name, "claimed", "", now))
            return True
        return False


def unclaim_ticket(tid, user_id, user_name):
    with connect() as db:
        now = _now()
        cur = db.execute("UPDATE tickets SET claimed_by_id=NULL,claimed_by_name=NULL,claimed_at=NULL,status='open' WHERE id=? AND status!='closed' AND claimed_by_id=?", (tid, str(user_id)))
        if cur.rowcount:
            db.execute("INSERT INTO ticket_events VALUES (?,?,?,?,?,?,?)", (make_id("te"), tid, str(user_id), user_name, "unclaimed", "", now))
            return True
        return False


def update_ticket_priority(tid, priority, actor_id=None, actor_name=None):
    with connect() as db:
        old = db.execute("SELECT priority FROM tickets WHERE id=?", (tid,)).fetchone()
        if not old:
            return False
        db.execute("UPDATE tickets SET priority=? WHERE id=? AND status!='closed'", (priority, tid))
        if actor_id:
            db.execute("INSERT INTO ticket_events VALUES (?,?,?,?,?,?,?)", (make_id("te"), tid, str(actor_id), actor_name or "System", "priority", f"{old[0]} → {priority}", _now()))
        return True


def close_ticket(tid, reason, transcript_path="", closed_by_id=None, closed_by_name=None):
    with connect() as db:
        now = _now()
        cur = db.execute(
            "UPDATE tickets SET status='closed',closed_at=?,close_reason=?,transcript_path=?,closed_by_id=?,closed_by_name=? WHERE id=? AND status!='closed'",
            (now, reason or "", transcript_path or "", str(closed_by_id) if closed_by_id else None, closed_by_name or None, tid),
        )
        if cur.rowcount:
            db.execute("INSERT INTO ticket_events VALUES (?,?,?,?,?,?,?)", (make_id("te"), tid, str(closed_by_id or "0"), closed_by_name or "System", "closed", reason or "", now))
            return True
        return False


def rate_ticket(tid, rating, comment=""):
    rating = max(1, min(5, int(rating)))
    with connect() as db:
        return db.execute("UPDATE tickets SET rating=?,rating_comment=? WHERE id=?", (rating, comment or "", tid)).rowcount > 0


def ticket_events(ticket_id, limit=100):
    with connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM ticket_events WHERE ticket_id=? ORDER BY created_at DESC LIMIT ?", (ticket_id, limit)).fetchall()]


def save_wiki(title, category, content, author_id, author_name, page_id=None):
    with connect() as db:
        now = _now(); pid = page_id or make_id("wiki")
        if page_id:
            db.execute("UPDATE wiki_pages SET title=?,category=?,content=?,author_id=?,author_name=?,updated_at=? WHERE id=?", (title, category, content, str(author_id), author_name, now, page_id))
        else:
            db.execute("INSERT INTO wiki_pages VALUES (?,?,?,?,?,?,?,?)", (pid, title, category, content, str(author_id), author_name, now, now))
        return pid


def wiki_pages():
    with connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM wiki_pages ORDER BY category,title").fetchall()]


def get_wiki(pid):
    with connect() as db:
        row = db.execute("SELECT * FROM wiki_pages WHERE id=?", (pid,)).fetchone()
        return dict(row) if row else None


def delete_wiki(pid):
    with connect() as db:
        return db.execute("DELETE FROM wiki_pages WHERE id=?", (pid,)).rowcount > 0


def save_meeting_history(title, date_time, description, notes, attendees):
    with connect() as db:
        mid = make_id("meeting")
        db.execute("INSERT INTO meetings_history VALUES (?,?,?,?,?,?,?)", (mid, title, date_time, description or "", notes or "", json.dumps(attendees or {}, ensure_ascii=False), _now()))
        return mid


def meeting_history(limit=100):
    with connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM meetings_history ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()]


def achievements():
    with connect() as db:
        rows = db.execute("SELECT * FROM achievements ORDER BY id").fetchall()
        return [dict(r) for r in rows]


def training_list():
    with connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM trainings ORDER BY created_at DESC").fetchall()]


def create_training(title, description, passing, time_limit, creator_id, creator_name, questions):
    with connect() as db:
        tid = make_id("training"); now = _now()
        db.execute("INSERT INTO trainings VALUES (?,?,?,?,?,?,?,?,?)", (tid, title, description or "", int(passing), int(time_limit), 1, str(creator_id), creator_name, now))
        for i, q in enumerate(questions):
            db.execute("INSERT INTO training_questions VALUES (?,?,?,?,?,?)", (make_id("q"), tid, q["question"], json.dumps(q["options"], ensure_ascii=False), int(q["answer_index"]), i))
        return tid


def get_training(tid):
    with connect() as db:
        row = db.execute("SELECT * FROM trainings WHERE id=?", (tid,)).fetchone()
        if not row:
            return None
        item = dict(row); qs = db.execute("SELECT * FROM training_questions WHERE training_id=? ORDER BY position", (tid,)).fetchall()
        item["questions"] = [{**dict(q), "options": json.loads(q["options_json"])} for q in qs]
        return item


def save_attempt(training_id, user_id, user_name, score, passed):
    with connect() as db:
        aid = make_id("attempt")
        db.execute("INSERT INTO training_attempts VALUES (?,?,?,?,?,?,?)", (aid, training_id, str(user_id), user_name, int(score), 1 if passed else 0, _now()))
        return aid


def attempts(user_id=None, limit=100):
    with connect() as db:
        if user_id:
            rows = db.execute("SELECT * FROM training_attempts WHERE user_id=? ORDER BY created_at DESC LIMIT ?", (str(user_id), limit)).fetchall()
        else:
            rows = db.execute("SELECT * FROM training_attempts ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


def set_team_status(user_id, user_name, status, message=""):
    valid = {"available", "busy", "away", "dnd", "offline"}
    status = status if status in valid else "available"
    with connect() as db:
        db.execute("INSERT OR REPLACE INTO team_status(user_id,user_name,status,message,updated_at) VALUES (?,?,?,?,?)", (str(user_id), user_name, status, message[:160], _now()))


def get_team_status(user_id):
    with connect() as db:
        row = db.execute("SELECT * FROM team_status WHERE user_id=?", (str(user_id),)).fetchone()
        return dict(row) if row else None


def list_team_status():
    with connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM team_status ORDER BY user_name COLLATE NOCASE").fetchall()]


def create_handover(title, content, author_id, author_name, priority="normal"):
    with connect() as db:
        hid = make_id("handover")
        db.execute("INSERT INTO handover_notes VALUES (?,?,?,?,?,?,?,NULL)", (hid, title[:120], content[:2500], str(author_id), author_name, priority, _now()))
        return hid


def list_handovers(limit=100, include_archived=False):
    with connect() as db:
        if include_archived:
            rows = db.execute("SELECT * FROM handover_notes ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        else:
            rows = db.execute("SELECT * FROM handover_notes WHERE archived_at IS NULL ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


def archive_handover(hid):
    with connect() as db:
        return db.execute("UPDATE handover_notes SET archived_at=? WHERE id=?", (_now(), hid)).rowcount > 0


def create_announcement(title, content, author_id, author_name, kind="info", discord_message_id=None):
    with connect() as db:
        aid = make_id("announcement")
        db.execute("INSERT INTO announcements VALUES (?,?,?,?,?,?,?,?)", (aid, title[:160], content[:4000], str(author_id), author_name, kind, str(discord_message_id) if discord_message_id else None, _now()))
        return aid


def announcements(limit=100):
    with connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM announcements ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()]


def create_promotion_request(target_user_id, target_user_name, current_role, requested_role, reason, created_by_id, created_by_name, requested_role_id=None, current_role_id=None):
    with connect() as db:
        pid = make_id("promotion")
        db.execute("INSERT INTO promotion_requests(id,target_user_id,target_user_name,current_role,requested_role,reason,created_by_id,created_by_name,status,decided_by_id,decided_by_name,decision_note,created_at,decided_at,requested_role_id,current_role_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (pid, str(target_user_id), target_user_name, current_role or "", requested_role[:120], reason[:2000], str(created_by_id), created_by_name, "pending", None, None, "", _now(), None, str(requested_role_id) if requested_role_id else None, str(current_role_id) if current_role_id else None))
        return pid


def promotion_requests(status=None, limit=100):
    with connect() as db:
        if status:
            rows = db.execute("SELECT * FROM promotion_requests WHERE status=? ORDER BY created_at DESC LIMIT ?", (status, limit)).fetchall()
        else:
            rows = db.execute("SELECT * FROM promotion_requests ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


def decide_promotion_request(pid, status, decided_by_id, decided_by_name, note=""):
    if status not in ("approved", "rejected", "cancelled"):
        raise ValueError("Ungültiger Status")
    with connect() as db:
        cur = db.execute("UPDATE promotion_requests SET status=?,decided_by_id=?,decided_by_name=?,decision_note=?,decided_at=? WHERE id=? AND status='pending'", (status, str(decided_by_id), decided_by_name, note[:1000], _now(), pid))
        return cur.rowcount > 0


def create_checkin(user_id, user_name, kind="activity"):
    with connect() as db:
        cid = make_id("checkin")
        db.execute("INSERT INTO checkins VALUES (?,?,?,?,?)", (cid, str(user_id), user_name, kind, _now()))
        return cid


def checkin_stats(kind="activity", since=None):
    with connect() as db:
        if since:
            row = db.execute("SELECT COUNT(*) AS n FROM checkins WHERE kind=? AND created_at>=?", (kind, since)).fetchone()
        else:
            row = db.execute("SELECT COUNT(*) AS n FROM checkins WHERE kind=?", (kind,)).fetchone()
        return int(row["n"])


def record_event(event_type, target_type="", target_id="", actor_id="", actor_name="", payload: Any = None):
    with connect() as db:
        eid = make_id("event")
        db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?)", (eid, event_type, target_type or "", str(target_id or ""), str(actor_id or ""), actor_name or "", json.dumps(payload if payload is not None else {}, ensure_ascii=False), _now()))
        return eid


def events(limit=200, event_type=None, target_type=None, actor_id=None):
    with connect() as db:
        sql = "SELECT * FROM events WHERE 1=1"; args = []
        if event_type:
            sql += " AND event_type=?"; args.append(event_type)
        if target_type:
            sql += " AND target_type=?"; args.append(target_type)
        if actor_id:
            sql += " AND actor_id=?"; args.append(str(actor_id))
        sql += " ORDER BY created_at DESC LIMIT ?"; args.append(limit)
        rows = db.execute(sql, args).fetchall()
        return [dict(r) for r in rows]


def get_setting(key, default=None):
    with connect() as db:
        row = db.execute("SELECT value_json FROM settings WHERE key=?", (key,)).fetchone()
        if not row:
            return default
        try:
            return json.loads(row[0])
        except Exception:
            return row[0]


def set_setting(key, value):
    with connect() as db:
        db.execute("INSERT OR REPLACE INTO settings(key,value_json) VALUES (?,?)", (key, json.dumps(value, ensure_ascii=False)))


def purge_old_notifications(days=90):
    cutoff = datetime.now(timezone.utc).timestamp() - max(1, int(days)) * 86400
    cutoff_iso = datetime.fromtimestamp(cutoff, timezone.utc).isoformat(timespec="seconds")
    with connect() as db:
        return db.execute("DELETE FROM notifications WHERE read_at IS NOT NULL AND created_at < ?", (cutoff_iso,)).rowcount


TABLES = [
    "notifications", "tasks", "task_history", "tickets", "ticket_events", "wiki_pages", "achievements",
    "achievement_progress", "trainings", "training_questions", "training_attempts", "meetings_history",
    "team_status", "handover_notes", "announcements", "promotion_requests", "checkins", "events", "settings",
]


def export_state():
    state = {}
    with connect() as db:
        for table in TABLES:
            rows = db.execute(f"SELECT * FROM {table}").fetchall()
            state[table] = [dict(r) for r in rows]
    return state


def import_state(state):
    if not isinstance(state, dict):
        raise ValueError("Ungültiger Pulse-Datenstand")
    with connect() as db:
        for table in TABLES:
            rows = state.get(table)
            if not isinstance(rows, list):
                continue
            db.execute(f"DELETE FROM {table}")
            cols = [c[1] for c in db.execute(f"PRAGMA table_info({table})").fetchall()]
            for row in rows:
                if not isinstance(row, dict):
                    continue
                usable = [c for c in cols if c in row]
                if not usable:
                    continue
                vals = [row[c] for c in usable]
                db.execute(f"INSERT OR REPLACE INTO {table} ({','.join(usable)}) VALUES ({','.join('?' for _ in usable)})", vals)


init_db()
