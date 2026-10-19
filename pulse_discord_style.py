"""Send, edit and recognise Pulse messages in the configured style (modern container or classic embed).

Cogs keep building ordinary discord.Embed objects (business logic unchanged) and hand
them to these helpers:

* send_styled()   – new messages use the style chosen in Verwaltung → Discord-Auftritt.
* edit_styled()   – edits keep the *existing* message format (Discord cannot convert a
                    message between embed and Components V2), so old tickets/cases keep working.
* upsert_styled() – single status boards/panels: replaced exactly once after a style change.
* card_info() / same_content() – read title, footer and text from embeds *and* containers,
                    so duplicate detection and "nothing changed" checks keep working.
"""
from __future__ import annotations

import json

import discord
from discord import ui
from discord.utils import MISSING

from pulse_embed_design import branding, is_modern, layout_from_spec, truncate

# --------------------------------------------------------------------------- conversion


def embed_to_spec(embed: discord.Embed) -> dict:
    """Plain spec (as used by layout_from_spec) from an existing embed – nothing is dropped."""
    title = embed.title or ""
    if title and embed.url:
        title = f"[{title}]({embed.url})"
    description = embed.description or ""
    author = getattr(embed.author, "name", None)
    if author:
        description = f"-# {author}\n{description}" if description else f"-# {author}"
    color = embed.colour.value if embed.colour is not None else 0x5B5CF0
    return {
        "color": color,
        "title": title,
        "description": description,
        "fields": [(f.name or "​", f.value or "–", bool(f.inline)) for f in embed.fields],
        "thumbnail_url": getattr(embed.thumbnail, "url", None) or "",
        "image_url": getattr(embed.image, "url", None) or "",
        "footer": getattr(embed.footer, "text", None) or "",
        "timestamp": embed.timestamp,
        "author_name": "",
        "author_icon_url": "",
    }


def view_rows(view) -> list[list]:
    """Group the items of a classic ui.View into action rows (5 buttons or 1 select per row)."""
    if view is None:
        return []
    rows: dict[int, list] = {}
    auto_row = 100
    for item in list(view.children):
        is_select = not isinstance(item, ui.Button)
        row = item.row if item.row is not None else None
        if row is None:
            target = auto_row
            if is_select or len(rows.get(target, [])) >= 5 or any(not isinstance(i, ui.Button) for i in rows.get(target, [])):
                auto_row += 1
                target = auto_row
            rows.setdefault(target, []).append(item)
            if is_select:
                auto_row += 1
        else:
            rows.setdefault(row, []).append(item)
    return [items for _, items in sorted(rows.items()) if items][:5]


def layout_from_embed(embed: discord.Embed, *, content: str | None = "", view=None) -> ui.LayoutView:
    """Components V2 version of an embed message; buttons of `view` move into the container."""
    spec = embed_to_spec(embed)
    rows = view_rows(view)
    for items in rows:
        for item in items:
            # Items keep their original callbacks; detach them from the classic view first.
            item._view = None
            item._parent = None
    return layout_from_spec(spec, mention_line=truncate(content or "", 1000), action_rows=rows)


def text_layout(text: str) -> ui.LayoutView:
    layout = ui.LayoutView(timeout=None)
    layout.add_item(ui.Container(ui.TextDisplay(truncate(text, 3900))))
    return layout


def is_v2(message) -> bool:
    flags = getattr(message, "flags", None)
    return bool(getattr(flags, "components_v2", False))


# --------------------------------------------------------------------------- reading
def _component_texts(components) -> list[str]:
    texts = []
    for component in components or []:
        content = getattr(component, "content", None)
        if isinstance(content, str):
            texts.append(content)
        texts.extend(_component_texts(getattr(component, "children", None)))
        texts.extend(_component_texts(getattr(component, "components", None)))
    return texts


def _dict_texts(data) -> list[str]:
    texts = []
    if isinstance(data, dict):
        if data.get("type") == 10 and isinstance(data.get("content"), str):
            texts.append(data["content"])
        for value in data.values():
            if isinstance(value, (list, dict)):
                texts.extend(_dict_texts(value))
    elif isinstance(data, list):
        for value in data:
            texts.extend(_dict_texts(value))
    return texts


def _card_from_texts(texts: list[str]) -> dict:
    title = ""
    footer = ""
    for text in texts:
        for line in text.splitlines():
            if not title and line.startswith("### "):
                title = line[4:].strip()
    if texts and texts[-1].startswith("-# "):
        footer = texts[-1][3:].split(" · <t:", 1)[0].strip()
    return {"title": title, "footer": footer, "text": "\n".join(texts)}


