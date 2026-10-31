import asyncio
import os
from pathlib import Path
import discord
from pulse_command_errors import PulseCommandTree
from pulse_interactions import DEFAULT_ALLOWED_MENTIONS
import pulse_logging
from discord.ext import commands
from dotenv import load_dotenv
import uvicorn

BASE_DIR = Path(__file__).resolve().parent


def prepare_process() -> None:
    """Phase 9: process setup for `python main.py` – not on import (tests import main for CustomBot).
    Before, `import main` changed the working directory, created logs/ and loaded the checkout's .env
    (real tokens and data paths) into the importing process."""
    os.chdir(BASE_DIR)
    pulse_logging.safe_console()  # emoji output must not crash on a cp1252 console
    pulse_logging.setup(str(BASE_DIR))  # P-10: logs/pulse.log (1 MB x 3) + error ring buffer

    # .env muss vor dem Import von webserver/pulse_db geladen sein, da diese
    # Module ihre Konfiguration beim Import lesen.
    load_dotenv(BASE_DIR / ".env")

    # O-06: before webserver is imported – a malformed SESSION_DAYS/MAX_BACKUPS would stop the import there,
    # and this message names the cause. Only names and expected ranges are printed, never values.
    from pulse_env_validation import validate_environment
    for config_warning in validate_environment():
        print(f"⚠️ Konfigurationsprüfung: {config_warning}")
        pulse_logging.get("config").warning("Konfigurationsprüfung: %s", config_warning)


def message_cache_size() -> int:
    """discord.py caches recent messages to resolve references and certain gateway
    events. On a small hosting plan, 1,000 Message objects can needlessly consume
    memory. Keep a useful cache and make the limit configurable without disabling
    any Cog or intent. Read when the bot is created (after .env is loaded)."""
    try:
        return max(100, min(1000, int(os.getenv("DISCORD_MESSAGE_CACHE_SIZE", "250"))))
    except (TypeError, ValueError):
        return 250


