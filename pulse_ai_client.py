"""Dependency-free helpers for Pulse's OpenAI-compatible AI calls.

Everything here is pure and side-effect free so it can be unit-tested without
network access or Discord: provider detection, request payloads, response
parsing, provider error classification and a small offline knowledge search
that keeps the support chat useful while no AI provider is reachable.
"""
from __future__ import annotations

import os
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

PROVIDERS = ("gemini", "openai")
REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high")
DEFAULT_MODELS = {"gemini": "gemini-3.1-flash-lite", "openai": "gpt-4.1-mini"}
# Tried in order when the configured model is unknown to the provider (HTTP 404
# or "model not found"). Preview IDs come first because Google often publishes
# new Flash-Lite models only with a "-preview" suffix.
DEFAULT_FALLBACK_MODELS = {
    "gemini": ("gemini-3.1-flash-lite-preview", "gemini-2.5-flash-lite", "gemini-2.5-flash"),
    "openai": (),
}


def resolve_provider(env: Mapping[str, str] | None = None) -> str:
    """Return the configured provider; without one, pick the provider whose key exists.

    Gemini is the default because it has a free tier (Pulse must work without
    paid AI credit). Previously the support chat defaulted to OpenAI while the
    moderation defaulted to Gemini, so a setup with only GEMINI_API_KEY left the
    chat permanently "not configured".
    """
    env = os.environ if env is None else env
    explicit = str(env.get("PULSE_SUPPORT_PROVIDER", "") or "").strip().lower()
    if explicit in PROVIDERS:
        return explicit
    if not str(env.get("GEMINI_API_KEY", "") or "").strip() and str(env.get("OPENAI_API_KEY", "") or "").strip():
        return "openai"
    return "gemini"


def model_chain(provider: str, primary: str, raw_fallbacks: str | None) -> list[str]:
    """Primary model followed by unique fallback models (env list or defaults)."""
    if raw_fallbacks is None or not raw_fallbacks.strip():
        fallbacks = list(DEFAULT_FALLBACK_MODELS.get(provider, ()))
    elif raw_fallbacks.strip().lower() in {"off", "none", "0", "false"}:
        fallbacks = []
    else:
        fallbacks = [item.strip() for item in raw_fallbacks.split(",")]
    chain: list[str] = []
    for model in [primary, *fallbacks]:
        if model and model not in chain:
            chain.append(model)
    return chain[:6]


def resolve_reasoning_effort(provider: str, raw: str | None) -> str | None:
    """Reasoning effort to send, or None to omit the parameter.

    Gemini 2.5/3 are thinking models: via the OpenAI-compatible endpoint their
    hidden thinking tokens count against ``max_tokens``. Without a limit the
    answer can come back empty with ``finish_reason=length``. "low" works for
    Gemini 2.5 and 3; non-reasoning OpenAI models reject the parameter, so it is
    only sent there when explicitly configured.
    """
    value = (raw or "").strip().lower()
    if value in {"off", "omit", "default"}:
        return None
    if value in REASONING_EFFORTS:
        return value
    return "low" if provider == "gemini" else None


def extract_completion(data: Any) -> tuple[str, str]:
    """Return (text, finish_reason) from a chat-completions response.

    Accepts plain string content as well as a list of content parts, which some
    OpenAI-compatible providers return.
    """
    if not isinstance(data, dict):
        return "", ""
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return "", ""
    choice = choices[0]
    finish_reason = str(choice.get("finish_reason") or "")
    message = choice.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        content = "".join(parts)
    return (content.strip() if isinstance(content, str) else ""), finish_reason


@dataclass(frozen=True)
class ProviderError:
    status_code: int
    error_type: str = ""
    code: str = ""
    message: str = ""
    retry_delay: int = 0

    @property
    def text(self) -> str:
        return f"{self.error_type} {self.code} {self.message}".lower()

    @property
    def model_not_found(self) -> bool:
        if self.status_code == 404:
            return True
        text = self.text
        return self.status_code == 400 and "model" in text and (
            "not found" in text or "does not exist" in text or "not supported" in text
            or "unknown" in text or "invalid model" in text
        )

    @property
    def reasoning_rejected(self) -> bool:
        text = self.text
        return self.status_code == 400 and ("reasoning" in text or "thinking" in text)

    @property
    def quota_exhausted(self) -> bool:
        """True for daily/billing quota (long pause), False for per-minute limits."""
        if self.status_code != 429:
            return False
        text = self.text
        return any(marker in text for marker in (
            "insufficient_quota", "per day", "perday", "daily", "billing",
            "check your plan", "credit",
        ))


