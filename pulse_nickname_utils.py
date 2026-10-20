"""Pure helpers for configurable Discord nickname prefixes."""
from __future__ import annotations

import re
import unicodedata
import pulse_ids as _pulse_ids  # A-09: fixed Discord IDs live in one place

MAX_DISCORD_NICKNAME_LENGTH = 32
MAX_ROLE_PREFIX_LENGTH = 24

# This role marks members who have left the team. It must override every active rank.
EX_TEAMLER_ROLE_ID = _pulse_ids.EX_TEAMLER_ROLE_ID
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


# Added by the absence system (cogs/abmeldung.py); the prefix sync must keep it,
# otherwise both systems rename each other every 15 minutes.
ABSENCE_SUFFIX = " | Abgemeldet"


def split_absence_suffix(nickname: object) -> tuple[str | None, str]:
    """('MA ✗ Richy', ' | Abgemeldet') for 'MA ✗ Richy | Abgemeldet'; suffix '' otherwise."""
    if nickname is None:
        return None, ""
    text = str(nickname)
    if text.endswith(ABSENCE_SUFFIX):
        return text[: -len(ABSENCE_SUFFIX)].rstrip() or None, ABSENCE_SUFFIX
    return text, ""


def format_prefixed_nickname(prefix: object, base_name: object, suffix: str = "") -> str:
    """Format 'Prefix ✗ Name[suffix]' within Discord's 32-character nickname limit."""
    clean_prefix = normalize_role_prefix(prefix)
    name = str(base_name or "").strip() or "Mitglied"
    available = MAX_DISCORD_NICKNAME_LENGTH - len(clean_prefix) - 1 - len(suffix)
    if available < 1:
        raise ValueError("Der Präfix lässt keinen Platz für den Namen.")
    return f"{clean_prefix} {name[:available].rstrip()}{suffix}"



def _role_label_key(value: object) -> str:
    """Normalize decorated Discord role labels so spelling/punctuation variants match."""
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(character for character in normalized if character.isalnum())


