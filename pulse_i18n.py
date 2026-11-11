"""Dependency-light locale support for Pulse Discord responses.

User preference is stored per Discord user. The auto preference follows the Discord
client locale when supported and otherwise safely falls back to German.
"""
from __future__ import annotations

LANGUAGES = {"de": "Deutsch", "en": "English"}
PREFERENCE_CHOICES = {
    "auto": "Automatisch (Discord)",
    "de": "Deutsch",
    "en": "English",
}
DEFAULT_LOCALE = "de"
SETTING_PREFIX = "pulse_locale"


def setting_key(user_id) -> str:
    return f"{SETTING_PREFIX}:{str(user_id)}"


def normalize_preference(value) -> str:
    """Normalize a stored or requested preference to auto, de or en."""
    raw = str(value or "auto").strip().lower().replace("_", "-")
    if raw in {"", "auto", "automatic", "system"}:
        return "auto"
    language = raw.split("-", 1)[0]
    if language in LANGUAGES:
        return language
    raise ValueError("Unterstützte Sprachen sind automatisch, Deutsch und English.")


def _locale_language(value) -> str | None:
    raw = getattr(value, "value", value)
    language = str(raw or "").strip().lower().replace("_", "-").split("-", 1)[0]
    return language if language in LANGUAGES else None


def resolve_locale(preference="auto", discord_locale=None) -> str:
    """Resolve a preference; unsupported Discord locales always fall back to German."""
    selected = normalize_preference(preference)
    if selected != "auto":
        return selected
    return _locale_language(discord_locale) or DEFAULT_LOCALE


def get_preference(user_id) -> str:
    """Load only this user's locale preference; broken/missing storage means auto."""
    if user_id is None:
        return "auto"
    try:
        import pulse_db
        return normalize_preference(pulse_db.get_setting(setting_key(user_id), "auto"))
    except Exception:
        return "auto"


def get_locale(user_id, discord_locale=None) -> str:
    return resolve_locale(get_preference(user_id), discord_locale)


def set_locale(user_id, preference) -> str:
    """Persist a validated preference for exactly one Discord user."""
    if user_id is None:
        raise ValueError("Für die Spracheinstellung wird eine Discord-Nutzer-ID benötigt.")
    selected = normalize_preference(preference)
    import pulse_db
    pulse_db.set_setting(setting_key(user_id), selected)
    return selected


MESSAGES = {
    "de": {
        "guild_only": "Nur auf dem Server verfügbar.",
        "no_access": "❌ Du hast keinen Zugriff auf Pulse.",
        "language_title": "🌐 Pulse-Sprache",
        "language_saved": "Deine Pulse-Sprache wurde auf **{language}** gesetzt.",
        "language_auto": "Deine Pulse-Sprache folgt jetzt der unterstützten Sprache deines Discord-Clients. Nicht unterstützte Sprachen verwenden Deutsch.",
        "language_error": "Deine Spracheinstellung konnte gerade nicht gespeichert werden.",
        "profile_title": "👤 Dein Pulse-Profil · {display_name}",
        "profile_description": "Deine persönlichen Team- und Arbeitsinformationen. Nur für dich sichtbar.",
        "profile_role": "Teamrolle",
        "role_none": "Keine Teamrolle",
        "profile_duty": "Dienststatus",
        "duty_online": "🟢 Im Dienst",
        "duty_break": "☕ In Pause",
        "duty_offline": "⚪ Nicht im Dienst",
        "profile_week": "Wochenzeit",
        "profile_tasks": "Offene Aufgaben",
        "profile_tickets": "Übernommene Tickets",
        "profile_training": "Bestandene Schulungen",
        "profile_dashboard": "Mein Pulse",
        "profile_open_dashboard": "Persönliche Übersicht öffnen",
        "profile_footer": "Pulse Pro · Persönliche Ansicht",
        "profile_error": "Dein Pulse-Profil konnte gerade nicht geladen werden.",
        "help_title": "📖 Pulse-Hilfe",
        "help_dashboard": "Alles Wichtige findest du auch im Dashboard: [Mein Pulse]({url})",
        "help_continued": " (Forts.)",
        "help_overflow": "\nNicht alle Befehle passen in diese Nachricht – Discord zeigt alle unter „/“.",
        "help_footer": "Es werden nur Befehle angezeigt, die zu deinen Rechten passen.",
        "section_duty": "⏱ Dienst & ich",
        "section_team": "🧭 Team",
        "section_moderation": "⚠ Moderation",
        "section_leadership": "🛡 Führung",
        "section_setup": "⚙ Einrichtung & Wartung",
    },
    "en": {
        "guild_only": "This command is only available in a server.",
        "no_access": "❌ You do not have access to Pulse.",
        "language_title": "🌐 Pulse language",
        "language_saved": "Your Pulse language is now **{language}**.",
        "language_auto": "Pulse will now follow your Discord client's supported language. Unsupported languages fall back to German.",
        "language_error": "Your language preference could not be saved right now.",
        "profile_title": "👤 Your Pulse profile · {display_name}",
        "profile_description": "Your personal team and work information. Only visible to you.",
        "profile_role": "Team role",
        "role_none": "No team role",
        "profile_duty": "Duty status",
        "duty_online": "🟢 On duty",
        "duty_break": "☕ On break",
        "duty_offline": "⚪ Off duty",
        "profile_week": "Weekly time",
        "profile_tasks": "Open tasks",
        "profile_tickets": "Assigned tickets",
        "profile_training": "Completed training",
        "profile_dashboard": "My Pulse",
        "profile_open_dashboard": "Open personal overview",
        "profile_footer": "Pulse Pro · Personal view",
        "profile_error": "Your Pulse profile could not be loaded right now.",
        "help_title": "📖 Pulse help",
        "help_dashboard": "You can also find everything important in the dashboard: [My Pulse]({url})",
        "help_continued": " (continued)",
        "help_overflow": "\nNot all commands fit in this message – Discord lists all commands under “/”.",
        "help_footer": "Only commands available to your permissions are shown.",
        "section_duty": "⏱ Duty & me",
        "section_team": "🧭 Team",
        "section_moderation": "⚠ Moderation",
        "section_leadership": "🛡 Leadership",
        "section_setup": "⚙ Setup & maintenance",
    },
}

_SECTION_KEYS = {
    "⏱ Dienst & ich": "section_duty",
    "🧭 Team": "section_team",
    "⚠ Moderation": "section_moderation",
    "🛡 Führung": "section_leadership",
    "⚙ Einrichtung & Wartung": "section_setup",
}


def translate(locale, key: str, **values) -> str:
    """Translate a known message and safely fall back to its German source string."""
    language = _locale_language(locale) or DEFAULT_LOCALE
    template = MESSAGES.get(language, MESSAGES[DEFAULT_LOCALE]).get(
        key, MESSAGES[DEFAULT_LOCALE].get(key, key)
    )
    try:
        return template.format(**values)
    except (KeyError, ValueError):
        return template


def translate_help_section(locale, title: str) -> str:
    key = _SECTION_KEYS.get(str(title or ""))
    return translate(locale, key) if key else str(title or "")
