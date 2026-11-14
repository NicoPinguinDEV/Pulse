"""Public website of Bochum RP: homepage, rules, factions, news, FAQ and legal pages.

Everything here is readable without login and shows only public-facing facts. The public team page
uses the configured TeamBot role lists and renders only approved display names, role names and
locally generated initials. It never requests Discord CDN avatars or renders presence, leave data,
moderation records or internal notes.

The dashboard stays apart: / forwards signed-in team members to their start page, and the team login
lives at /team-login. /team remains the internal team list.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sqlite3
import time
import unicodedata
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from fastapi import Cookie, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

import pulse_db
import pulse_public_content as public_content
import pulse_public_consent as public_team_consent
import pulse_public_legal as legal
from pulse_team_roles import TEAM_ROLE_GROUPS, team_role_group, team_role_priority

ROOT = Path(__file__).resolve().parent
STATIC_FILES = {"public.css": "text/css", "public.js": "text/javascript"}
KNOWLEDGE_FILE = ROOT / "support_knowledge.md"
CONSENT_VERSION = 1
# {"id", "label", "purpose", "provider"} per optional service; empty means no consent banner.
OPTIONAL_SERVICES: tuple[dict, ...] = ()

# Public pages do not embed remote images; scripts, styles and images remain first-party/local.
CSP = (
    "default-src 'none'; img-src 'self' data:; style-src 'self'; script-src 'self'; font-src 'self'; "
    "connect-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'; object-src 'none'"
)
PUBLIC_PATHS = ("/", "/regelwerk", "/faq", "/fraktionen", "/news", "/team-vorstellung",
                "/nutzungsbedingungen", "/impressum", "/datenschutz", "/cookies", "/sicherheit", "/sitemap.xml")
PUBLIC_RATE_LIMIT = 240  # page views per minute and address
NEWS_LIMIT = 30
CACHE_SECONDS = 60.0
INVITE = re.compile(r"^https://(?:discord\.gg|discord\.com/invite)/[A-Za-z0-9-]{2,64}$")


def public_access_enabled() -> bool:
    """Public pages stay closed unless explicitly enabled and every legal release gate passes."""
    return os.getenv("PULSE_PUBLIC_SITE_ENABLED", "0").strip() == "1" and legal.publication_ready()
_cache: dict[str, tuple[float, object]] = {}


def e(value) -> str:
    return escape(str(value if value is not None else ""), quote=True)


def discord_invite() -> str:
    """Only a real Discord invite is ever linked; a typo in the setting shows no button."""
    value = os.getenv("PULSE_PUBLIC_DISCORD_URL", "").strip()
    return value if INVITE.match(value) else ""


def asset_version(name: str) -> str:
    try:
        return hashlib.sha256((ROOT / "static" / name).read_bytes()).hexdigest()[:10]
    except OSError:
        return "0"


def _cached(key: str, loader):
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and now - hit[0] < CACHE_SECONDS:
        return hit[1]
    value = loader()
    _cache[key] = (now, value)
    return value


# --- approved content -----------------------------------------------------------------------

def _news_table(db) -> None:
    db.execute(
        "CREATE TABLE IF NOT EXISTS public_news (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, "
        "body TEXT NOT NULL, published_at TEXT NOT NULL, author TEXT NOT NULL DEFAULT '')"
    )
    # Additive migration for existing installations created before public categories existed.
    columns = {row[1] for row in db.execute("PRAGMA table_info(public_news)").fetchall()}
    if "category" not in columns:
        db.execute("ALTER TABLE public_news ADD COLUMN category TEXT NOT NULL DEFAULT 'Community'")


def list_news(limit: int = NEWS_LIMIT) -> list[dict]:
    with pulse_db.connect() as db:
        _news_table(db)
        rows = db.execute(
            "SELECT id, title, body, published_at, category FROM public_news "
            "ORDER BY published_at DESC, id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    return [{"id": r[0], "title": r[1], "body": r[2], "published_at": r[3], "category": r[4]} for r in rows]


def get_news(news_id: int) -> dict | None:
    with pulse_db.connect() as db:
        _news_table(db)
        row = db.execute(
            "SELECT id, title, body, published_at, category FROM public_news WHERE id=?",
            (int(news_id),),
        ).fetchone()
    if not row:
        return None
    return {"id": row[0], "title": row[1], "body": row[2], "published_at": row[3], "category": row[4]}


def add_news(title: str, body: str, author: str, category: str = "Community") -> int:
    title, body = " ".join(str(title).split())[:120], str(body).strip()[:4000]
    category = " ".join(str(category or "Community").split())[:40] or "Community"
    if not title or not body:
        raise ValueError("Titel und Text sind Pflichtfelder.")
    with pulse_db.connect() as db:
        _news_table(db)
        cursor = db.execute(
            "INSERT INTO public_news(title, body, published_at, author, category) VALUES (?,?,?,?,?)",
            (title, body, datetime.now(timezone.utc).isoformat(timespec="seconds"), str(author)[:100], category),
        )
        news_id = int(cursor.lastrowid)
    _cache.pop("news", None)
    return news_id


def delete_news(news_id: int) -> bool:
    with pulse_db.connect() as db:
        _news_table(db)
        removed = db.execute("DELETE FROM public_news WHERE id=?", (int(news_id),)).rowcount
    _cache.pop("news", None)
    return bool(removed)


def _safe_news() -> list[dict]:
    try:
        return _cached("news", list_news)
    except sqlite3.Error:
        return []


def official_factions() -> list[dict]:
    """Only name, category and description of official factions leave the database."""
    import pulse_factions

    def load():
        return [
            {"name": item.get("name", ""), "category": item.get("category_name") or "Weitere Bereiche",
             "description": item.get("description") or ""}
            for item in pulse_factions.list_factions(status="official")
        ]

    try:
        return _cached("factions", load)
    except (sqlite3.Error, ValueError):
        return []


def member_count(request: Request) -> int | None:
    import pulse_ids

    bot = getattr(request.app.state, "bot", None)
    guild = bot.get_guild(pulse_ids.GUILD_ID) if bot else None
    count = getattr(guild, "member_count", None)
    return count if isinstance(count, int) and count > 0 else None


def _inline(text: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", e(text))


def parse_rulebooks(markdown: str) -> list[dict]:
    """Only chapters A and B are rules; the assistant's instructions and chapter C stay out."""
    books: list[dict] = []
    book = section = None
    for raw in markdown.splitlines():
        line = raw.strip()
        if line.startswith("# "):
            title = line[2:].strip()
            book = {"title": title.split(".", 1)[-1].strip(), "sections": []} if re.match(r"^[AB]\. ", title) else None
            if book:
                books.append(book)
            section = None
        elif book is None or not line or line == "---":
            continue
        elif line.startswith("## "):
            section = {"title": line[3:].strip(), "items": []}
            book["sections"].append(section)
        elif section is not None:
            section["items"].append(line[2:] if line.startswith("- ") else line)
    return books


