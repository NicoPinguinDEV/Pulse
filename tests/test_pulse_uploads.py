import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pulse_uploads
from pulse_ticket_design import detect_image_type

PNG = b"\x89PNG\r\n\x1a\n"


class FakeUpload:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0
        self.reads = []
        self.closed = False

    async def read(self, size=-1):
        self.reads.append(size)
        chunk = self.data[self.pos:self.pos + size] if size >= 0 else self.data[self.pos:]
        self.pos += len(chunk)
        return chunk

    async def close(self):
        self.closed = True


def save(upload, folder, max_bytes=1024 * 1024):
    return asyncio.run(pulse_uploads.save_image_upload(
        upload, folder, lambda ext: f"bild.{ext}", max_bytes, detect=detect_image_type,
        too_large="zu groß", wrong_type="falscher Typ"))


class StreamingUploadTests(unittest.TestCase):
    def test_streams_in_chunks_and_stores_file(self):
        data = PNG + b"x" * (pulse_uploads.CHUNK_BYTES * 2 + 10)
        upload = FakeUpload(data)
        with tempfile.TemporaryDirectory() as tmp:
            name, error = save(upload, Path(tmp))
            self.assertEqual((name, error), ("bild.png", ""))
            self.assertEqual((Path(tmp) / "bild.png").read_bytes(), data)
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ["bild.png"])  # no temp leftovers
        self.assertTrue(upload.closed)
        self.assertTrue(all(size <= pulse_uploads.CHUNK_BYTES for size in upload.reads))  # never one big read

    def test_wrong_type_is_rejected_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(save(FakeUpload(b"<?php echo 1; ?>"), Path(tmp)), ("", "falscher Typ"))
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_oversized_upload_aborts_and_cleans_up(self):
        upload = FakeUpload(PNG + b"x" * 5000)
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(save(upload, Path(tmp), max_bytes=1000), ("", "zu groß"))
            self.assertEqual(list(Path(tmp).iterdir()), [])
        self.assertLess(upload.pos, 5000 + len(PNG) + 1)

    def test_release_is_safe_everywhere(self):
        with patch.object(pulse_uploads.sys, "platform", "win32"):
            self.assertFalse(pulse_uploads.release_freed_memory())
        with patch.object(pulse_uploads.sys, "platform", "linux"), \
                patch.object(pulse_uploads, "_LIBC", None), \
                patch.object(pulse_uploads.ctypes, "CDLL", side_effect=OSError("no libc")):
            self.assertFalse(pulse_uploads.release_freed_memory())


if __name__ == "__main__":
    unittest.main()