# Compact, recognizable nicknames for Bochum RP's team hierarchy.
# The Discord role names themselves are never renamed. Add aliases here when a
# role is spelled slightly differently in the Discord server.
_ROLE_PREFIX_ABBREVIATIONS = {
    _role_label_key(label): short
    for label, short in (
        ("Founder", "Founder"),
        ("FND", "Founder"),
        ("Inhaber", "Inhaber"),
        ("Inh.", "Inhaber"),
        ("Stv.Inhaber", "Stv.Inh"),
        ("Stv.Inh.", "Stv.Inh"),
        ("Stv.Inh", "Stv.Inh"),
        ("Rechte Hand", "Rechte Hand"),
        ("RH", "Rechte Hand"),
        ("Owner", "Owner"),
        ("Co.Owner", "Co.Owner"),
        ("Champ", "Champ"),
        ("DC Rechte", "DC"),
        ("Dev-Leitung", "L.Dev"),
        ("L.Dev", "L.Dev"),
        ("DevL", "L.Dev"),
        ("Developer", "Dev"),
        ("Leitende Projektleitung", "L.PL"),
        ("Projektleitung", "PL"),
        ("Stv.Projektleitung", "Stv.PL"),
        ("Leitende Projektkoordination", "L.PK"),
        ("Projektkoordination", "PK"),
        ("Stv.Projektkoordination", "Stv.PK"),
        ("Leitender Direktor", "L.DK"),
        ("L.DK", "L.DK"),
        ("Direktor", "DK"),
        ("DK", "DK"),
        ("Stv.Direktor", "Stv.DK"),
        ("Stv.DK", "Stv.DK"),
        ("Leitende Serverleitung", "L.SL"),
        ("Serverleitung", "SL"),
        ("Stv.Serverleitung", "Stv.SL"),
        ("Leitender Manager", "L.MA"),
        ("L.MA", "L.MA"),
        ("Management", "MA"),
        ("MA", "MA"),
        ("Stv.Management", "Stv.MA"),
        ("Stv.MA", "Stv.MA"),
        ("Leitende Teamleitung", "L.TL"),
        ("Teamleitung", "TL"),
        ("Stv.Teamleitung", "Stv.TL"),
        ("Leitende Teamaufsicht", "L.TA"),
        ("Teamaufsicht", "TA"),
        ("Stv.Teamaufsicht", "Stv.TA"),
        ("Stv Teamaufsicht", "Stv.TA"),
        ("Leitender-Supervisor", "L.Supervisor"),
        ("L.Supervisor", "L.Supervisor"),
        ("Leitender Supervisor", "L.Supervisor"),
        ("Supervisor", "Supervisor"),
        ("Stv.Supervisor", "Stv.Supervisor"),
        ("Stv Supervisor", "Stv.Supervisor"),
        ("Ausbilder-Leitung", "L.AB"),
        ("L.AB", "L.AB"),
        ("AusbL", "L.AB"),
        ("Ausbilder", "AB"),
        ("AB", "AB"),
        ("Ausb", "AB"),
        ("Highteamanwärter", "HTA"),
        ("Community-Management", "CM"),
        ("Stv.Community-Management", "Stv.CM"),
        ("Stv Community-Management", "Stv.CM"),
        ("Fraktionsverwaltung", "FV"),
        ("Stv.Fraktionsverwaltung", "Stv.FV"),
        ("Stv Fraktionsverwaltung", "Stv.FV"),
        ("Eventleitung", "EL"),
        ("Admin-Leitung", "L.Admin"),
        ("L.Admin", "L.Admin"),
        ("AdminL", "L.Admin"),
        ("Sr.Admin", "Sr.Admin"),
        ("Admin", "Admin"),
        ("Jr.Admin", "Jr.Admin"),
        ("Mod-Leitung", "L.Mod"),
        ("L.Mod", "L.Mod"),
        ("ModL", "L.Mod"),
        ("Sr.Moderator", "Sr.Mod"),
        ("Moderator", "Mod"),
        ("Jr.Moderator", "Jr.Mod"),
        ("Sup-Leitung", "L.Sup"),
        ("L.Sup", "L.Sup"),
        ("SupL", "L.Sup"),
        ("Sr.Supporter", "Sr.Sup"),
        ("Supporter", "Sup"),
        ("Test Supporter", "T.Sup"),
        ("Aushilfe", "Aushilfe"),
        ("Aush.", "Aushilfe"),
        ("Bauteamler Leitung", "L.Bau"),
        ("L.Bau", "L.Bau"),
        ("BauL", "L.Bau"),
        ("Stv Bauteamler Leitung", "Stv.L.Bau"),
        ("Stv.L.Bau", "Stv.L.Bau"),
        ("Stv.BauL", "Stv.L.Bau"),
        ("Stv. Bauteamler Leitung", "Stv.L.Bau"),
        ("Senior Bauteamler", "Sr.Bau"),
        ("Sr. Bauteamler", "Sr.Bau"),
        ("Bauteamler", "Bau"),
        ("Test Bauteamler", "T.Bau"),
        ("Teamler", "Team"),
        ("VIP", "VIP"),
        ("Bürger", "Bürger"),
    )
}


def _extract_role_label(role_name: object) -> str:
    """Strip hierarchy branding and decorative glyphs from a Discord role name."""
    original = unicodedata.normalize("NFKC", str(role_name or "")).strip()
    if not original:
        return ""

    # Discord role names often have a brand/category before the actual rank.
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

    return re.sub(r"\s+", " ", label).strip()