def rulebooks() -> list[dict]:
    def load():
        try:
            return parse_rulebooks(KNOWLEDGE_FILE.read_text(encoding="utf-8"))
        except OSError:
            return []

    return _cached("rules", load)


def rule_stand() -> str:
    try:
        match = re.search(r"^Stand[^\n]*", KNOWLEDGE_FILE.read_text(encoding="utf-8"), re.MULTILINE)
    except OSError:
        return ""
    return match.group(0).rstrip(".") if match else ""


# --- page frame ------------------------------------------------------------------------------

def _discord_button(css: str = "btn btn-main", label: str = "Discord beitreten") -> str:
    invite = discord_invite()
    return f'<a class="{css}" href="{e(invite)}" rel="noopener noreferrer">{label}</a>' if invite else ""


NAV = (("/#ueber", "Über uns"), ("/fraktionen", "Fraktionen"), ("/team-vorstellung", "Team"),
       ("/regelwerk", "Regelwerk"), ("/news", "News"), ("/faq", "FAQ"), ("/apply", "Bewerben"))


def page(title: str, description: str, body: str, *, path: str) -> str:
    links = "".join(
        f'<a href="{href}"{" aria-current=page" if href == path or (href == "/news" and path.startswith("/news/")) else ""}>{label}</a>'
        for href, label in NAV
    )
    actions = f'{_discord_button("btn btn-main btn-small")}<a class="btn btn-quiet btn-small" href="/team-login">Anmelden</a>'
    consent = ""
    if OPTIONAL_SERVICES:
        config = json.dumps({"version": CONSENT_VERSION, "services": list(OPTIONAL_SERVICES)}, ensure_ascii=False)
        consent = f'<script type="application/json" id="consent-config">{config.replace("</", "<\\/")}</script>'
    return f"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<meta name="theme-color" content="#1F2225">
<title>{e(title)}</title>
<meta name="description" content="{e(description)}">
<link rel="icon" href="/favicon.ico">
<link rel="stylesheet" href="/static/public.css?v={asset_version('public.css')}">
<script src="/static/public.js?v={asset_version('public.js')}" defer></script>
</head>
<body>
<a class="skip" href="#inhalt">Zum Inhalt springen</a>
<header class="top">
  <a class="brand" href="/" aria-label="Bochum RP, zur Startseite">BOCHUM <span>RP</span></a>
  <nav class="links" aria-label="Hauptnavigation">{links}</nav>
  <div class="top-actions">{actions}</div>
  <details class="menu">
    <summary>Menü</summary>
    <nav aria-label="Menü">{links}{_discord_button("", "Discord beitreten")}<a href="/team-login">Anmelden</a></nav>
  </details>
</header>
<main id="inhalt">{body}</main>
<footer class="foot">
  <div>
    <p class="brand">BOCHUM <span>RP</span></p>
    <p>© BOCHUM RP | V1. Roleplay-Community auf Discord.</p>
  </div>
  <nav aria-label="Community"><a href="/">Startseite</a><a href="/team-vorstellung">Team</a><a href="/regelwerk">Regelwerk</a><a href="/fraktionen">Fraktionen</a><a href="/news">News & Blog</a><a href="/faq">FAQ</a><a href="/apply">Bewerben</a></nav>
  <nav aria-label="Rechtliches"><a href="/nutzungsbedingungen">Nutzungsbedingungen</a><a href="/sicherheit">Sicherheit</a><a href="/impressum">Impressum</a><a href="/datenschutz">Datenschutz</a><a href="/cookies">Cookies</a></nav>
</footer>
{consent}
</body>
</html>"""


HEADFRAME = """<svg class="pit" viewBox="0 0 900 640" role="img" aria-label="Förderturm über der nächtlichen Stadt" preserveAspectRatio="xMaxYMax meet">
  <g class="city">
    <path d="M0 640V500h60v-46h44v70h52v-96h70v120h40v-64h66v110h60v-70h48v-40h52v110h64v-80h70v50h60v-30h114v206z"/>
  </g>
  <g class="lit">
    <rect x="72" y="472" width="8" height="10"/><rect x="176" y="452" width="8" height="10"/><rect x="196" y="486" width="8" height="10"/>
    <rect x="280" y="498" width="8" height="10"/><rect x="430" y="474" width="8" height="10"/><rect x="520" y="512" width="8" height="10"/>
    <rect x="610" y="520" width="8" height="10"/><rect x="668" y="542" width="8" height="10"/>
  </g>
  <g class="frame">
    <path d="M520 640 610 150h34l92 490h-30L628 196 550 640z"/>
    <path d="M592 300h78l5 22h-87zM574 400h116l5 22H570zM556 500h152l5 22H552z"/>
    <path d="M628 150 440 640h-30L612 130h32z"/>
    <path d="M560 120h156v30H560z"/>
  </g>
  <g class="wheel" transform="translate(600 92)">
    <g class="spin">
      <circle r="58" fill="none" stroke-width="10"/>
      <path d="M-58 0H58M0-58V58M-41-41 41 41M41-41-41 41" fill="none" stroke-width="5"/>
    </g>
  </g>
  <g class="wheel" transform="translate(690 92)">
    <g class="spin spin-back">
      <circle r="44" fill="none" stroke-width="9"/>
      <path d="M-44 0H44M0-44V44M-31-31 31 31M31-31-31 31" fill="none" stroke-width="4"/>
    </g>
  </g>
