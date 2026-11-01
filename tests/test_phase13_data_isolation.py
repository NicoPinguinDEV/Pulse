"""Phase 13 (D-06 / SEC R-05): code and runtime data are separated.

Covers PULSE_DATA_DIR (central path handling, legacy default), the start checks, every module path, the migration
script (dry run, confirmation, checksums, idempotency, no overwrite, aborted runs), the container configuration,
the deploy scripts, the commit shown without .git and that tests/tools never touch the checkout's data.
No Discord, no Docker, no real data – everything runs in temp folders.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pulse_paths  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
import pulse_migrate_data as mig  # noqa: E402


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def clean_env(**extra) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("PULSE_", "DISCORD_")) and k not in ("SESSION_SECRET", "TMPDIR")}
    env.update(PYTHONIOENCODING="utf-8", **extra)
    return env


class PathModuleTests(unittest.TestCase):
    def test_legacy_default_is_the_working_directory(self):
        self.assertEqual(pulse_paths._resolve(""), Path("."))
        self.assertEqual(pulse_paths._resolve(None), Path("."))
        self.assertEqual(str(Path(".") / "team_data.json"), "team_data.json")  # identical to the old literals

    def test_configured_directory_is_absolute(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(pulse_paths._resolve(tmp), Path(tmp).resolve())
            self.assertTrue(pulse_paths._resolve(tmp).is_absolute())

    def test_legacy_mode_needs_no_marker(self):
        with patch.dict(os.environ, {"PULSE_DATA_DIR": ""}):
            self.assertEqual(pulse_paths.verify_data_dir(), [])

    def test_missing_directory_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            problems = pulse_paths.verify_data_dir(Path(tmp) / "fehlt", allow_init=True)
            self.assertTrue(problems and "existiert nicht" in problems[0])
            self.assertFalse((Path(tmp) / "fehlt").exists())

    def test_empty_directory_without_marker_is_refused_and_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            problems = pulse_paths.verify_data_dir(tmp, allow_init=False)
            self.assertTrue(problems and "weder migriert noch initialisiert" in problems[0])
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_explicit_init_writes_marker_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(pulse_paths.verify_data_dir(tmp, allow_init=True), [])
            self.assertEqual(pulse_paths.read_marker(tmp)["state"], "initialized")
            self.assertEqual(pulse_paths.verify_data_dir(tmp, allow_init=False), [])

    def test_aborted_migration_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            pulse_paths.write_marker(tmp, "in_progress")
            problems = pulse_paths.verify_data_dir(tmp, allow_init=True)
            self.assertTrue(problems and "in_progress" in problems[0])
            self.assertEqual(pulse_paths.read_marker(tmp)["state"], "in_progress")  # init never overrides it

    def test_damaged_marker_is_refused_not_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / pulse_paths.MARKER_NAME).write_text("{kaputt", encoding="utf-8")
            problems = pulse_paths.verify_data_dir(tmp, allow_init=True)
            self.assertTrue(problems and "beschädigt" in problems[0])
            self.assertEqual((Path(tmp) / pulse_paths.MARKER_NAME).read_text(encoding="utf-8"), "{kaputt")

    def test_overrides_must_stay_inside_the_data_directory(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as other:
            pulse_paths.write_marker(tmp, "complete")
            with patch.dict(os.environ, {"PULSE_DB_PATH": str(Path(other) / "pulse.db")}):
                problems = pulse_paths.verify_data_dir(tmp)
            self.assertTrue(problems and "PULSE_DB_PATH" in problems[0])
            with patch.dict(os.environ, {"PULSE_DB_PATH": str(Path(tmp) / "pulse.db")}):
                self.assertEqual(pulse_paths.verify_data_dir(tmp), [])

    def test_marker_names_match_the_migration_script(self):
        self.assertEqual(mig.MARKER_NAME, pulse_paths.MARKER_NAME)
        self.assertEqual(mig.MARKER_FORMAT, pulse_paths.MARKER_FORMAT)


PROBE = r"""
import json, sys
from pathlib import Path
import pulse_paths, pulse_db, webserver as ws, pulse_next, pulse_ultimate, pulse_panels, pulse_ticket_design
import pulse_transcripts, pulse_welcome, pulse_verification
import cogs.abmeldung as ab, cogs.activity_check as ac, cogs.teamliste as tl, cogs.buero_system as bs
import cogs.flaggenquiz as fq, cogs.rp_verwaltung as rp, cogs.pulse_suite as su, cogs.social_promo as sp
import cogs.ai_moderation as aim, cogs.role_nickname_prefixes as rn
out = {
  "DATA_DIR": str(pulse_paths.DATA_DIR), "ws.DATA_DIR": str(ws.DATA_DIR), "ws.BASE_DIR": str(ws.BASE_DIR),
  "STATIC_CSS": str(ws.STATIC_CSS), "DB_PATH": pulse_db.DB_PATH,
  "DATA_FILE": ws.DATA_FILE, "CONFIG_FILE": ws.CONFIG_FILE, "APPS_FILE": ws.APPS_FILE, "SHIFTS_FILE": ws.SHIFTS_FILE,
  "LOGS_FILE": ws.LOGS_FILE, "AUDIT_FILE": ws.AUDIT_FILE, "MEETINGS_FILE": ws.MEETINGS_FILE,
  "DB_ABMELDUNGEN": ws.DB_ABMELDUNGEN, "ACTIVITY_DB": ws.ACTIVITY_DB, "BACKUP_DIR": ws.BACKUP_DIR,
  "SECRET_FILE": ws.SECRET_FILE, "VOICE_AUDIO": str(ws.VOICE_SUPPORT_AUDIO_DIR),
  "PANEL_IMAGES": str(ws.TICKET_PANEL_IMAGE_DIR), "full_backup_dir": ws.full_backup_dir(),
  "next.BACKUP_DIR": str(pulse_next.BACKUP_DIR), "ultimate.ATTACHMENTS": str(pulse_ultimate.ATTACHMENTS_DIR),
  "ultimate.DB": str(pulse_ultimate.ULTIMATE_DB), "panels.BASE": str(pulse_panels.BASE_DIR),
  "ticket_design.PANEL": pulse_ticket_design.PANEL_IMAGE_DIR,
  "abmeldung": ab.DB_NAME, "activity": ac.DB_NAME, "activity.abm": ac.ABMELDUNGEN_DB, "teamliste": tl.DB_NAME,
  "buero": bs.DB_NAME, "flaggen": fq.DB_NAME, "rp": rp.DB_NAME, "suite": su.DB_PATH, "social": str(sp.DATA_FILE),
  "ai_cases": str(aim.CASES_PATH), "role_state": str(rn.STATE_FILE),
}
print(json.dumps(out))
"""


class ModulePathTests(unittest.TestCase):
    """Imports every affected module in a fresh process – no import here may create files (A-04)."""

    def _probe(self, cwd, **env):
        result = subprocess.run([sys.executable, "-c", PROBE], cwd=cwd, env=clean_env(PYTHONPATH=str(ROOT), **env),
                                capture_output=True, timeout=180)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace")[-3000:])
        return json.loads(result.stdout.decode().strip().splitlines()[-1])

    def test_every_runtime_path_is_below_pulse_data_dir(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as cwd:
            out = self._probe(cwd, PULSE_DATA_DIR=data)
            root = Path(data).resolve()
            code_only = {"ws.BASE_DIR", "STATIC_CSS"}
            for key, value in out.items():
                with self.subTest(key=key):
                    path = Path(value).resolve()
                    if key in code_only:
                        self.assertEqual(path if key == "ws.BASE_DIR" else path.parents[1], ROOT)
                    else:
                        self.assertTrue(path == root or root in path.parents, f"{key} = {value}")
            self.assertEqual([p.name for p in Path(cwd).iterdir()], [])  # nothing written into the working dir

    def test_legacy_layout_keeps_the_old_relative_names(self):
        with tempfile.TemporaryDirectory() as cwd:
            out = self._probe(cwd)
            self.assertEqual(out["DATA_DIR"], ".")
            expected = {"DATA_FILE": "team_data.json", "CONFIG_FILE": "config.json", "APPS_FILE": "applications.json",
                        "SHIFTS_FILE": "shifts.json", "LOGS_FILE": "logs.json", "AUDIT_FILE": "audit_logs.json",
                        "MEETINGS_FILE": "meetings.json", "BACKUP_DIR": "backups", "SECRET_FILE": ".session_secret",
                        "DB_PATH": "pulse.db", "buero": "buero_system.db", "flaggen": "flaggenquiz.db",
                        "rp": "rp_verwaltung.db"}
            for key, value in expected.items():
                with self.subTest(key=key):
                    self.assertEqual(out[key], value)
            # former __file__-based paths now follow the working directory (main.py changes into the code folder)
            self.assertEqual(Path(out["abmeldung"]), Path("abmeldungen.db"))
            self.assertEqual(Path(out["role_state"]), Path("role_nickname_state.json"))


class SourceGuardTests(unittest.TestCase):
    """No new relative or code-folder-based runtime paths."""

    MODULES = sorted(list(ROOT.glob("*.py")) + list((ROOT / "cogs").glob("*.py")))

    def test_no_relative_sqlite_or_runtime_folder_literals(self):
        patterns = [
            r"""sqlite3\.connect\(\s*['"][\w.]+\.db['"]""",
            r"""os\.makedirs\(\s*['"](transcripts|uploads|attachments|backups|logs)['"]""",
            r"""^\s*\w+\s*=\s*['"][\w]+\.(db|json)['"]\s*$""",
            r"""Path\(__file__\)[^\n]*['"][\w]+\.(db|json)['"]""",
        ]
        allowed = {"pulse_backup.py", "pulse_paths.py", "pulse_consistency.py"}
        for path in self.MODULES:
            if path.name in allowed:
                continue
            text = path.read_text(encoding="utf-8")
            for pattern in patterns:
                with self.subTest(module=path.name, pattern=pattern):
                    self.assertIsNone(re.search(pattern, text, re.M), path.name)

    def test_code_assets_stay_in_the_code_folder(self):
        voice = read("cogs/voice_support.py")
        self.assertIn('Path(__file__).resolve().parents[1] / "assets" / "voice_support_after_hours.mp3.b64"', voice)
        self.assertIn('STATIC_CSS = BASE_DIR / "static" / "pulse.css"', read("webserver.py"))
        self.assertIn("os.path.join(ws.BASE_DIR,'cogs')", read("pulse_pro.py"))
        self.assertIn('Path(ws.DATA_DIR) / "assets"', read("pulse_pro.py"))  # uploaded music = data

    def test_pulse_paths_is_imported_after_dotenv_in_main(self):
        main = read("main.py")
        self.assertLess(main.index("load_dotenv("), main.index("import pulse_paths"))
        self.assertLess(main.index("verify_data_dir()"), main.index("pulse_logging.setup("))
        self.assertIn("raise SystemExit(pulse_paths.EXIT_CONFIG)", main)

    def test_tests_and_tools_patch_the_data_root(self):
        """Isolation: whoever redirects ws.BASE_DIR into a temp folder must also redirect ws.DATA_DIR."""
        for path in sorted(list((ROOT / "tests").glob("*.py")) + list((ROOT / "tools").glob("*.py"))):
            text = path.read_text(encoding="utf-8")
            if re.search(r"""patch\.object\(ws, "BASE_DIR"|ws\.BASE_DIR = """, text):
                with self.subTest(file=path.name):
                    self.assertRegex(text, r"""patch\.object\(ws, "DATA_DIR"|ws\.DATA_DIR = """)


def make_source(root: Path) -> dict:
    """A small fake checkout with every kind of runtime data (no real data)."""
    (root / "pulse.py").write_text("code", encoding="utf-8")
    con = sqlite3.connect(root / "pulse.db")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE t (v TEXT)")
    con.execute("INSERT INTO t VALUES ('a')")
    con.commit()
    con.close()
    (root / "config.json").write_text('{"x": 1}', encoding="utf-8")
    (root / "team_data.json").write_text('{"1": {}}', encoding="utf-8")
    (root / "shifts.json.corrupt-20261001").write_text("{", encoding="utf-8")
    (root / ".session_secret").write_text("geheim", encoding="utf-8")
    (root / ".env").write_text("DISCORD_TOKEN=x", encoding="utf-8")
    for rel in ("uploads/welcome_banners/b.png", "attachments/1/a.txt", "transcripts/T-1.txt", "backups/pulse_full_x.zip",
                "logs/pulse.log", "assets/support_wait.mp3"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(rel.encode())
    (root / "assets" / "voice_support_after_hours.mp3.b64").write_text("code asset", encoding="utf-8")
    return {p.relative_to(root).as_posix(): mig.sha256(p) for p in root.rglob("*") if p.is_file()}


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.source, self.target, self.backups = base / "Pulse", base / "data", base / "bk"
        self.source.mkdir()
        self.before = make_source(self.source)

    def tearDown(self):
        self.tmp.cleanup()

    def run_mig(self, *args):
        argv = ["--source", str(self.source), "--target", str(self.target), "--backup-dir", str(self.backups), *args]
        with patch.object(mig, "container_state", lambda name: "exited"), patch("builtins.print"):
            return mig.main(argv)

    def assert_source_unchanged(self):
        now = {p.relative_to(self.source).as_posix(): mig.sha256(p) for p in self.source.rglob("*") if p.is_file()}
        self.assertEqual(now, self.before)

    def test_inventory_contains_data_but_never_code_or_secrets_file(self):
        items = set(mig.inventory(self.source))
        for rel in ("pulse.db", "config.json", "team_data.json", ".session_secret", "shifts.json.corrupt-20261001",
                    "uploads/welcome_banners/b.png", "attachments/1/a.txt", "transcripts/T-1.txt",
                    "backups/pulse_full_x.zip", "logs/pulse.log", "assets/support_wait.mp3"):
            self.assertIn(rel, items)
        for rel in ("pulse.py", ".env", "assets/voice_support_after_hours.mp3.b64"):
            self.assertNotIn(rel, items)

    def test_lists_match_the_full_backup(self):
        import pulse_backup
        self.assertEqual(mig.SQLITE_FILES, pulse_backup.SQLITE_FILES)
        self.assertEqual(mig.JSON_FILES, pulse_backup.JSON_FILES)
        self.assertEqual(mig.MEDIA_FILES, pulse_backup.MEDIA_FILES)
        self.assertTrue(set(pulse_backup.FOLDERS) <= set(mig.FOLDERS))

    def test_dry_run_is_the_default_and_writes_nothing(self):
        self.assertEqual(self.run_mig(), 0)
        self.assertFalse(self.target.exists())
        self.assertFalse(self.backups.exists())
        self.assert_source_unchanged()

    def test_execute_without_confirmation_changes_nothing(self):
        with patch("sys.stdin.isatty", return_value=False):
            self.assertEqual(self.run_mig("--execute"), 3)
            self.assertEqual(self.run_mig("--execute", "--confirm", "ja"), 3)
        self.assertFalse(self.target.exists())
        self.assertFalse(self.backups.exists())

    def test_running_container_blocks_the_migration(self):
        argv = ["--source", str(self.source), "--target", str(self.target), "--backup-dir", str(self.backups),
                "--execute", "--confirm", mig.CONFIRM_WORD]
        with patch.object(mig, "container_state", lambda name: "running"), patch("builtins.print"):
            self.assertEqual(mig.main(argv), 2)
        self.assertFalse(self.target.exists())

    def test_confirmed_migration_copies_verifies_backs_up_and_marks_complete(self):
        self.assertEqual(self.run_mig("--execute", "--confirm", mig.CONFIRM_WORD), 0)
        items = mig.inventory(self.source)
        for rel in items:
            self.assertEqual(mig.sha256(self.target / rel), self.before[rel], rel)
        self.assertFalse((self.target / ".env").exists())
        self.assertFalse((self.target / "pulse.py").exists())
        marker = pulse_paths.read_marker(self.target)
        self.assertEqual((marker["state"], marker["files"]), ("complete", len(items)))
        self.assertEqual(pulse_paths.verify_data_dir(self.target), [])
        con = sqlite3.connect(self.target / "pulse.db")
        self.assertEqual(con.execute("SELECT v FROM t").fetchall(), [("a",)])  # WAL content came along
        con.close()
        archives = list(self.backups.glob("pulse-premigration-*.tar.gz"))
        self.assertEqual(len(archives), 1)
        with tarfile.open(archives[0]) as tar:
            names = set(tar.getnames())
        self.assertTrue(set(items) <= names and "pulse-migration-manifest.json" in names)
        self.assert_source_unchanged()
        self.assertEqual(self.run_mig("--verify"), 0)

    def test_second_run_is_idempotent(self):
        self.assertEqual(self.run_mig("--execute", "--confirm", mig.CONFIRM_WORD), 0)
        stamp = (self.target / "config.json").stat().st_mtime_ns
        self.assertEqual(self.run_mig("--execute", "--confirm", mig.CONFIRM_WORD), 0)
        self.assertEqual((self.target / "config.json").stat().st_mtime_ns, stamp)
        self.assertEqual(len(list(self.backups.glob("*.tar.gz"))), 1)  # "already complete": no new backup

    def test_existing_different_file_is_never_overwritten(self):
        self.target.mkdir()
        (self.target / "config.json").write_text('{"neu": true}', encoding="utf-8")
        self.assertEqual(self.run_mig("--execute", "--confirm", mig.CONFIRM_WORD), 1)
        self.assertEqual((self.target / "config.json").read_text(encoding="utf-8"), '{"neu": true}')
        self.assertEqual(sorted(p.name for p in self.target.iterdir()), ["config.json"])  # nothing else copied

    def test_aborted_run_is_detected_and_can_be_resumed(self):
        real_copy = mig.copy_file
        calls = {"n": 0}

        def failing(src, dst):
            calls["n"] += 1
            if calls["n"] == 3:
                raise OSError("Platte voll (Simulation)")
            real_copy(src, dst)

        with patch.object(mig, "copy_file", failing), self.assertRaises(OSError):
            self.run_mig("--execute", "--confirm", mig.CONFIRM_WORD)
        self.assertEqual(pulse_paths.read_marker(self.target)["state"], "in_progress")
        self.assertTrue(pulse_paths.verify_data_dir(self.target, allow_init=True))  # Pulse refuses to start
        self.assertEqual(self.run_mig("--execute", "--confirm", mig.CONFIRM_WORD), 0)
        self.assertEqual(pulse_paths.read_marker(self.target)["state"], "complete")
        self.assertEqual(self.run_mig("--verify"), 0)
        self.assert_source_unchanged()

    def test_new_source_files_after_migration_are_reported_not_lost(self):
        self.assertEqual(self.run_mig("--execute", "--confirm", mig.CONFIRM_WORD), 0)
        (self.source / "meetings.json").write_text("[]", encoding="utf-8")
        self.assertEqual(self.run_mig("--verify"), 1)


class ContainerSmokeTests(unittest.TestCase):
    """tests/container_smoke.py outside Docker: start checks, init_runtime, JSON, backup/restore, commit display –
    and afterwards the code folder must be unchanged (stand-in for the read-only root filesystem)."""

    def _status(self):
        out = subprocess.run(["git", "status", "--porcelain", "--ignored", "--untracked-files=all"], cwd=ROOT,
                             capture_output=True, timeout=60)
        if out.returncode != 0:
            raise unittest.SkipTest("git nicht verfügbar")
        return {line for line in out.stdout.decode(errors="replace").splitlines() if "__pycache__" not in line}

    def test_full_start_writes_only_into_the_data_directory(self):
        before = self._status()
        with tempfile.TemporaryDirectory() as data:
            result = subprocess.run([sys.executable, str(ROOT / "tests" / "container_smoke.py")], cwd=ROOT,
                                    env=clean_env(PULSE_DATA_DIR=data, PULSE_DATA_DIR_INIT="1",
                                                  PULSE_GIT_SHA="ab" * 20), capture_output=True, timeout=300)
            self.assertEqual(result.returncode, 0, result.stdout.decode(errors="replace")[-2000:]
                             + result.stderr.decode(errors="replace")[-3000:])
            self.assertIn(b"container smoke ok", result.stdout)
        self.assertEqual(self._status() - before, set())

    def test_start_is_refused_without_marker(self):
        with tempfile.TemporaryDirectory() as data:
            code = ("import sys; sys.path.insert(0, %r); import main; main.prepare_process()" % str(ROOT))
            result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=clean_env(PULSE_DATA_DIR=data),
                                    capture_output=True, timeout=120)
            self.assertEqual(result.returncode, pulse_paths.EXIT_CONFIG, result.stderr.decode(errors="replace"))
            self.assertEqual(list(Path(data).iterdir()), [])


class CommitDisplayTests(unittest.TestCase):
    def test_build_sha_wins_without_git(self):
        import pulse_server_control as scc
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"PULSE_GIT_SHA": "C" * 40,
                                                                          "PULSE_GIT_BRANCH": "main<script>"}):
            info = scc.installed_commit(tmp)
        self.assertEqual(info, {"sha": "c" * 40, "short": "ccccccc", "branch": "mainscript", "source": "build"})

    def test_invalid_or_missing_sha_falls_back_to_git(self):
        import pulse_server_control as scc
        with tempfile.TemporaryDirectory() as tmp:
            git = Path(tmp) / ".git"
            (git / "refs" / "heads").mkdir(parents=True)
            (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
            (git / "refs" / "heads" / "main").write_text("d" * 40 + "\n", encoding="utf-8")
            with patch.dict(os.environ, {"PULSE_GIT_SHA": "nicht-hex"}):
                self.assertEqual(scc.installed_commit(tmp)["source"], "git")
            with patch.dict(os.environ, {"PULSE_GIT_SHA": ""}):
                self.assertEqual(scc.installed_commit(tmp)["sha"], "d" * 40)
        with tempfile.TemporaryDirectory() as empty, patch.dict(os.environ, {"PULSE_GIT_SHA": ""}):
            self.assertEqual(scc.installed_commit(empty), {})

    def test_system_status_uses_the_build_aware_reader(self):
        text = read("pulse_server_control.py")
        self.assertNotIn("read_git_head(ws.BASE_DIR)", text)
        self.assertEqual(text.count("installed_commit(ws.BASE_DIR)"), 2)


class ContainerConfigTests(unittest.TestCase):
    def setUp(self):
        self.compose = read("compose.yaml")
        self.dockerfile = read("Dockerfile")

    def test_read_only_code_and_only_data_mounted(self):
        c = self.compose
        self.assertIn("    read_only: true\n", c)
        self.assertIn("- /tmp:size=64m,mode=1777,nosuid,nodev,noexec", c)
        self.assertIn("source: ${PULSE_DATA_HOST_DIR:-/opt/pulse/data}\n        target: /data", c)
        self.assertIn("create_host_path: false", c)
        self.assertIn("- ./.pulse-agent:/app/.pulse-agent\n", c)
        volumes = c.split("    volumes:\n", 1)[1].split("    working_dir:", 1)[0]
        mounts = re.findall(r"^\s+- (\./[^\s:]*):", volumes, re.M)
        self.assertEqual(mounts, ["./.pulse-agent"])  # nothing else of the checkout

    def test_paths_are_fixed_not_taken_from_env_file(self):
        self.assertIn("      PULSE_DATA_DIR: /data\n", self.compose)
        self.assertIn("      PULSE_DB_PATH: /data/pulse.db\n", self.compose)
        self.assertIn("      TMPDIR: /data/tmp\n", self.compose)
        self.assertNotIn("PULSE_DB_PATH: ${", self.compose)
        self.assertNotIn("PULSE_DATA_DIR: ${", self.compose)

    def test_existing_limits_and_hardening_are_kept(self):
        for needle in ("mem_limit: 768m", "memswap_limit: 1024m", "cap_drop: [ALL]", "- no-new-privileges:true",
                       "pids_limit: 256", 'restart: "no"', "init: true", "stop_grace_period: 40s", "/healthz",
                       "env_file:\n      - .env", "max-size: \"10m\""):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.compose)

    def test_build_args_carry_the_commit(self):
        self.assertIn("PULSE_GIT_SHA: ${PULSE_GIT_SHA:-}", self.compose)
        self.assertIn('ARG PULSE_GIT_SHA=""', self.dockerfile)
        self.assertIn("ENV PULSE_GIT_SHA=${PULSE_GIT_SHA}", self.dockerfile)

    def test_dockerfile_code_is_root_owned_and_user_is_unprivileged(self):
        d = self.dockerfile
        self.assertIn("COPY . /app\n", d)
        self.assertNotIn("--chown=pulse:pulse . /app", d)
        self.assertIn("chmod -R a+rX,go-w /app", d)
        self.assertIn("python -m compileall -q /app", d)
        self.assertIn("install -d -o pulse -g pulse -m 750 /data", d)
        self.assertIn("ENV PULSE_DATA_DIR=/data", d)
        self.assertIn("\nUSER pulse\n", d)
        self.assertLess(d.index("COPY . /app"), d.index("USER pulse"))

    def test_runtime_data_never_lands_in_the_image(self):
        ignore = read(".dockerignore").splitlines()
        for entry in (".env", "*.db", "config.json", "team_data.json", "uploads", "attachments", "transcripts",
                      "backups", "logs", ".session_secret", "assets/support_wait.*", ".pulse-data.json"):
            with self.subTest(entry=entry):
                self.assertIn(entry, ignore)

    def test_ci_starts_the_read_only_container(self):
        ci = read(".github/workflows/python-syntax.yml")
        self.assertIn("docker run --rm --read-only", ci)
        self.assertIn("tests/container_smoke.py", ci)
        self.assertIn('test "$code" = "78"', ci)


