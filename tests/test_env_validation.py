"""O-06: optional .env settings are checked without ever echoing their values; every variable is documented."""
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pulse_env_validation as ev
import pulse_system_checks as sc

ROOT = Path(__file__).resolve().parents[1]
SECRETISH = "geheimer-wert-123"


class ValidationTests(unittest.TestCase):
    def test_valid_and_empty_settings_are_quiet(self):
        env = {"SERVER_PORT": "25095", "MAX_SHIFT_HOURS": "12", "SESSION_DAYS": "7", "AUTO_BACKUP_HOURS": "0",
               "PULSE_AI_MODERATION_CONFIDENCE": "0.72", "PUBLIC_BASE_URL": "https://pulse.example",
               "PULSE_SUPPORT_TEAM_ROLE_IDS": "12345, 67890", "PULSE_RSSHUB_FALLBACK_URLS": "https://a.example,https://b.example",
               "PULSE_SUPPORT_PROVIDER": "Gemini", "SYNC_WARN_ROLES": "1", "PULSE_SUPPORT_ENABLED": "true",
               "PULSE_REPORT_CHANNEL_ID": "", "OPENAI_BASE_URL": ""}
        self.assertEqual(ev.validate_environment(env), [])
        self.assertEqual(ev.validate_environment({}), [])

    def test_bad_values_are_named_but_never_echoed(self):
        env = {"SERVER_PORT": SECRETISH, "SESSION_DAYS": "sieben", "MAX_SHIFT_HOURS": "999", "AUTO_BACKUP_HOURS": "nan",
               "PUBLIC_BASE_URL": SECRETISH, "DISCORD_GUILD_ID": SECRETISH, "PULSE_SUPPORT_TEAM_ROLE_IDS": f"1,{SECRETISH}",
               "PULSE_RSSHUB_FALLBACK_URLS": f"https://ok.example,{SECRETISH}", "PULSE_SUPPORT_PROVIDER": SECRETISH,
               "PULSE_SUPPORT_ENABLED": SECRETISH}
        warnings = ev.validate_environment(env)
        rendered = "\n".join(warnings)
        for name in env:
            self.assertIn(name, rendered)
        self.assertNotIn(SECRETISH, rendered)
        self.assertNotIn("sieben", rendered)
        self.assertNotIn("999", rendered)

    def test_ranges_follow_the_code(self):
        self.assertTrue(ev.validate_environment({"MAX_SHIFT_HOURS": "0.4"}))  # parse_max_shift_hours falls back below 0.5
        self.assertEqual(ev.validate_environment({"MAX_SHIFT_HOURS": "0.5"}), [])
        self.assertTrue(ev.validate_environment({"PULSE_AI_MODERATION_CONFIDENCE": "0.5"}))  # clamped to 0.65 in the cog
        self.assertTrue(ev.validate_environment({"DISCORD_MESSAGE_CACHE_SIZE": "5000"}))      # clamped to 1000 in main.py

    def test_switches_read_as_equals_one_must_be_0_or_1(self):
        warnings = ev.validate_environment({"SYNC_WARN_ROLES": "true"})  # would mean OFF in webserver.py
        self.assertEqual(len(warnings), 1)
        self.assertIn("SYNC_WARN_ROLES", warnings[0])

    def test_example_domain_is_flagged(self):
        self.assertTrue(any("Beispiel-Domain" in w for w in ev.validate_environment({"PUBLIC_BASE_URL": "https://your-domain.example"})))

    def test_system_page_shows_the_check(self):
        names = {name: (ok, detail) for name, ok, detail in sc.run(None, None, {}, environ={"SESSION_DAYS": SECRETISH})}
        ok, detail = names["Konfigurationswerte"]
        self.assertFalse(ok)
        self.assertIn("SESSION_DAYS", detail)
        self.assertNotIn(SECRETISH, detail)
        self.assertTrue(sc.run(None, None, {}, environ={})[4][1])


class DocumentationTests(unittest.TestCase):
    def test_every_validated_and_read_variable_is_in_env_example(self):
        example = set(re.findall(r"(?m)^#?\s*([A-Z][A-Z0-9_]+)=", (ROOT / ".env.example").read_text(encoding="utf-8")))
        validated = (set(ev.INTEGER_RANGES) | set(ev.FLOAT_RANGES) | set(ev.ID_VARS) | set(ev.ID_LIST_VARS) | set(ev.URL_VARS)
                     | set(ev.URL_LIST_VARS) | set(ev.CHOICES) | set(ev.BOOLEAN_VARS) | set(ev.STRICT_SWITCH_VARS))
        read = set()
        pattern = re.compile(r"""(?:getenv|environ\.get|_env\w*|env_\w+)\(\s*["']([A-Z][A-Z0-9_]+)["']""")
        for path in list(ROOT.glob("*.py")) + list((ROOT / "cogs").glob("*.py")):
            read |= set(pattern.findall(path.read_text(encoding="utf-8", errors="replace")))
        self.assertEqual(sorted((validated | read) - example), [])

    def test_max_shift_hours_comment_matches_e2(self):
        text = (ROOT / ".env.example").read_text(encoding="utf-8")
        before = text.split("MAX_SHIFT_HOURS=")[0].rsplit("\n\n", 1)[-1]
        self.assertIn("0 h", before)
        self.assertIn("24", before)