</svg>"""

ABOUT = (
    ("Klare Regeln", "Zwei getrennte Regelwerke für Ingame und Discord, jede Regel mit eigener Nummer. Im Streitfall lässt sich nachlesen, was gilt."),
    ("Ausgespielt statt abgebrochen", "Situationen werden zu Ende gespielt und erst danach geklärt. Dein Charakter hat ein Leben, das er nicht leichtfertig riskiert."),
    ("Ein Team, das du erreichst", "Für Fragen und Beschwerden gibt es den Support. Wer ins Team möchte, besteht zuerst einen Eignungstest und Praxisaufgaben."),
)
FEATURES = (
    ("Getrennte Welten", "Was im RP passiert, bleibt im RP. Ingame und Discord haben eigene Regeln."),
    ("Safezones", "Krankenhaus, Polizei, Feuerwehr und Tuning Garage sind gewaltfrei."),
    ("Peacetime", "Sind weniger als 3 Polizeibeamte online, sind Raub, Mord und Geiselnahmen tabu."),
    ("Fraktionen mit Genehmigung", "Fraktionen entstehen nicht nebenbei, sie brauchen eine Genehmigung."),
    ("Hilfe im Discord", "Der Pulse-Assistent kann Regeln erklären. Entscheidungen trifft immer das Team."),
)
STEPS = (
    ("Discord beitreten", "Hier läuft die Community zusammen: Ankündigungen, Support und Bewerbungen."),
    ("Regelwerk kennenlernen", "Lies die Ingame- und die Discord-Regeln, bevor du startest."),
    ("Voraussetzungen erfüllen", "Für das RP brauchst du ein Mikrofon, denn es gilt jederzeit Mikrofonpflicht. Im Discord-Profil wird dein Roblox-/Ingame-Name empfohlen."),
    ("Bei Bedarf bewerben", "Fürs Team bewirbst du dich über das Portal. Dafür brauchst du die Bürgerrolle, die du beim Serverbeitritt automatisch bekommst."),
    ("Ins Roleplay starten", "Such dir deinen Charakter und spiel deine erste Situation aus."),
)
FAQ = (
    ("Was brauche ich, um mitzuspielen?", "Einen Discord-Account und ein Mikrofon, denn im RP gilt jederzeit Mikrofonpflicht (§ 8.3 Ingame). Der Roblox-/Ingame-Name im Discord-Profil wird empfohlen (§ 4.1 Discord)."),
    ("Wo melde ich einen Regelverstoß?", "Spiel die Situation zuerst weiter, sichere danach Beweise und kontaktiere den Support (§ 18). Beschwerden brauchen Beweise (§ 16.2)."),
    ("Wo darf keine Gewalt stattfinden?", "In den Safezones: Krankenhaus, Polizei, Feuerwehr und Tuning Garage. Ausnahmen gibt es nur mit ausdrücklicher Supportfreigabe (§ 12)."),
    ("Was bedeutet Peacetime?", "Sind weniger als 3 Cops online, sind Raub, Mord und Geiselnahmen verboten. Kleine illegale Aktivitäten sind mit realistischem RP erlaubt (§ 13)."),
    ("Was passiert nach dem Tod meines Charakters?", "Du erinnerst dich nicht an den Tod und darfst 15 Minuten nicht an den Ereignisort zurück (§ 8.2)."),
    ("Wie komme ich ins Team?", "Über das Bewerbungsportal. Es gibt die Laufbahnen Low Team, High Team und Leitungsebene, jeweils mit Eignungstest und Praxisaufgaben. Das Portal setzt die Bürgerrolle voraus, die du beim Serverbeitritt automatisch bekommst."),
    ("Antwortet im Support eine KI?", "Im Discord kann der Pulse-Assistent Fragen beantworten. Er kennzeichnet sich als KI und trifft keine Entscheidungen. Sanktionen und Bewerbungen entscheidet das Team."),
)


def home_html(request: Request) -> str:
    count = member_count(request)
    about = "".join(f"<div><h3>{e(t)}</h3><p>{e(d)}</p></div>" for t, d in ABOUT)
    features = "".join(f"<li><strong>{e(t)}</strong> {e(d)}</li>" for t, d in FEATURES)
    steps = "".join(f"<li><h3>{e(t)}</h3><p>{e(d)}</p></li>" for t, d in STEPS)
    factions = official_factions()
    grouped: dict[str, list[str]] = {}
    for item in factions:
        grouped.setdefault(item["category"], []).append(item["name"])
    faction_block = (
        "".join(
            f'<div class="group"><h3>{e(category)}</h3><p>{e(", ".join(names))}</p></div>'
            for category, names in grouped.items()
        )
        if grouped
        else '<p class="empty">Die offiziellen Fraktionen stehen hier, sobald die Fraktionsverwaltung sie freigegeben hat.</p>'
    )
    news = _safe_news()[:3]
    news_block = (
        "".join(_news_item(item) for item in news)
        if news
        else '<p class="empty">Noch keine Neuigkeiten. Aktuelle Ankündigungen findest du im Discord.</p>'
    )
    community = (
        f'<p class="count"><strong>{count:,}</strong> Mitglieder im Discord</p>'.replace(",", ".") if count else ""
    )
    faq = "".join(f"<details><summary>{e(q)}</summary><p>{e(a)}</p></details>" for q, a in FAQ[:4])
    return f"""
<section class="hero">
  <canvas class="sparks" aria-hidden="true"></canvas>
  {HEADFRAME}
  <div class="hero-text">
    <p class="hero-kicker"><span aria-hidden="true"></span> BOCHUM · DEUTSCHLAND · ROLEPLAY</p>
    <h1>BOCHUM <span>RP</span></h1>
    <p class="hero-lead">Deine Stadt. Deine Geschichte. Dein Roleplay.</p>
    <p class="hero-copy">Entdecke eine Community mit klaren Regeln, genehmigten Fraktionen und einem Team, das dich begleitet.</p>
    <div class="cta">{_discord_button()}<a class="btn btn-line" href="/apply">Jetzt bewerben</a><a class="btn btn-line" href="/regelwerk">Regelwerk lesen</a></div>
    <div class="hero-meta"><span>COMMUNITY</span><i aria-hidden="true"></i><span>ROLEPLAY</span><i aria-hidden="true"></i><span>BOCHUM RP</span></div>
  </div>
  <a class="hero-scroll" href="#ueber"><span>SCROLLEN</span><b aria-hidden="true"></b></a>
</section>
<section class="band" id="ueber">
  <p class="section-kicker">01 / DIE COMMUNITY</p>
  <h2>Über Bochum RP</h2>
  <div class="trio">{about}</div>
