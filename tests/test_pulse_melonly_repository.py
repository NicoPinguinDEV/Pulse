import copy
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from pulse_melonly_repository import JsonMelonlyRepository, MelonlyBackendError, MelonlyRepository
from pulse_melonly_store import MelonlyStore


class MemoryJson:
    def __init__(self, rows=None):
        self.rows = copy.deepcopy(rows or [])

    def load(self, _path, default):
        return copy.deepcopy(self.rows) if self.rows is not None else default

    def save(self, _path, rows):
        self.rows = copy.deepcopy(rows)


def make_sqlite_backend(root: Path, rows):
    db_path = root / "pulse.db"
    store = MelonlyStore(db_path)
    store.initialize()
    store.import_entries(rows, source_sha256="test-fixture")
    # The adapter must never do this itself. The fixture represents a separately
    # reviewed and explicitly activated deployment.
    with sqlite3.connect(db_path) as con:
        con.execute("UPDATE melonly_logs_meta SET value='sqlite' WHERE key='active_backend'")
    return db_path, store


class MelonlyRepositoryParityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.rows = [
            {"id": "duplicate", "target_user": "Alpha", "roblox_id": "100", "type": "Warn",
             "reason": "Erster Eintrag", "created_at": "old-format", "unknown": {"preserve": True}},
            {"id": "duplicate", "target_user": "Beta", "roblox_id": "200", "type": "Ban",
             "reason": "Zweiter Eintrag"},
            {"target_user": "AlphaAlt", "roblox_id": "100", "type": "Kick", "reason": "ID-Treffer"},
            {"id": "missing-id", "target_user": "Alpha", "roblox_id": "300", "type": "Notiz",
             "reason": "Unicode: äöü"},
            {"id": "unexpected", "target_user": "Unknown", "roblox_id": "N/A", "type": "Legacy",
             "reason": "Unbekannter Typ"},
            {"id": "legacy-int-type", "target_user": "OldFormat", "roblox_id": "999",
             "type": 1, "reason": "Legacy numeric action type"},
        ]
        self.memory = MemoryJson(self.rows)
        self.lock = threading.RLock()
        self.db_path = self.root / "pulse.db"
        self.store = MelonlyStore(self.db_path)
        self.store.initialize()
        self.store.import_entries(self.rows, source_sha256="test-fixture")
        common = {
            "json_path": self.root / "logs.json",
            "db_path": self.db_path,
            "load_json": self.memory.load,
            "save_json": self.memory.save,
            "io_lock": self.lock,
        }
        # JSON selection must be constructed while the database marker still says JSON.
        self.json_repo = MelonlyRepository(**common, environ={})
        with sqlite3.connect(self.db_path) as con:
            con.execute("UPDATE melonly_logs_meta SET value='sqlite' WHERE key='active_backend'")
        self.sqlite_repo = MelonlyRepository(**common, environ={"PULSE_LOGS_BACKEND": "sqlite"})

    def test_backend_selection_is_opt_in_and_does_not_create_a_database(self):
        root = self.root / "fresh"
        root.mkdir()
        memory = MemoryJson([{"id": "json-only"}])
        repo = MelonlyRepository(
            json_path=root / "logs.json", db_path=root / "pulse.db",
            load_json=memory.load, save_json=memory.save, environ={}
        )
        self.assertEqual(repo.backend_name, "json")
        self.assertEqual(repo.export_all(), [{"id": "json-only"}])
        self.assertFalse((root / "pulse.db").exists())

    def test_sqlite_selection_requires_matching_active_marker(self):
        with self.assertRaises(MelonlyBackendError):
            MelonlyRepository(
                json_path=self.root / "logs.json", db_path=self.db_path,
                load_json=self.memory.load, save_json=self.memory.save, environ={}
            )
        with self.assertRaises(MelonlyBackendError):
            MelonlyRepository(
                json_path=self.root / "logs.json", db_path=self.db_path,
                load_json=self.memory.load, save_json=self.memory.save,
                environ={"PULSE_LOGS_BACKEND": "postgres"}
            )

    def test_read_contract_is_equivalent_between_json_and_sqlite(self):
        methods = (
            ("count", ()),
            ("count_by_type", ()),
            ("latest", (3,)),
            ("latest_matching_types", (("Warn", "Ban", "Kick", "Notiz", "Ban BOLO"), 3)),
            ("export_all", ()),
            ("player_history", ("100",)),
            ("player_history", ("@Alpha",)),
            ("player_history", ("lph",)),
            ("roblox_user_history", ("100", "Alpha")),
            ("roblox_user_history", ("999", "Beta")),
            ("roblox_suggestions", (3,)),
            ("search_payload", ("ERSTER",)),
            ("search_payload", ("unicode",)),
        )
        for method, args in methods:
            with self.subTest(method=method, args=args):
                self.assertEqual(getattr(self.json_repo, method)(*args),
                                 getattr(self.sqlite_repo, method)(*args))

    def test_crud_roundtrip_and_duplicate_id_behavior_are_equivalent(self):
        for repo in (self.json_repo, self.sqlite_repo):
            created = {"id": "new", "target_user": "NewPlayer", "roblox_id": "400",
                       "type": "Warn", "reason": "Test", "unknown_field": [1, 2]}
            repo.append(created)
            found = repo.find_first_by_id("duplicate")
            self.assertIsNotNone(found)
            seq, entry = found
            entry = dict(entry)
            entry["reason"] = "Geändert"
            self.assertTrue(repo.update_by_seq(seq, entry))
            self.assertEqual(repo.find_first_by_id("duplicate")[1]["reason"], "Geändert")
            self.assertEqual(repo.delete_all_by_id("duplicate"), 2)
            self.assertIsNone(repo.find_first_by_id("duplicate"))
        self.assertEqual(self.json_repo.export_all(), self.sqlite_repo.export_all())

    def test_numeric_id_is_not_coerced_to_form_string_for_edit_or_delete(self):
        rows = [{"id": 123, "target_user": "Numeric"}, {"id": "123", "target_user": "Text"}]
        memory = MemoryJson(rows)
        json_repo = MelonlyRepository(
            json_path=self.root / "numeric.json", db_path=self.root / "numeric.db",
            load_json=memory.load, save_json=memory.save, environ={}
        )
        numeric_db, store = make_sqlite_backend(self.root / "numeric-db", rows)
        # use a separate path for this fixture and emulate an already-active SQLite backend
        with sqlite3.connect(numeric_db) as con:
            con.execute("UPDATE melonly_logs_meta SET value='sqlite' WHERE key='active_backend'")
        sqlite_repo = MelonlyRepository(
            json_path=self.root / "numeric.json", db_path=numeric_db,
            load_json=memory.load, save_json=memory.save, environ={"PULSE_LOGS_BACKEND": "sqlite"}
        )
        for repo in (json_repo, sqlite_repo):
            self.assertEqual(repo.find_first_by_id("123"), (2, rows[1]))
            self.assertEqual(repo.delete_all_by_id("123"), 1)
            self.assertEqual(repo.export_all(), [rows[0]])

    def test_action_type_filter_does_not_coerce_legacy_numeric_values(self):
        self.assertEqual(self.json_repo.latest_matching_types(("1",), 10), [])
        self.assertEqual(self.sqlite_repo.latest_matching_types(("1",), 10), [])
        self.assertEqual(
            self.json_repo.latest_matching_types((1,), 10),
            self.sqlite_repo.latest_matching_types((1,), 10),
        )

    def test_page_filter_is_newest_matching_entries_not_newest_raw_rows(self):
        for repo in (self.json_repo, self.sqlite_repo):
            got = repo.latest_matching_types(("Warn", "Kick"), 2)
            self.assertEqual([x["type"] for x in got], ["Kick", "Warn"])
            self.assertEqual(got[0]["target_user"], "AlphaAlt")

    def test_explicit_json_rollback_exports_and_verifies_before_switching_marker(self):
        stale_json = self.root / "logs.json"
        stale_json.write_text(json.dumps([{"id": "stale"}]), encoding="utf-8")
        rolled_back = MelonlyRepository(
            json_path=stale_json, db_path=self.db_path,
            load_json=self.memory.load, save_json=self.memory.save,
            io_lock=self.lock, environ={"PULSE_LOGS_BACKEND": "json"}
        )
        self.assertEqual(rolled_back.backend_name, "json")
        self.assertEqual(json.loads(stale_json.read_text(encoding="utf-8")), self.rows)
        self.assertEqual(self.store.metadata()["active_backend"], "json")
        backups = list(self.root.glob("logs_pre_sqlite_*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text(encoding="utf-8")), [{"id": "stale"}])


class MelonlyActiveRouteTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        import pulse_db
        import webserver as ws
        from fastapi.testclient import TestClient
        from types import SimpleNamespace

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.logs_path = root / "logs.json"
        self.db_path = root / "pulse.db"
        self.patches = [
            patch.object(ws, "LOGS_FILE", str(self.logs_path)),
            patch.object(pulse_db, "DB_PATH", str(self.db_path)),
            patch.dict(os.environ, {"PULSE_LOGS_BACKEND": ""}),
            patch.object(ws, "log_audit", lambda *args, **kwargs: None),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.ctx = SimpleNamespace(
            perms={"can_warn": True, "can_add_notes": True, "can_moderate_kicks": True,
                   "can_moderate_bans": True, "can_moderate_bolo": True, "can_view_analytics": True},
            user={"id": "123", "global_name": "Tester", "username": "tester"},
        )
        self.auth_patch = patch.object(ws, "auth", return_value=self.ctx)
        self.auth_patch.start()
        self.addCleanup(self.auth_patch.stop)
        self.ws = ws
        self.client = TestClient(ws.app)

    def test_get_melonly_repository_reuses_snapshot_owner_for_same_runtime_config(self):
        first = self.ws.get_melonly_repository()
        second = self.ws.get_melonly_repository()
        self.assertIs(first, second)
        self.assertEqual(first.backend_name, "json")

    def test_create_edit_and_delete_routes_use_the_json_repository(self):
        created = self.client.post("/log/create", data={
            "target_user": "PlayerOne", "roblox_id": "12345", "log_type": "Warn",
            "reason": "Initial reason", "return_to": "/melonly",
        }, follow_redirects=False)
        self.assertEqual(created.status_code, 303, created.text)
        entries = self.ws.load_json(str(self.logs_path), [])
        self.assertEqual(len(entries), 1)
        log_id = entries[0]["id"]
        self.assertEqual(entries[0]["reason"], "Initial reason")

        edited = self.client.post("/log/edit", data={
            "log_id": log_id, "target_user": "PlayerOne", "roblox_id": "12345",
            "log_type": "Warn", "reason": "Updated reason", "return_to": "/melonly",
        }, follow_redirects=False)
        self.assertEqual(edited.status_code, 303, edited.text)
        entries = self.ws.load_json(str(self.logs_path), [])
        self.assertEqual(entries[0]["reason"], "Updated reason")
        self.assertEqual(entries[0]["edited_by"], "Tester")

        deleted = self.client.post("/log/delete", data={
            "log_id": log_id, "return_to": "/melonly",
        }, follow_redirects=False)
        self.assertEqual(deleted.status_code, 303, deleted.text)
        self.assertEqual(self.ws.load_json(str(self.logs_path), []), [])

    def test_crud_routes_use_explicitly_activated_sqlite_backend(self):
        from unittest.mock import patch

        store = MelonlyStore(self.db_path)
        store.initialize()
        store.import_entries([], source_sha256="empty-route-fixture")
        with sqlite3.connect(self.db_path) as con:
            con.execute("UPDATE melonly_logs_meta SET value='sqlite' WHERE key='active_backend'")

        with patch.dict(os.environ, {"PULSE_LOGS_BACKEND": "sqlite"}):
            created = self.client.post("/log/create", data={
                "target_user": "SQLitePlayer", "roblox_id": "98765", "log_type": "Warn",
                "reason": "SQLite create", "return_to": "/melonly",
            }, follow_redirects=False)
            self.assertEqual(created.status_code, 303, created.text)
            entries = store.export_all()
            self.assertEqual(len(entries), 1)
            log_id = entries[0]["id"]

            edited = self.client.post("/log/edit", data={
                "log_id": log_id, "target_user": "SQLitePlayer", "roblox_id": "98765",
                "log_type": "Warn", "reason": "SQLite edit", "return_to": "/melonly",
            }, follow_redirects=False)
            self.assertEqual(edited.status_code, 303, edited.text)
            self.assertEqual(store.find_first_by_id(log_id)[1]["reason"], "SQLite edit")

            deleted = self.client.post("/log/delete", data={
                "log_id": log_id, "return_to": "/melonly",
            }, follow_redirects=False)
            self.assertEqual(deleted.status_code, 303, deleted.text)
            self.assertEqual(store.export_all(), [])

    def test_csv_bytes_keep_the_existing_bom_header_columns_and_row_order(self):
        import csv
        import io
        from unittest.mock import patch
        rows = [
            {"id": "1", "created_at": "01.11.2026 10:00", "type": "Warn",
             "target_user": "=Formula", "roblox_id": "123", "moderator": "A", "reason": "Erste Zeile"},
            {"id": "2", "created_at": "01.11.2026 11:00", "type": "Ban",
             "target_user": "PlayerTwo", "roblox_id": "456", "moderator": "B", "reason": "Zweite Zeile"},
        ]
        self.ws.save_json(str(self.logs_path), rows)
        response_json = self.client.get("/export/logs")
        self.assertEqual(response_json.status_code, 200)
        output = io.StringIO()
        output.write("\ufeff")
        writer = csv.writer(output, delimiter=";")
        writer.writerow(["ID", "Datum", "Typ", "Ziel-Nutzer", "Roblox ID", "Moderator", "Grund"])
        writer.writerow(["1", rows[0]["created_at"], "Warn", "'=Formula", "123", "A", "Erste Zeile"])
        writer.writerow(["2", rows[1]["created_at"], "Ban", "PlayerTwo", "456", "B", "Zweite Zeile"])
        self.assertEqual(response_json.content.decode("utf-8"), output.getvalue())
        self.assertEqual(response_json.headers["content-type"], "text/csv; charset=utf-8")

        store = MelonlyStore(self.db_path)
        store.initialize()
        store.import_entries(rows, source_sha256="csv-fixture")
        with sqlite3.connect(self.db_path) as con:
            con.execute("UPDATE melonly_logs_meta SET value='sqlite' WHERE key='active_backend'")
        with patch.dict(os.environ, {"PULSE_LOGS_BACKEND": "sqlite"}):
            response_sqlite = self.client.get("/export/logs")
        self.assertEqual(response_sqlite.status_code, 200)
        self.assertEqual(response_sqlite.content, response_json.content)
        self.assertEqual(response_sqlite.headers["content-type"], response_json.headers["content-type"])





class MelonlyJsonSnapshotCacheTests(unittest.TestCase):
    def test_snapshot_cache_reuses_reads_invalidates_external_replace_and_returns_copies(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "logs.json"
            initial = [{"id": "one", "target_user": "Alpha", "roblox_id": "100",
                        "reason": "Initial", "extra": {"keep": True}}]
            path.write_text(json.dumps(initial, ensure_ascii=False), encoding="utf-8")
            calls = {"loads": 0, "saves": 0}

            def load_json(source, default):
                calls["loads"] += 1
                try:
                    return json.loads(Path(source).read_text(encoding="utf-8"))
                except FileNotFoundError:
                    return default

            def save_json(target, rows):
                calls["saves"] += 1
                temporary_path = Path(str(target) + ".tmp")
                temporary_path.write_text(
                    json.dumps(rows, ensure_ascii=False), encoding="utf-8"
                )
                os.replace(temporary_path, target)

            repo = JsonMelonlyRepository(path, load_json, save_json, threading.RLock())
            first = repo.export_all()
            self.assertEqual(repo.count(), 1)
            self.assertEqual(repo.search_payload("initial")[0]["id"], "one")
            self.assertEqual(calls["loads"], 1)

            first[0]["extra"]["keep"] = False
            first[0]["reason"] = "caller mutation"
            self.assertTrue(repo.export_all()[0]["extra"]["keep"])
            self.assertEqual(repo.export_all()[0]["reason"], "Initial")
            self.assertEqual(calls["loads"], 1)

            replacement = [{"id": "two", "target_user": "Beta", "roblox_id": "200",
                            "reason": "External replacement"}]
            replacement_path = Path(temporary) / "replacement.json"
            replacement_path.write_text(json.dumps(replacement), encoding="utf-8")
            os.replace(replacement_path, path)
            self.assertEqual(repo.export_all(), replacement)
            self.assertEqual(calls["loads"], 2)

            repo.append({"id": "three", "target_user": "Gamma", "roblox_id": "300"})
            self.assertEqual(repo.count(), 2)
            self.assertEqual(calls["loads"], 2)
            self.assertEqual(calls["saves"], 1)

    def test_cached_lookup_indexes_preserve_roblox_history_and_partial_name_order(self):
        rows = [
            {"id": "1", "target_user": "@Alpha", "roblox_id": " 100 ", "reason": "padded"},
            {"id": "2", "target_user": "AlphaAlt", "roblox_id": "100", "reason": "normal"},
            {"id": "3", "target_user": "Beta", "roblox_id": 0, "reason": "numeric zero"},
            {"id": "4", "target_user": "Zero", "roblox_id": "0", "reason": "string zero"},
            {"id": "5", "target_user": None, "roblox_id": None, "reason": "explicit null"},
            {"id": "6", "roblox_id": "50", "reason": "missing name"},
            {"id": "7", "target_user": "AlphaPartial", "roblox_id": "300", "reason": "partial"},
        ]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "logs.json"
            path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
            repo = JsonMelonlyRepository(
                path,
                lambda source, default: json.loads(Path(source).read_text(encoding="utf-8")),
                lambda target, data: Path(target).write_text(json.dumps(data), encoding="utf-8"),
            )
            self.assertEqual([x["id"] for x in repo.player_history("100")], ["1", "2"])
            self.assertEqual([x["id"] for x in repo.player_history("lph")], ["1", "2", "7"])
            self.assertEqual([x["id"] for x in repo.player_history("0")], ["4"])
            self.assertEqual(
                [x["id"] for x in repo.roblox_user_history("None", "none")],
                ["5"],
            )


class MelonlySqliteIndexedHistoryTests(unittest.TestCase):
    def test_normalized_id_expression_index_exists_and_is_used(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows = [
                {"id": f"row-{i}", "target_user": f"Spieler-{i % 100:03d}",
                 "roblox_id": f"{1000 + i % 500}", "type": "Warn"}
                for i in range(5000)
            ]
            _db_path, store = make_sqlite_backend(root, rows)
            with sqlite3.connect(store.db_path) as con:
                plan = con.execute(
                    """EXPLAIN QUERY PLAN
                       SELECT seq FROM melonly_logs
                       WHERE pulse_roblox_id_key(roblox_id)=?""",
                    ("1001",),
                ).fetchall()
            self.assertTrue(
                any("idx_melonly_logs_player_id_key" in str(row[3]) for row in plan),
                plan,
            )

    def test_sqlite_history_is_payload_compatible_for_legacy_edge_values(self):
        rows = [
            {"id": "1", "target_user": "@Alpha", "roblox_id": " 100 ", "reason": "padded"},
            {"id": "2", "target_user": "AlphaAlt", "roblox_id": "100", "reason": "normal"},
            {"id": "3", "target_user": "Beta", "roblox_id": 0, "reason": "numeric zero"},
            {"id": "4", "target_user": "Zero", "roblox_id": "0", "reason": "string zero"},
            {"id": "5", "target_user": None, "roblox_id": None, "reason": "explicit null"},
            {"id": "6", "roblox_id": "50", "reason": "missing name"},
            {"id": "7", "target_user": "AlphaPartial", "roblox_id": "300", "reason": "partial"},
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            memory = MemoryJson(rows)
            db_path, store = make_sqlite_backend(root / "sqlite", rows)
            lock = threading.RLock()
            json_repo = JsonMelonlyRepository(
                root / "logs.json", memory.load, memory.save, lock
            )
            with sqlite3.connect(db_path) as con:
                con.execute("UPDATE melonly_logs_meta SET value='sqlite' WHERE key='active_backend'")
            common = {
                "json_path": root / "logs.json",
                "db_path": db_path,
                "load_json": memory.load,
                "save_json": memory.save,
                "io_lock": lock,
            }
            sqlite_repo = MelonlyRepository(
                **common, environ={"PULSE_LOGS_BACKEND": "sqlite"}
            )
            for query in ("100", "lph", "0", "none", "300"):
                with self.subTest(query=query):
                    self.assertEqual(
                        json_repo.player_history(query),
                        sqlite_repo.player_history(query),
                    )
            for roblox_id, username in (
                (" 100 ", "Alpha"),
                ("100", "alphaalt"),
                ("None", "none"),
                ("50", ""),
                ("0", "zero"),
            ):
                with self.subTest(roblox_id=roblox_id, username=username):
                    self.assertEqual(
                        json_repo.roblox_user_history(roblox_id, username),
                        sqlite_repo.roblox_user_history(roblox_id, username),
                    )


if __name__ == "__main__":
    unittest.main()