# Older values could have been saved manually by an earlier Pulse version.
# If a setting still contains the unshortened role name or one of these former
# generated labels, migrate it to the current canonical prefix. Truly custom
# values continue to take precedence.
_PREVIOUS_ROLE_PREFIX_LABELS = {
    _role_label_key(role): old
    for role, old in (
        ("Founder", "FND"),
        ("Rechte Hand", "RH"),
        ("Dev-Leitung", "DevL"),
        ("Ausbilder-Leitung", "AusbL"),
        ("Ausbilder", "Ausb"),
        ("Admin-Leitung", "AdminL"),
        ("Mod-Leitung", "ModL"),
        ("Sup-Leitung", "SupL"),
        ("Aushilfe", "Aush."),
        ("Bauteamler Leitung", "BauL"),
        ("Stv Bauteamler Leitung", "Stv.BauL"),
        ("Inhaber", "Inh."),
        ("Stv.Inhaber", "Stv.Inh."),
        ("Leitender Direktor", "L.Dir"),
        ("Direktor", "Dir"),
        ("Stv.Direktor", "Stv.Dir"),
        ("Leitender Manager", "L.Mgmt"),
        ("Management", "Mgmt"),
        ("Stv.Management", "Stv.Mgmt"),
        ("Leitender-Supervisor", "L.Sup"),
        ("Leitender Supervisor", "L.Sup"),
        ("Supervisor", "Sup"),
        ("Stv.Supervisor", "Stv.Sup"),
        ("Stv Supervisor", "Stv.Sup"),
    )
}


def automatic_role_prefix(role_name: object) -> str:
    """Build a compact role prefix, cleaning the server's decorative role names."""
    label = _extract_role_label(role_name)
    if not label:
        return "Bürger ✗"

    short_label = _ROLE_PREFIX_ABBREVIATIONS.get(_role_label_key(label))
    if short_label:
        label = short_label
    elif "bürger" in label.casefold():
        label = "Bürger"
    elif label.casefold() == "vip":
        label = "VIP"

    # Keep a little room for the member name within Discord's 32-character limit.
    if len(label) > 22:
        label = label[:21].rstrip() + "…"
    return f"{label} ✗"


# Explicit Pulse team hierarchy, highest to lowest. The values match the canonical
# prefixes returned by automatic_role_prefix(); Discord role positions break ties
# between alternate spellings of the same logical rank.
_TEAM_ROLE_PREFIX_ORDER = (
    "Founder", "Inhaber", "Stv.Inh", "Rechte Hand", "Owner", "Co.Owner",
    "L.Dev", "Dev",
    "L.PL", "PL", "Stv.PL",
    "L.PK", "PK", "Stv.PK",
    "L.DK", "DK", "Stv.DK",
    "L.SL", "SL", "Stv.SL",
    "L.MA", "MA", "Stv.MA",
    "L.TL", "TL", "Stv.TL",
    "L.TA", "TA", "Stv.TA",
    "L.Supervisor", "Supervisor", "Stv.Supervisor",
    "L.AB", "AB", "HTA",
    "CM", "Stv.CM", "FV", "Stv.FV", "EL",
    "L.Admin", "Sr.Admin", "Admin", "Jr.Admin",
    "L.Mod", "Sr.Mod", "Mod", "Jr.Mod",
    "L.Sup", "Sr.Sup", "Sup", "T.Sup", "Aushilfe",
    "L.Bau", "Stv.L.Bau", "Sr.Bau", "Bau", "T.Bau",
)
_TEAM_ROLE_PREFIX_PRIORITY = {
    _role_label_key(label): priority
    for priority, label in enumerate(_TEAM_ROLE_PREFIX_ORDER)
}


def _is_section_divider_role(role_name: object) -> bool:
    """True for decorative heading roles such as '━━━━ Administrator-Ebene ━━━━'."""
    normalized = unicodedata.normalize("NFKC", str(role_name or ""))
    return bool(re.search(r"[━─═]{2,}", normalized))


