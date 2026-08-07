import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, project_root)

from src.main.python.sheng_wen.transcriber.tingwu_transcriber import TingwuTranscriber
from src.main.python.sheng_wen.transcriber.transcriber import TranscriptionError
from tingwu.tingwu_http_transcribe import update_auth_session, write_outputs, write_private_json


class TestTingwuIntegration(unittest.TestCase):
    def test_adapter_builds_timestamped_result_and_artifacts(self):
        remote_result = {
            "transId": "remote-task-1",
            "elapsedSeconds": 2.5,
            "parsedResult": {
                "pg": [
                    {
                        "sc": [
                            {"tc": "第一句。", "bt": 0, "et": 1200},
                            {"tc": "第二句。", "bt": 1500, "et": 2800},
                        ]
                    }
                ]
            },
            "textPath": "/tmp/result.txt",
            "srtPath": "/tmp/result.srt",
            "taskPath": "/tmp/task.json",
            "subtitleCount": 2,
        }
        events = []
        progress = []

        with tempfile.TemporaryDirectory() as temp_dir:
            transcriber = TingwuTranscriber(
                config_path=os.path.join(temp_dir, "config.json"),
                task_root=os.path.join(temp_dir, "tasks"),
            )

            def fake_transcribe(*args, **kwargs):
                kwargs["event_callback"](
                    {"stage": "uploading", "progress": 0.5, "message": "uploading"}
                )
                return remote_result

            with patch(
                "src.main.python.sheng_wen.transcriber.tingwu_transcriber.transcribe_media",
                side_effect=fake_transcribe,
            ):
                result = transcriber.transcribe_with_context(
                    os.path.join(temp_dir, "sample.mp3"),
                    task_id="task-1",
                    progress_callback=progress.append,
                    event_callback=events.append,
                )

        self.assertEqual(result.provider, "tingwu")
        self.assertEqual(len(result.segments), 2)
        self.assertEqual(result.segments[0]["start"], 0.0)
        self.assertEqual(result.segments[1]["end"], 2.8)
        self.assertEqual(result.artifacts["remote_task_id"], "remote-task-1")
        self.assertEqual(result.artifacts["subtitle_path"], "/tmp/result.srt")
        self.assertEqual(events[0]["stage"], "uploading")
        self.assertAlmostEqual(progress[0], 0.25)

    def test_disabled_fallback_does_not_initialize_whisper(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            media_path = os.path.join(temp_dir, "sample.mp3")
            Path(media_path).write_bytes(b"sample")
            transcriber = TingwuTranscriber(
                config_path=os.path.join(temp_dir, "config.json"),
                task_root=os.path.join(temp_dir, "tasks"),
                fallback_to_whisper=False,
            )

            with patch(
                "src.main.python.sheng_wen.transcriber.tingwu_transcriber.transcribe_media",
                side_effect=RuntimeError("remote failed"),
            ), patch(
                "src.main.python.sheng_wen.transcriber.tingwu_transcriber.get_transcriber"
            ) as get_transcriber_mock:
                with self.assertRaises(TranscriptionError):
                    transcriber.transcribe(media_path)

        get_transcriber_mock.assert_not_called()

    def test_outputs_include_private_text_and_srt_files(self):
        response = {
            "data": {
                "result": json.dumps(
                    {
                        "pg": [
                            {
                                "sc": [
                                    {"tc": "你好，世界。", "bt": 0, "et": 1400},
                                    {"tc": "这是测试。", "bt": 1800, "et": 3200},
                                ]
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            }
        }
        status = {"status": 0, "statusMsg": "completed"}

        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_outputs(Path(temp_dir), "sample", response, "task-1", status)
            text_path = Path(outputs["textPath"])
            srt_path = Path(outputs["srtPath"])

            self.assertIn("你好，世界。", text_path.read_text(encoding="utf-8"))
            srt_content = srt_path.read_text(encoding="utf-8")
            self.assertIn("00:00:00,000 --> 00:00:01,400", srt_content)
            self.assertEqual(text_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(srt_path.stat().st_mode & 0o777, 0o600)

    def test_session_update_validates_and_rolls_back_on_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config = {
                "base_url": "https://tingwu.example",
                "cookie": "session=old",
                "user_agent": "test",
                "origin": "https://tingwu.example",
                "referer": "https://tingwu.example/folders/0",
            }
            write_private_json(config_path, config)

            with patch(
                "tingwu.tingwu_http_transcribe.validate_auth_config",
                return_value={"configured": True, "valid": True, "message": "ok"},
            ):
                success = update_auth_session(config_path, "session=new")

            self.assertTrue(success["valid"])
            self.assertTrue(success["updated"])
            saved = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["cookie"], "session=new")

            with patch(
                "tingwu.tingwu_http_transcribe.validate_auth_config",
                return_value={"configured": True, "valid": False, "message": "expired"},
            ):
                failed = update_auth_session(config_path, "session=invalid")

            self.assertFalse(failed["valid"])
            self.assertFalse(failed["updated"])
            restored = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(restored["cookie"], "session=new")


if __name__ == "__main__":
    unittest.main()
