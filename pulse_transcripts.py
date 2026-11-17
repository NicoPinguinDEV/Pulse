"""K-06: ticket transcripts that must exist before a ticket channel may be deleted.

Attachments are retained locally because Discord CDN links expire. Hard per-file, per-ticket,
message-count and transcript-size ceilings prevent a ticket author from exhausting storage/RAM.
"""
from __future__ import annotations

import os
import re

import discord

import pulse_paths
from pulse_discord_style import is_v2, message_texts

TRANSCRIPT_DIR = "transcripts"
MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024
MAX_TICKET_ATTACHMENTS = 50
MAX_TICKET_ATTACHMENT_BYTES = 32 * 1024 * 1024
MAX_TRANSCRIPT_MESSAGES = 10_000
MAX_TRANSCRIPT_TEXT_BYTES = 8 * 1024 * 1024


class TranscriptError(Exception):
    """The transcript is missing, incomplete or exceeds a safety limit – keep the ticket channel."""


def _safe(name: str, fallback: str = "datei") -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(name or ""))[:80].strip("._") or fallback


def _private_file(path: str) -> None:
    if os.name == "posix":
        os.chmod(path, 0o600)


def _private_dir(path: str) -> None:
    if os.name == "posix":
        os.chmod(path, 0o700)


async def build_transcript(channel, ticket_id: str, base_dir: str = "") -> tuple[str, int]:
    """Return transcript text and saved attachment count; propagate history and hard-limit failures."""
    rows, saved, saved_bytes, text_bytes, messages = [], 0, 0, 0, 0
    base_dir = base_dir or str(pulse_paths.DATA_DIR)
    files_dir = os.path.join(base_dir, TRANSCRIPT_DIR, f"{_safe(ticket_id, 'ticket')}_anhaenge")

    async for m in channel.history(limit=None, oldest_first=True):
        messages += 1
        if messages > MAX_TRANSCRIPT_MESSAGES:
            raise TranscriptError(f"Transkript überschreitet das Nachrichtenlimit ({MAX_TRANSCRIPT_MESSAGES})")

        stamp = m.created_at.strftime("%d.%m.%Y %H:%M:%S")
        text = m.content or "[kein Text]"
        notes = []
        for att in m.attachments:
            try:
                declared_size = int(getattr(att, "size", 0) or 0)
            except (TypeError, ValueError):
                declared_size = -1

            if declared_size < 0 or declared_size > MAX_ATTACHMENT_BYTES:
                notes.append(f"{att.url} (zu groß oder ungültige Größenangabe, nicht gesichert)")
                continue
            if saved >= MAX_TICKET_ATTACHMENTS:
                notes.append(f"{att.url} (Ticketlimit von {MAX_TICKET_ATTACHMENTS} Anhängen erreicht)")
                continue
            if saved_bytes + declared_size > MAX_TICKET_ATTACHMENT_BYTES:
                notes.append(f"{att.url} (Gesamtlimit von {MAX_TICKET_ATTACHMENT_BYTES // (1024 * 1024)} MB erreicht)")
                continue

            local = f"{m.id}_{_safe(att.filename)}"
            path = os.path.join(files_dir, local)
            try:
                os.makedirs(files_dir, mode=0o700, exist_ok=True)
                _private_dir(files_dir)
                await att.save(path)
                actual_size = os.path.getsize(path)
                if actual_size > MAX_ATTACHMENT_BYTES or saved_bytes + actual_size > MAX_TICKET_ATTACHMENT_BYTES:
                    os.remove(path)
                    notes.append(f"{att.url} (tatsächliche Dateigröße überschreitet das Sicherungslimit)")
                    continue
                _private_file(path)
                saved += 1
                saved_bytes += actual_size
                notes.append(f"{os.path.basename(files_dir)}/{local} (gesichert)")
            except (discord.HTTPException, OSError):
                try:
                    if os.path.exists(path):
                        os.remove(path)
                except OSError:
                    pass
                notes.append(f"{att.url} (Sicherung fehlgeschlagen)")

        if notes:
            text += " | Anhänge: " + ", ".join(notes)
        if m.embeds:
            text += " | Embeds: " + ", ".join((e.title or "Embed") for e in m.embeds)
        elif is_v2(m):
            text += " | Inhalt: " + " / ".join(t.replace("\n", " ") for t in message_texts(m))[:2000]

        row = f"[{stamp}] {m.author} ({m.author.id}): {text}"
        row_size = len(row.encode("utf-8")) + 1
        if text_bytes + row_size > MAX_TRANSCRIPT_TEXT_BYTES:
            raise TranscriptError(f"Transkript überschreitet das Textlimit ({MAX_TRANSCRIPT_TEXT_BYTES // (1024 * 1024)} MB)")
        rows.append(row)
        text_bytes += row_size

    return "\n".join(rows), saved


async def archive_transcript(channel, ticket_id: str, *, base_dir: str = "", log_channel=None) -> str:
    """Write a private transcript and send it to the ticket and log channel."""
    try:
        text, saved = await build_transcript(channel, ticket_id, base_dir)
    except Exception as exc:
        raise TranscriptError(f"Verlauf konnte nicht gelesen/gesichert werden ({type(exc).__name__}: {exc})") from exc
    name = f"{_safe(ticket_id, 'ticket')}.txt"
    directory = os.path.join(base_dir or str(pulse_paths.DATA_DIR), TRANSCRIPT_DIR)
    path = os.path.join(directory, name)
    try:
        os.makedirs(directory, mode=0o700, exist_ok=True)
        _private_dir(directory)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        _private_file(path)
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
                attachment.close()
    return name


async def keep_channel_after_failure(channel, ticket: dict, error: str) -> None:
    """Instead of deleting: rename, lock for the creator and explain how to retry."""
    ticket_id = str(ticket.get("id") or "ticket")
    reason = "Pulse: Transkript fehlgeschlagen, Kanal bleibt erhalten"
    new_name = f"geschlossen-{_safe(ticket_id, 'ticket')}"[:100].lower()
    if str(getattr(channel, "name", "")) != new_name:
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
