"""K-10: missing ffmpeg/libopus is reported clearly instead of failing silently."""
import unittest
from pathlib import Path
from unittest.mock import patch

import pulse_audio_check

ROOT = Path(__file__).resolve().parents[1]


class AudioCheckTests(unittest.TestCase):
    def test_missing_ffmpeg_and_opus_are_reported(self):
        with patch.object(pulse_audio_check.shutil, "which", return_value=None), \
                patch("discord.opus.is_loaded", return_value=False), patch("discord.opus._load_default", return_value=False):
            status = pulse_audio_check.audio_status()
        self.assertFalse(status["ok"])
        self.assertIn("ffmpeg fehlt", status["message"])
        self.assertIn("libopus", status["message"])

    def test_available_tools_are_ok(self):
        with patch.object(pulse_audio_check.shutil, "which", return_value="/usr/bin/ffmpeg"), \
                patch("discord.opus.is_loaded", return_value=True):
            self.assertTrue(pulse_audio_check.audio_status()["ok"])

    def test_dockerfile_installs_ffmpeg_and_opus(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("ffmpeg", dockerfile)
        self.assertIn("libopus0", dockerfile)


if __name__ == "__main__":
    unittest.main()
