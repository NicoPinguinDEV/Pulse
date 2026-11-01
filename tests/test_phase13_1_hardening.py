"""Phase 13.1: production hardening of the migration, the rollback way and the deploy chain.

Fail-closed Docker check, no-overwrite copy (also against parallel writers), symlinks/traversal, damaged markers,
damaged backups, missing disk space, SQLite WAL, resume, run lock, the real way back (--rollback) and the deploy
scripts (locks, layout switch never starts on outdated data). Temp folders and fakes only – no Docker, no VPS.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import pulse_paths  # noqa: E402
import pulse_migrate_data as mig  # noqa: E402


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def fake_run(responses):
    """subprocess.run stand-in: responses maps the docker sub-command to (returncode, stdout, stderr) or an exception."""
    calls = []

    def run(cmd, **_kwargs):
        calls.append(cmd)
        answer = responses[cmd[1]]
        if isinstance(answer, BaseException):
            raise answer
        code, out, err = answer
        return SimpleNamespace(returncode=code, stdout=out, stderr=err)
    return run, calls


class DockerFailClosedTests(unittest.TestCase):
    def _state(self, responses, which="/usr/bin/docker"):
        run, _ = fake_run(responses)
        with patch.object(mig.shutil, "which", lambda name: which), patch.object(mig.subprocess, "run", run):
            return mig.container_state("pulse-teamos")

    def test_docker_missing_is_an_abort(self):
        with self.assertRaisesRegex(mig.MigrationError, "nicht installiert"):
            self._state({}, which=None)

    def test_daemon_unreachable_is_an_abort(self):
        for answer in ((1, "", "Cannot connect to the Docker daemon"), (0, "", ""),
                       subprocess.TimeoutExpired("docker", 30), OSError("kaputt")):
            with self.subTest(answer=repr(answer)), self.assertRaisesRegex(mig.MigrationError, "Daemon"):
                self._state({"info": answer})

    def test_inspect_failure_is_an_abort(self):
        for answer in ((1, "", "permission denied while trying to connect"), subprocess.TimeoutExpired("docker", 30)):
            with self.subTest(answer=repr(answer)), self.assertRaisesRegex(mig.MigrationError, "inspect"):
                self._state({"info": (0, "27.0.1\n", ""), "inspect": answer})

    def test_reported_states(self):
        self.assertEqual(self._state({"info": (0, "27.0.1\n", ""), "inspect": (0, "exited\n", "")}), "exited")
        self.assertEqual(self._state({"info": (0, "27.0.1\n", ""),
                                      "inspect": (1, "", "Error: No such object: pulse-teamos")}), "absent")

    def test_only_a_proven_stop_allows_the_run(self):
        for state in ("running", "restarting", "paused", "removing", "unknown"):
            with self.subTest(state=state), patch.object(mig, "container_state", lambda name, s=state: s):
                with self.assertRaisesRegex(mig.MigrationError, "Zustand"):
                    mig.require_stopped("pulse-teamos", absent_ok=True)
        for state in ("exited", "created", "dead"):
            with self.subTest(state=state), patch.object(mig, "container_state", lambda name, s=state: s):
                self.assertEqual(mig.require_stopped("pulse-teamos", absent_ok=False), state)
        with patch.object(mig, "container_state", lambda name: "absent"):
            with self.assertRaisesRegex(mig.MigrationError, "--container-absent"):
                mig.require_stopped("pulse-teamos", absent_ok=False)
            self.assertEqual(mig.require_stopped("pulse-teamos", absent_ok=True), "absent")

    def test_no_bypass_flag_exists(self):
        self.assertNotIn("--allow-running", read("scripts/pulse_migrate_data.py"))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.source, self.target, self.backups = base / "Pulse", base / "data", base / "bk"
        self.source.mkdir()
        (self.source / "config.json").write_text('{"x": 1}', encoding="utf-8")
        (self.source / "team_data.json").write_text('{"1": {}}', encoding="utf-8")
        (self.source / ".env").write_text("DISCORD_TOKEN=x", encoding="utf-8")
        (self.source / "transcripts").mkdir()
        (self.source / "transcripts" / "T-1.txt").write_text("t", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def run_mig(self, *args, state="exited"):
        argv = ["--source", str(self.source), "--target", str(self.target), "--backup-dir", str(self.backups), *args]
        with patch.object(mig, "container_state", lambda name: state), patch("builtins.print"):
            return mig.main(argv)

    def execute(self, *extra, state="exited"):
        return self.run_mig("--execute", "--confirm", mig.CONFIRM_WORD, *extra, state=state)


class MigrationHardeningTests(Base):
    def test_dry_run_and_verify_work_without_docker(self):
        with patch.object(mig.shutil, "which", lambda name: None):
            argv = ["--source", str(self.source), "--target", str(self.target), "--backup-dir", str(self.backups)]
            with patch("builtins.print"):
                self.assertEqual(mig.main(argv), 0)
                self.assertEqual(mig.main(argv + ["--verify"]), 1)  # target empty: differences, nothing written
        self.assertFalse(self.target.exists())

    def test_real_run_without_docker_changes_nothing(self):
        argv = ["--source", str(self.source), "--target", str(self.target), "--backup-dir", str(self.backups),
                "--execute", "--confirm", mig.CONFIRM_WORD]
        with patch.object(mig.shutil, "which", lambda name: None), patch("builtins.print"):
            self.assertEqual(mig.main(argv), 2)
        self.assertFalse(self.target.exists())
        self.assertFalse(self.backups.exists())

    def test_absent_container_needs_the_explicit_flag(self):
        self.assertEqual(self.execute(state="absent"), 2)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.execute("--container-absent", state="absent"), 0)
        self.assertEqual(pulse_paths.read_marker(self.target)["state"], "complete")

    def test_damaged_marker_is_never_replaced(self):
        self.target.mkdir()
        for content in ("{kaputt", '{"format": "fremd", "state": "complete"}', "[]"):
            with self.subTest(content=content):
                (self.target / mig.MARKER_NAME).write_text(content, encoding="utf-8")
                self.assertEqual(self.execute(), 2)
                self.assertEqual((self.target / mig.MARKER_NAME).read_text(encoding="utf-8"), content)
                self.assertEqual(sorted(p.name for p in self.target.iterdir()), [mig.MARKER_NAME])
        self.assertFalse(self.backups.exists())

    def test_not_enough_space_changes_nothing(self):
        with patch.object(mig, "free_bytes", lambda path: 1024):
            self.assertEqual(self.execute(), 2)
            self.assertEqual(self.run_mig(), 2)  # the dry run reports it too
        self.assertFalse(self.target.exists())

    def test_damaged_backup_stops_before_copying(self):
        with patch.object(mig, "verify_backup", lambda archive, manifest: ["config.json fehlt oder weicht ab"]):
            self.assertEqual(self.execute(), 2)
        self.assertFalse((self.target / "config.json").exists())
        self.assertIsNone(pulse_paths.read_marker(self.target))
        self.assertEqual(list(self.backups.glob("*.tar.gz")), [])  # only the .partial remains for inspection

    def test_backup_verification_detects_tampering(self):
        items = mig.inventory(self.source)
        archive, manifest = mig.create_backup(self.source, items, self.backups, "probe")
        self.assertEqual(mig.verify_backup(archive, manifest), [])
        self.assertTrue(mig.verify_backup(archive, {**manifest, "config.json": "0" * 64}))
        broken = self.backups / "broken.tar.gz"
        broken.write_bytes(archive.read_bytes()[:40])
        self.assertTrue(mig.verify_backup(broken, manifest))

    def test_run_lock_blocks_a_parallel_run(self):
        self.target.mkdir()
        (self.target / mig.LOCK_NAME).write_text("4711", encoding="utf-8")
        self.assertEqual(self.execute(), 2)
        self.assertFalse((self.target / "config.json").exists())
        self.assertTrue((self.target / mig.LOCK_NAME).exists())  # never removed by someone who did not create it

    def test_lock_is_released_after_success_and_failure(self):
        self.assertEqual(self.execute(), 0)
        self.assertFalse((self.target / mig.LOCK_NAME).exists())

    def test_rolled_back_directory_is_not_migrated_over(self):
        self.assertEqual(self.execute(), 0)
        pulse_paths.write_marker(self.target, "rolled_back")
        self.assertEqual(self.execute(), 2)
        self.assertEqual(pulse_paths.read_marker(self.target)["state"], "rolled_back")
        self.assertTrue(pulse_paths.verify_data_dir(self.target))  # Pulse refuses to start on it

    def test_leftover_temp_files_are_ignored_and_kept(self):
        self.target.mkdir()
        leftover = self.target / f".config.json.abc{mig.TMP_SUFFIX}"
        leftover.write_text("halb", encoding="utf-8")
        self.assertEqual(self.execute(), 0)
        self.assertTrue(leftover.exists())
        self.assertEqual(self.run_mig("--verify"), 0)


class CopyFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.src = self.dir / "src.json"
        self.src.write_text("neu", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def leftovers(self):
        return list(self.dir.rglob(f"*{mig.TMP_SUFFIX}"))

    def test_copies_content_and_metadata(self):
        dst = self.dir / "sub" / "dst.json"
        mig.copy_file(self.src, dst)
        self.assertEqual(dst.read_text(encoding="utf-8"), "neu")
        self.assertEqual(int(dst.stat().st_mtime), int(self.src.stat().st_mtime))
        self.assertEqual(self.leftovers(), [])

    def test_existing_file_is_never_overwritten(self):
        dst = self.dir / "dst.json"
        dst.write_text("alt", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            mig.copy_file(self.src, dst)
        self.assertEqual(dst.read_text(encoding="utf-8"), "alt")
        self.assertEqual(self.leftovers(), [])

    def test_file_created_in_parallel_wins(self):
        """A writer that creates dst after the check but before the publish must not lose its file."""
        dst = self.dir / "dst.json"
        real_copystat = mig.shutil.copystat

        def racing_copystat(src, tmp):
            real_copystat(src, tmp)
            dst.write_text("parallel", encoding="utf-8")

        with patch.object(mig.shutil, "copystat", racing_copystat), self.assertRaises(FileExistsError):
            mig.copy_file(self.src, dst)
        self.assertEqual(dst.read_text(encoding="utf-8"), "parallel")
        self.assertEqual(self.leftovers(), [])

    def test_aborted_copy_leaves_no_partial_target(self):
        dst = self.dir / "dst.json"
        with patch.object(mig.os, "fsync", side_effect=OSError("Platte voll")), self.assertRaises(OSError):
            mig.copy_file(self.src, dst)
        self.assertFalse(dst.exists())
        self.assertEqual(self.leftovers(), [])

    def test_never_uses_replace_or_rename_onto_the_target(self):
        source = read("scripts/pulse_migrate_data.py")
        body = source.split("def copy_file", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("os.link(tmp, dst)", body)
        self.assertNotIn("os.replace(", body)
        self.assertNotIn("os.rename(", body)


class PathSafetyTests(unittest.TestCase):
    def test_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            for rel in ("../x", "a/../../x", "/etc/passwd", "", "uploads/../../x"):
                with self.subTest(rel=rel), self.assertRaises(mig.MigrationError):
                    mig.safe_path(Path(tmp), rel)
            self.assertEqual(mig.safe_path(Path(tmp), "uploads/a.png"), Path(tmp) / "uploads" / "a.png")

    def test_symlinks_are_not_followed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, outside = Path(tmp) / "root", Path(tmp) / "outside"
            root.mkdir()
            outside.mkdir()
            (outside / "secret.txt").write_text("x", encoding="utf-8")
            try:
                (root / "uploads").symlink_to(outside, target_is_directory=True)
                (root / "config.json").symlink_to(outside / "secret.txt")
            except (OSError, NotImplementedError):
                self.skipTest("Symlinks auf diesem System nicht erlaubt")
            self.assertEqual(mig.inventory(root, tracked=set()), [])
            with self.assertRaises(mig.MigrationError):
                mig.safe_path(root, "uploads/secret.txt")

    def test_git_listing_failure_is_not_guessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".git").mkdir()
            with patch.object(mig.shutil, "which", lambda name: None), self.assertRaises(mig.MigrationError):
                mig.tracked_files(Path(tmp))


class SqliteWalTests(Base):
    def test_uncheckpointed_wal_content_is_migrated_and_verified(self):
        db = self.source / "pulse.db"
        con = sqlite3.connect(db)
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA wal_autocheckpoint=0")
            con.execute("CREATE TABLE t (v TEXT)")
            con.execute("INSERT INTO t VALUES ('nur-im-wal')")
            con.commit()
            self.assertTrue((self.source / "pulse.db-wal").stat().st_size > 0)
            items = mig.inventory(self.source)
            self.assertIn("pulse.db-wal", items)
            self.assertEqual(self.execute(), 0)
        finally:
            con.close()
        self.assertTrue((self.target / "pulse.db-wal").exists())
        self.assertEqual(mig.sqlite_ok([self.target / "pulse.db", self.target / "pulse.db-wal",
                                        self.target / "pulse.db-shm"]), "ok")
        check = Path(self.tmp.name) / "check"
        check.mkdir()
        for name in ("pulse.db", "pulse.db-wal"):
            (check / name).write_bytes((self.target / name).read_bytes())
        con = sqlite3.connect(check / "pulse.db")
        self.assertEqual(con.execute("SELECT v FROM t").fetchall(), [("nur-im-wal",)])
        con.close()

    def test_integrity_check_never_touches_the_checked_files(self):
        db = self.source / "x.db"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE t (v)")
        con.commit()
        con.close()
        before = {p.name: p.stat().st_mtime_ns for p in self.source.iterdir()}
        self.assertEqual(mig.sqlite_ok([db]), "ok")
        self.assertEqual({p.name: p.stat().st_mtime_ns for p in self.source.iterdir()}, before)

    def test_damaged_database_fails_verification(self):
        (self.source / "flaggenquiz.db").write_bytes(b"SQLite format 3\0" + b"\xff" * 2000)
        self.assertEqual(self.execute(), 1)
        self.assertEqual(pulse_paths.read_marker(self.target)["state"], "in_progress")


class RollbackWayTests(Base):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.execute(), 0)
        # Pulse ran in the new layout: data changed and grew, the checkout copies are outdated now.
        (self.target / "config.json").write_text('{"x": 2}', encoding="utf-8")
        (self.target / "uploads" / "welcome_banners").mkdir(parents=True)
        (self.target / "uploads" / "welcome_banners" / "b.png").write_bytes(b"png")

    def roll(self, *extra, state="exited"):
        return self.run_mig("--rollback", *extra, state=state)

    def test_dry_run_changes_nothing(self):
        before = {p: p.read_bytes() for p in self.source.rglob("*") if p.is_file()}
        self.assertEqual(self.roll(), 0)
        self.assertEqual({p: p.read_bytes() for p in self.source.rglob("*") if p.is_file()}, before)
        self.assertEqual(pulse_paths.read_marker(self.target)["state"], "complete")

    def test_needs_confirmation_and_stopped_container(self):
        self.assertEqual(self.roll("--execute", "--confirm", mig.CONFIRM_WORD), 3)  # wrong word
        self.assertEqual(self.roll("--execute", "--confirm", mig.ROLLBACK_WORD, state="running"), 2)
        self.assertEqual(json.loads((self.source / "config.json").read_text(encoding="utf-8")), {"x": 1})

    def test_way_back_restores_current_data_and_keeps_the_old_copies(self):
        self.assertEqual(self.roll("--execute", "--confirm", mig.ROLLBACK_WORD), 0)
        self.assertEqual(json.loads((self.source / "config.json").read_text(encoding="utf-8")), {"x": 2})
        self.assertEqual((self.source / "uploads" / "welcome_banners" / "b.png").read_bytes(), b"png")
        asides = list(self.source.glob(".pulse-legacy-*"))
        self.assertEqual(len(asides), 1)
        self.assertEqual(json.loads((asides[0] / "config.json").read_text(encoding="utf-8")), {"x": 1})
        self.assertEqual((self.source / ".env").read_text(encoding="utf-8"), "DISCORD_TOKEN=x")
        self.assertEqual(pulse_paths.read_marker(self.target)["state"], "rolled_back")
        self.assertTrue(pulse_paths.verify_data_dir(self.target))  # new layout refuses the outdated directory
        self.assertTrue((self.target / "config.json").exists())  # nothing deleted in the data directory
        self.assertEqual(len(list(self.backups.glob("pulse-prerollback-*.tar.gz"))), 2)

    def test_refused_without_valid_marker(self):
        pulse_paths.write_marker(self.target, "in_progress")
        self.assertEqual(self.roll("--execute", "--confirm", mig.ROLLBACK_WORD), 2)
        self.assertEqual(json.loads((self.source / "config.json").read_text(encoding="utf-8")), {"x": 1})


class DeployChainTests(unittest.TestCase):
    def test_every_root_script_takes_the_deploy_lock(self):
        for name in ("deploy/remote_deploy.sh", "deploy/rollback_code.sh", "scripts/pulse-vps-boot-update.sh",
                     "scripts/pulse-crash-restart.sh"):
            with self.subTest(script=name):
                script = read(name)
                self.assertIn("/run/lock/pulse-deploy.lock", script)
                self.assertIn("flock", script)

    def test_layout_switch_never_starts_the_old_layout_automatically(self):
        deploy = read("deploy/remote_deploy.sh")
        switch = deploy.index('if uses_data_dir "$SHA" && ! uses_data_dir "$PREV"; then')
        self.assertLess(switch, deploy.rindex("docker compose up -d --build"))
        block = deploy[switch:deploy.index("fi\n", deploy.index("exit 2", switch)) + 3]
        self.assertIn("docker compose stop pulse", block)
        self.assertIn("exit 2", block)
        self.assertNotIn("up -d", block)
        boot = read("scripts/pulse-vps-boot-update.sh")
        self.assertIn('if uses_data_dir "$target" && ! uses_data_dir "$previous"; then', boot)

    def test_build_information_on_every_build(self):
        boot = read("scripts/pulse-vps-boot-update.sh")
        main_part = boot.split('\ncd "$REPO_DIR"\n', 1)[1]
        self.assertLess(main_part.index('export PULSE_GIT_SHA="$(git rev-parse HEAD'), main_part.index("    start_current_version ||"))
        self.assertIn('export PULSE_GIT_SHA="$PREV"', read("deploy/rollback_code.sh"))

    def test_env_file_is_kept_private(self):
        self.assertIn("chmod go-rwx .env", read("deploy/remote_deploy.sh"))


class ComposeIntegrationConfigTests(unittest.TestCase):
    def test_ci_override_only_replaces_the_command(self):
        override = read("tests/compose.ci.yaml")
        for forbidden in ("volumes:", "read_only", "tmpfs", "mem_limit", "cap_drop", "security_opt", "ports:", "user:"):
            with self.subTest(key=forbidden):
                self.assertNotIn(forbidden, override)
        self.assertIn('command: ["python", "tests/compose_probe.py"]', override)

    def test_integration_script_covers_the_required_cases(self):
        script = read("tests/compose_integration.sh")
        for needle in ("fehlt", "for case in none in_progress damaged rolled_back", '= 78 ]', "touch /app/pulse_ci_probe",
                       "touch /data/ci-write-probe", "/proc/mounts", ".pulse-agent", "wait_healthy", "docker compose stop",
                       "shutdown ok", "PULSE_GIT_SHA", "id -u"):
            with self.subTest(needle=needle):
                self.assertIn(needle, script)
        self.assertNotIn("down -v", script)
        self.assertIn("bash tests/compose_integration.sh", read(".github/workflows/python-syntax.yml"))

    def test_probe_never_logs_into_discord(self):
        probe = read("tests/compose_probe.py")
        self.assertNotIn(".run(token", probe)
        self.assertNotIn("CustomBot", probe)
        self.assertNotIn("DISCORD_TOKEN", probe)


if __name__ == "__main__":
    unittest.main()
