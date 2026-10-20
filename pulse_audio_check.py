"""K-10: are ffmpeg and libopus available for the voice-support announcement and waiting music?

Usable as a module (system status, /pulse-diagnose, voice-support start) and as a script inside the
built container:  python pulse_audio_check.py  → exit code 0 when both are available.
"""
from __future__ import annotations

import shutil


def audio_status() -> dict:
    ffmpeg = shutil.which("ffmpeg") or ""
    opus = False
    try:
        import discord
        if not discord.opus.is_loaded():
            try:
                discord.opus._load_default()  # same lookup discord.py uses when connecting to voice
            except Exception:
                pass
        opus = bool(discord.opus.is_loaded())
    except Exception:
        opus = False
    problems = []
    if not ffmpeg:
        problems.append("ffmpeg fehlt")
    if not opus:
        problems.append("libopus nicht geladen")
    return {"ffmpeg": ffmpeg, "opus": opus, "ok": not problems,
            "message": " · ".join(problems) + " – Ansage und Wartemusik im Voice-Support fallen aus" if problems else "ffmpeg und opus verfügbar"}


if __name__ == "__main__":
    status = audio_status()
    print(f"ffmpeg: {status['ffmpeg'] or 'FEHLT'} · opus: {'OK' if status['opus'] else 'FEHLT'}")
    raise SystemExit(0 if status["ok"] else 1)