</section>
<section class="band band-alt" id="community">
  <p class="section-kicker">02 / WAS UNS AUSMACHT</p>
  <div class="split">
    <div><h2>Unsere Community</h2><p>Bochum RP lebt im Discord. Dort findest du Ankündigungen, den Support und alle, die mitspielen.</p>{community}</div>
    <div><h2>Was uns ausmacht</h2><ul class="facts">{features}</ul></div>
  </div>
</section>
<section class="band" id="fraktionen">
  <p class="section-kicker">03 / FRAKTIONEN & BEREICHE</p>
  <h2>Fraktionen und Bereiche</h2>
  {faction_block}
  <p><a class="more" href="/fraktionen">Alle Fraktionen ansehen</a></p>
</section>
{public_team_teaser()}
<section class="band band-shaft" id="mitglied">
  <p class="section-kicker">04 / DEIN EINSTIEG</p>
  <h2>So wirst du Mitglied</h2>
  <ol class="shaft">{steps}</ol>
</section>
<section class="band" id="news">
  <p class="section-kicker">05 / AUS DER COMMUNITY</p>
  <h2>News</h2>
  {news_block}
  <p><a class="more" href="/news">Alle News lesen</a></p>
</section>
<section class="band band-alt" id="faq">
  <p class="section-kicker">06 / GUT ZU WISSEN</p>
  <h2>Häufige Fragen</h2>
  <div class="faq">{faq}</div>
  <p><a class="more" href="/faq">Alle Fragen ansehen</a></p>
</section>
<section class="final">
  <p class="section-kicker">DEIN NÄCHSTES KAPITEL BEGINNT HIER</p>
  <h2>Bochum wartet auf deine Geschichte.</h2>
  <div class="cta">{_discord_button()}<a class="btn btn-line" href="/apply">Jetzt bewerben</a></div>
