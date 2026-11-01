"""K-06: ticket transcripts that must exist before a ticket channel may be deleted.

Used by the Discord close flow (cogs/tickets.py) and the dashboard close (webserver.py).
Attachments up to MAX_ATTACHMENT_BYTES are stored next to the transcript (E-7: 8 MB, kept
without time limit) because Discord's signed CDN links expire.
"""
from __future__ import annotations

import os
import re

import discord

import pulse_paths
from pulse_discord_style import is_v2, message_texts

TRANSCRIPT_DIR = "transcripts"
MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024


class TranscriptError(Exception):
    """The transcript is missing or incomplete – the channel must not be deleted."""


def _safe(name: str, fallback: str = "datei") -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(name or ""))[:80].strip("._") or fallback


async def build_transcript(channel, ticket_id: str, base_dir: str = "") -> tuple[str, int]:
    """(transcript text, number of attachments saved). History errors propagate to the caller."""
    rows, saved = [], 0
    base_dir = base_dir or str(pulse_paths.DATA_DIR)  # Phase 13: data directory
    files_dir = os.path.join(base_dir, TRANSCRIPT_DIR, f"{_safe(ticket_id, 'ticket')}_anhaenge")
    async for m in channel.history(limit=None, oldest_first=True):
        stamp = m.created_at.strftime("%d.%m.%Y %H:%M:%S")
        text = m.content or "[kein Text]"
        notes = []
        for att in m.attachments:
            if (getattr(att, "size", 0) or 0) > MAX_ATTACHMENT_BYTES:
                notes.append(f"{att.url} (zu groß, nicht gesichert)")
                continue
            local = f"{m.id}_{_safe(att.filename)}"
            try:
                os.makedirs(files_dir, exist_ok=True)
                await att.save(os.path.join(files_dir, local))
                saved += 1
                notes.append(f"{os.path.basename(files_dir)}/{local} (gesichert)")
            except (discord.HTTPException, OSError):
                notes.append(f"{att.url} (Sicherung fehlgeschlagen)")
        if notes:
            text += " | Anhänge: " + ", ".join(notes)
        if m.embeds:
            text += " | Embeds: " + ", ".join((e.title or "Embed") for e in m.embeds)
        elif is_v2(m):
            text += " | Inhalt: " + " / ".join(t.replace("\n", " ") for t in message_texts(m))[:2000]
        rows.append(f"[{stamp}] {m.author} ({m.author.id}): {text}")
    return "\n".join(rows), saved


async def archive_transcript(channel, ticket_id: str, *, base_dir: str = "", log_channel=None) -> str:
    """Write transcripts/<id>.txt and post it in the ticket and log channel. Returns the file name.

    Raises TranscriptError when the history could not be read or the file could not be written.
    """
    try:
        text, saved = await build_transcript(channel, ticket_id, base_dir)
    except Exception as exc:
        raise TranscriptError(f"Verlauf konnte nicht gelesen werden ({type(exc).__name__})") from exc
    name = f"{_safe(ticket_id, 'ticket')}.txt"
    path = os.path.join(base_dir or str(pulse_paths.DATA_DIR), TRANSCRIPT_DIR, name)
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
    except OSError as exc:
        raise TranscriptError(f"Transkript konnte nicht gespeichert werden ({type(exc).__name__})") from exc
    note = f"📄 Transkript zu Ticket `{ticket_id}`" + (f" · {saved} Anhang/Anhänge lokal gesichert" if saved else "")
    for destination in (channel, log_channel):
        if destination is None:
            continue
        attachment = None
        try:
            attachment = discord.File(path, filename=name)
            await destination.send(content=note, file=attachment, allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException, OSError):
            pass
        finally:
            if attachment is not None:
                attachment.close()  # never keep the transcript file handle open (Windows locks it)
    return name


async def keep_channel_after_failure(channel, ticket: dict, error: str) -> None:
    """Instead of deleting: rename, lock for the creator and explain how to retry."""
    ticket_id = str(ticket.get("id") or "ticket")
    reason = "Pulse: Transkript fehlgeschlagen, Kanal bleibt erhalten"
    new_name = f"geschlossen-{_safe(ticket_id, 'ticket')}"[:100].lower()
    if str(getattr(channel, "name", "")) != new_name:  # Discord allows only 2 renames per 10 minutes
        try:
            await channel.edit(name=new_name, reason=reason)
        except (discord.Forbidden, discord.HTTPException):
            pass
    try:
        creator = channel.guild.get_member(int(ticket.get("user_id") or 0))
        if creator is not None:
            await channel.set_permissions(creator, send_messages=False, view_channel=True, reason=reason)
    except (discord.Forbidden, discord.HTTPException, ValueError):
        pass
    try:
        await channel.send(
            f"⚠️ **Archivierung fehlgeschlagen:** {error}.\n"
            "Der Kanal wurde **nicht gelöscht**, damit kein Verlauf verloren geht. Das Ticket bleibt offen; "
            "zum erneuten Archivieren einfach noch einmal „Ticket schließen“ verwenden.",
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except (discord.Forbidden, discord.HTTPException):
        pass
