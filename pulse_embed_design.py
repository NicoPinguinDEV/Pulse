"""Central Discord design system for Pulse embeds.

One place for semantic colours, brand footer, Discord limits, safe text and
template rendering. Cogs ask for a *meaning* ("success", "warning") instead of
hard-coding discord.Color.* values, so the look stays consistent and can be
changed by an administrator in "Verwaltung → Discord-Auftritt".

The pure helpers here need no Discord connection and are unit-tested.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from string import Formatter

BRANDING_SETTING_KEY = "discord_branding"

# Pulse brand indigo plus calm semantic colours (no rainbow).
DEFAULT_COLORS = {
    "brand": "#5B5CF0",    # Pulse-Hauptfarbe, Information
    "success": "#12B76A",  # erledigt, bestätigt
    "warning": "#F79009",  # Aufmerksamkeit nötig
    "danger": "#F04438",   # Fehler, kritisch, Sanktion
    "neutral": "#667085",  # Protokoll, Audit, Status
    "highlight": "#EAB308",  # Auszeichnung, Beförderung
}
# Semantic aliases used by the cogs.
KIND_ALIASES = {
    "info": "brand", "primary": "brand", "brand": "brand",
    "success": "success", "ok": "success",
    "warning": "warning", "warn": "warning",
    "danger": "danger", "error": "danger", "critical": "danger",
    "neutral": "neutral", "audit": "neutral", "log": "neutral",
    "highlight": "highlight", "award": "highlight",
}
DEFAULT_BRANDING = {
    "brand_name": "Pulse TeamOS",
    "footer_text": "© BOCHUM RP | V1 · Pulse TeamOS",
    "colors": dict(DEFAULT_COLORS),
    # "modern" = Discord Components V2 container (like GalaxyBot), "classic" = embed.
    "style": "modern",
    "accent_bar": False,
}
STYLES = ("modern", "classic")
# Components V2: all text of one message together may not exceed 4000 characters.
LAYOUT_TEXT_LIMIT = 4000

# Discord hard limits (https://discord.com/developers/docs/resources/message#embed-object-embed-limits)
LIMITS = {
    "title": 256, "description": 4096, "field_name": 256, "field_value": 1024,
    "footer": 2048, "author": 256, "fields": 25, "total": 6000, "content": 2000,
}

_HEX_RE = re.compile(r"^#?([0-9A-Fa-f]{6})$")
_cache: dict = {"at": 0.0, "value": None}
_CACHE_SECONDS = 30.0


# --------------------------------------------------------------------------- branding
def normalize_hex(value, fallback: str) -> str:
    match = _HEX_RE.match(str(value or "").strip())
    return f"#{match.group(1).upper()}" if match else fallback


def normalize_branding(raw) -> dict:
    """Validated branding with defaults for every missing/invalid value."""
    raw = raw if isinstance(raw, dict) else {}
    colors_raw = raw.get("colors") if isinstance(raw.get("colors"), dict) else {}
    brand_name = re.sub(r"\s+", " ", str(raw.get("brand_name") or "")).strip()[:60]
    footer = re.sub(r"\s+", " ", str(raw.get("footer_text") or "")).strip()[:160]
    return {
        "brand_name": brand_name or DEFAULT_BRANDING["brand_name"],
        "footer_text": footer or DEFAULT_BRANDING["footer_text"],
        "colors": {key: normalize_hex(colors_raw.get(key), default) for key, default in DEFAULT_COLORS.items()},
        "style": raw.get("style") if raw.get("style") in STYLES else DEFAULT_BRANDING["style"],
        "accent_bar": bool(raw.get("accent_bar", DEFAULT_BRANDING["accent_bar"])),
    }


def branding() -> dict:
    """Current branding (cached briefly so busy cogs do not hit SQLite per embed)."""
    now = time.monotonic()
    if _cache["value"] is not None and now - _cache["at"] < _CACHE_SECONDS:
        return _cache["value"]
    try:
        import pulse_db
        value = normalize_branding(pulse_db.get_setting(BRANDING_SETTING_KEY, {}))
    except Exception:
        value = normalize_branding({})
    _cache.update(at=now, value=value)
    return value


def save_branding(raw) -> dict:
    import pulse_db
    value = normalize_branding(raw)
    pulse_db.set_setting(BRANDING_SETTING_KEY, value)
    invalidate_cache()
    return value


def invalidate_cache() -> None:
    _cache.update(at=0.0, value=None)


def color_int(kind: str = "info", brand: dict | None = None) -> int:
    palette = (brand or branding())["colors"]
    key = KIND_ALIASES.get(str(kind or "info").lower(), "brand")
    return int(palette.get(key, DEFAULT_COLORS[key]).lstrip("#"), 16)


def color(kind: str = "info"):
    """discord.Colour for a semantic kind ('success', 'warning', 'danger', ...)."""
    import discord
    return discord.Colour(color_int(kind))


def footer_text(extra: str = "") -> str:
    base = branding()["footer_text"]
    return truncate(f"{base} · {extra}" if extra else base, LIMITS["footer"])


# --------------------------------------------------------------------------- text safety
def truncate(value, limit: int, ellipsis: str = "…") -> str:
    text = str(value if value is not None else "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(ellipsis))].rstrip() + ellipsis


_MASS_MENTION_RE = re.compile(r"@(everyone|here)", re.IGNORECASE)
_ROLE_MENTION_RE = re.compile(r"<@&\d+>")


def neutralize_mentions(text) -> str:
    """Break @everyone/@here and role pings in user-provided text (zero-width joiner)."""
    text = _MASS_MENTION_RE.sub(lambda m: "@​" + m.group(1), str(text or ""))
    return _ROLE_MENTION_RE.sub(lambda m: m.group(0).replace("<@&", "<@​&"), text)


def discord_timestamp(value: datetime | None, style: str = "f") -> str:
    """Discord renders <t:…> in each reader's local time zone."""
    if not isinstance(value, datetime):
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return f"<t:{int(value.timestamp())}:{style}>"


