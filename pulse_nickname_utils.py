"""Pure helpers for configurable Discord nickname prefixes."""
from __future__ import annotations

MAX_DISCORD_NICKNAME_LENGTH = 32
MAX_ROLE_PREFIX_LENGTH = 24


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