class CustomBot(commands.Bot):
    # K-08: answers every failed slash command (discord.py routes them to CommandTree.on_error).
    TREE_CLASS = PulseCommandTree

    def __init__(self):
        MESSAGE_CACHE_SIZE = message_cache_size()
        intents = discord.Intents.default()
        intents.members = True  # Rollen & Teamler erkennen
        intents.presences = True
        intents.message_content = True

        super().__init__(
            command_prefix="!",
            intents=intents,
            max_messages=MESSAGE_CACHE_SIZE,
            tree_cls=self.TREE_CLASS,
            # BOT-01: no @everyone/@here and no role pings unless a message allows them explicitly.
            # Every wanted ping already passes its own allowed_mentions (recruiting, verification reminder,
            # ticket ping roles, office roles, RP @everyone, social promo, send_styled) – see tests/test_interactions.py.
            allowed_mentions=DEFAULT_ALLOWED_MENTIONS,
        )
        self.pulse_message_cache_limit = MESSAGE_CACHE_SIZE
        self._pulse_web_server = None
        self._pulse_web_server_task = None
        # Startup report data (shown by the Pulse Pro cog in the alert channel).
        self.pulse_loaded_cogs = []
        self.pulse_failed_cogs = []
        self.pulse_synced_commands = None

    async def setup_hook(self):
        from webserver import app  # after prepare_process() loaded .env (validate_environment() ran before)

        # WICHTIG: Bot-Instanz an die Webserver-App übergeben
        app.state.bot = self

        # Cogs aus dem Ordner /cogs laden
        if os.path.exists("./cogs"):
            for filename in os.listdir("./cogs"):
                if filename.endswith(".py") and not filename.startswith("__"):
                    try:
                        await self.load_extension(f"cogs.{filename[:-3]}")
                        self.pulse_loaded_cogs.append(filename[:-3])
                        print(f"📦 Cog geladen: {filename[:-3]}")
                    except Exception as e:
                        self.pulse_failed_cogs.append(f"{filename[:-3]}: {type(e).__name__}")
                        print(f"❌ Fehler beim Laden von {filename}: {e}")

        # Slash Commands synchronisieren
        # P-09: only when the command tree changed (or PULSE_FORCE_COMMAND_SYNC=1 / admin button).
        try:
            from pulse_command_groups import add_alias_groups
            from pulse_command_help import apply_visibility
            from pulse_command_sync import sync_commands
            missing = add_alias_groups(self.tree)  # BOT-04 (E-11): /team …, /setup … – old names stay as aliases
            if missing:
                print(f"⚠️ Befehlsgruppen: nicht gefunden (Cog nicht geladen?): {', '.join(missing)}")
            apply_visibility(self.tree)  # BOT-04: setup/maintenance commands hidden from ordinary members
            result = await sync_commands(self.tree)
            self.pulse_synced_commands = result["count"]
            if result["synced"]:
                print(f"🔄 {result['count']} Slash Commands synchronisiert ({result['reason']}).")
            else:
                print(f"🔄 Slash Commands unverändert ({result['count']}) – kein Sync nötig.")
        except Exception as e:
            print(f"❌ Fehler beim Sync: {e}")

        # Pulse Ultimate Automation Engine starten
        try:
            from pulse_ultimate import start_automation
            await start_automation(self)
            print("🤖 Pulse Ultimate Automation Engine gestartet.")
        except Exception as e:
            print(f"⚠️ Pulse Ultimate Automation Engine konnte nicht gestartet werden: {e}")

        # Webserver im Hintergrund starten
        self._start_web_server(app)

    def _start_web_server(self, app):
        """Start the dashboard once. Phase 9: a second call (setup_hook again) returns the running task
        instead of a second uvicorn on the same port."""
        task = self._pulse_web_server_task
        if task is not None and not task.done():
            return task
        port = int(os.getenv("SERVER_PORT", 25095))
        # O-02: behind a reverse proxy (Caddy) the real client IP comes from X-Forwarded-For – trusted only from
        # PULSE_FORWARDED_ALLOW_IPS (default 127.0.0.1), otherwise anyone could fake it to dodge rate limits.
        config = uvicorn.Config(
            app=app, host=os.getenv("PULSE_BIND_HOST", "0.0.0.0"), port=port, log_level="info",
            proxy_headers=True, forwarded_allow_ips=os.getenv("PULSE_FORWARDED_ALLOW_IPS", "127.0.0.1"),
        )
        server = uvicorn.Server(config)
        pulse_logging.quiet_healthcheck_access_log()  # Docker's 30-s probe flooded the live log
        self._pulse_web_server = server
        self._pulse_web_server_task = asyncio.create_task(server.serve(), name="pulse-webserver")
        print(f"🌐 Webserver gestartet auf Port {port}")
        return self._pulse_web_server_task

    async def close(self):
        """Stop the dashboard cleanly before closing Discord's event loop."""
        # Phase 9: the automation engine (and its watchdog thread) ran on after close – stop it first.
        try:
            from pulse_ultimate import stop_automation
            await stop_automation()
        except Exception as exc:
            print(f"⚠️ Automation Engine nicht sauber beendet: {type(exc).__name__}: {exc}")

        server = self._pulse_web_server
        task = self._pulse_web_server_task
        if server is not None:
            server.should_exit = True

        if task is not None and task is not asyncio.current_task():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=8.0)
            except asyncio.TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                print(f"⚠️ Webserver-Shutdown meldet einen Fehler: {type(exc).__name__}: {exc}")

        await super().close()

    async def on_ready(self):
        print(f"✅ Bot ist online als {self.user} (ID: {self.user.id})")
        try:
            import pulse_presence  # BOT-13: the one place that sets the status
            if await pulse_presence.update(self, force=True):
                print("🎮 Discord-Status gesetzt: " + str(pulse_presence._state["text"]))
        except Exception as exc:
            print(f"⚠️ Discord Presence konnte nicht gesetzt werden: {exc}")
        from pulse_ready import first_ready
        if not first_ready(self, "warn-role-reconcile"):  # P-08: not again after every reconnect
            return
        try:
            from webserver import load_config, reconcile_warning_roles
            guild = self.get_guild(int(os.getenv("DISCORD_GUILD_ID", str(__import__("pulse_ids").GUILD_ID))))
            report = await reconcile_warning_roles(guild, load_config())
            print(
                f"🛡️ Warnrollen abgeglichen: {report['checked']} geprüft, "
                f"{report['updated']} synchron, {report['failed']} Fehler."
            )
            if report["errors"]:
                for err in report["errors"]:
                    print(f"   ⚠️ {err}")
        except Exception as exc:
            print(f"⚠️ Warnrollen-Startup-Abgleich fehlgeschlagen: {exc}")


bot: CustomBot | None = None  # created by run(); importing main creates no client (Phase 9)


def run() -> None:
    global bot
    prepare_process()
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("❌ FEHLER: Kein DISCORD_TOKEN in der .env-Datei gefunden!")
        return
    from webserver import init_runtime
    init_runtime()  # A-04: databases, folders and session secret are created here, not on import
    bot = CustomBot()
    bot.run(token)


if __name__ == "__main__":
    run()
