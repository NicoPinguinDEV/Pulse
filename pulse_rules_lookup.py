"""Audit #47 und #133: Regelwerk-Suche mit belegten Antworten (ohne KI, nur lesend).

Liest die freigegebene Wissensbasis (``support_knowledge.md``) und zerlegt sie in zitierbare Stellen:
Regelwerk (Ingame/RP, Discord, Häufige Antworten), Paragraph, Absatznummer, Text. Jede Antwort nennt ihre Quelle
(Regelwerk, §, Stand) und die Fassung der Datei (Kurz-Hash), sodass erkennbar ist, auf welcher Version sie beruht.
Es wird nichts erfunden und nichts entschieden: Treffer sind wörtliche Stellen, Maßnahmen entscheidet das Serverteam.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from fastapi import Cookie, Request
from fastapi.responses import HTMLResponse

ROOT = Path(__file__).resolve().parent
MAX_QUERY = 100
MAX_RESULTS = 8
DOCS = {"A": ("ingame", "Ingame/RP-Regelwerk"), "B": ("discord", "Discord-Regelwerk"), "C": ("faq", "Häufige Antworten")}
_REF = re.compile(r"^-?\s*(§\s*\d+(?:\.\d+)*)\s*(.*)$")
_SECTION = re.compile(r"^##\s+(§\s*\d+)\s+(.*)$")
_DOC = re.compile(r"^#\s+([ABC])\.\s+(.*)$")
_STAND = re.compile(r"^Stand:\s*(.+)$")
_cache: dict = {}


def knowledge_path() -> Path:
    return Path(os.getenv("SUPPORT_KNOWLEDGE_FILE", str(ROOT / "support_knowledge.md")))


def parse(text: str) -> list[dict]:
    """Zerlegt die Wissensbasis in Stellen ``{doc, doc_label, stand, section, section_title, ref, text}``."""
    doc, stand, section, section_title = "", "", "", ""
    items: list[dict] = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _DOC.match(line)
        if m:
            doc, stand, section, section_title = m.group(1), "", "", ""
            continue
        if doc and not stand:
            s = _STAND.match(line)
            if s:
                stand = s.group(1).strip()
                continue
        m = _SECTION.match(line)
        if m and doc:
            section, section_title = re.sub(r"\s+", "", m.group(1)), m.group(2).strip()
            continue
        if line.startswith("## ") and doc == "C":
            section, section_title = "", line[3:].strip()
            continue
        if not doc or not line.startswith("- "):
            continue
        body = line[2:].strip()
        ref_match = _REF.match(line)
        ref = re.sub(r"\s+", "", ref_match.group(1)) if ref_match else section
        key, label = DOCS[doc]
        items.append({"doc": key, "doc_label": label, "stand": stand, "section": section, "section_title": section_title,
                      "ref": ref, "text": body})
    return items


def _load() -> tuple[list[dict], str]:
    path = knowledge_path()
    try:
        stat = path.stat()
        stamp = (str(path), stat.st_mtime_ns, stat.st_size)
        if _cache.get("stamp") != stamp:
            raw = path.read_bytes()
            _cache.update(stamp=stamp, items=parse(raw.decode("utf-8", "replace")), version=hashlib.sha256(raw).hexdigest()[:8])
        return _cache["items"], _cache["version"]
    except OSError:
        return [], ""


def cite(item: dict, version: str = "") -> str:
    stand = f", Stand {item['stand']}" if item.get("stand") else ""
    where = item["ref"] or item["section_title"]
    return f"{item['doc_label']} {where}{stand}" + (f" · Fassung {version}" if version else "")


def search(query: str, doc: str | None = None, limit: int = MAX_RESULTS) -> dict:
    """``{results: [{...stelle, cite, score}], version, query}``; leere/zu kurze Suchen liefern nichts."""
    items, version = _load()
    q = str(query or "").strip()[:MAX_QUERY]
    terms = [t for t in re.split(r"\s+", q.casefold()) if len(t) >= 2][:6]
    ref_query = re.sub(r"\s+", "", q.casefold()).lstrip("§")
    results = []
    if terms:
        for item in items:
            if doc and item["doc"] != doc:
                continue
            hay = f"{item['ref']} {item['section_title']} {item['text']}".casefold()
            if not all(t in hay for t in terms):
                continue
            score = sum(hay.count(t) for t in terms) + (5 * sum(t in item["section_title"].casefold() for t in terms))
            if ref_query and item["ref"].casefold().lstrip("§") == ref_query:
                score += 50  # exakte Absatznummer wie „7.3“
            results.append({**item, "cite": cite(item, version), "score": score})
    results.sort(key=lambda r: (-r["score"], r["doc"], r["ref"]))
    return {"results": results[:max(1, min(int(limit), 20))], "version": version, "query": q}


_STOPWORDS = frozenset("""aber auch dass dazu dein deine denn dich dies diese dieser eine einen einer eines euch habe haben hatte kann kannst kein keine
mich mein meine muss nicht noch oder sein sind uber ueber wann warum was welche wenn werden wie wird wollen wurde zum zur""".split())


def best_sources(question: str, limit: int = 2, min_terms: int = 2) -> dict:
    """#133: Regelwerkstellen, die zu einer Frage passen - zur Quellenangabe unter KI-Antworten.

    Rein lexikalisch und nachvollziehbar (keine KI): Je Stelle zählen die verschiedenen Fragebegriffe (ab 4 Zeichen, ohne Füllwörter, mit
    5-Zeichen-Wortstamm wegen deutscher Beugung), die in Paragraph, Titel oder Text vorkommen. Eine Stelle wird nur genannt, wenn mindestens
    ``min_terms`` Begriffe passen. ``{sources: [{cite, ref, doc, stand, score}], version}``."""
    items, version = _load()
    words = [w for w in re.findall(r"[a-zäöüß]{4,}", str(question or "").casefold()[:600]) if w not in _STOPWORDS]
    stems = list(dict.fromkeys(w[:5] for w in words))[:12]
    scored = []
    if len(stems) >= 1:
        for item in items:
            hay = f"{item['ref']} {item['section_title']} {item['text']}".casefold()
            hits = sum(1 for stem in stems if stem in hay)
            if hits >= min_terms:
                scored.append((hits, item))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["doc"], pair[1]["ref"]))
    seen, out = set(), []
    for hits, item in scored:
        key = (item["doc"], item["ref"].split(".")[0])      # eine Stelle je Paragraph, damit die Liste nicht nur einen Abschnitt nennt
        if key in seen:
            continue
        seen.add(key)
        out.append({"cite": cite(item, ""), "ref": item["ref"], "doc": item["doc"], "stand": item["stand"], "score": hits})
        if len(out) >= max(1, min(int(limit), 4)):
            break
    return {"sources": out, "version": version}


def sources_line(question: str, limit: int = 2) -> str:
    """One Discord line under an answer (``""`` when nothing fits): ``-# 📎 Passende Regelwerkstellen: … · Fassung abcd1234``."""
    try:
        data = best_sources(question, limit)
    except Exception:  # noqa: BLE001 - a missing citation never blocks an answer
        return ""
    if not data["sources"]:
        return ""
    names = "; ".join(s["cite"] for s in data["sources"])
    return f"-# 📎 Passende Regelwerkstellen (automatisch ermittelt, keine Auslegung): {names}" + (f" · Fassung {data['version']}" if data["version"] else "")


