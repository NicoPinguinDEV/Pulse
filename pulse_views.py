"""Personal saved filters ("Ansichten") for dashboard tables (#61).

A saved view is a name plus the filter part of a list page's query string. It belongs to exactly one dashboard
user and one page from the allow-list below. Opening a view is a plain link to that page, so the page's own
permission check still decides whether the person may see anything – a saved view never grants access.
"""
from __future__ import annotations

import html
import re
from urllib.parse import parse_qsl, urlencode

from fastapi import Cookie, Request

import pulse_db

# Only these list pages can carry saved views (path → label). The path is never taken from user input.
PAGES = {
    "/tickets": "Tickets",
    "/applications": "Bewerbungen",
    "/ultimate/team": "Teamakten",
    "/mod-cases": "Bürger-Moderation",
    "/ultimate/automations": "Automationen",
}
MAX_VIEWS_PER_PAGE = 12
MAX_NAME = 60
MAX_PAIRS = 12
MAX_VALUE = 100
# Never part of a view: flash messages, paging and edit state.
IGNORED_KEYS = frozenset({"msg", "t", "page", "lpage", "edit", "edit_log"})
_KEY = re.compile(r"[a-z][a-z0-9_]{0,23}")
_STYLES = {
    "pulse": {"btn": "pulse-btn ghost", "active": "pulse-btn primary", "input": "pulse-input", "box": "pulse-card", "inner": "pulse-card-b"},
    "ultimate": {"btn": "btn", "active": "btn primary", "input": "input", "box": "card", "inner": ""},
}


def clean_query(items) -> list[tuple[str, str]]:
    """Filter pairs that may be stored: simple keys, bounded printable values, bounded count, no duplicates."""
    pairs, seen = [], set()
    for raw_key, raw_value in items or ():
        key, value = str(raw_key), str(raw_value).strip()
        if not _KEY.fullmatch(key) or key in IGNORED_KEYS or (key, value) in seen:
            continue
        if not value or len(value) > MAX_VALUE or any(ord(ch) < 32 for ch in value):
            continue
        seen.add((key, value))
        pairs.append((key, value))
        if len(pairs) >= MAX_PAIRS:
            break
    return pairs


def query_string(items) -> str:
    return urlencode(clean_query(items))


def href(page: str, query: str) -> str:
    """Local link of a view. Unknown pages fall back to the personal start page."""
    if page not in PAGES:
        return "/me"
    cleaned = query_string(parse_qsl(str(query or ""), keep_blank_values=False))
    return page + ("?" + cleaned if cleaned else "")


def list_views(user_id, page: str) -> list[dict]:
    if page not in PAGES:
        return []
    with pulse_db.connect() as db:
        rows = db.execute("SELECT * FROM dashboard_saved_views WHERE user_id=? AND page=? ORDER BY name COLLATE NOCASE, id",
                          (str(user_id), page)).fetchall()
    return [dict(r) for r in rows]


