"""D-01 / D-02 / D-04: complete backup roundtrip, safe restore and coverage of every runtime store."""
import json
import re
import sqlite3
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pulse_backup
import pulse_db

ROOT = Path(__file__).resolve().parents[1]


def make_db(path, rows):
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE IF NOT EXISTS t(id INTEGER PRIMARY KEY, v TEXT)")
    con.executemany("INSERT INTO t(v) VALUES (?)", [(r,) for r in rows])
    con.commit()
    return con  # kept open: snapshot must work with an open WAL connection


class FullBackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.base = Path(self.tmp.name) / "app"
        self.backups = Path(self.tmp.name) / "backups"
        self.base.mkdir()
        self.db_patch = patch.object(pulse_db, "DB_PATH", str(self.base / "pulse.db"))
        self.db_patch.start()
        self.cons = [make_db(self.base / "pulse.db", ["a", "b"]), make_db(self.base / "pulse_suite.db", ["x"])]
        (self.base / "team_data.json").write_text(json.dumps({"1": {"warns_list": [{"id": "w1"}]}}), encoding="utf-8")
        (self.base / "uploads" / "welcome_banners").mkdir(parents=True)
        (self.base / "uploads" / "welcome_banners" / "b.png").write_bytes(b"\x89PNG" + b"0" * 100)
        (self.base / "transcripts").mkdir()
        (self.base / "transcripts" / "T-1.txt").write_text("Verlauf", encoding="utf-8")
        (self.base / ".session_secret").write_text("geheim", encoding="utf-8")

    def tearDown(self):
        for con in self.cons:
            con.close()
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_roundtrip_restores_everything_identically(self):
        path, manifest = pulse_backup.create_full_backup(self.base, self.backups, discord_structure={"roles": []})
        names = {f["name"] for f in manifest["files"]}
        self.assertTrue({"db/pulse.db", "db/pulse_suite.db", "json/team_data.json", "discord_structure.json",
                         "files/uploads/welcome_banners/b.png", "files/transcripts/T-1.txt"} <= names)
        self.assertFalse(any(".session_secret" in n for n in names))
        self.assertEqual(pulse_backup.verify_full_backup(path), [])
        # change live data, then restore
        self.cons[0].execute("INSERT INTO t(v) VALUES ('neu')")
        self.cons[0].commit()
        (self.base / "team_data.json").write_text("{}", encoding="utf-8")
        (self.base / "transcripts" / "T-1.txt").unlink()
        preview = pulse_backup.preview_restore(path, self.base)
        self.assertIn({"db": "pulse.db", "table": "t", "current": 3, "backup": 2}, preview["tables"])
        self.assertIn("geändert", {f["status"] for f in preview["files"] if f["name"] == "json/team_data.json"})
        self.assertEqual((self.base / "team_data.json").read_text(encoding="utf-8"), "{}")  # preview writes nothing
        report = pulse_backup.restore_full_backup(path, self.base, self.backups)
        self.assertIn("pulse.db", report["databases"])
        self.assertTrue((self.backups / report["safety_backup"]).is_file())
        self.assertEqual(self.cons[0].execute("SELECT COUNT(*) FROM t").fetchone()[0], 2)  # live connection sees restored data
        self.assertEqual(json.loads((self.base / "team_data.json").read_text(encoding="utf-8"))["1"]["warns_list"][0]["id"], "w1")
        self.assertEqual((self.base / "transcripts" / "T-1.txt").read_text(encoding="utf-8"), "Verlauf")
        self.assertFalse(pulse_backup.maintenance_active())

    def test_uploaded_waiting_music_is_backed_up_and_restored(self):
        """The voice-support waiting music is uploaded in the dashboard to assets/support_wait.<ext> (pulse_pro) –
        local media that GitHub does not have, so a lost VPS must get it back from the backup."""
        (self.base / "assets").mkdir()
        music = self.base / "assets" / "support_wait.ogg"
        music.write_bytes(b"OggS" + b"1" * 200)
        (self.base / "assets" / "logo.png").write_bytes(b"\x89PNG")  # tracked asset: comes from git, not the backup
        path, manifest = pulse_backup.create_full_backup(self.base, self.backups)
        names = {f["name"] for f in manifest["files"]}
        self.assertIn("files/assets/support_wait.ogg", names)
        self.assertNotIn("files/assets/logo.png", names)
        music.unlink()
        report = pulse_backup.restore_full_backup(path, self.base, self.backups)
        self.assertTrue(report)
        self.assertEqual(music.read_bytes(), b"OggS" + b"1" * 200)

    def test_tampered_backup_is_rejected_before_any_change(self):
        path, _ = pulse_backup.create_full_backup(self.base, self.backups)
        tampered = self.backups / "pulse_full_20260101_000000.zip"
        with zipfile.ZipFile(path) as src, zipfile.ZipFile(tampered, "w") as dst:
            for item in src.infolist():
                data = src.read(item.filename)
                dst.writestr(item, b"{}" if item.filename == "json/team_data.json" else data)
        self.assertTrue(pulse_backup.verify_full_backup(tampered))
        with self.assertRaises(ValueError):
            pulse_backup.restore_full_backup(tampered, self.base, self.backups)
        self.assertIn("w1", (self.base / "team_data.json").read_text(encoding="utf-8"))

    def test_failure_while_writing_rolls_back_automatically(self):
        path, _ = pulse_backup.create_full_backup(self.base, self.backups)
        self.cons[0].execute("INSERT INTO t(v) VALUES ('nach dem backup')")
        self.cons[0].commit()
        real = pulse_backup.restore_sqlite_file
        calls = []

        def flaky(extracted, target):
            calls.append(target)
            if len(calls) == 2:  # second database of the backup fails (e.g. locked)
                raise OSError("gesperrt")
            return real(extracted, target)
        with patch.object(pulse_backup, "restore_sqlite_file", flaky):
            with self.assertRaises(ValueError) as ctx:
                pulse_backup.restore_full_backup(path, self.base, self.backups)
        self.assertIn("zurückgespielt", str(ctx.exception))
        # rollback restored the state from right before the restore (3 rows), not the backup (2)
        self.assertEqual(self.cons[0].execute("SELECT COUNT(*) FROM t").fetchone()[0], 3)
        self.assertFalse(pulse_backup.maintenance_active())

    def test_kind_comes_from_path_not_manifest(self):
        path, _ = pulse_backup.create_full_backup(self.base, self.backups)
        forged = self.backups / "pulse_full_20260102_000000.zip"
        with zipfile.ZipFile(path) as src, zipfile.ZipFile(forged, "w") as dst:
            for item in src.infolist():
                data = src.read(item.filename)
                if item.filename == "manifest.json":
                    manifest = json.loads(data)
                    for entry in manifest["files"]:
                        entry["kind"] = "file"
                    data = json.dumps(manifest).encode()
                dst.writestr(item, data)
        self.cons[0].execute("INSERT INTO t(v) VALUES ('x')")
        self.cons[0].commit()
        report = pulse_backup.restore_full_backup(forged, self.base, self.backups)
        self.assertIn("pulse.db", report["databases"])  # still restored through the SQLite backup API
        self.assertEqual(self.cons[0].execute("SELECT COUNT(*) FROM t").fetchone()[0], 2)

    def test_safety_copies_are_thinned_out(self):
        for _ in range(5):
            pulse_backup.create_full_backup(self.base, self.backups, prefix="pre_restore_")
        pulse_backup.create_full_backup(self.base, self.backups)  # manual – never pruned
        pulse_backup.prune_auto_backups(self.backups)
        names = [p.name for p in pulse_backup.list_backups(self.backups)]
        self.assertEqual(sum("pre_restore_" in n for n in names), pulse_backup.SAFETY_COPIES_KEPT)
        self.assertEqual(sum("pre_restore_" not in n for n in names), 1)

    def test_size_budget_is_visible_not_silent(self):
        _, manifest = pulse_backup.create_full_backup(self.base, self.backups, budget_bytes=50)
        self.assertTrue(manifest["skipped"])
        self.assertTrue(any("Größenbudget" in w for w in manifest["warnings"]))

    def _rewrite(self, path, replace: dict[str, bytes]):
        """Copy the archive with some entries replaced and the manifest hashes updated to match."""
        import hashlib
        out = path.with_name("pulse_full_20260101_000000.zip")
        with zipfile.ZipFile(path) as src, zipfile.ZipFile(out, "w") as dst:
            manifest = json.loads(src.read("manifest.json"))
            for name in src.namelist():
                if name != "manifest.json" and name not in replace:
                    dst.writestr(name, src.read(name))
            for name, data in replace.items():
                dst.writestr(name, data)
                entry = next((f for f in manifest["files"] if f["name"] == name), None)
                if entry is None:
                    entry = {"name": name, "kind": "file"}
                    manifest["files"].append(entry)
                entry["sha256"] = hashlib.sha256(data).hexdigest()
                entry["size"] = len(data)
            dst.writestr("manifest.json", json.dumps(manifest))
        return out

    def test_trial_restore_restores_everything_in_isolation(self):
        path, _ = pulse_backup.create_full_backup(self.base, self.backups)
        live = self.base / "pulse.db"
        before = (live.stat().st_mtime_ns, pulse_backup._sha256(live))
        result = pulse_backup.restore_test(path)
        self.assertTrue(result["ok"], result["problems"])
        self.assertEqual((result["databases"], result["json"], result["files"]), (2, 1, 2))
        self.assertEqual(result["rows"], 3)  # pulse.db: a, b · pulse_suite.db: x
        self.assertEqual((live.stat().st_mtime_ns, pulse_backup._sha256(live)), before, "Live-Datenbank verändert")

    def test_trial_restore_finds_damaged_content(self):
        path, _ = pulse_backup.create_full_backup(self.base, self.backups)
        cases = {
            "json/team_data.json": b"{kaputt",
            "db/pulse_suite.db": b"SQLite format 3\x00" + b"\x00" * 200,
            "files/secret/x.txt": b"ohne Ziel",
        }
        for name, data in cases.items():
            with self.subTest(entry=name):
                result = pulse_backup.restore_test(self._rewrite(path, {name: data}))
                self.assertFalse(result["ok"])
                self.assertTrue(any(name in p for p in result["problems"]), result["problems"])
        truncated = path.with_name("pulse_full_20260102_000000.zip")
        truncated.write_bytes(path.read_bytes()[:200])
        self.assertFalse(pulse_backup.restore_test(truncated)["ok"])

    def test_file_vanishing_during_backup_is_reported_not_fatal(self):
        """Phase 8: an upload deleted between listing and copying (retention, ticket cleanup) must not abort the
        whole backup – it is listed as skipped with a warning, everything else is still backed up."""
        gone = self.base / "uploads" / "welcome_banners" / "b.png"
        real_open = open

        def vanishing_open(file, *args, **kwargs):
            if Path(str(file)) == gone:
                raise FileNotFoundError(file)
            return real_open(file, *args, **kwargs)
        with patch("builtins.open", vanishing_open):
            path, manifest = pulse_backup.create_full_backup(self.base, self.backups)
        names = {f["name"] for f in manifest["files"]}
        self.assertIn("db/pulse.db", names)
        self.assertNotIn("files/uploads/welcome_banners/b.png", names)
        self.assertIn({"name": "files/uploads/welcome_banners/b.png", "reason": "nicht lesbar"},
                      [{k: s[k] for k in ("name", "reason")} for s in manifest["skipped"]])
        self.assertTrue(any("nicht lesbar" in w for w in manifest["warnings"]))
        self.assertEqual(pulse_backup.verify_full_backup(path), [])

    def test_failed_backup_leaves_no_partial_file(self):
        """Phase 8: a backup that fails part-way (disk full, unreadable file) must not leave a *.zip.tmp behind –
        such files were never listed, never pruned and silently used up the disk."""
        with patch.object(pulse_backup, "_snapshot_sqlite", side_effect=OSError("Kein Speicherplatz")):
            with self.assertRaises(OSError):
                pulse_backup.create_full_backup(self.base, self.backups)
        self.assertEqual(sorted(p.name for p in self.backups.iterdir()), [])

    def test_preview_of_damaged_backup_reports_problems_instead_of_crashing(self):
        """Phase 8: the dry run must show *why* a backup is unusable (restore stays locked) – a damaged database
        with a matching checksum or an entry missing from the archive used to raise DatabaseError/KeyError (HTTP 500)."""
        path, _ = pulse_backup.create_full_backup(self.base, self.backups)
        damaged = self._rewrite(path, {"db/pulse_suite.db": b"SQLite format 3\x00" + b"\x00" * 200})
        preview = pulse_backup.preview_restore(damaged, self.base)
        self.assertTrue(any("pulse_suite.db" in p for p in preview["problems"]), preview["problems"])
        missing = path.with_name("pulse_full_20260103_000000.zip")
        with zipfile.ZipFile(path) as src, zipfile.ZipFile(missing, "w") as dst:
            for item in src.infolist():
                if item.filename != "db/pulse.db":
                    dst.writestr(item, src.read(item.filename))
        preview = pulse_backup.preview_restore(missing, self.base)
        self.assertTrue(any("db/pulse.db" in p for p in preview["problems"]), preview["problems"])
        self.assertEqual(self.cons[0].execute("SELECT COUNT(*) FROM t").fetchone()[0], 2)  # nothing live touched

    def test_restore_test_and_maintenance_blocks_json_writes(self):
        path, _ = pulse_backup.create_full_backup(self.base, self.backups)
        self.assertTrue(pulse_backup.restore_test(path)["ok"])
        import webserver as ws
        pulse_backup._MAINTENANCE.set()
        try:
            with self.assertRaises(ws.JsonWriteBlocked):
                ws.save_json(str(self.base / "x.json"), {})
        finally:
            pulse_backup._MAINTENANCE.clear()

    def test_retention_scheme(self):
        now = datetime(2026, 10, 20, 3, 0)
        items = [(f"pulse_full_auto_{i}.zip", now - timedelta(days=i)) for i in range(0, 200)]
        keep = pulse_backup.retention_keep(items)
        self.assertTrue(all(f"pulse_full_auto_{i}.zip" in keep for i in range(7)))  # 7 daily
        self.assertLessEqual(len(keep), 7 + 4 + 6)
        self.assertIn(max(keep, key=lambda n: int(re.search(r"\d+", n).group())), keep)


