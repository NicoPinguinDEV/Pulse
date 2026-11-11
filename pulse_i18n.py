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
        "profile_week_value": "{duration} von {goal:g} h · {percent} %",
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
        "duty_team_only": "❌ Den Dienst können nur Teammitglieder starten und beenden.",
        "duty_action_unavailable": "Diese Aktion ist gerade nicht möglich.",
        "duty_start_already_running": "Du hast bereits eine laufende Schicht.",
        "duty_start_warning_lock": "Schicht-Start gesperrt: Du hast bereits 5 aktive Verwarnungen!",
        "duty_start_success": "Schicht gestartet – viel Erfolg!",
        "duty_start_leave_active": "Schicht gestartet. Deine Abmeldung bleibt bis {date} hinterlegt und wird nicht automatisch beendet.",
        "duty_break_success": "Pause gestartet.",
        "duty_resume_success": "Schicht fortgesetzt.",
        "duty_end_success": "Schicht beendet – {duration} gutgeschrieben.",
        "duty_status_offline": "Du bist nicht im Dienst. Diese Woche: {week}.",
        "duty_status_online": "Du bist im Dienst seit {duration}. Diese Woche: {week}.",
        "duty_status_break": "Du bist in der Pause seit {duration}. Diese Woche: {week}.",
        "duty_note_team_only": "❌ Schichtnotizen können nur Teammitglieder verwenden.",
        "duty_note_permission": "❌ Dir fehlt die Berechtigung zum Erstellen von Schichtnotizen.",
        "duty_note_required": "❌ Bitte gib eine Notiz ein.",
        "duty_note_too_long": "❌ Eine Schichtnotiz darf höchstens 1.000 Zeichen enthalten.",
        "duty_note_read_error": "❌ Der aktuelle Dienststatus konnte nicht gelesen werden ({error_type}).",
        "duty_note_inactive": "❌ Du bist aktuell nicht im Dienst. Starte zuerst mit /dienst start eine Schicht.",
        "duty_note_saved": "✅ Schichtnotiz wurde privat gespeichert und deiner laufenden Schicht zugeordnet.",
        "duty_notes_team_only": "❌ Schichtnotizen können nur Teammitglieder einsehen.",
        "duty_notes_lead_only": "❌ Schichtnotizen anderer Teammitglieder sind nur für die Leitung freigegeben.",
        "duty_notes_none": "📝 Für **{name}** liegen noch keine Schichtnotizen vor.",
        "duty_notes_title": "**Schichtnotizen für {name}**",
        "duty_notes_more_hidden": "… weitere Einträge ausgeblendet.",
        "duty_panel_admin_only": "❌ Nur Admins können das Dienst-Panel veröffentlichen.",
        "duty_panel_send_error": "❌ Pulse kann hier nicht senden ({error_type}). Kanalrechte prüfen.",
        "duty_panel_published": "✅ Dienst-Panel veröffentlicht.",
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
        "profile_week_value": "{duration} of {goal:g} h · {percent} %",
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
        "duty_team_only": "❌ Only team members can start or end a shift.",
        "duty_action_unavailable": "This action is not available right now.",
        "duty_start_already_running": "You already have an active shift.",
        "duty_start_warning_lock": "Shift start blocked: you have 5 active warnings.",
        "duty_start_success": "Shift started – good luck!",
        "duty_start_leave_active": "Shift started. Your leave remains recorded until {date} and will not be ended automatically.",
        "duty_break_success": "Break started.",
        "duty_resume_success": "Shift resumed.",
        "duty_end_success": "Shift ended – {duration} credited.",
        "duty_status_offline": "You are off duty. This week: {week}.",
        "duty_status_online": "You have been on duty for {duration}. This week: {week}.",
        "duty_status_break": "You have been on break for {duration}. This week: {week}.",
        "duty_note_team_only": "❌ Only team members can use shift notes.",
        "duty_note_permission": "❌ You do not have permission to create shift notes.",
        "duty_note_required": "❌ Please enter a note.",
        "duty_note_too_long": "❌ A shift note may contain no more than 1,000 characters.",
        "duty_note_read_error": "❌ The current shift status could not be read ({error_type}).",
        "duty_note_inactive": "❌ You are currently off duty. Start a shift with /dienst start first.",
        "duty_note_saved": "✅ Shift note saved privately and linked to your active shift.",
        "duty_notes_team_only": "❌ Only team members can view shift notes.",
        "duty_notes_lead_only": "❌ Shift notes for other team members are only available to leadership.",
        "duty_notes_none": "📝 There are no shift notes for **{name}** yet.",
        "duty_notes_title": "**Shift notes for {name}**",
        "duty_notes_more_hidden": "… additional entries hidden.",
        "duty_panel_admin_only": "❌ Only admins can publish the duty panel.",
        "duty_panel_send_error": "❌ Pulse cannot send messages here ({error_type}). Check channel permissions.",
        "duty_panel_published": "✅ Duty panel published.",
    },
}

_SECTION_KEYS = {

_DUTY_FIXED_MESSAGES = {
    "Du hast bereits eine laufende Schicht.": "duty_start_already_running",
    "Schicht-Start gesperrt: Du hast bereits 5 aktive Verwarnungen!": "duty_start_warning_lock",
    "Schicht gestartet – viel Erfolg!": "duty_start_success",
    "Pause gestartet.": "duty_break_success",
    "Schicht fortgesetzt.": "duty_resume_success",
    "Diese Aktion ist gerade nicht möglich.": "duty_action_unavailable",
}


def translate_duty_message(locale, message: str) -> str:
    """Translate the shared /dienst result returned by webserver without changing shift logic."""
    text = str(message or "")
    if _locale_language(locale) != "en":
        return text
    key = _DUTY_FIXED_MESSAGES.get(text)
    if key:
        return translate(locale, key)
    leave_prefix = "Schicht gestartet. Deine Abmeldung bleibt bis "
    leave_suffix = " hinterlegt und wird nicht automatisch beendet."
    if text.startswith(leave_prefix) and text.endswith(leave_suffix):
        date = text[len(leave_prefix):-len(leave_suffix)]
        return translate(locale, "duty_start_leave_active", date=date)
    end_prefix = "Schicht beendet – "
    end_marker = " gutgeschrieben."
    if text.startswith(end_prefix) and end_marker in text:
        duration, tail = text[len(end_prefix):].split(end_marker, 1)
        # Keep any optional handover details intact; only translate the stable result sentence.
        return translate(locale, "duty_end_success", duration=duration) + tail
    status_offline = "Du bist nicht im Dienst. Diese Woche: "
    if text.startswith(status_offline) and text.endswith("."):
        week = text[len(status_offline):-1]
        return translate(locale, "duty_status_offline", week=week)
    status_online = "Du bist im Dienst seit "
    if text.startswith(status_online) and ". Diese Woche: " in text and text.endswith("."):
        duration, week = text[len(status_online):].rsplit(". Diese Woche: ", 1)
        return translate(locale, "duty_status_online", duration=duration, week=week[:-1])
    status_break = "Du bist in der Pause seit "
    if text.startswith(status_break) and ". Diese Woche: " in text and text.endswith("."):
        duration, week = text[len(status_break):].rsplit(". Diese Woche: ", 1)
        return translate(locale, "duty_status_break", duration=duration, week=week[:-1])
    return text


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
