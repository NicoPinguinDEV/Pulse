"""Audit #121: one confirmation card for every sensitive TeamOS action in Discord.

Three confirm views exist today with different safety properties (``ConfirmAction`` in cogs/mod_cases.py,
``Confirm`` in cogs/pulse_suite.py, ``MessageDeleteConfirmationView``). Only one of them re-checks the
person's rights at the moment of confirmation. :class:`ActionCard` is the single contract:

* only the person who started the action may confirm or cancel (others get a private notice, nothing is used up),
* single use – the flag is set *before* the action runs, so a double click or a second click never runs it twice,
* it expires after ``ttl`` seconds and then does nothing,
* the right (``pulse_policy`` action) is checked **again at confirmation**, with freshly computed permissions,
  so a role removed while the card was open stops the action,
* the action's errors become an error ID in the log and a generic message – never a traceback in Discord,
* every outcome (confirmed, cancelled, denied, expired, failed) is written as a ``pulse_events`` contract event.

The decision logic (:class:`ActionCard`) has no Discord dependency; :class:`ActionCardView` is the thin
button adapter. Existing views are not replaced yet.
"""
from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass

import discord

import pulse_events as events
import pulse_policy as policy
from pulse_interactions import PulseView

log = logging.getLogger("pulse.actions")

DEFAULT_TTL = 60.0
MAX_TITLE = 100


@dataclass(frozen=True)
class Outcome:
    state: str            # executed | cancelled | denied_owner | denied_rights | already_used | expired | failed
    message: str
    error_id: str = ""
    consumed: bool = False  # True when this call used the card up (the message is then replaced)


class ActionCard:
    def __init__(self, *, owner_id, action, title, run, perms_provider, domain="system", action_name="action",
                 target_type="", target_id="", case_ref=None, ttl=DEFAULT_TTL, clock=time.monotonic, emitter=None):
        if action not in policy.ACTIONS:
            raise ValueError(f"Unbekannte Policy-Aktion: {action!r}")
        self.owner_id = str(owner_id)
        self.action = action
        self.title = str(title or "")[:MAX_TITLE] or "Aktion"
        self._run = run
        self._perms = perms_provider
        self.domain, self.action_name = domain, action_name
        self.target_type, self.target_id, self.case_ref = target_type, str(target_id or ""), case_ref
        self._clock, self._deadline = clock, clock() + float(ttl)
        self._emit = emitter or events.emit_safe
        self.used = False

    # ------------------------------------------------------------------ helpers
    def _audit(self, outcome: str, actor_id, payload=None) -> None:
        self._emit(self.domain, f"{self.action_name}_{outcome}", target_type=self.target_type, target_id=self.target_id,
                   actor_id=str(actor_id), payload={"policy_action": self.action, **(payload or {})},
                   case_ref=self.case_ref)

    def expired(self) -> bool:
        return self._clock() >= self._deadline

    # ------------------------------------------------------------------ decisions
    async def confirm(self, user_id, interaction=None) -> Outcome:
        if str(user_id) != self.owner_id:
            return Outcome("denied_owner", "Nur wer die Aktion gestartet hat, kann sie bestätigen.")
        if self.used:
            return Outcome("already_used", "⏳ Die Aktion wurde schon ausgeführt oder abgebrochen.")
        if self.expired():
            self.used = True
            self._audit("expired", user_id)
            return Outcome("expired", "⌛ Bestätigung abgelaufen – es wurde nichts ausgeführt.", consumed=True)
        self.used = True  # before anything awaits: a second click can never start a second run
        try:
            allowed = policy.can(self._perms(), self.action)
        except Exception:  # noqa: BLE001 - unknown rights = no rights
            log.warning("Rechteprüfung der Aktionskarte fehlgeschlagen", exc_info=True)
            allowed = False
        if not allowed:
            self._audit("denied", user_id)
            return Outcome("denied_rights", "❌ Abgebrochen: Dir fehlt dafür inzwischen die Berechtigung.", consumed=True)
        try:
            await self._run(interaction)
        except Exception as exc:  # noqa: BLE001 - reported with an ID, details only in the log
            error_id = secrets.token_hex(3).upper()
            log.error("Aktionskarte fehlgeschlagen [Fehler-ID %s] %s: %s", error_id, self.action, type(exc).__name__,
                      exc_info=True)
            self._audit("failed", user_id, {"error_id": error_id})
            return Outcome("failed", f"❌ Dabei ist ein Fehler aufgetreten. Fehler-ID **{error_id}** – das Team findet sie im Log.",
                           error_id, consumed=True)
        self._audit("confirmed", user_id)
        return Outcome("executed", "✅ Erledigt.", consumed=True)

    async def cancel(self, user_id) -> Outcome:
        if str(user_id) != self.owner_id:
            return Outcome("denied_owner", "Nur wer die Aktion gestartet hat, kann sie abbrechen.")
        if self.used:
            return Outcome("already_used", "⏳ Die Aktion wurde schon ausgeführt oder abgebrochen.")
        self.used = True
        self._audit("cancelled", user_id)
        return Outcome("cancelled", "❌ Abgebrochen – nichts wurde ausgeführt.", consumed=True)


class ActionCardView(PulseView):
    """Buttons for an :class:`ActionCard`; all decisions are made by the card."""

    def __init__(self, card: ActionCard, *, timeout: float | None = None):
        super().__init__(timeout=timeout or card._deadline - card._clock() + 1)
        self.card = card

    async def _finish(self, interaction: discord.Interaction, outcome: Outcome) -> None:
        if not outcome.consumed:  # owner mismatch: answer privately, leave the card usable
            await interaction.response.send_message(outcome.message, ephemeral=True)
            return
        self.stop()
        text = outcome.message
        try:
            if interaction.response.is_done():
                await interaction.edit_original_response(content=text, embed=None, view=None)
            else:
                await interaction.response.edit_message(content=text, embed=None, view=None)
        except discord.HTTPException:
            pass

    @discord.ui.button(label="Bestätigen", style=discord.ButtonStyle.danger, emoji="✅")
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) == self.card.owner_id and not self.card.used and not self.card.expired():
            await interaction.response.edit_message(content="⏳ Wird ausgeführt …", embed=None, view=None)
        outcome = await self.card.confirm(interaction.user.id, interaction)
        await self._finish(interaction, outcome)

    @discord.ui.button(label="Abbrechen", style=discord.ButtonStyle.secondary, emoji="❌")
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._finish(interaction, await self.card.cancel(interaction.user.id))

    async def on_timeout(self) -> None:
        if not self.card.used and self.message is not None:
            try:
                await self.message.edit(content="⌛ Bestätigung abgelaufen – es wurde nichts ausgeführt.", view=None)
            except discord.HTTPException:
                pass