def message_texts(message) -> list[str]:
    """All visible text of a Components V2 message (used for transcripts and recognition)."""
    return _component_texts(getattr(message, "components", None))


def card_info(message) -> dict | None:
    """{'title','footer','text'} for a classic embed message or a modern container message."""
    if message is None:
        return None
    if getattr(message, "embeds", None):
        embed = message.embeds[0]
        return {"title": embed.title or "", "footer": getattr(embed.footer, "text", None) or "",
                "text": json.dumps(embed.to_dict(), sort_keys=True, default=str)}
    if is_v2(message):
        return _card_from_texts(message_texts(message))
    return None


def same_content(message, embed: discord.Embed) -> bool:
    """True if `message` already shows exactly this embed in its own format (skip the edit)."""
    if message is None:
        return False
    if getattr(message, "embeds", None) and not is_v2(message):
        return message.embeds[0].to_dict() == embed.to_dict()
    if is_v2(message):
        wanted = _dict_texts(layout_from_embed(embed).to_components())
        return message_texts(message) == wanted
    return False


# --------------------------------------------------------------------------- sending
async def send_styled(destination, *, content: str | None = None, embed: discord.Embed | None = None,
                      view=None, files=None, file=None, allowed_mentions=None, modern: bool | None = None, **kwargs):
    """Send an embed message in the configured style. Without an embed, nothing changes."""
    use_modern = is_modern() if modern is None else modern
    extra = dict(kwargs)
    if files is not None:
        extra["files"] = files
    if file is not None:
        extra["file"] = file
    if allowed_mentions is not None:
        extra["allowed_mentions"] = allowed_mentions
    if embed is None or not use_modern:
        if content is not None:
            extra["content"] = content
        if embed is not None:
            extra["embed"] = embed
        if view is not None:
            extra["view"] = view
        return await destination.send(**extra)
    if allowed_mentions is None:
        # Mentions inside an embed never pinged; in a container they would. Only allow what the
        # former plain-text content pinged, never the embed text.
        extra["allowed_mentions"] = mentions_from_content(content)
    return await destination.send(view=layout_from_embed(embed, content=content, view=view), **extra)


def mentions_from_content(content: str | None) -> discord.AllowedMentions:
    import re
    text = str(content or "")
    users = [discord.Object(id=int(x)) for x in re.findall(r"<@!?(\d{15,22})>", text)]
    roles = [discord.Object(id=int(x)) for x in re.findall(r"<@&(\d{15,22})>", text)]
    return discord.AllowedMentions(everyone=False, users=users or False, roles=roles or False, replied_user=False)


async def edit_styled(message, *, embed=MISSING, content=MISSING, view=MISSING, allowed_mentions=None, **kwargs):
    """Edit keeping the message's own format (embed stays embed, container stays container).

    Arguments that are not passed stay unchanged on classic messages. A container is always
    rebuilt completely, so callers pass `view` whenever the message carries buttons.
    """
    extra = dict(kwargs)
    if allowed_mentions is not None:
        extra["allowed_mentions"] = allowed_mentions
    if is_v2(message):
        extra.setdefault("allowed_mentions", discord.AllowedMentions.none())
        text = content if isinstance(content, str) else ""
        rows_view = view if view not in (MISSING, None) else None
        if isinstance(embed, discord.Embed):
            layout = layout_from_embed(embed, content=text, view=rows_view)
        else:
            layout = text_layout(text or "–")
        return await message.edit(view=layout, **extra)
    return await message.edit(content=content, embed=embed, view=view, **extra)


async def upsert_styled(channel, message, *, embed: discord.Embed, view=MISSING, allowed_mentions=None, content=MISSING):
    """Edit the board/panel in place, or replace it exactly once when the style changed."""
    if message is not None and is_v2(message) != is_modern():
        try:
            await message.delete()
        except discord.NotFound:
            pass
        message = None
    if message is None:
        return await send_styled(channel, content=None if content is MISSING else content, embed=embed,
                                 view=None if view is MISSING else view, allowed_mentions=allowed_mentions)
    await edit_styled(message, embed=embed, content=content, view=view, allowed_mentions=allowed_mentions)
    return message


async def respond_edit_styled(interaction, *, text: str, view=None):
    """interaction.response.edit_message for a plain confirmation, matching the message format."""
    if is_v2(getattr(interaction, "message", None)):
        await interaction.response.edit_message(view=text_layout(text))
    else:
        await interaction.response.edit_message(content=text, view=view)


def current_style() -> str:
    return branding().get("style", "modern")