def _parse_duration(value: Any) -> int:
    if isinstance(value, (int, float)):
        return max(0, int(value + 0.999))
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*s?\s*", str(value or ""))
    return max(0, int(float(match.group(1)) + 0.999)) if match else 0


def parse_provider_error(status_code: int, payload: Any) -> ProviderError:
    """Read the short error metadata (never the request) from an error body.

    Gemini's OpenAI-compatible endpoint sometimes wraps the error in a list and
    carries a ``RetryInfo.retryDelay`` such as ``"23s"`` in ``details``.
    """
    if isinstance(payload, list) and payload:
        payload = payload[0]
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return ProviderError(status_code=status_code)
    retry_delay = 0
    details = error.get("details")
    if isinstance(details, list):
        for detail in details:
            if isinstance(detail, dict) and "retryDelay" in detail:
                retry_delay = max(retry_delay, _parse_duration(detail.get("retryDelay")))
            if isinstance(detail, dict) and isinstance(detail.get("violations"), list):
                for violation in detail["violations"]:
                    if isinstance(violation, dict) and "PerDay" in str(violation.get("quotaId", "")):
                        error = {**error, "message": f"{error.get('message', '')} per day"}
    message = error.get("message")
    return ProviderError(
        status_code=status_code,
        error_type=str(error.get("type") or error.get("status") or "")[:80],
        code=str(error.get("code") or "")[:80],
        message=" ".join(message.split())[:240] if isinstance(message, str) else "",
        retry_delay=min(3600, retry_delay),
    )


# --- Offline knowledge search -------------------------------------------------

_STOPWORDS = frozenset(
    "der die das den dem des ein eine einer eines einem einen und oder aber ist sind war "
    "wie was wer wo wann warum wieso weshalb welche welcher welches welchen ich du er sie es "
    "wir ihr mir mich dir dich man kann darf soll muss bitte mal gibt geht zu im in am an auf "
    "aus bei mit nach von vor für fur über uber unter um nicht kein keine noch auch nur so "
    "dass da dann wenn hier dort hat habe haben wird werden ja nein doch sich mein dein "
    "kannst könnt konnt hallo hi hey pulse frage fragen hilfe danke".split()
)
_STOPWORDS = frozenset(word.replace("ü", "ue").replace("ö", "oe") for word in _STOPWORDS)
_WORD = re.compile(r"§\s*\d+|[a-z0-9]+")
_SUFFIXES = ("en", "er", "es", "e", "n", "s")
# Request verbs say what the user wants done, not what the question is about.
_STOP_STEMS = frozenset(
    "schrei erstel formul mach sag erklae brauch gib geb zeig nenn find such moecht "
    "will wuerd koennt hilf helf bitt fass entwer entwir gedich".split()
)


def _fold(text: str) -> str:
    text = (text or "").lower().replace("ß", "ss")
    text = text.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def _terms(text: str) -> list[str]:
    terms = []
    for raw in _WORD.findall(_fold(text)):
        if raw.startswith("§"):
            terms.append("§" + re.sub(r"\D", "", raw))
            continue
        if raw.isdigit():
            # Numbers identify templates ("Broadcast 08") and rule sections.
            terms.append(raw.lstrip("0") or "0")
            continue
        if len(raw) < 3 or raw in _STOPWORDS:
            continue
        # Light stemming so "Safezone"/"Safezones" or "Regel"/"Regeln" match.
        for suffix in _SUFFIXES:
            if len(raw) > 4 and raw.endswith(suffix):
                raw = raw[: -len(suffix)]
                break
        stem = raw[:6]
        if stem not in _STOP_STEMS:
            terms.append(stem)
    return terms