class StartupTests(unittest.TestCase):
    def test_main_validates_before_importing_webserver(self):
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertLess(main.index("validate_environment()"), main.index("from webserver import app"))
        self.assertGreater(main.index("validate_environment()"), main.index("load_dotenv("))

    def test_validator_module_imports_without_side_effects(self):
        code = "import sys; sys.path.insert(0, '.'); import pulse_env_validation; sys.exit(0 if pulse_env_validation.validate_environment({}) == [] else 1)"
        self.assertEqual(subprocess.run([sys.executable, "-I", "-c", code], cwd=ROOT, capture_output=True).returncode, 0)


    def test_safe_numeric_settings_use_defaults_for_invalid_values(self):
        env = {
            "SERVER_PORT": "not-a-port",
            "SESSION_DAYS": "0",
            "MAX_BACKUPS": "-3",
            "AUTO_BACKUP_HOURS": "Infinity",
            "PULSE_BACKUP_MAX_MB": "NaN",
            "PULSE_BACKUP_TOTAL_MAX_MB": "not-a-number",
        }
        self.assertEqual(ev.safe_int("SERVER_PORT", 25095, 1, 65535, env), 25095)
        self.assertEqual(ev.safe_int("SESSION_DAYS", 7, 1, 365, env), 7)
        self.assertEqual(ev.safe_int("MAX_BACKUPS", 30, 1, 1000, env), 30)
        self.assertEqual(ev.safe_int("DISCORD_GUILD_ID", 0, 0, 2**63 - 1, {"DISCORD_GUILD_ID": ""}), 0)
        self.assertEqual(ev.safe_float("AUTO_BACKUP_HOURS", 24.0, 0.0, 8760.0, env), 24.0)
        self.assertEqual(ev.safe_float("PULSE_BACKUP_MAX_MB", 500.0, 1.0, 100000.0, env), 500.0)
        self.assertEqual(ev.safe_float("PULSE_BACKUP_TOTAL_MAX_MB", 4000.0, 1.0, 1000000.0, env), 4000.0)

    def test_invalid_startup_settings_do_not_break_dashboard_or_backup_imports(self):
        # Exercise the real import-time consumers with isolated, temporary data paths.
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "PATH": os.environ.get("PATH", ""),
                "PYTHONPATH": str(ROOT),
                "PYTHONIOENCODING": "utf-8",
                "PULSE_DATA_DIR": tmp,
                "PULSE_DB_PATH": str(Path(tmp) / "pulse.db"),
                "PULSE_BACKUP_MAX_MB": "NaN",
                "PULSE_BACKUP_TOTAL_MAX_MB": "not-a-number",
                "SESSION_DAYS": "0",
                "AUTO_BACKUP_HOURS": "Infinity",
                "MAX_BACKUPS": "-5",
                "PULSE_SESSION_IDLE_HOURS": "NaN",
                "DISCORD_GUILD_ID": "not-a-snowflake",
            }
            code = (
                "import pulse_backup as b; import webserver as w; import pulse_sessions as s; "
                "assert b.DEFAULT_BUDGET_BYTES == 500 * 1024 * 1024; "
                "assert b.TOTAL_MAX_BYTES == 4000 * 1024 * 1024; "
                "assert (w.SESSION_DAYS, w.AUTO_BACKUP_HOURS, w.MAX_BACKUPS) == (7, 24.0, 30); "
                "assert s.MAX_SESSION_SECONDS == 7 * 86400; "
                "assert s.IDLE_SECONDS == 24 * 3600; "
                "import pulse_ids; assert w.GUILD_ID == pulse_ids.GUILD_ID; "
                "import cogs.pulse_pro as pro; import cogs.pulse_suite as suite; "
                "assert pro.GUILD_ID == 0 and suite.GUILD_ID == 0"
            )
            result = subprocess.run(
                [sys.executable, "-c", code],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
            )
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])


if __name__ == "__main__":
    unittest.main()
