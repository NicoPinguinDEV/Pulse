"""Explicit, least-privilege Discord mention policy shared by Pulse senders.

The bot-wide policy blocks role and @everyone/@here notifications. A message that
intentionally pings a configured role must explicitly pass that role to
role_ping_allowed(); user mentions keep discord.py's normal behavior unless that
message opts out itself.
"""
from __future__ import annotations

import discord


def default_allowed_mentions() -> discord.AllowedMentions:
    """Use safe client defaults without breaking direct user notifications."""
    return discord.AllowedMentions(
        everyone=False,
        users=True,
        roles=False,
        replied_user=False,
    )


def role_ping_allowed(role) -> discord.AllowedMentions:
    """Allow exactly one configured role ping, or no role pings if no role exists."""
    return discord.AllowedMentions(
        everyone=False,
        users=False,
        roles=[role] if role is not None else False,
        replied_user=False,
    )