@dataclass(frozen=True)
class KnowledgeSection:
    title: str
    body: str
    source: str


def split_sections(markdown: str, source: str) -> list[KnowledgeSection]:
    """Split markdown into level-2 (or deeper) sections with their parent heading."""
    sections: list[KnowledgeSection] = []
    title = ""
    chapter = ""
    lines: list[str] = []

    def flush() -> None:
        body = "\n".join(lines).strip()
        if title and body:
            sections.append(KnowledgeSection(title=title, body=body, source=source))

    for line in (markdown or "").splitlines():
        if line.startswith("## "):
            flush()
            title = line[3:].strip()
            lines = []
        elif line.startswith("# "):
            flush()
            title = ""
            chapter = line[2:].strip()
            lines = []
        elif _NUMBERED_ENTRY.match(line) and (not title or title.startswith(chapter_prefix(chapter))):
            # Numbered entries such as "**08 | [RP] – ...**" become own sections.
            flush()
            entry = _NUMBERED_ENTRY.match(line)
            title = f"{chapter_prefix(chapter)}{entry.group(1)} | {entry.group(2).strip()}"
            lines = []
        elif title:
            lines.append(line)
        else:
            # FAQ bullets such as "- **Safezones:** ..." outside a "##" section.
            faq = _FAQ_BULLET.match(line)
            if faq:
                sections.append(KnowledgeSection(title=faq.group(1).strip(), body=faq.group(2).strip(), source=source))
    flush()
    return sections


_FAQ_BULLET = re.compile(r"^- \*\*(.{2,80}?):\*\*\s*(.+)$")
_NUMBERED_ENTRY = re.compile(r"^\*\*(\d{1,3}) \| (.{2,120}?)\*\*\s*$")


def chapter_prefix(chapter: str) -> str:
    """Prefix numbered entries with their chapter keyword (e.g. "Broadcast 08")."""
    return "Broadcast " if "broadcast" in chapter.lower() else "Nr. "


def search_sections(question: str, sections: list[KnowledgeSection], limit: int = 2) -> list[KnowledgeSection]:
    """Rank sections by term overlap; titles and § numbers weigh most."""
    query = set(_terms(question))
    if not query:
        return []
    paragraph_refs = {term for term in query if term.startswith("§")}
    scored: list[tuple[float, int, KnowledgeSection]] = []
    for index, section in enumerate(sections):
        title_terms = set(_terms(section.title))
        body_terms = _terms(section.body)
        body_set = set(body_terms)
        title_hits = query & title_terms
        matched = title_hits | (query & body_set)
        # Require a meaningful match: a title hit or at least half of the
        # question's terms, so small talk does not produce random excerpts.
        if not title_hits and len(matched) * 2 < len(query):
            continue
        score = 3.0 * len(title_hits) + len(matched)
        if paragraph_refs & title_terms:
            score += 6.0
        # Prefer focused sections over very long ones with incidental matches.
        score /= 1.0 + len(body_terms) / 400.0
        scored.append((score, -index, section))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    if not scored:
        return []
    best = scored[0][0]
    return [section for score, _, section in scored[:limit] if score >= best * 0.6]


def offline_answer(
    question: str,
    knowledge: str,
    templates: str | None = None,
    *,
    max_chars: int = 1500,
) -> str | None:
    """Build a clearly labelled excerpt answer from the local knowledge files."""
    sections = split_sections(knowledge, "Wissensbasis")
    if templates:
        sections += split_sections(templates, "Team-Vorlagen")
    hits = search_sections(question, sections, limit=2)
    if not hits:
        return None
    budget = max(200, max_chars)
    parts: list[str] = []
    for section in hits:
        remaining = budget - sum(len(part) + 2 for part in parts)
        if remaining < 120:
            break
        body = section.body
        header = f"**{section.title}** · _{section.source}_\n"
        if len(header) + len(body) > remaining:
            body = body[: max(0, remaining - len(header) - 1)].rsplit("\n", 1)[0].rstrip() + "\n…"
        parts.append(header + body)
    return "\n\n".join(parts) if parts else None
