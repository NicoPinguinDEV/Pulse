"""Lightweight, API-free search and plain-language formatting for old announcements."""
from __future__ import annotations

import re

_STOPWORDS = {
    "aber", "als", "also", "am", "an", "auch", "auf", "aus", "bei", "bin",
    "bis", "bitte", "das", "dass", "dem", "den", "der", "des", "die", "dies",
    "diese", "dieser", "dieses", "du", "durch", "ein", "eine", "einem", "einen",
    "einer", "eines", "er", "es", "für", "hat", "haben", "hier", "ich", "im",
    "in", "ist", "mit", "nach", "nicht", "oder", "sie", "sind", "so", "und",
    "uns", "von", "vor", "war", "was", "wie", "wir", "zu", "zum", "zur",
}
_TOKEN_RE = re.compile(r"[a-z0-9äöüß]+", re.IGNORECASE)
_BULLET_RE = re.compile(r"^\s*(?:[-*•‣▪]|\d{1,2}[.)])\s+")
_LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\((https?://[^)]+)\)")


def _tokens(text: str) -> list[str]:
    return [
        token.casefold()
        for token in _TOKEN_RE.findall(text or "")
        if len(token) > 1 and token.casefold() not in _STOPWORDS
    ]


def score_announcement(query: str, text: str) -> int:
    """Return a simple relevance score; zero means no meaningful keyword matched."""
    terms = list(dict.fromkeys(_tokens(query)))
    body_tokens = _tokens(text)
    if not terms or not body_tokens:
        return 0

    body_set = set(body_tokens)
    score = 0
    matched = 0
    for term in terms:
        if term in body_set:
            score += 4
            matched += 1
        elif any(term in token or token in term for token in body_tokens):
            score += 2
            matched += 1

    normalised_query = " ".join(terms)
    normalised_body = " ".join(body_tokens)
    if normalised_query and normalised_query in normalised_body:
        score += 4
    if matched == len(terms):
        score += min(4, len(terms))
    return score if matched else 0


def _clean_inline(line: str) -> str:
    """Remove common Discord/Markdown presentation while preserving the wording."""
    text = (line or "").strip()
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text)
    text = re.sub(r"^\s*>\s?", "", text)
    text = _LINK_RE.sub(r"\1", text)
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"__(.*?)__", r"\1", text)
    text = re.sub(r"~~(.*?)~~", r"\1", text)
    text = text.replace(chr(96), "")
    text = re.sub(r"[*_]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def clean_announcement_text(text: str) -> str:
    """Make announcement text readable for snippets and search results."""
    output: list[str] = []
    for raw_line in (text or "").splitlines():
        if not raw_line.strip():
            if output and output[-1] != "":
                output.append("")
            continue
        is_bullet = bool(_BULLET_RE.match(raw_line))
        line = _clean_inline(_BULLET_RE.sub("", raw_line) if is_bullet else raw_line)
        if line:
            output.append(f"• {line}" if is_bullet else line)
    while output and output[-1] == "":
        output.pop()
    return "\n".join(output)


def build_announcement_explanation(title: str, text: str) -> str:
    """Build an extractive, plain-language guide without inventing server rules."""
    paragraphs: list[str] = []
    bullets: list[str] = []
    current: list[str] = []
    title_key = " ".join(_tokens(title))

    def flush_paragraph() -> None:
        if current:
            paragraph = " ".join(current).strip()
            if paragraph:
                paragraphs.append(paragraph)
            current.clear()

    for raw_line in (text or "").splitlines():
        if not raw_line.strip():
            flush_paragraph()
            continue
        bullet_match = _BULLET_RE.match(raw_line)
        if bullet_match:
            flush_paragraph()
            point = _clean_inline(_BULLET_RE.sub("", raw_line))
            if point:
                bullets.append(point)
            continue
        line = _clean_inline(raw_line)
        if not line:
            continue
        if title_key and " ".join(_tokens(line)) == title_key:
            continue
        current.append(line)
    flush_paragraph()

    # If there were no explicit bullets, use the source's own sentences as key points.
    if not bullets:
        all_text = " ".join(paragraphs)
        bullets = [
            sentence.strip()
            for sentence in re.split(r"(?<=[.!?])\s+", all_text)
            if sentence.strip()
        ]

    bullets = list(dict.fromkeys(bullets))
    if paragraphs:
        summary = paragraphs[0]
    elif bullets:
        summary = "Die Ankündigung nennt die folgenden konkreten Punkte."
    else:
        summary = "In der gefundenen Nachricht ist kein auswertbarer Text enthalten."

    if len(summary) > 420:
        summary = summary[:417].rsplit(" ", 1)[0] + "…"

    shown_points: list[str] = []
    for point in bullets[:6]:
        if len(point) > 240:
            point = point[:237].rsplit(" ", 1)[0] + "…"
        shown_points.append(f"• {point}")
    if len(bullets) > 6:
        shown_points.append("• Weitere Einzelheiten stehen im Originaltext.")

    points_block = "\n".join(shown_points) if shown_points else "• Die Nachricht enthält keine weiteren lesbaren Einzelpunkte."
    practical = (
        "Orientiere dich an den ausdrücklich genannten Bedingungen, Beispielen und Ausnahmen. "
        "Diese Aufbereitung ergänzt oder verändert keine Serverregel."
    )
    return (
        f"**Einfach erklärt**\n{summary}\n\n"
        f"**Die wichtigsten Aussagen**\n{points_block}\n\n"
        f"**Was bedeutet das praktisch?**\n{practical}\n\n"
        "Die Original-Ankündigung ist für den genauen Wortlaut maßgeblich."
    )