class DeployScriptTests(unittest.TestCase):
    def test_remote_deploy_checks_migration_before_any_change(self):
        s = read("deploy/remote_deploy.sh")
        check = s.index('if uses_data_dir "$SHA" && ! data_dir_ready; then')
        self.assertLess(check, s.index("BACKUP_PY="))
        self.assertLess(check, s.index('git reset --hard "$SHA"'))
        self.assertIn('base = os.getenv("PULSE_DATA_DIR") or "."', s)
        self.assertLess(s.index('export PULSE_GIT_SHA="$SHA"'), s.index("docker compose build"))

    def test_rollback_to_the_old_layout_is_refused(self):
        s = read("deploy/rollback_code.sh")
        self.assertIn('if uses_data_dir "$PREV" && ! uses_data_dir "$SHA"; then', s)
        self.assertLess(s.index('uses_data_dir "$PREV"'), s.index("BACKUP_PY="))
        self.assertIn('base = os.getenv("PULSE_DATA_DIR") or "."', s)

    def test_boot_update_checks_migration_before_stopping(self):
        s = read("scripts/pulse-vps-boot-update.sh")
        self.assertLess(s.index('if uses_data_dir "$target" && ! data_dir_ready; then'),
                        s.index('docker compose stop "$SERVICE_NAME"'))
        self.assertIn('"$DATA_HOST_DIR"/*.db', s)
        self.assertIn('export PULSE_GIT_SHA="$target"', s)

    def test_crash_restart_leaves_config_errors_alone(self):
        s = read("scripts/pulse-crash-restart.sh")
        self.assertLess(s.index('"$exit_code" == "78"'), s.index('docker start "$CONTAINER"'))

    def test_scripts_never_delete_runtime_data(self):
        for name in ("deploy/remote_deploy.sh", "deploy/rollback_code.sh", "scripts/pulse-vps-boot-update.sh",
                     "deploy/vps_bootstrap.sh"):
            s = read(name)
            with self.subTest(script=name):
                self.assertNotRegex(s, r"rm -rf|git clean|down -v|DATA_HOST_DIR[^\n]*\brm\b")


