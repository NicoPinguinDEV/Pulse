"""Local profanity detection for the AutoMod rule "profanity" (no AI, no network, no new dependencies).

Pure functions only – nothing here talks to Discord or the database, and message text is never stored.
The cog (cogs/automod.py), the dashboard (pulse_guard_admin.py) and the tests share this module.

How a message is checked
------------------------
1. URLs and custom emoji are removed, then the text is Unicode-normalised (NFKC), zero-width/format
   characters are dropped, case is folded (ß → ss), umlauts become digraphs (ö → oe), accents/combining marks
   are removed and common look-alike letters (Cyrillic/Greek) are mapped to Latin.
2. The text is split into word tokens. Markdown edges (``**``, ``||``, ``_``) are stripped.
3. Every token is compared as a *whole word*: leet characters (``1 3 4 5 7 0 @ $ !``) are translated and repeated
   letters are collapsed on both sides ("Fiiiiick" = "fick"). Substrings are never matched, so "class",
   "Spaß", "Assistent" or "Wirtschaft" stay harmless.
4. Evasions that spread a word over several tokens are rebuilt: "f u c k", "f.i.c.k", "H U R E N S O H N"
   (runs of single characters) and short fragments ("fu ck"). Masked letters ("f*ck", "f_ck", "h*rensohn")
   are matched against the list with the wildcard standing for exactly one letter.
5. Only a few unambiguous German stems may be part of a compound word (``stamm*`` / ``*ende`` entries below).

Word list entries: ``wort`` (whole word), ``stamm*`` (word start), ``*ende`` (word end), ``zwei woerter``
(phrase of consecutive words). Defaults are shipped here and are never written into config.json; admins add
their own words and may switch single default words off (``removed_terms``).

The own list is stored in config.json and is practically unlimited (MAX_CUSTOM_TERMS = 50,000 entries of at most
MAX_TERM_LENGTH = 100 characters; these are safety limits, exceeding them is reported when saving, nothing is cut
silently). The matcher is built once per word list (small LRU cache), whole-word lookups are hash based and
stems/wildcards are indexed, so message checks stay fast for big lists.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache

# --------------------------------------------------------------------------- policy (binding, see docs)
PROFANITY_TIMEOUT_AFTER = 3          # 3rd violation inside the window …
PROFANITY_TIMEOUT_MINUTES = 60       # … and every further one: exactly 60 minutes (no doubling)
CATEGORIES = ("de", "en", "discrimination", "gaming")
CATEGORY_LABELS = {
    "de": "Deutsche Schimpfwörter",
    "en": "Englische Schimpfwörter",
    "discrimination": "Diskriminierende Beleidigungen",
    "gaming": "Gaming- und Discord-Beleidigungen",
    "custom": "Eigene Wörter",
}
# Safety limits (not a feature limit): the own list is practically unlimited. 50,000 entries are far more than
# any server needs, keep config.json small (< ~1.5 MB) and the matcher below ~25 MB. Exceeding a limit is always
# reported to the admin - nothing is cut silently when saving.
MAX_CUSTOM_TERMS = 50_000
MAX_REMOVED_TERMS = 2_000
MAX_TERM_LENGTH = 100
MAX_INPUT_CHARS = 2_500_000  # accepted size of one submitted word list (characters)
_MAX_TEXT = 4000
_MAX_TOKENS = 400

# --------------------------------------------------------------------------- default word lists
DEFAULT_WORDS: dict[str, tuple[str, ...]] = {
    "de": (
        "arsch*", "*arschloch", "*arschgesicht", "arschkriecher", "arschgeige", "arschbacke",
        "scheiss*", "*scheisse", "kack*", "kackbratze",
        "drecksau", "drecksack", "dreckskerl", "dreckspack", "drecksschwein", "dreckschwein", "drecksgegend",
        "wichser", "*wichser", "wichserin", "wixer", "wixxer", "wichsa",
        "fick", "ficke", "fickst", "fickt", "ficken", "gefickt", "ficker", "fickerin",
        "fick dich", "fickt euch", "fick dich ins knie",
        "hurensohn*", "hurensöhn*", "*hurensohn", "huso", "husos", "hure", "huren", "hurentochter", "hurenkind",
        "bastard", "bastarde", "missgeburt", "missgeburten",
        "vollidiot", "*idiot", "idiot", "idioten", "dummkopf", "vollpfosten", "volltrottel",
        "spast", "spasten", "spasti", "spastis",
        "fotze", "*fotze", "fotzen", "schlampe", "schlampen", "nutte", "nutten",
        "schwanz", "schwanzlutscher", "pimmel", "möse", "titten",
        "hundesohn", "hundesöhne", "mistkerl", "arschficker",
        "verpiss dich", "halt die fresse", "halt die fresse du",
    ),
    "en": (
        "fuck*", "fck", "fuk", "fuq", "fcking", "*fucker", "motherfucker", "motherfuckers", "motherfucking",
        "shit", "*shit", "shits", "shitty", "shithead", "shitface", "shitfaced",
        "asshole", "assholes", "asswipe", "dumbass", "jackass", "dickhead", "dickheads",
        "bitch", "bitches", "bitchy", "bastard", "bastards", "cunt", "cunts", "pussy", "pussies",
        "slut", "sluts", "whore", "whores", "crap", "wanker", "twat", "prick", "cocksucker",
        "son of a bitch", "sonofabitch", "piece of shit",
    ),
    "discrimination": (
        # rassistisch
        "neger", "negerin", "negersau", "kanake", "kanaken", "kanacke", "zigeuner", "schlitzauge", "kümmeltürke",
        "itaker", "polacke", "polacken", "kameltreiber",
        "nigger", "niggers", "nigga", "niggas", "chink", "chinks", "gook", "gooks", "spic", "spics",
        "wetback", "kike", "kikes", "towelhead", "raghead", "paki", "beaner",
        # antisemitisch / NS-Bezug
        "judensau", "judenschwein", "drecksjude", "saujude", "judenpack", "heil hitler", "sieg heil", "ab ins gas",
        # homophob
        "schwuchtel", "schwuchteln", "schwule sau", "faggot", "faggots", "fagot",
        # transfeindlich
        "transe", "tranny", "trannies", "shemale",
        # sexistisch
        "kampflesbe", "lesbenschlampe",
        # menschenverachtend / ableistisch
        "untermensch", "untermenschen", "mongo", "mongos", "mongoloid", "behindi", "krüppel",
        "retard", "retards", "retarded",
    ),
    "gaming": (
        "kys", "kill yourself", "kill urself", "stfu", "gtfo",
        "bring dich um", "bring dich selbst um", "geh sterben", "geh doch sterben",
    ),
}

# --------------------------------------------------------------------------- normalisation
_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_EMOJI_RE = re.compile(r"<a?:\w{1,32}:\d{1,20}>")
_TOKEN_RE = re.compile(r"[a-z0-9@$!+*_|]+")
_WILD_SPLIT = re.compile(r"[*_|]+")
_REPEAT_RE = re.compile(r"(.)\1+")
_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue"})
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s", "!": "i", "+": "t"})
_HOMOGLYPHS = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i", "ѕ": "s", "ј": "j",
    "к": "k", "м": "m", "н": "h", "т": "t", "в": "b", "ο": "o", "ι": "i", "α": "a", "ε": "e", "ν": "v",
    "ı": "i", "ƒ": "f",
})


def fold(text: str) -> str:
    """Canonical lower-case ASCII-ish form used for all comparisons."""
    text = unicodedata.normalize("NFKC", str(text or ""))
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")  # zero-width joiners, soft hyphen, …
    text = text.casefold().translate(_UMLAUTS)
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.translate(_HOMOGLYPHS)


def _collapse(word: str) -> str:
    return _REPEAT_RE.sub(r"\1", word)


def _letters(token: str) -> list[str]:
    """Letter-only readings of a token after leet translation ("1" may be i or l)."""
    readings = [token.translate(_LEET)]
    if "1" in token:
        readings.append(token.replace("1", "l").translate(_LEET))
    return [r for r in dict.fromkeys(readings) if r.isalpha() and r.isascii()]


def _entry_words(raw: str) -> list[str]:
    """Words of a list entry in comparison form (fold → leet → collapse)."""
    words = re.findall(r"[a-z0-9@$!+]+", fold(raw))
    return [_collapse(w.translate(_LEET)) for w in words if w.translate(_LEET).isalpha()]


def _entry_key(raw: str) -> str:
    return " ".join(_entry_words(str(raw).replace("*", " ")))


# --------------------------------------------------------------------------- matcher
@dataclass(frozen=True)
class _Matcher:
    exact: dict = field(default_factory=dict)       # collapsed word → category
    prefix: dict = field(default_factory=dict)      # collapsed stem → (insertion order, category)
    suffix: dict = field(default_factory=dict)
    prefix_lengths: tuple = ()                       # distinct stem lengths (ascending) – lookups are O(lengths), not O(words)
    suffix_lengths: tuple = ()
    phrases: dict = field(default_factory=dict)     # tuple of collapsed words → category
    wild: dict = field(default_factory=dict)        # (length, first letter, last letter) → ((folded word, category), …)
    max_phrase: int = 1


@lru_cache(maxsize=4)  # a built matcher of a big custom list is several MB: keep very few
def _build(categories: tuple, custom: tuple, removed: tuple) -> _Matcher:
    removed_keys = {_entry_key(r) for r in removed}
    entries: list[tuple[str, str]] = []
    for category in categories:
        entries += [(entry, category) for entry in DEFAULT_WORDS.get(category, ())]
    entries += [(entry, "custom") for entry in custom]

    exact: dict = {}
    prefix: dict = {}
    suffix: dict = {}
    phrases: dict = {}
    wild: dict = {}
    for order, (entry, category) in enumerate(entries):
        entry = str(entry).strip()
        if not entry or _entry_key(entry) in removed_keys:
            continue
        starts, ends = entry.endswith("*"), entry.startswith("*")
        words = _entry_words(entry.strip("*"))
        if not words:
            continue
        if len(words) > 1:
            phrases[tuple(words)] = category
            continue
        elif starts and ends:
            continue
        else:
            if starts:
                prefix.setdefault(words[0], (order, category))
            elif ends:
                suffix.setdefault(words[0], (order, category))
            exact.setdefault(words[0], category)  # the stem alone is a word too ("fuck", "arsch")
            plain = "".join(re.findall(r"[a-z]+", fold(entry)))
            if plain:
                wild.setdefault((len(plain), plain[0], plain[-1]), []).append((plain, category))
    return _Matcher(
        exact=exact,
        prefix=prefix,
        suffix=suffix,
        prefix_lengths=tuple(sorted({len(k) for k in prefix})),
        suffix_lengths=tuple(sorted({len(k) for k in suffix})),
        phrases=phrases,
        wild={key: tuple(items) for key, items in wild.items()},
        max_phrase=max([len(k) for k in phrases] or [1]),
    )


def _word_hit(matcher: _Matcher, word: str, *, compounds: bool = True) -> str | None:
    collapsed = _collapse(word)
    if collapsed in matcher.exact:
        return matcher.exact[collapsed]
    if compounds:
        # Same answer as scanning the stems in list order (first listed stem wins), without touching every stem.
        best = None
        for size in matcher.prefix_lengths:
            if size > len(collapsed):
                break
            hit = matcher.prefix.get(collapsed[:size])
            if hit and (best is None or hit[0] < best[0]):
                best = hit
        for size in matcher.suffix_lengths:
            if size > len(collapsed):
                break
            hit = matcher.suffix.get(collapsed[len(collapsed) - size:])
            if hit and (best is None or hit[0] < best[0]):
                best = hit
        if best is not None:
            return best[1]
    return None


def _wildcard_hit(matcher: _Matcher, token: str) -> str | None:
    """"f*ck", "f_ck", "h*rensohn": every * or _ inside the token stands for exactly one letter."""
    inner = token[1:-1]
    wildcards = inner.count("*") + inner.count("_")
    length = len(token)
    if not wildcards or length < 4 or wildcards > 2 or length - wildcards < 2 or wildcards > length - wildcards:
        return None
    if token[0] in "*_" or token[-1] in "*_":
        return None
    template = []
    for ch in token:
        if ch in "*_":
            template.append(None)
            continue
        letter = ch.translate(_LEET)
        if not (letter.isalpha() and letter.isascii()):
            return None
        template.append(letter)
    for word, category in matcher.wild.get((length, template[0], template[-1]), ()):
        if all(t is None or t == w for t, w in zip(template, word)):
            return category
    return None


def _prepare(text: str) -> str:
    text = _EMOJI_RE.sub(" ", _URL_RE.sub(" ", str(text or "")[:_MAX_TEXT]))
    return fold(text)


def _scan(matcher: _Matcher, text: str) -> str | None:
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall(_prepare(text))[:_MAX_TOKENS]:
        token = raw.strip("*_|+").rstrip("!")
        if token:
            tokens.append(token)

    stream: list[str | None] = []  # letter-only readings, None = not a word (breaks fragment runs)
    for token in tokens:
        if len(token) > 3 and re.search(r"[a-z0-9@$!+][*_]+[a-z0-9@$!+]", token):
            hit = _wildcard_hit(matcher, token)
            if hit:
                return hit
        for part in _WILD_SPLIT.split(token):
            if not part:
                continue
            readings = _letters(part)
            for reading in readings:
                hit = _word_hit(matcher, reading)
                if hit:
                    return hit
            stream.append(readings[0] if readings else None)

    # Phrases of consecutive words ("fick dich", "son of a bitch").
    words = [_collapse(w) if w else None for w in stream]
    for size in range(2, matcher.max_phrase + 1):
        for start in range(0, len(words) - size + 1):
            window = words[start:start + size]
            if None not in window and tuple(window) in matcher.phrases:
                return matcher.phrases[tuple(window)]

    # Words spread over short fragments: "f u c k", "f.i.c.k", "fu ck".
    count = len(stream)
    for start in range(count):
        first = stream[start]
        if first is None or len(first) > 3:
            continue
        joined = first
        for end in range(start + 1, min(count, start + 14)):
            piece = stream[end]
            if piece is None or len(piece) > 3:
                break
            joined += piece
            pieces = end - start + 1
            singles = all(len(p) == 1 for p in stream[start:end + 1])
            if (singles and len(joined) >= 3) or (pieces <= 3 and len(joined) >= 4):
                hit = _word_hit(matcher, joined, compounds=False)
                if hit:
                    return hit
            if len(joined) > 24:
                break
    return None


# --------------------------------------------------------------------------- public API
@lru_cache(maxsize=4)
def _clean_terms(values: tuple, limit: int) -> tuple:
    """Trimmed, case-folded, de-duplicated (case-insensitive, order kept) terms; O(n) for any list size."""
    out: dict[str, None] = {}
    for value in values:
        text = str(value or "").strip().casefold()[:MAX_TERM_LENGTH]
        if text:
            out.setdefault(text)
    return tuple(out)[:limit]


def normalize_profanity(raw) -> dict:
    """Bounded configuration (stored in config.json → pulse_automod → profanity). Defaults: all categories on."""
    raw = raw if isinstance(raw, dict) else {}
    stored = raw.get("categories") if isinstance(raw.get("categories"), dict) else {}

    def terms(values, limit):
        return list(_clean_terms(tuple(values), limit)) if isinstance(values, (list, tuple)) else []

    return {
        "categories": {key: bool(stored.get(key, True)) for key in CATEGORIES},
        "custom_terms": terms(raw.get("custom_terms"), MAX_CUSTOM_TERMS),
        "removed_terms": terms(raw.get("removed_terms"), MAX_REMOVED_TERMS),
    }


def detect_category(text: str, config: dict | None = None) -> str | None:
    """Category key ("de", "en", "discrimination", "gaming", "custom") of the first banned word, else None."""
    raw = config if isinstance(config, dict) else {}
    stored = raw.get("categories") if isinstance(raw.get("categories"), dict) else {}
    categories = tuple(key for key in CATEGORIES if bool(stored.get(key, True)))
    custom = raw.get("custom_terms")
    removed = raw.get("removed_terms")
    matcher = _build(
        categories,
        _clean_terms(tuple(custom), MAX_CUSTOM_TERMS) if isinstance(custom, (list, tuple)) else (),
        _clean_terms(tuple(removed), MAX_REMOVED_TERMS) if isinstance(removed, (list, tuple)) else (),
    )
    return _scan(matcher, text)


def valid_term(term: str) -> bool:
    """Whether an admin-supplied list entry contains at least one usable word."""
    return bool(_entry_words(str(term or "").strip("*")))


def category_label(category: str) -> str:
    return CATEGORY_LABELS.get(category, "Schimpfwortfilter")


def default_word_counts() -> dict[str, int]:
    return {key: len(DEFAULT_WORDS[key]) for key in CATEGORIES}


def default_words(category: str) -> list[str]:
    return list(DEFAULT_WORDS.get(category, ()))


def profanity_step(strike_number: int) -> dict:
    """1st/2nd violation in the window: warning; from the 3rd on: timeout of exactly 60 minutes (never doubled)."""
    number = max(1, int(strike_number or 1))
    if number < PROFANITY_TIMEOUT_AFTER:
        return {"action": "warn", "level": number, "minutes": 0}
    return {"action": "timeout", "level": number, "minutes": PROFANITY_TIMEOUT_MINUTES}


def escalation_overview() -> list[str]:
    return ["1. Verstoß: Nachricht löschen + 1. Verwarnung", "2. Verstoß: Nachricht löschen + 2. Verwarnung",
            f"3. Verstoß: Nachricht löschen + 3. Verwarnung + {PROFANITY_TIMEOUT_MINUTES} Min. Timeout",
            f"4. Verstoß und weitere: Nachricht löschen + erneut {PROFANITY_TIMEOUT_MINUTES} Min. Timeout"]


def dm_text(strike_number: int, server_name: str, *, timeout_applied: bool = True) -> tuple[str, str]:
    """(title, description) of the private warning. Plain text only – no mentions."""
    server = re.sub(r"[@`*_~|<>\[\]]", "", str(server_name or "BOCHUM RP"))[:80] or "BOCHUM RP"
    number = max(1, int(strike_number or 1))
    if number == 1:
        return ("⚠️ Pulse AutoMod – Erste Verwarnung",
                f"Deine Nachricht auf {server} wurde automatisch entfernt, weil sie ein verbotenes Schimpfwort enthielt.\n\n"
                "Dies ist deine erste Verwarnung.\n\n"
                "Bitte achte künftig auf deine Wortwahl und halte dich an unser Serverregelwerk.\n"
                "Bei weiteren Verstößen folgen zusätzliche Sanktionen.")
    if number == 2:
        return ("⚠️ Pulse AutoMod – Zweite Verwarnung",
                "Deine Nachricht wurde erneut wegen eines verbotenen Schimpfwortes gelöscht.\n\n"
                "Dies ist deine zweite Verwarnung.\n\n"
                f"Bei deinem dritten Verstoß erhältst du automatisch einen Timeout von {PROFANITY_TIMEOUT_MINUTES} Minuten.")
    if timeout_applied:
        return (f"🔇 Pulse AutoMod – {PROFANITY_TIMEOUT_MINUTES} Minuten Timeout",
                "Du hast wiederholt verbotene Schimpfwörter verwendet.\n\n"
                f"Du erhältst deshalb einen automatischen Timeout von {PROFANITY_TIMEOUT_MINUTES} Minuten.\n\n"
                f"Bitte beachte künftig die Regeln von {server}.")
    return ("⚠️ Pulse AutoMod – Verwarnung",
            "Du hast wiederholt verbotene Schimpfwörter verwendet. Deine Nachricht wurde gelöscht.\n\n"
            f"Bitte beachte künftig die Regeln von {server}.")
