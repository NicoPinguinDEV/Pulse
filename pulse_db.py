import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

DB_PATH = "pulse.db"
_LOCK = threading.RLock()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    with _LOCK:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def init_db():
    with connect() as db:
        db.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS notifications (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                kind TEXT DEFAULT 'info',
                url TEXT DEFAULT '/pulse-inbox',
                read_at TEXT,
                created_at TEXT NOT NULL
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
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, due_at);
            CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(assignee_id, status);

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
                transcript_path TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status, priority, opened_at);

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

            CREATE TABLE IF NOT EXISTS meetings_history (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                date_time TEXT NOT NULL,
                description TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                attendees_json TEXT DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL
            );
            """
        )
        _seed()


def _seed():
    defaults = [
        ("ach_first_shift", "Erste Schicht", "Schließe deine erste Schicht ab.", "⏱️", "shifts", 1),
        ("ach_ticket_50", "Support-Profi", "Bearbeite 50 Tickets.", "🎫", "tickets_closed", 50),
        ("ach_hours_10", "Dauerbrenner", "Sammle 10 Dienststunden.", "🔥", "hours", 10),
        ("ach_meeting_10", "Besprechungsprofi", "Nimm an 10 Meetings teil.", "🎙️", "meetings", 10),
        ("ach_flag_100", "Flaggenmeister", "Erreiche 100 richtige Flaggenantworten.", "🚩", "flag_correct", 100),
    ]
    conn = sqlite3.connect(DB_PATH)
    try:
        for row in defaults:
            conn.execute("INSERT OR IGNORE INTO achievements VALUES (?, ?, ?, ?, ?, ?)", row)
        conn.commit()
    finally:
        conn.close()


def make_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def notify(user_id, title, body, kind="info", url="/pulse-inbox"):
    with connect() as db:
        nid = make_id("notif")
        db.execute(
            "INSERT INTO notifications(id,user_id,title,body,kind,url,created_at) VALUES (?,?,?,?,?,?,?)",
            (nid, str(user_id), title, body, kind, url, _now()),
        )
        return nid


def notifications(user_id, limit=50):
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (str(user_id), limit),
        ).fetchall()
        return [dict(r) for r in rows]


def unread_count(user_id):
    with connect() as db:
        return db.execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND read_at IS NULL", (str(user_id),)).fetchone()[0]


def mark_notifications_read(user_id, nid=None):
    with connect() as db:
        if nid:
            db.execute("UPDATE notifications SET read_at=? WHERE id=? AND user_id=?", (_now(), nid, str(user_id)))
        else:
            db.execute("UPDATE notifications SET read_at=? WHERE user_id=? AND read_at IS NULL", (_now(), str(user_id)))


def create_task(title, description, assignee_id, assignee_name, creator_id, creator_name, priority="normal", due_at=None):
    with connect() as db:
        tid = make_id("task")
        now = _now()
        db.execute(
            "INSERT INTO tasks(id,title,description,assignee_id,assignee_name,creator_id,creator_name,status,priority,due_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid,title,description or "",str(assignee_id) if assignee_id else None,assignee_name or "",str(creator_id),creator_name,"open",priority,due_at,now,now),
        )
        return tid


def list_tasks(status=None, assignee_id=None, limit=200):
    with connect() as db:
        sql = "SELECT * FROM tasks WHERE 1=1"
        args=[]
        if status:
            sql += " AND status=?"; args.append(status)
        if assignee_id:
            sql += " AND assignee_id=?"; args.append(str(assignee_id))
        sql += " ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END, due_at IS NULL, due_at, created_at DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in db.execute(sql,args).fetchall()]


def update_task(tid, status=None, priority=None, due_at=None):
    fields=[]; args=[]
    if status is not None: fields.append("status=?"); args.append(status)
    if priority is not None: fields.append("priority=?"); args.append(priority)
    if due_at is not None: fields.append("due_at=?"); args.append(due_at)
    if not fields: return
    fields.append("updated_at=?"); args.append(_now()); args.append(tid)
    with connect() as db:
        db.execute(f"UPDATE tasks SET {', '.join(fields)} WHERE id=?", args)


def create_ticket(channel_id, guild_id, user_id, user_name, category, priority="normal"):
    with connect() as db:
        tid = make_id("ticket")
        db.execute(
            "INSERT INTO tickets(id,channel_id,guild_id,user_id,user_name,category,status,priority,opened_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (tid,str(channel_id),str(guild_id),str(user_id),user_name,category,"open",priority,_now()),
        )
        return tid


def get_ticket(ticket_id=None, channel_id=None):
    with connect() as db:
        if ticket_id:
            row=db.execute("SELECT * FROM tickets WHERE id=?",(ticket_id,)).fetchone()
        else:
            row=db.execute("SELECT * FROM tickets WHERE channel_id=?",(str(channel_id),)).fetchone()
        return dict(row) if row else None


def list_tickets(status=None, limit=200):
    with connect() as db:
        if status:
            rows=db.execute("SELECT * FROM tickets WHERE status=? ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END, opened_at DESC LIMIT ?",(status,limit)).fetchall()
        else:
            rows=db.execute("SELECT * FROM tickets ORDER BY opened_at DESC LIMIT ?",(limit,)).fetchall()
        return [dict(r) for r in rows]


def claim_ticket(tid, user_id, user_name):
    with connect() as db:
        db.execute("UPDATE tickets SET claimed_by_id=?,claimed_by_name=?,claimed_at=?,status='in_progress' WHERE id=?",(str(user_id),user_name,_now(),tid))


def close_ticket(tid, reason, transcript_path=""):
    with connect() as db:
        db.execute("UPDATE tickets SET status='closed',closed_at=?,close_reason=?,transcript_path=? WHERE id=?",(_now(),reason or "",transcript_path or "",tid))


def rate_ticket(tid, rating, comment=""):
    with connect() as db:
        db.execute("UPDATE tickets SET rating=?,rating_comment=? WHERE id=?",(int(rating),comment or "",tid))


def save_wiki(title, category, content, author_id, author_name, page_id=None):
    with connect() as db:
        now=_now()
        pid=page_id or make_id("wiki")
        if page_id:
            db.execute("UPDATE wiki_pages SET title=?,category=?,content=?,updated_at=? WHERE id=?",(title,category,content,now,page_id))
        else:
            db.execute("INSERT INTO wiki_pages VALUES (?,?,?,?,?,?,?)",(pid,title,category,content,str(author_id),author_name,now,now))
        return pid


def wiki_pages():
    with connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM wiki_pages ORDER BY category,title").fetchall()]


def get_wiki(pid):
    with connect() as db:
        row=db.execute("SELECT * FROM wiki_pages WHERE id=?",(pid,)).fetchone()
        return dict(row) if row else None


def save_meeting_history(title, date_time, description, notes, attendees):
    with connect() as db:
        mid=make_id("meeting")
        db.execute("INSERT INTO meetings_history VALUES (?,?,?,?,?,?,?)",(mid,title,date_time,description or "",notes or "",json.dumps(attendees or {},ensure_ascii=False),_now()))
        return mid


def meeting_history(limit=100):
    with connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM meetings_history ORDER BY created_at DESC LIMIT ?",(limit,)).fetchall()]


def training_list():
    with connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM trainings ORDER BY created_at DESC").fetchall()]


def create_training(title, description, passing, time_limit, creator_id, creator_name, questions):
    with connect() as db:
        tid=make_id("training"); now=_now()
        db.execute("INSERT INTO trainings VALUES (?,?,?,?,?,?,?,?,?)",(tid,title,description or "",int(passing),int(time_limit),1,str(creator_id),creator_name,now))
        for i,q in enumerate(questions):
            db.execute("INSERT INTO training_questions VALUES (?,?,?,?,?,?)",(make_id("q"),tid,q["question"],json.dumps(q["options"],ensure_ascii=False),int(q["answer_index"]),i))
        return tid


def get_training(tid):
    with connect() as db:
        row=db.execute("SELECT * FROM trainings WHERE id=?",(tid,)).fetchone()
        if not row: return None
        item=dict(row); qs=db.execute("SELECT * FROM training_questions WHERE training_id=? ORDER BY position",(tid,)).fetchall()
        item["questions"]=[{**dict(q),"options":json.loads(q["options_json"])} for q in qs]
        return item


def save_attempt(training_id,user_id,user_name,score,passed):
    with connect() as db:
        aid=make_id("attempt")
        db.execute("INSERT INTO training_attempts VALUES (?,?,?,?,?,?)",(aid,training_id,str(user_id),user_name,int(score),1 if passed else 0,_now()))
        return aid


def attempts(user_id=None,limit=100):
    with connect() as db:
        if user_id:
            rows=db.execute("SELECT * FROM training_attempts WHERE user_id=? ORDER BY created_at DESC LIMIT ?",(str(user_id),limit)).fetchall()
        else:
            rows=db.execute("SELECT * FROM training_attempts ORDER BY created_at DESC LIMIT ?",(limit,)).fetchall()
        return [dict(r) for r in rows]


def get_setting(key, default=None):
    with connect() as db:
        row=db.execute("SELECT value_json FROM settings WHERE key=?",(key,)).fetchone()
        if not row: return default
        try: return json.loads(row[0])
        except Exception: return row[0]


def set_setting(key, value):
    with connect() as db:
        db.execute("INSERT OR REPLACE INTO settings(key,value_json) VALUES (?,?)",(key,json.dumps(value,ensure_ascii=False)))


init_db()

# Backup helpers appended for backwards-compatible upgrade.

def export_state():
    tables = [
        "notifications", "tasks", "tickets", "wiki_pages", "achievements",
        "achievement_progress", "trainings", "training_questions", "training_attempts",
        "meetings_history", "settings",
    ]
    state={}
    with connect() as db:
        for table in tables:
            rows=db.execute(f"SELECT * FROM {table}").fetchall()
            state[table]=[dict(r) for r in rows]
    return state


def import_state(state):
    if not isinstance(state, dict):
        raise ValueError("Ungültiger Pulse-Datenstand")
    tables = [
        "notifications", "tasks", "tickets", "wiki_pages", "achievements",
        "achievement_progress", "trainings", "training_questions", "training_attempts",
        "meetings_history", "settings",
    ]
    with connect() as db:
        for table in tables:
            rows=state.get(table)
            if not isinstance(rows,list):
                continue
            db.execute(f"DELETE FROM {table}")
            for row in rows:
                if not isinstance(row,dict): continue
                cols=[c[1] for c in db.execute(f"PRAGMA table_info({table})").fetchall()]
                usable=[c for c in cols if c in row]
                if not usable: continue
                vals=[row[c] for c in usable]
                db.execute(f"INSERT OR REPLACE INTO {table} ({','.join(usable)}) VALUES ({','.join('?' for _ in usable)})",vals)
