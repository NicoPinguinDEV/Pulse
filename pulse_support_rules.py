"""Dependency-free helpers for Pulse's AI support assistant."""
from __future__ import annotations

import re

_SUPPORT_CUE = re.compile(
    r"\b(?:wie|was|warum|wieso|weshalb|wann|wo|wer|welche|welcher|welches|"
    r"welchen|kannst du|kann ich|könnt ihr|könnte ich|habt ihr|gibt es|"
    r"brauche hilfe|hilfe bei|hilfe|bitte hilf|unterstützung|problem|probleme|"
    r"könntest du|soll ich|darf ich|funktioniert nicht|"
    r"geht nicht|bewerbung|support|erstelle|erstell|schreib|schreibe|formulier|"
    r"formuliere|erklär|erkläre|zusammenfassung|zusammenfass|übersetze|übersetz|"
    r"überarbeite|verbessere|analysiere|analyse|ideen|vorschlag|vorschläge|"
    r"checkliste|vorlage|plan|plane|nenn mir|gib mir|fass zusammen|"
    r"hilf mir|entwirf|entwerfe|beispiel|erläutere)\b",
)


def should_answer(
    content: str | None,
    *,
    bot_mentioned: bool = False,
    replying_to_bot: bool = False,
) -> bool:
    """Return whether a channel message looks like a question/support request."""
    if bot_mentioned or replying_to_bot:
        return True

    text = (content or "").strip()
    if not text:
        return False

    return "?" in text or bool(_SUPPORT_CUE.search(text))


def fit_response(content: str | None, max_chars: int = 1650) -> str:
    """Keep an AI response within a safe Discord message length."""
    text = (content or "").strip()
    if max_chars < 2:
        raise ValueError("max_chars muss mindestens 2 sein")
    if len(text) <= max_chars:
        return text

    clipped = text[: max_chars - 1].rstrip()
    if " " in clipped:
        clipped = clipped.rsplit(" ", 1)[0].rstrip()
    return (clipped or text[: max_chars - 1]).rstrip() + "…"