# --------------------------------------------------------------------------- templates
class TemplateError(ValueError):
    pass


def template_variables(template: str) -> list[str]:
    """Names used in a {placeholder} template; raises TemplateError on broken braces."""
    names = []
    try:
        for _, field, spec, conversion in Formatter().parse(str(template or "")):
            if field is None:
                continue
            if not re.fullmatch(r"[a-z_]{1,32}", field) or spec or conversion:
                raise TemplateError(f"Ungültiger Platzhalter „{{{field}}}“.")
            names.append(field)
    except ValueError as exc:
        if isinstance(exc, TemplateError):
            raise
        raise TemplateError("Geschweifte Klammern sind nicht geschlossen. Nutze Platzhalter wie {display_name}.") from exc
    return names


def validate_template(template: str, allowed) -> list[str]:
    """Return human-readable problems (empty list = fine)."""
    try:
        used = template_variables(template)
    except TemplateError as exc:
        return [str(exc)]
    unknown = sorted({name for name in used if name not in set(allowed)})
    return [f"Unbekannter Platzhalter {{{name}}}." for name in unknown]


def render_template(template: str, values: dict) -> str:
    """Fill placeholders; unknown/unavailable ones become '–' instead of leaking '{x}'."""
    out = []
    try:
        parts = list(Formatter().parse(str(template or "")))
    except ValueError:
        return str(template or "").replace("{", "(").replace("}", ")")
    for literal, field, _, _ in parts:
        out.append(literal)
        if field is not None:
            value = values.get(field)
            out.append(str(value) if value not in (None, "") else "–")
    return "".join(out)


# --------------------------------------------------------------------------- embed spec
def build_embed_spec(kind: str = "info", *, title: str = "", description: str = "", fields=(),
                     thumbnail_url: str = "", image_url: str = "", footer: str | None = None,
                     timestamp: datetime | None = None, author_name: str = "", author_icon_url: str = "",
                     brand: dict | None = None) -> dict:
    """Pure, limit-safe embed description (dict) – converted by to_discord_embed()."""
    brand = brand or branding()
    spec = {
        "color": color_int(kind, brand),
        "title": truncate(title.strip(), LIMITS["title"]) if title else "",
        "description": truncate(description.strip(), LIMITS["description"]) if description else "",
        "fields": [],
        "thumbnail_url": safe_image_url(thumbnail_url),
        "image_url": safe_image_url(image_url),
        "footer": truncate(brand["footer_text"] if footer is None else footer, LIMITS["footer"]),
        "timestamp": timestamp,
        "author_name": truncate(author_name, LIMITS["author"]) if author_name else "",
        "author_icon_url": safe_image_url(author_icon_url),
    }
    for field in list(fields)[: LIMITS["fields"]]:
        name, value, inline = (tuple(field) + (False,))[:3]
        name = truncate(str(name or "").strip() or "​", LIMITS["field_name"])
        value = truncate(str(value or "").strip() or "–", LIMITS["field_value"])
        spec["fields"].append((name, value, bool(inline)))
    _fit_total(spec)
    return spec


