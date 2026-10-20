"""K-15: runtime data (members, evidence, settings) must never end up in Git or the Docker image."""
import fnmatch
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Every runtime location from docs/DATENLANDKARTE.md (+ temp/backup variants).
RUNTIME_PATHS = [
    "config.json", "team_data.json", "applications.json", "shifts.json", "logs.json", "audit_logs.json",
    "meetings.json", "pulse_announcement_archive.json", "role_nickname_state.json", "social_promo_sources.json",
    "ai_moderation_cases.json",
    "pulse.db", "abmeldungen.db", "activity_check.db", "pulse_suite.db", "teamliste.db", "buero_system.db",
    "rp_verwaltung.db", "flaggenquiz.db", "pulse.db-wal", "pulse.db-shm", "pulse.restore.tmp.db",
    "team_data.json.corrupt-20261020-120000", "config.json.corrupt-20261020-120000",
    "config.json.tmp", "abmeldungen.db.vor-reparatur-20261020-120000.bak",
    "uploads/ticket_panels/panel_x_banner_aa.png", "uploads/welcome_banners/welcome_banner_aa.png",
    "uploads/voice_support_audio/opening.mp3", "uploads/anything/new.bin",
    "attachments/case_1/beweis.png", "transcripts/ticket_1.txt", "backups/pulse_backup_20261020_120000.zip",
    ".session_secret", ".env",
]


def docker_ignored(path: str, patterns: list[str]) -> bool:
    ignored = False
    for raw in patterns:
        pattern = raw.strip()
        if not pattern or pattern.startswith("#"):
            continue
        negate = pattern.startswith("!")
        pattern = pattern.lstrip("!").rstrip("/")
        parts = path.split("/")
        hit = (fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(path, pattern + "/*")
               or any(fnmatch.fnmatch("/".join(parts[:i]), pattern) for i in range(1, len(parts))))
        if hit:
            ignored = not negate
    return ignored


class IgnoreFileTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("git"), "git nicht verfügbar")
    def test_git_ignores_all_runtime_paths(self):
        missing = []
        for path in RUNTIME_PATHS:
            result = subprocess.run(["git", "check-ignore", "-q", "--no-index", path], cwd=ROOT,
                                    capture_output=True)
            if result.returncode != 0:
                missing.append(path)
        self.assertEqual(missing, [], "nicht von .gitignore erfasst")

    def test_docker_image_excludes_all_runtime_paths(self):
        patterns = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        missing = [p for p in RUNTIME_PATHS if not docker_ignored(p, patterns)]
        self.assertEqual(missing, [], "nicht von .dockerignore erfasst")


if __name__ == "__main__":
    unittest.main()