def select_highest_role_prefix(role_records, configured_prefixes: dict[str, str]):
    """Select the highest team rank from Pulse's explicit hierarchy.

    role_records contains (role_id, role_position, role_name, is_default_role).
    The former-team role is a special status and deliberately overrides every
    remaining role. Known team ranks follow the ordered Pulse hierarchy instead
    of Discord's raw role position; that keeps Citizen/VIP/utility roles from
    masking a member's actual team rank. For members without a recognized team
    rank, the highest remaining Discord role is used as a fallback.
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

    ranked_candidates = []
    fallback_candidates = []
    for raw_role_id, raw_position, role_name, is_default in records:
        if is_default:
            continue
        try:
            role_id = int(raw_role_id)
            position = int(raw_position)
        except (TypeError, ValueError):
            continue
        if _is_section_divider_role(role_name):
            continue

        automatic_prefix = automatic_role_prefix(role_name)
        automatic_core = re.sub(r"\s*✗\s*$", "", automatic_prefix).strip()
        rank_priority = _TEAM_ROLE_PREFIX_PRIORITY.get(_role_label_key(automatic_core))
        prefix = automatic_prefix
        configured = configured_prefixes.get(str(role_id))
        if configured:
            try:
                configured_prefix = normalize_role_prefix(configured)
                configured_core = re.sub(r"\s*✗\s*$", "", configured_prefix).strip()
                configured_key = _role_label_key(configured_core)
                role_label_key = _role_label_key(_extract_role_label(role_name))
                automatic_core_key = _role_label_key(automatic_core)
                previous_label = _PREVIOUS_ROLE_PREFIX_LABELS.get(role_label_key, "")
                previous_key = _role_label_key(previous_label)
                # Stored old names/abbreviations migrate to canonical labels;
                # genuinely custom prefix choices continue to take precedence.
                if configured_key not in {role_label_key, automatic_core_key, previous_key}:
                    prefix = configured_prefix
            except ValueError:
                prefix = automatic_prefix

        if rank_priority is not None:
            ranked_candidates.append((rank_priority, position, role_id, prefix))
        else:
            fallback_candidates.append((position, role_id, prefix))

    if ranked_candidates:
        _, _, role_id, prefix = min(
            ranked_candidates,
            key=lambda item: (item[0], -item[1], -item[2]),
        )
        return role_id, prefix
    if not fallback_candidates:
        return None
    _, role_id, prefix = max(fallback_candidates, key=lambda item: (item[0], item[1]))
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


_PULSE_PREFIX_LABELS = {
    _role_label_key(label)
    for label in (*_ROLE_PREFIX_ABBREVIATIONS.values(), *_TEAM_ROLE_PREFIX_ORDER, "Bürger", "VIP", "Team", "Unverifiziert")
}


def strip_pulse_prefixes(name: object, extra_prefixes=(), max_layers: int = 4) -> str | None:
    """Remove Pulse-generated rank prefixes ('L.Mod ✗ …'), including stacked ones.

    After a rank change the previous prefix is no longer derived from the member's
    current roles; without this it stayed in the name ('Jr.Admin ✗ L.Mod ✗ Dawo').
    Only labels Pulse itself generates (or configured prefixes) are removed, so
    ordinary names containing '✗' are left alone.
    """
    nickname = str(name or "").strip()
    extra = {str(p).strip() for p in extra_prefixes if str(p or "").strip()}
    for _ in range(max_layers):
        if nickname.startswith(EX_TEAMLER_PREFIX + " "):
            nickname = nickname[len(EX_TEAMLER_PREFIX) + 1:].strip()
            continue
        configured = next((p for p in sorted(extra, key=len, reverse=True) if nickname.startswith(p + " ")), None)
        if configured:
            nickname = nickname[len(configured) + 1:].strip()
            continue
        match = re.match(r"^(?P<label>[^✗]{1,24}?)\s*✗\s+(?P<rest>\S.*)$", nickname)
        if not match or _role_label_key(match.group("label")) not in _PULSE_PREFIX_LABELS:
            break
        nickname = match.group("rest").strip()
    return nickname or None


_DATE_ONLY_RE = re.compile(r"^\s*(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}\.\d{1,2}\.\d{2,4})\s*$")


def looks_like_date_only(name: object) -> bool:
    """True for values like '2026-10-19' or '19.10.2026' – never a real nickname.

    An older dashboard absence insert stored the end date as original_nick;
    such values must not be restored as a member's name.
    """
    return bool(_DATE_ONLY_RE.match(str(name or "")))


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