def register(app) -> None:
    import webserver as ws
    e = ws.esc

    @app.get("/regelwerk-suche", response_class=HTMLResponse)
    async def rules_page(request: Request, user_session: str = Cookie(None)):
        ctx = ws.auth(request, user_session)
        q = str(request.query_params.get("q") or "")[:MAX_QUERY]
        doc = request.query_params.get("doc")
        doc = doc if doc in {k for k, _ in DOCS.values()} else None
        data = search(q, doc) if q.strip() else {"results": [], "version": _load()[1], "query": q}
        rows = "".join(f'<div class="{ws.CARD} p-4 mb-2"><div class="text-[10px] text-indigo-500 font-semibold">{e(r["cite"])}</div>'
                       f'<div class="text-sm mt-1">{e(r["text"])}</div></div>' for r in data["results"])
        if q.strip() and not rows:
            rows = f'<div class="pulse-empty">Keine Stelle zu „{e(q)}“. Im Zweifel fragt das Serverteam.</div>'
        options = '<option value="">Alle Regelwerke</option>' + "".join(
            f'<option value="{k}"{" selected" if k == doc else ""}>{e(label)}</option>' for k, label in DOCS.values())
        body = (f'<div class="max-w-3xl"><h1 class="text-2xl font-bold mb-1">Regelwerk-Assistent</h1>'
                f'<p class="text-xs text-slate-500 mb-3">Wörtliche Stellen mit Quelle. Keine Auslegung und keine Entscheidung: Maßnahmen trifft das Serverteam.</p>'
                f'<form method="get" class="flex flex-wrap gap-2 mb-4"><input class="{ws.INPUT} flex-1" name="q" value="{e(q)}" maxlength="{MAX_QUERY}" placeholder="Stichwort oder Paragraph, z. B. Safezone oder 7.3">'
                f'<select class="{ws.INPUT}" name="doc">{options}</select><button class="{ws.BTN} py-2 px-4">Suchen</button></form>'
                f'{rows}<p class="text-[11px] text-slate-400 mt-3">Fassung der Wissensbasis: {e(data["version"] or "nicht verfügbar")}</p></div>')
        return HTMLResponse(ws.render_page("Regelwerk-Assistent", ctx, "wiki", body))
