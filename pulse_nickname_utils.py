"""Pure helpers for configurable Discord nickname prefixes."""
from __future__ import annotations

import re
import unicodedata

MAX_DISCORD_NICKNAME_LENGTH = 32
MAX_ROLE_PREFIX_LENGTH = 24

# This role marks members who have left the team. It must override every active rank.
EX_TEAMLER_ROLE_ID = 1489912887949394082
EX_TEAMLER_PREFIX = "𝐄𝐗 𝐓𝐄𝐀𝐌𝐋𝐄𝐑 ✗"


def normalize_role_prefix(value: object) -> str:
    """Validate a prefix while keeping the delimiter separate from the setting."""
    prefix = str(value or "").strip()
    if not prefix:
        raise ValueError("Bitte gib einen Namenspräfix ein.")
    if len(prefix) > MAX_ROLE_PREFIX_LENGTH:
        raise ValueError(f"Der Präfix darf höchstens {MAX_ROLE_PREFIX_LENGTH} Zeichen lang sein.")
    if any(ord(character) < 32 or ord(character) == 127 for character in prefix):
        raise ValueError("Der Präfix darf keine Steuer- oder Zeilenumbruchzeichen enthalten.")
    return prefix


def format_prefixed_nickname(prefix: object, base_name: object) -> str:
    """Format 'Prefix ✗ Name' within Discord's 32-character nickname limit."""
    clean_prefix = normalize_role_prefix(prefix)
    name = str(base_name or "").strip() or "Mitglied"
    available = MAX_DISCORD_NICKNAME_LENGTH - len(clean_prefix) - 1
    if available < 1:
        raise ValueError("Der Präfix lässt keinen Platz für den Namen.")
    return f"{clean_prefix} {name[:available]}"



def automatic_role_prefix(role_name: object) -> str:
    """Build 'Role ✗' from a Discord role name, cleaning common server decorations."""
    original = unicodedata.normalize("NFKC", str(role_name or "")).strip()
    if not original:
        return "Bürger ✗"

    # Discord role names here often have a brand/category before the actual rank.
    separators = ("メ", "║", "✘", "✗", "⌯⌲", "||")
    found = [(original.rfind(separator), separator) for separator in separators if separator in original]
    label = original
    if found:
        position, separator = max(found, key=lambda item: item[0])
        label = original[position + len(separator):].strip()

    # Remove leading emoji/decorative glyphs and trailing decorative marks.
    while label and unicodedata.category(label[0])[0] not in {"L", "N"}:
        label = label[1:].lstrip()
    while label and unicodedata.category(label[-1])[0] not in {"L", "N"} and label[-1] not in "+#":
        label = label[:-1].rstrip()

    label = re.sub(r"\s+", " ", label).strip()
    if not label:
        label = original
    if "bürger" in label.casefold():
        label = "Bürger"
    elif label.casefold() == "vip":
        label = "VIP"

    # A Discord nickname is limited to 32 characters including the member name.
    if len(label) > 22:
        label = label[:21].rstrip() + "…"
    return f"{label} ✗"


def select_highest_role_prefix(role_records, configured_prefixes: dict[str, str]):
    """Return the forced former-team prefix or the highest non-@everyone role.

    role_records contains (role_id, role_position, role_name, is_default_role).
    The former-team role is a special status and deliberately overrides any
    remaining VIP/citizen/old-rank roles after a team exit.
    """
    records = list(role_records)
    for raw_role_id, _, _, is_default in records:
        if is_default:
            continue
        try:
            role_id = int(raw_role_id)
        except (TypeError, ValueError):
            continue
        if role_id == EX_TEAMLER_ROLE_ID:
            return EX_TEAMLER_ROLE_ID, EX_TEAMLER_PREFIX

    candidates = []
    for raw_role_id, raw_position, role_name, is_default in records:
        if is_default:
            continue
        try:
            role_id = int(raw_role_id)
            position = int(raw_position)
        except (TypeError, ValueError):
            continue
        configured = configured_prefixes.get(str(role_id))
        try:
            prefix = normalize_role_prefix(configured) if configured else automatic_role_prefix(role_name)
        except ValueError:
            prefix = automatic_role_prefix(role_name)
        candidates.append((position, role_id, prefix))

    if not candidates:
        return None
    _, role_id, prefix = max(candidates, key=lambda item: (item[0], item[1]))
    return role_id, prefix


_LEGACY_RANK_HINTS = {
    "owner", "founder", "teamleitung", "teamaufsicht", "supervisor", "sr", "stv",
    "lta", "sup", "srsup", "tsup", "supporter", "moderator", "srmoderator",
    "admin", "jradmin", "dev", "direktor", "manager", "projekt", "koordination",
    "ausbilder", "fraktionsverwaltung", "eventleitung", "bürger", "vip", "pl",
    "pk", "lpl", "lpk", "lsl", "sl", "tl", "cm", "hta", "highteam", "leitender",
    "leitende", "leitend", "leitung", "serverleitung", "teamler", "rechtehand",
}


def strip_legacy_rank_prefix(name: object) -> str | None:
    """Remove a common pre-existing rank tag from a nickname before re-prefixing it."""
    nickname = str(name or "").strip()
    match = re.match(
        r"^\s*(?P<label>[^メ║✗✘|]{1,48}?)\s*(?:メ|║|✗|✘|\|{1,2})\s*(?P<rest>.+?)\s*$",
        nickname,
    )
    if not match:
        return nickname or None

    raw_label = match.group("label").strip()
    label = unicodedata.normalize("NFKC", raw_label).casefold()
    tokens = re.findall(r"[a-z0-9]+", label)
    compact = "".join(tokens)
    looks_like_rank = (
        compact in _LEGACY_RANK_HINTS
        or any(token in _LEGACY_RANK_HINTS for token in tokens)
        or any(compact.startswith(hint) for hint in ("teamleitung", "teamaufsicht", "serverleitung", "srsup", "srsupporter"))
        # Short uppercase tags such as DK, MA, or PK are used as legacy rank labels.
        or (raw_label.isupper() and 2 <= len(compact) <= 4 and compact.isalpha())
    )
    if looks_like_rank:
        rest = match.group("rest").strip()
        return rest or None
    return nickname or None


def strip_known_prefix(name: object, prefixes) -> str | None:
    """Remove one known configured prefix from the beginning of a nickname."""
    nickname = str(name or "")
    clean_prefixes = sorted(
        {str(prefix).strip() for prefix in prefixes if str(prefix).strip()},
        key=lambda item: (-len(item), item.casefold()),
    )
    for prefix in clean_prefixes:
        marker = f"{prefix} "
        if nickname.startswith(marker):
            return nickname[len(marker):].strip() or None
    return None
