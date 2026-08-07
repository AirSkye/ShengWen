import os
import sys
import unittest


path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, path)

from src.main.python.sheng_wen.utils.media import (
    AUDIO_MEDIA_EXTENSIONS,
    build_transcriber_payload,
    is_audio_media,
)


class TestMediaUtils(unittest.TestCase):
    def test_m4s_is_supported_as_audio_and_routes_to_transcriber_audio_file(self):
        payload = build_transcriber_payload(
            task_id="task_m4s",
            media_path=os.path.join("temp", "task_m4s.m4s"),
            output_dir="temp",
        )

        self.assertIn(".m4s", AUDIO_MEDIA_EXTENSIONS)
        self.assertTrue(is_audio_media("example.m4s"))
        self.assertEqual(payload["audio_file"], os.path.join("temp", "task_m4s.m4s"))
        self.assertIsNone(payload["video_file"])


if __name__ == "__main__":
    unittest.main()