def save_view(user_id, page: str, name: str, items) -> dict:
    """Create a view or replace the one with the same name. Raises ValueError with a German message."""
    if page not in PAGES:
        raise ValueError("Für diese Seite können keine Ansichten gespeichert werden.")
    name = " ".join(str(name or "").split())
    if not name or len(name) > MAX_NAME or any(ord(ch) < 32 for ch in name):
        raise ValueError(f"Bitte einen Namen mit höchstens {MAX_NAME} Zeichen angeben.")
    query = query_string(items)
    if not query:
        raise ValueError("Es ist kein Filter gesetzt – erst filtern, dann als Ansicht speichern.")
    now = pulse_db._now()
    with pulse_db.connect() as db:
        existing = db.execute("SELECT id FROM dashboard_saved_views WHERE user_id=? AND page=? AND name=? COLLATE NOCASE",
                              (str(user_id), page, name)).fetchone()
        if existing:
            db.execute("UPDATE dashboard_saved_views SET name=?,query=?,updated_at=? WHERE id=?", (name, query, now, existing["id"]))
            view_id = existing["id"]
        else:
            count = db.execute("SELECT COUNT(*) FROM dashboard_saved_views WHERE user_id=? AND page=?", (str(user_id), page)).fetchone()[0]
            if count >= MAX_VIEWS_PER_PAGE:
                raise ValueError(f"Höchstens {MAX_VIEWS_PER_PAGE} gespeicherte Ansichten je Seite – bitte erst eine löschen.")
            view_id = pulse_db.make_id("view")
            db.execute("INSERT INTO dashboard_saved_views(id,user_id,page,name,query,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                       (view_id, str(user_id), page, name, query, now, now))
    return {"id": view_id, "page": page, "name": name, "query": query, "replaced": bool(existing)}


def delete_view(user_id, view_id: str) -> dict | None:
    """Delete one of the person's own views. Someone else's view is simply not found."""
    with pulse_db.connect() as db:
        row = db.execute("SELECT * FROM dashboard_saved_views WHERE id=? AND user_id=?", (str(view_id), str(user_id))).fetchone()
        if not row:
            return None
        db.execute("DELETE FROM dashboard_saved_views WHERE id=? AND user_id=?", (str(view_id), str(user_id)))
    return dict(row)


def bar(page: str, user_id, query_items, style: str = "pulse") -> str:
    """The "Meine Ansichten" strip of a list page: one link per saved view, delete buttons and the save form."""
    if page not in PAGES:
        return ""
    css = _STYLES.get(style, _STYLES["pulse"])
    e = html.escape
    current = query_string(query_items)
    chips = []
    for view in list_views(user_id, page):
        active = view["query"] == current
        chips.append(
            f"<span style='display:inline-flex;gap:4px;align-items:center'>"
            f"<a class='{css['active'] if active else css['btn']}' href='{e(href(page, view['query']))}'"
            f"{' aria-current=\"true\"' if active else ''}>{e(view['name'])}</a>"
            f"<form method='post' action='/me/views/{e(view['id'])}/delete' style='display:inline'>"
            f"<input type='hidden' name='query' value='{e(current)}'>"
            f"<button class='{css['btn']}' aria-label='Ansicht {e(view['name'])} löschen' title='Ansicht löschen'>✕</button></form></span>")
    save = (f"<form method='post' action='/me/views/save' style='display:inline-flex;flex-wrap:wrap;gap:6px;align-items:center'>"
            f"<input type='hidden' name='page' value='{e(page)}'><input type='hidden' name='query' value='{e(current)}'>"
            f"<label class='sr-only' for='savedViewName'>Name der Ansicht</label>"
            f"<input id='savedViewName' class='{css['input']}' style='max-width:220px' name='name' maxlength='{MAX_NAME}' required "
            f"placeholder='Aktuellen Filter speichern als …'><button class='{css['btn']}'>💾 Speichern</button></form>"
            if current else "<span style='font-size:11px;opacity:.7'>Filter setzen, um ihn als Ansicht zu speichern.</span>")
    inner = (f"<div style='display:flex;flex-wrap:wrap;gap:8px;align-items:center'><b style='font-size:12px'>⭐ Meine Ansichten</b>"
             f"{''.join(chips) or '<span style=\"font-size:11px;opacity:.7\">Noch keine gespeichert.</span>'}{save}</div>")
    wrapped = f"<div class='{css['inner']}'>{inner}</div>" if css["inner"] else inner
    return f"<section class='{css['box']}' style='margin-bottom:12px' aria-label='Gespeicherte Ansichten'>{wrapped}</section>"


def register(app) -> None:
    import webserver as ws

    async def views_save(request: Request, user_session: str = Cookie(None)):
        ctx = ws.auth(request, user_session)
        form = await request.form()
        page = str(form.get("page") or "")
        if page not in PAGES:
            return ws.back("/me", "Für diese Seite können keine Ansichten gespeichert werden.", False)
        items = parse_qsl(str(form.get("query") or "")[:2000], keep_blank_values=False)
        target = href(page, urlencode(items))
        try:
            saved = save_view(ctx.user["id"], page, form.get("name"), items)
        except ValueError as exc:
            return ws.back(target, str(exc), False)
        return ws.back(target, f"Ansicht „{saved['name']}“ {'aktualisiert' if saved['replaced'] else 'gespeichert'}.", True)

    async def views_delete(request: Request, view_id: str, user_session: str = Cookie(None)):
        ctx = ws.auth(request, user_session)
        form = await request.form()
        removed = delete_view(ctx.user["id"], view_id)
        if removed is None:
            return ws.back("/me", "Diese Ansicht wurde nicht gefunden.", False)
        return ws.back(href(removed["page"], str(form.get("query") or "")[:2000]), f"Ansicht „{removed['name']}“ gelöscht.", True)

    app.post("/me/views/save")(views_save)
    app.post("/me/views/{view_id}/delete")(views_delete)