class RemoteDeployDataDirRunTests(unittest.TestCase):
    """Runs remote_deploy.sh against fake git/docker: with the new layout and no migration nothing may happen."""

    def _bash(self) -> str:
        bash = os.environ.get("PULSE_TEST_BASH", "bash")
        try:
            probe = subprocess.run([bash, "-c", "echo ok"], capture_output=True, timeout=20)
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            raise unittest.SkipTest("bash nicht verfügbar")
        if probe.returncode != 0 or probe.stdout.strip() != b"ok":
            raise unittest.SkipTest("bash nicht lauffähig")
        return bash

    def _run(self, marker_state: str | None, failing: str = "never-fails"):
        bash = self._bash()
        tmp = Path(tempfile.mkdtemp())
        for name in ("bin", "repo", "data"):
            (tmp / name).mkdir()
        if marker_state:
            pulse_paths.write_marker(tmp / "data", marker_state)
        log = (tmp / "calls.log").as_posix()
        (tmp / "bin" / "git").write_text(
            "#!/usr/bin/env bash\n"
            f'echo "git $*" >> "{log}"\n'
            f'if [ "$1" = rev-parse ]; then echo {"a" * 40}; fi\n'
            'if [ "$1" = show ]; then case "$2" in ' + "b" * 40 + ':*) echo "        target: /data";; esac; fi\n'
            "exit 0\n", encoding="utf-8", newline="\n")
        (tmp / "bin" / "docker").write_text(f'#!/usr/bin/env bash\necho "docker $*" >> "{log}"\n'
                                            f'case "$*" in *"{failing}"*) exit 1;; esac\nexit 0\n',
                                            encoding="utf-8", newline="\n")
        (tmp / "bin" / "flock").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8", newline="\n")
        (tmp / "bin" / "sleep").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8", newline="\n")
        script = (
            f'export PATH="$(cd "{(tmp / "bin").as_posix()}" && pwd):$PATH"; chmod +x "{(tmp / "bin").as_posix()}"/*; '
            f'export PULSE_UID="$(stat -c %u "{(tmp / "repo").as_posix()}")" PULSE_DEPLOY_HEALTH_SECONDS=5 '
            f'PULSE_DATA_HOST_DIR="{(tmp / "data").as_posix()}" PULSE_DEPLOY_LOCK="{(tmp / "deploy.lock").as_posix()}"; '
            f'bash "{(ROOT / "deploy" / "remote_deploy.sh").as_posix()}" {"b" * 40} "{(tmp / "repo").as_posix()}"'
        )
        result = subprocess.run([self._bash(), "-c", script], capture_output=True, timeout=60)
        calls = Path(log).read_text(encoding="utf-8").splitlines() if Path(log).exists() else []
        return result, calls

    def test_unmigrated_data_dir_stops_before_backup_and_reset(self):
        for state in (None, "in_progress"):
            with self.subTest(state=state):
                result, calls = self._run(state)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("noch nicht migriert", result.stdout.decode(errors="replace"))
                self.assertFalse([c for c in calls if c.startswith(("git reset", "docker"))], calls)

    def test_migrated_data_dir_deploys_with_commit_build_arg(self):
        result, calls = self._run("complete")
        self.assertEqual(result.returncode, 0, result.stdout.decode(errors="replace") + result.stderr.decode(errors="replace"))
        self.assertEqual([c for c in calls if c.startswith("git reset --hard")], [f"git reset --hard {'b' * 40}"])
        self.assertTrue(any(c.startswith("docker compose build") for c in calls), calls)

    def test_failed_layout_switch_stops_instead_of_starting_outdated_data(self):
        """Phase 13.1: PREV (old layout) must never be started automatically on the outdated checkout data."""
        result, calls = self._run("complete", failing="exec -T pulse")  # /healthz never answers
        out = result.stdout.decode(errors="replace")
        self.assertEqual(result.returncode, 2, out + result.stderr.decode(errors="replace"))
        self.assertIn("KEIN automatischer Rollback", out)
        self.assertEqual([c for c in calls if c.startswith("git reset --hard")], [f"git reset --hard {'b' * 40}"])
        self.assertIn("docker compose stop pulse", calls)
        self.assertFalse([c for c in calls if c.startswith("docker compose up -d --build")], calls)


if __name__ == "__main__":
    unittest.main()