</section>"""


def _news_item(item: dict) -> str:
    try:
        when = datetime.fromisoformat(item["published_at"]).strftime("%d.%m.%Y")
    except (ValueError, TypeError):
        when = ""
    paragraphs = "".join(f"<p>{_inline(part)}</p>" for part in item["body"].split("\n") if part.strip())
    return (
        f'<article class="news"><time>{e(when)}</time><p class="tag">{e(item.get("category") or "Community")}</p>'
        f'<h3><a href="/news/{int(item["id"])}">{e(item["title"])}</a></h3>{paragraphs}'
        f'<p><a class="more" href="/news/{int(item["id"])}">Beitrag öffnen</a></p></article>'
    )


def rules_html() -> str:
    books = rulebooks()
    if not books:
        return '<div class="doc"><h1>Regelwerk</h1><p class="empty">Das Regelwerk ist gerade nicht verfügbar. Das Team hilft dir im Discord weiter.</p></div>'
    toc, content = [], []
    for index, book in enumerate(books):
        slug = f"teil-{index + 1}"
        toc.append(f'<li><a href="#{slug}">{e(book["title"])}</a></li>')
        sections = "".join(
            f'<section><h3>{e(s["title"])}</h3><ul>{"".join(f"<li>{_inline(item)}</li>" for item in s["items"])}</ul></section>'
            for s in book["sections"]
        )
        content.append(f'<h2 id="{slug}">{e(book["title"])}</h2>{sections}')
    stand = rule_stand()
    return (
        f'<div class="doc"><h1>Regelwerk</h1>{f"<p>{e(stand)}.</p>" if stand else ""}'
        f'<ul class="toc">{"".join(toc)}</ul>{"".join(content)}</div>'
    )


def factions_html() -> str:
    items = official_factions()
    rows = "".join(
        f'<article class="group"><h2>{e(item["name"])}</h2><p class="tag">{e(item["category"])}</p>'
        f'{f"<p>{e(item["description"])}</p>" if item["description"] else ""}</article>'
        for item in items
    )
    return (
        '<div class="doc"><h1>Fraktionen</h1><p>Hier stehen die offiziellen Fraktionen von Bochum RP. '
        "Neue Fraktionen brauchen eine Genehmigung (§ 10 Ingame).</p>"
        f'{rows or "<p class=empty>Noch keine offiziellen Fraktionen veröffentlicht.</p>"}</div>'
    )


def news_html() -> str:
    items = _safe_news()
    return (
        '<div class="doc"><h1>News & Blog</h1><p>Offizielle Neuigkeiten und Einblicke aus der Community.</p>'
        f'{"".join(_news_item(item) for item in items) or "<p class=empty>Noch keine Neuigkeiten. Aktuelle Ankündigungen findest du im Discord.</p>"}</div>'
    )


def news_detail_html(item: dict) -> str:
    try:
        when = datetime.fromisoformat(item["published_at"]).strftime("%d.%m.%Y")
    except (ValueError, TypeError):
        when = ""
    paragraphs = "".join(f"<p>{_inline(part)}</p>" for part in item["body"].split("\n") if part.strip())
    return (
        f'<article class="doc article"><p><a class="more" href="/news">← Alle News</a></p>'
        f'<p class="tag">{e(item.get("category") or "Community")} · {e(when)}</p>'
        f'<h1>{e(item["title"])}</h1>{paragraphs}<p><a class="more" href="/news">Zurück zu News</a></p></article>'
    )


# Public profiles are grouped by the same recognizable rank families used by Pulse's
# nickname hierarchy. Only profiles which have passed the existing person-consent and admin
# publication checks are considered here; this never pulls names or IDs from the internal roster.
TEAM_LEVELS: tuple[tuple[str, str, tuple[str, ...]], ...] = tuple(
    (f"{index:02d}", label, role_names)
    for index, (_group_key, label, role_names) in enumerate(TEAM_ROLE_GROUPS, start=1)
)
_TEAM_LEVEL_BY_GROUP = {
    group_key: (f"{index:02d}", label)
    for index, (group_key, label, _role_names) in enumerate(TEAM_ROLE_GROUPS, start=1)
}


def _public_rank_key(value: object) -> str:
    """Normalize a public rank label, including BOCHUM decoration, without exposing role IDs."""
    label = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    separators = ("メ", "║", "✘", "✗", "⌯⌲", "||")
    positions = [(label.rfind(separator), separator) for separator in separators if separator in label]
    if positions:
        position, separator = max(positions, key=lambda item: item[0])
        label = label[position + len(separator):].strip()
    return re.sub(r"[^a-z0-9äöüß]", "", label)


def _team_level(profile: dict) -> tuple[str, str]:
    """Group a published profile using the current BOCHUM role catalogue only."""
    group_key = team_role_group(profile.get("role_label", ""))
    return _TEAM_LEVEL_BY_GROUP.get(group_key, ("99", "Weitere Teamfunktionen"))


def _team_sections(profiles: list[dict]) -> str:
    grouped: dict[tuple[str, str], list[dict]] = {}
    for profile in profiles:
        grouped.setdefault(_team_level(profile), []).append(profile)
    sections = []
    for number, label, _ in TEAM_LEVELS:
        members = grouped.get((number, label), [])
        if not members:
            continue
        sections.append(
            f'<section class="team-level" aria-labelledby="team-level-{number}">'
            f'<header class="team-level-head"><p class="tag">EBENE {number} · {len(members)} PROFILE</p>'
            f'<h2 id="team-level-{number}">{e(label)}</h2></header>{_team_cards(members)}</section>'
        )
    other = grouped.get(("99", "Weitere Teamfunktionen"), [])
    if other:
        sections.append(
            '<section class="team-level" aria-labelledby="team-level-other">'
            f'<header class="team-level-head"><p class="tag">WEITERE FUNKTIONEN · {len(other)} PROFILE</p>'
            f'<h2 id="team-level-other">Weitere Teamfunktionen</h2></header>{_team_cards(other)}</section>'
        )
    return "".join(sections)


def _team_cards(profiles: list[dict]) -> str:
    cards = []
    for profile in profiles:
        initials = "".join(part[0] for part in profile["display_name"].split()[:2]).upper()
        avatar = profile.get("avatar_data") or ""
        portrait = (
            f'<img class="team-avatar" src="{e(avatar)}" alt="Porträt von {e(profile["display_name"])}" loading="lazy" decoding="async">'
            if avatar else f'<div class="team-initials" aria-hidden="true">{e(initials)}</div>'
        )
        bio_html = f"<p>{e(profile['bio'])}</p>" if profile.get("bio") else ""
        cards.append(
            f'<article class="team-card">{portrait}'
            f'<p class="tag">{e(profile["role_label"])}</p><h3>{e(profile["display_name"])}</h3>'
            f'{bio_html}</article>'
        )
    return '<div class="team-grid">' + "".join(cards) + "</div>"


def _safe_public_profiles(limit: int | None = None) -> list[dict]:
    try:
        profiles = public_content.list_profiles(published_only=True)
        return profiles[:limit] if limit is not None else profiles
    except sqlite3.Error:
        return []


def _discord_team_roster(request: Request) -> tuple[list[str], int]:
    """Render only TeamBot members with explicit, current-version consent for this role set."""
    import pulse_ids
    from cogs.teamliste import get_roles_by_category

    bot = getattr(request.app.state, "bot", None)
    guild = bot.get_guild(pulse_ids.GUILD_ID) if bot else None
    if guild is None:
        return [], 0

    categories = (
        ("fuehrungsebene", "Führungsebene"),
        ("highteam", "HighTeam"),
        ("lowteam", "LowTeam"),
    )
    configured_groups: list[tuple[str, str, list]] = []
    member_role_ids: dict[int, set[int]] = {}

    # Build the complete current TeamBot role scope before checking any individual consent.
    for category_key, category_label in categories:
        try:
            raw_role_ids = get_roles_by_category(category_key)
        except (sqlite3.Error, OSError, RuntimeError):
            raw_role_ids = []
        normalized_ids = []
        for raw_id in raw_role_ids:
            try:
                role_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            if role_id > 0 and role_id not in normalized_ids:
                normalized_ids.append(role_id)
        roles = [guild.get_role(role_id) for role_id in normalized_ids]
        roles = sorted(
            (
                role for role in roles
                if role is not None and team_role_group(getattr(role, "name", "")) == category_key
            ),
            key=lambda role: (
                team_role_priority(getattr(role, "name", "")) is None,
                team_role_priority(getattr(role, "name", "")) if team_role_priority(getattr(role, "name", "")) is not None else 10_000,
                -getattr(role, "position", 0),
            ),
        )
        configured_groups.append((category_key, category_label, roles))
        for role in roles:
            try:
                role_id = int(role.id)
            except (TypeError, ValueError, AttributeError):
                continue
            for member in getattr(role, "members", ()) or ():
                try:
                    member_id = int(member.id)
                except (TypeError, ValueError, AttributeError):
                    continue
                member_role_ids.setdefault(member_id, set()).add(role_id)

    consents = public_team_consent.active_consents()
    permitted_ids = {
        member_id
        for member_id, current_roles in member_role_ids.items()
        if (
            (record := consents.get(str(member_id))) is not None
            and record.get("role_ids") == sorted(current_roles)
        )
    }
    if not permitted_ids:
        return [], 0

    sections: list[str] = []
    for _category_key, category_label, roles in configured_groups:
        role_groups = []
        for role in roles:
            members = sorted(
                (
                    member for member in (getattr(role, "members", ()) or ())
                    if getattr(member, "id", None) in permitted_ids
                ),
                key=lambda member: str(getattr(member, "display_name", "")).casefold(),
            )
            if not members:
                continue
            cards = []
            for member in members:
                display_name = str(getattr(member, "display_name", "") or "Teammitglied")
                initials = "".join(part[0] for part in display_name.split()[:2]).upper() or "?"
                # Do not make public visitors' browsers contact Discord's CDN for a team avatar.
                # Initials keep the public roster useful without a third-party image request.
                portrait = f'<div class="team-initials" aria-hidden="true">{e(initials)}</div>'
                cards.append(
                    f'<article class="team-card">{portrait}<p class="tag">{e(role.name)}</p>'
                    f'<h3>{e(display_name)}</h3></article>'
                )
            role_groups.append(
                f'<section class="team-rank" aria-label="{e(role.name)}">'
                f'<header class="team-rank-head"><h3>{e(role.name)}</h3>'
                f'<span>{len(cards)} {"Mitglied" if len(cards) == 1 else "Mitglieder"}</span></header>'
                f'<div class="team-grid">{"".join(cards)}</div></section>'
            )
        if role_groups:
            sections.append(
                f'<section class="team-level"><header class="team-level-head">'
                f'<p class="tag">{e(category_label)}</p></header>{"".join(role_groups)}</section>'
            )
    return sections, len(permitted_ids)


def public_team_html(request: Request) -> str:
    sections, member_total = _discord_team_roster(request)
    if not sections:
        return (
            '<div class="doc"><header class="team-intro"><p class="tag">BOCHUM RP · TEAM</p>'
            '<h1>Die Köpfe hinter BOCHUM RP</h1><p>Engagiert, erfahren und für unsere Community im Einsatz.</p></header>'
            '<p class="empty">Die Teamvorstellung zeigt ausschließlich Personen mit einer freiwilligen, ausdrücklich gespeicherten Freigabe. Aktuell sind keine Profile freigegeben. Die Teammitgliedschaft ist nicht von einer Veröffentlichung abhängig.</p></div>'
        )
    return (
        '<div class="doc"><header class="team-intro"><p class="tag">BOCHUM RP · TEAM</p>'
        '<h1>Die Köpfe hinter BOCHUM RP</h1>'
        f'<p class="team-total">{member_total} {"Mitglied" if member_total == 1 else "Mitglieder"}</p>'
        '<p>Hier findest du die Menschen hinter BOCHUM RP – sortiert nach ihrer aktuellen Teamfunktion.</p>'
        '</header>'
        f'{"".join(sections)}</div>'
    )


def _read_team_avatar(upload: UploadFile | None) -> str:
    """Validate an optional locally uploaded PNG for legacy manually managed profile records."""
    if upload is None or not upload.filename:
        return ""
    payload = upload.file.read(512 * 1024 + 1)
    if not payload:
        return ""
    if len(payload) > 512 * 1024 or len(payload) < 24 or payload[:8] != bytes.fromhex("89504e470d0a1a0a") or payload[12:16] != b"IHDR":
        raise ValueError("Profilbild: Bitte ein PNG bis 512 KB hochladen.")
    width = int.from_bytes(payload[16:20], "big")
    height = int.from_bytes(payload[20:24], "big")
    if not (1 <= width <= 1024 and 1 <= height <= 1024):
        raise ValueError("Profilbild: Maximal 1024 × 1024 Pixel.")
    return "data:image/png;base64," + base64.b64encode(payload).decode("ascii")


def public_team_teaser() -> str:
    return (
        '<section class="band band-alt" id="team"><p class="section-kicker">03 / DEINE ANSPRECHPARTNER</p>'
        '<h2>Das Team hinter Bochum RP</h2>'
        '<p>Die öffentliche Teamvorstellung wird automatisch aus den aktuellen Teamrollen gepflegt.</p>'
        '<p><a class="more" href="/team-vorstellung">Teamvorstellung ansehen</a></p></section>'
    )


def faq_html() -> str:
    rows = "".join(f"<details><summary>{e(q)}</summary><p>{e(a)}</p></details>" for q, a in FAQ)
    return f'<div class="doc"><h1>Häufige Fragen</h1><div class="faq">{rows}</div></div>'


def respond(html: str, *, cache: bool = True, status_code: int = 200) -> HTMLResponse:
    response = HTMLResponse(html, status_code=status_code)
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Frame-Options"] = "DENY"
    if cache:
        response.headers["Cache-Control"] = "public, max-age=300"
    else:
        response.headers["Cache-Control"] = "no-store"
    # Unfinished legal details: stay out of search engines until the operator completed them.
    response.headers["X-Robots-Tag"] = "index, follow" if legal.publication_ready() else "noindex, nofollow"
    return response


def sitemap_xml(base_url: str) -> str:
    """Generate a minimal sitemap only after the operator has approved public/legal publication."""
    if not legal.publication_ready():
        return ""
    paths = [path for path in PUBLIC_PATHS if path != "/sitemap.xml"]
    paths.extend(f"/news/{int(item['id'])}" for item in list_news(limit=NEWS_LIMIT))
    prefix = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    )
    urls = "".join(f"<url><loc>{e(base_url.rstrip('/') + path)}</loc></url>" for path in paths)
    return prefix + urls + "</urlset>"


def robots_text(base_url: str = "") -> str:
    if not public_access_enabled():
        # Do not block crawling: bots must see 404/noindex so already indexed URLs can expire.
        return "User-agent: *\nAllow: /\n"
    allowed = "".join(f"Allow: {path}$\n" for path in PUBLIC_PATHS)
    sitemap = f"Sitemap: {base_url.rstrip('/')}/sitemap.xml\n" if base_url else ""
    return f"User-agent: *\n{allowed}Allow: /news/\nAllow: /static/public\nDisallow: /\n{sitemap}"


def home_response(request: Request) -> HTMLResponse:
    html = page("Bochum RP", "Roleplay-Community mit klaren Regeln und offiziellen Fraktionen.",
                home_html(request), path="/")
    return respond(html, cache=False)  # "/" depends on the session cookie


def register(app) -> None:
    import webserver as ws

    def limited(request: Request):
        if ws.rate_limited(ws.request_rate_key(request, "public-site"), limit=PUBLIC_RATE_LIMIT, window=60):
            return ws.rate_limit_response("Zu viele Seitenaufrufe. Bitte versuche es in einer Minute erneut.")
        return None

    def add_page(path: str, title: str, description: str, render, *, cache: bool = True, with_request: bool = False):
        async def handler(request: Request):
            if not public_access_enabled():
                raise HTTPException(status_code=404, headers={"X-Robots-Tag": "noindex, nofollow"})
            if limited_response := limited(request):
                return limited_response
            body = render(request) if with_request else render()
            return respond(page(title, description, body, path=path), cache=cache)

        handler.__name__ = "public_" + path.strip("/")
        app.add_api_route(path, handler, methods=["GET"], response_class=HTMLResponse, include_in_schema=False)

    add_page("/regelwerk", "Regelwerk | Bochum RP", "Ingame- und Discord-Regelwerk von Bochum RP.", rules_html)
    add_page("/fraktionen", "Fraktionen | Bochum RP", "Die offiziellen Fraktionen von Bochum RP.", factions_html)
    add_page("/news", "News | Bochum RP", "Neuigkeiten von Bochum RP.", news_html)
    add_page("/faq", "Häufige Fragen | Bochum RP", "Antworten zu Server, Roleplay und Bewerbung.", faq_html)
    # Public profile withdrawals must not remain in intermediary caches.
    add_page("/team-vorstellung", "Team | Bochum RP", "Das aktuelle Team von Bochum RP.",
             public_team_html, cache=False, with_request=True)
    add_page("/nutzungsbedingungen", "Nutzungsbedingungen | Bochum RP", "Nutzungs- und Community-Bedingungen von Bochum RP.", legal.terms_html)
    add_page("/impressum", "Impressum | Bochum RP", "Anbieterkennzeichnung von Bochum RP.", legal.impressum_html)
    # Legal disclosure contains environment-backed provider/controller details; never cache stale versions.
    add_page("/datenschutz", "Datenschutz | Bochum RP", "Wie Bochum RP und Pulse personenbezogene Daten verarbeiten.",
             legal.privacy_html, cache=False)
    add_page("/cookies", "Cookies | Bochum RP", "Welche Cookies und Browser-Speicher eingesetzt werden.",
             lambda: legal.cookies_html(OPTIONAL_SERVICES))
    add_page("/sicherheit", "Sicherheit | Bochum RP", "So schützt Pulse Konten und Daten.", legal.security_html)

    async def public_news_detail(news_id: int, request: Request):
        if not public_access_enabled():
            raise HTTPException(status_code=404, headers={"X-Robots-Tag": "noindex, nofollow"})
        limited_response = limited(request)
        if limited_response:
            return limited_response
        item = get_news(news_id)
        if not item:
            raise HTTPException(status_code=404)
        return respond(page(
            f'{item["title"]} | Bochum RP',
            f'Neuigkeiten von Bochum RP: {item["title"]}',
            news_detail_html(item),
            path="/news/" + str(news_id),
        ))

    app.add_api_route("/news/{news_id}", public_news_detail, methods=["GET"],
                      response_class=HTMLResponse, include_in_schema=False)

    async def sitemap(request: Request):
        if not public_access_enabled():
            return Response("", status_code=404, media_type="application/xml",
                            headers={"X-Robots-Tag": "noindex, nofollow"})
        base = str(request.base_url).rstrip("/")
        return Response(sitemap_xml(base), media_type="application/xml")

    app.add_api_route("/sitemap.xml", sitemap, methods=["GET"], include_in_schema=False)

    def add_static(name: str, media: str):
        async def handler():
            path = ROOT / "static" / name
            if not path.is_file():
                raise HTTPException(status_code=404)
            return FileResponse(path, media_type=media, headers={"Cache-Control": "public, max-age=604800, immutable"})

        handler.__name__ = "public_static_" + name.replace(".", "_")
        app.add_api_route(f"/static/{name}", handler, methods=["GET"], include_in_schema=False)

    for name, media in STATIC_FILES.items():
        add_static(name, media)

    # --- administrators publish public content; profiles remain drafts until explicit approval ----
    async def website_admin(request: Request, user_session: str = Cookie(None)):
        from pulse_pro import render_pro_page

        ctx = ws.auth(request, user_session, admin=True)
        missing = legal.publication_blockers()
        status = (
            '<p><span class="pulse-pill good">Freigaben vollständig</span></p>'
            if not missing
            else '<p><span class="pulse-pill warn">Website bleibt für Suchmaschinen gesperrt</span></p><ul>'
            + "".join(f"<li><code>{e(key)}</code>: {e(label)}</li>" for key, label in missing.items()) + "</ul>"
        )
        try:
            rows = list_news()
        except sqlite3.Error:
            rows = []
        items = "".join(
            f'<div class="pulse-row"><div class="pulse-row-main"><b>{e(row["title"])}</b></div>'
            f'<form method="post" action="/admin/website/news/{int(row["id"])}/delete">'
            f'<button class="btn" onclick="return confirm(\'News löschen?\')">Löschen</button></form></div>'
            for row in rows
        ) or "<p class='muted'>Noch keine News veröffentlicht.</p>"
        publication = (
            '<p><span class="pulse-pill good">Öffentliche Veröffentlichung freigegeben</span></p>'
            if public_access_enabled()
            else '<p><span class="pulse-pill warn">Website und Bewerbungsportal sind privat gesperrt</span></p>'
        )
        body = f"""<div class="pulse-card"><h2>Freigabestatus</h2>{status}</div>
        <div class="pulse-card"><h2>Private Vorschau</h2>{publication}
        <p class="muted">Nur Administratoren können diese Vorschau öffnen. Die öffentliche Website bleibt standardmäßig geschlossen, bis der Veröffentlichungs-Schalter aktiv ist und alle rechtlichen Freigaben vollständig sind.</p>
        <a class="btn btn-primary" href="/admin/website/preview">Startseite privat ansehen</a></div>
        <div class="pulse-card"><h2>Öffentliche Teamvorstellung</h2>
        <p class="muted">Die Teamseite wird automatisch aus den aktuellen Rollen der TeamBot-Kategorien Führungsebene, HighTeam und LowTeam erzeugt. Rollenänderungen werden direkt bei der nächsten Seitenanfrage übernommen; manuelle Profileinträge sind dafür nicht erforderlich.</p></div>
        <div class="pulse-card"><h2>News veröffentlichen</h2>
        <p class="muted">News werden gespeichert und erst nach der ausdrücklichen Freigabe der öffentlichen Website sichtbar.</p>
        <form method="post" action="/admin/website/news" class="grid gap-3">
        <label>Titel <input class="pulse-input" name="title" maxlength="120" required></label>
        <label>Kategorie <input class="pulse-input" name="category" maxlength="40" value="Community" required></label>
        <label>Text <textarea class="pulse-input" name="body" maxlength="4000" rows="6" required></textarea></label>
        <button class="btn btn-primary">Veröffentlichen</button></form></div>
        <div class="pulse-card"><h2>Veröffentlichte News</h2>{items}</div>"""
        return HTMLResponse(render_pro_page(ws, "Website", ctx, "website", body))

    async def website_preview(request: Request, user_session: str = Cookie(None)):
        """Render the retained public homepage only for authenticated administrators."""
        ws.auth(request, user_session, admin=True)
        response = home_response(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        return response

    async def website_team_admin(request: Request, user_session: str = Cookie(None)):
        from pulse_pro import render_pro_page

        ctx = ws.auth(request, user_session, admin=True)
        profiles = public_content.list_profiles(published_only=False)
        row_parts = []
        for item in profiles:
            if item["is_published"]:
                publication_action = (
                    f'<form method="post" action="/admin/website/team/{int(item["id"])}/publish">'
                    '<button class="btn" name="published" value="0">Veröffentlichung zurückziehen</button></form>'
                )
            else:
                publication_action = (
                    f'<form method="post" action="/admin/website/team/{int(item["id"])}/publish">'
                    '<label><input type="checkbox" name="consent_confirmed" value="1" required> '
                    'Die ausdrückliche Freigabe der betroffenen Person liegt vor.</label>'
                    '<button class="btn" name="published" value="1">Veröffentlichen</button></form>'
                )
            row_parts.append(
                f'<div class="pulse-row"><div class="pulse-row-main"><b>{e(item["display_name"])}</b>'
                f'<div class="muted">{e(item["role_label"])} · {"Öffentlich" if item["is_published"] else "Entwurf"}</div>'
                f'<div class="muted">{e(item["bio"])}</div></div>{publication_action}'
                f'<form method="post" action="/admin/website/team/{int(item["id"])}/delete">'
                f'<button class="btn" onclick="return confirm(\'Profil löschen?\')">Löschen</button></form></div>'
            )
        rows = "".join(row_parts) or "<p class='muted'>Noch keine Profile angelegt.</p>"
        body = f"""<div class="pulse-card"><h2>Profil anlegen</h2>
        <p class="muted">Nur Angaben und Bilder eintragen, deren Veröffentlichung ausdrücklich freigegeben wurde. Keine privaten Kontaktdaten oder internen Informationen.</p>
        <form method="post" enctype="multipart/form-data" action="/admin/website/team" class="grid gap-3">
        <label>Öffentlicher Anzeigename <input class="pulse-input" name="display_name" maxlength="40" required></label>
        <label>Öffentliche Teamfunktion <input class="pulse-input" name="role_label" maxlength="80" required></label>
        <label>Freigegebene Kurzbeschreibung <textarea class="pulse-input" name="bio" maxlength="360" rows="4"></textarea></label>
        <label>Öffentlich freigegebenes Profilbild (PNG, max. 512 KB, 1024 × 1024 px) <input class="pulse-input" type="file" name="avatar" accept="image/png"></label>
        <button class="btn btn-primary">Als Entwurf speichern</button></form></div>
        <div class="pulse-card"><h2>Profile und Veröffentlichungsstatus</h2>{rows}</div>"""
        return HTMLResponse(render_pro_page(ws, "Öffentliche Teamprofile", ctx, "website", body))

    async def website_team_add(request: Request, display_name: str = Form(...), role_label: str = Form(...),
                               bio: str = Form(""), avatar: UploadFile | None = File(None),
                               user_session: str = Cookie(None)):
        ctx = ws.auth(request, user_session, admin=True)
        actor = ctx.user.get("global_name") or ctx.user.get("username") or "Admin"
        try:
            avatar_data = _read_team_avatar(avatar)
            profile_id = public_content.create_profile(display_name, role_label, bio, avatar_data)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        ws.log_audit(actor, ctx.user["id"], "Öffentliches Teamprofil als Entwurf erstellt", f"#{profile_id}")
        return RedirectResponse("/admin/website/team", status_code=303)

    async def website_team_publish(profile_id: int, request: Request, published: int = Form(...),
                                   consent_confirmed: str = Form(""), user_session: str = Cookie(None)):
        ctx = ws.auth(request, user_session, admin=True)
        if published not in (0, 1):
            raise HTTPException(status_code=422, detail="Ungültiger Veröffentlichungsstatus.")
        if published == 1 and consent_confirmed != "1":
            raise HTTPException(status_code=422, detail="Vor der Veröffentlichung muss die ausdrückliche Freigabe der Person bestätigt werden.")
        actor = ctx.user.get("global_name") or ctx.user.get("username") or "Admin"
        if not public_content.set_published(profile_id, bool(published)):
            raise HTTPException(status_code=404)
        ws.log_audit(actor, ctx.user["id"], "Öffentlicher Teamprofil-Status geändert",
                     f"#{profile_id}: {'veröffentlicht' if published else 'zurückgezogen'}")
        return RedirectResponse("/admin/website/team", status_code=303)

    async def website_team_delete(profile_id: int, request: Request, user_session: str = Cookie(None)):
        ctx = ws.auth(request, user_session, admin=True)
        actor = ctx.user.get("global_name") or ctx.user.get("username") or "Admin"
        if public_content.delete_profile(profile_id):
            ws.log_audit(actor, ctx.user["id"], "Öffentliches Teamprofil gelöscht", f"#{profile_id}")
        return RedirectResponse("/admin/website/team", status_code=303)

    async def website_news_add(request: Request, title: str = Form(...), body: str = Form(...),
                               category: str = Form("Community"), user_session: str = Cookie(None)):
        ctx = ws.auth(request, user_session, admin=True)
        name = ctx.user.get("global_name") or ctx.user.get("username") or "Admin"
        try:
            news_id = add_news(title, body, name, category)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        ws.log_audit(name, ctx.user["id"], "Website-News veröffentlicht", f"#{news_id}")
        return RedirectResponse("/admin/website", status_code=303)

    async def website_news_delete(news_id: int, request: Request, user_session: str = Cookie(None)):
        ctx = ws.auth(request, user_session, admin=True)
        name = ctx.user.get("global_name") or ctx.user.get("username") or "Admin"
        if delete_news(news_id):
            ws.log_audit(name, ctx.user["id"], "Website-News gelöscht", f"#{news_id}")
        return RedirectResponse("/admin/website", status_code=303)

    app.add_api_route("/admin/website", website_admin, methods=["GET"], response_class=HTMLResponse)
    app.add_api_route("/admin/website/preview", website_preview, methods=["GET"], response_class=HTMLResponse)
    app.add_api_route("/admin/website/team", website_team_admin, methods=["GET"], response_class=HTMLResponse)
    app.add_api_route("/admin/website/team", website_team_add, methods=["POST"])
    app.add_api_route("/admin/website/team/{profile_id}/publish", website_team_publish, methods=["POST"])
    app.add_api_route("/admin/website/team/{profile_id}/delete", website_team_delete, methods=["POST"])
    app.add_api_route("/admin/website/news", website_news_add, methods=["POST"])
    app.add_api_route("/admin/website/news/{news_id}/delete", website_news_delete, methods=["POST"])


