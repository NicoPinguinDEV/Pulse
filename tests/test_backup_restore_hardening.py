"""Regression tests for bounded, stable and checksum-verified backup restore staging."""
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import pulse_backup
import pulse_db
from tests.runtime_isolation import setUpModule, tearDownModule  # noqa: F401


class RestoreHardeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.base = root / "data"
        self.backups = root / "backups"
        self.base.mkdir()
        self.backups.mkdir()
        self.db_patch = patch.object(pulse_db, "DB_PATH", str(self.base / "pulse.db"))
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.archive, _manifest = pulse_backup.create_full_backup(
            self.base, self.backups, discord_structure={"roles": []}
        )

    def test_zip_entry_limit_is_checked_before_manifest_processing(self):
        with patch.object(pulse_backup, "MAX_RESTORE_ENTRIES", 1):
            problems = pulse_backup.verify_full_backup(self.archive)
        self.assertTrue(any("Zu viele ZIP-Einträge" in problem for problem in problems))

    def test_uncompressed_total_size_limit_rejects_archive(self):
        with patch.object(pulse_backup, "MAX_RESTORE_TOTAL_BYTES", 1):
            problems = pulse_backup.verify_full_backup(self.archive)
        self.assertTrue(any("Gesamtgröße" in problem for problem in problems))

    def test_manifest_has_its_own_size_limit(self):
        with patch.object(pulse_backup, "MAX_RESTORE_MANIFEST_BYTES", 1):
            problems = pulse_backup.verify_full_backup(self.archive)
        self.assertTrue(any("Manifest" in problem and "Größenlimit" in problem for problem in problems))

    def test_stage_copy_rechecks_sha256_instead_of_trusting_prior_verification(self):
        with zipfile.ZipFile(self.archive) as archive:
            entry = dict(pulse_backup.read_manifest(archive)["files"][0])
            entry["sha256"] = "0" * 64
            destination = Path(self.tmp.name) / "staged.bin"
            with self.assertRaisesRegex(ValueError, "SHA-256-Prüfsumme"):
                pulse_backup._copy_verified_member(archive, entry, destination)
        self.assertFalse(destination.exists())

    def test_restore_staging_uses_data_volume_not_container_tmpfs(self):
        real_temporary_directory = tempfile.TemporaryDirectory
        observed_dirs = []

        def tracked_temporary_directory(*args, **kwargs):
            if kwargs.get("prefix") == "pulse-restore-":
                observed_dirs.append(Path(kwargs["dir"]).resolve())
            return real_temporary_directory(*args, **kwargs)

        with patch.object(
            pulse_backup.tempfile,
            "TemporaryDirectory",
            side_effect=tracked_temporary_directory,
        ):
            pulse_backup._restore_from(self.archive, self.base, True, None)

        self.assertEqual(observed_dirs, [self.base.resolve()])

    def test_preview_does_not_extract_archive_that_exceeds_resource_limits(self):
        with patch.object(pulse_backup, "MAX_RESTORE_ENTRIES", 1), \
                patch.object(pulse_backup, "_copy_verified_member") as copy_member:
            result = pulse_backup.preview_restore(self.archive, self.base)
        self.assertTrue(result["problems"])
        copy_member.assert_not_called()


if __name__ == "__main__":
    unittest.main()