def _fit_total(spec: dict) -> None:
    """Shrink the description if the whole embed exceeds Discord's 6000-char total."""
    def total():
        return (len(spec["title"]) + len(spec["description"]) + len(spec["footer"]) + len(spec["author_name"])
                + sum(len(n) + len(v) for n, v, _ in spec["fields"]))
    while total() > LIMITS["total"] and spec["fields"]:
        spec["fields"].pop()
    overflow = total() - LIMITS["total"]
    if overflow > 0:
        spec["description"] = truncate(spec["description"], max(0, len(spec["description"]) - overflow))


def safe_image_url(value) -> str:
    """Only https:// or attachment:// images; anything else is dropped."""
    url = str(value or "").strip()
    if url.startswith("attachment://") and re.fullmatch(r"attachment://[A-Za-z0-9_.-]{1,100}", url):
        return url
    if re.fullmatch(r"https://[^\s<>\"']{4,1500}", url):
        return url
    return ""


def to_discord_embed(spec: dict):
    import discord
    embed = discord.Embed(color=discord.Colour(spec["color"]), title=spec["title"] or None,
                          description=spec["description"] or None, timestamp=spec.get("timestamp"))
    for name, value, inline in spec["fields"]:
        embed.add_field(name=name, value=value, inline=inline)
    if spec["thumbnail_url"]:
        embed.set_thumbnail(url=spec["thumbnail_url"])
    if spec["image_url"]:
        embed.set_image(url=spec["image_url"])
    if spec["author_name"]:
        embed.set_author(name=spec["author_name"], icon_url=spec["author_icon_url"] or None)
    if spec["footer"]:
        embed.set_footer(text=spec["footer"])
    return embed


def make_embed(kind: str = "info", **kwargs):
    """Brand embed in one call: make_embed('success', title=..., description=..., fields=[...])."""
    return to_discord_embed(build_embed_spec(kind, **kwargs))


def apply_brand_footer(embed, extra: str = ""):
    """Add the brand footer to an existing embed that has none (keeps custom footers)."""
    try:
        if not (embed.footer and embed.footer.text):
            embed.set_footer(text=footer_text(extra))
    except Exception:
        pass
    return embed


# --------------------------------------------------------------------------- modern layout
def layout_text_blocks(spec: dict, *, mention_line: str = "") -> dict:
    """Pure text plan for the modern container, trimmed to Discord's 4000-character total."""
    head = f"### {spec['title']}" if spec.get("title") else ""
    body = spec.get("description") or ""
    fields = [f"**{name}**\n{value}" for name, value, _ in spec.get("fields", [])]
    footer = spec.get("footer") or ""
    stamp = discord_timestamp(spec.get("timestamp"), "f") if spec.get("timestamp") else ""
    footer_line = f"-# {footer}{' · ' + stamp if stamp else ''}" if (footer or stamp) else ""
    fixed = len(head) + len(mention_line) + sum(len(f) for f in fields) + len(footer_line) + 8
    body = truncate(body, max(0, LAYOUT_TEXT_LIMIT - fixed))
    main = "\n".join(part for part in (mention_line, head, body) if part)
    return {"main": main, "fields": fields, "footer": footer_line}


def layout_from_spec(spec: dict, *, mention_line: str = "", action_items=(), action_rows=(), view=None,
                     brand: dict | None = None):
    """Discord Components V2 message (container with text, avatar on the right, banner, footer).

    action_items: buttons placed in an action row inside the container (e.g. verification).
    view: an existing LayoutView to fill (persistent views); a new one is created otherwise.
    """
    import discord
    from discord import ui

    brand = brand or branding()
    blocks = layout_text_blocks(spec, mention_line=mention_line)
    children = []
    if spec.get("thumbnail_url"):
        children.append(ui.Section(ui.TextDisplay(blocks["main"] or "\u200b"), accessory=ui.Thumbnail(spec["thumbnail_url"])))
    elif blocks["main"]:
        children.append(ui.TextDisplay(blocks["main"]))
    for field in blocks["fields"]:
        children.append(ui.TextDisplay(field))
    if spec.get("image_url"):
        children.append(ui.MediaGallery(discord.MediaGalleryItem(spec["image_url"])))
    if action_items:
        children.append(ui.ActionRow(*action_items))
    for row in action_rows:
        if row:
            children.append(ui.ActionRow(*row))
    if blocks["footer"]:
        children.append(ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small))
        children.append(ui.TextDisplay(blocks["footer"]))
    accent = spec["color"] if brand.get("accent_bar") else None
    layout = view if view is not None else ui.LayoutView(timeout=None)
    layout.add_item(ui.Container(*children, accent_colour=accent))
    return layout


def is_modern(brand: dict | None = None) -> bool:
    return (brand or branding()).get("style") == "modern"