class FullBackupRouteTests(unittest.TestCase):
    def setUp(self):
        import webserver as ws
        from tests.test_pulse_admin_area import ADMIN, TEAMLER, fake_auth_for
        self.ws, self.ADMIN, self.TEAMLER, self.fake_auth_for = ws, ADMIN, TEAMLER, fake_auth_for
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        base = Path(self.tmp.name)
        (base / "team_data.json").write_text('{"1": {}}', encoding="utf-8")
        self.patches = [patch.object(ws, "BASE_DIR", base), patch.object(ws, "BACKUP_DIR", str(base / "backups")),
                        patch.object(pulse_db, "DB_PATH", str(base / "pulse.db")),
                        patch.object(ws, "build_discord_structure", lambda guild, icon=None: {"roles": []}),
                        patch.object(ws, "log_audit", lambda *a, **k: None)]
        for p in self.patches:
            p.start()
        pulse_db.init_db()
        from fastapi.testclient import TestClient
        self.client = TestClient(ws.app)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def test_admin_only_dry_run_and_confirmed_restore(self):
        ws = self.ws
        with patch.object(ws, "auth", self.fake_auth_for(self.TEAMLER)):
            self.assertEqual(self.client.post("/backup/full/create", follow_redirects=False).status_code, 403)
        with patch.object(ws, "auth", self.fake_auth_for(self.ADMIN)):
            created = self.client.post("/backup/full/create", follow_redirects=False)
            self.assertIn("t=ok", created.headers["location"])
            name = pulse_backup.list_backups(ws.full_backup_dir())[0].name
            self.assertIn(name, self.client.get("/backups").text)
            (Path(self.tmp.name) / "team_data.json").write_text("{}", encoding="utf-8")
            preview = self.client.get(f"/backup/full/preview/{name}")
            self.assertIn("json/team_data.json", preview.text)
            self.assertEqual((Path(self.tmp.name) / "team_data.json").read_text(encoding="utf-8"), "{}")
            refused = self.client.post("/backup/full/restore", data={"filename": name, "confirm": "ja"}, follow_redirects=False)
            self.assertIn("t=err", refused.headers["location"])
            done = self.client.post("/backup/full/restore", data={"filename": name, "confirm": "WIEDERHERSTELLEN"}, follow_redirects=False)
            self.assertIn("t=ok", done.headers["location"])
            self.assertEqual(self.client.get("/backup/full/download/..%2Fteam_data.json").status_code, 404)
        self.assertEqual((Path(self.tmp.name) / "team_data.json").read_text(encoding="utf-8"), '{"1": {}}')

    def test_preview_route_locks_restore_for_damaged_backup(self):
        """Phase 8: the dry run page shows the problem and offers no restore form (previously HTTP 500)."""
        import hashlib
        ws = self.ws
        bad = b"SQLite format 3\x00" + b"\x00" * 200
        con = sqlite3.connect(Path(self.tmp.name) / "pulse.db")
        con.close()
        with patch.object(ws, "auth", self.fake_auth_for(self.ADMIN)):
            self.client.post("/backup/full/create", follow_redirects=False)
            good = pulse_backup.list_backups(ws.full_backup_dir())[0]
            damaged = good.with_name("pulse_full_20260101_000000.zip")
            with zipfile.ZipFile(good) as src, zipfile.ZipFile(damaged, "w") as dst:
                manifest = json.loads(src.read("manifest.json"))
                for name in src.namelist():
                    if name not in ("manifest.json", "db/pulse.db"):
                        dst.writestr(name, src.read(name))
                dst.writestr("db/pulse.db", bad)
                for entry in manifest["files"]:
                    if entry["name"] == "db/pulse.db":
                        entry["sha256"] = hashlib.sha256(bad).hexdigest()
                dst.writestr("manifest.json", json.dumps(manifest))
            page = self.client.get(f"/backup/full/preview/{damaged.name}", follow_redirects=False)
        self.assertEqual(page.status_code, 200)
        self.assertIn("Wiederherstellung gesperrt", page.text)
        self.assertIn("db/pulse.db", page.text)
        self.assertNotIn('action="/backup/full/restore"', page.text)

    def test_json_backup_reads_discord_in_loop_and_files_in_thread(self):
        import asyncio
        import threading
        seen = {}

        def structure(guild, icon=None):
            seen["structure"] = threading.current_thread() is threading.main_thread()
            return {"panel_data": {}}

        def panel(data):
            seen["panel"] = threading.current_thread() is threading.main_thread()
            return data
        guild = type("G", (), {"icon": None})()
        Path(self.ws.BACKUP_DIR).mkdir(parents=True, exist_ok=True)
        with patch.object(self.ws, "build_discord_structure", structure), patch.object(self.ws, "add_panel_data", panel):
            asyncio.run(self.ws.create_backup_file_async(guild))
        self.assertEqual(seen, {"structure": True, "panel": False})

    def test_auto_run_creates_backup_and_weekly_restore_test(self):
        import asyncio
        with patch("builtins.print"):
            asyncio.run(self.ws.run_auto_full_backup(None))
        autos = [p.name for p in pulse_backup.list_backups(self.ws.full_backup_dir()) if "_auto_" in p.name]
        self.assertEqual(len(autos), 1)
        status = self.ws.full_backup_status()
        self.assertTrue(status["restore_test"]["ok"])
        self.assertEqual(status["latest"], autos[0])


class CoverageTests(unittest.TestCase):
    """A new runtime file (*.db / *.json literal in the code) must be added to the backup or excluded on purpose."""

    IGNORED = {"manifest.json", "discord_structure.json", "routen_snapshot.json", "package.json", "manifest.webmanifest"}

    def test_every_runtime_store_is_in_the_backup(self):
        found = set()
        for path in ROOT.rglob("*.py"):
            parts = set(path.relative_to(ROOT).parts)
            if parts & {"tests", "tools", "deps", "venv", ".venv", ".git"}:
                continue
            for match in re.finditer(r"""["']([A-Za-z0-9_.-]+\.(?:db|json))["']""", path.read_text(encoding="utf-8")):
                name = match.group(1)
                if name.startswith(".") or "restore" in name or "tmp" in name:
                    continue
                found.add(name)
        covered = set(pulse_backup.SQLITE_FILES) | set(pulse_backup.JSON_FILES) | self.IGNORED
        self.assertEqual(sorted(found - covered), [], "neue Laufzeitdatei ohne Backup-Eintrag (pulse_backup.py)")


if __name__ == "__main__":
    unittest.main()
