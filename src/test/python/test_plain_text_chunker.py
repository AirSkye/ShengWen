import unittest

from src.main.python.sheng_wen.summarization.chunker import (
    measure_transcript_complexity,
    split_transcript_into_chunks,
)


class PlainTextChunkerTest(unittest.TestCase):
    def test_plain_text_is_split_without_timestamps(self):
        text = "\n".join(
            [
                "这一段是没有时间戳的语音识别文本，包含背景、观点、例子和结论。" * 12,
                "第二段继续展开问题，并提供更多上下文。" * 12,
                "第三段补充转折和结果。" * 12,
            ]
        )

        chunks = split_transcript_into_chunks(
            transcript_text=text,
            target_duration_sec=260,
            min_duration_sec=160,
            max_duration_sec=320,
            boundary_jump_sec=10,
        )

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(not chunk.has_timestamps for chunk in chunks))
        self.assertTrue(all((chunk.label or "").startswith("无时间戳文本块") for chunk in chunks))
        self.assertIn("语音识别文本", "\n".join(chunk.text for chunk in chunks))

    def test_plain_text_complexity_uses_character_count(self):
        text = "没有时间戳的文本。" * 400
        metrics = measure_transcript_complexity(text)

        self.assertFalse(metrics["has_timestamps"])
        self.assertEqual(metrics["timestamp_line_count"], 0)
        self.assertGreater(metrics["plain_char_count"], 1000)
        self.assertGreater(metrics["line_count"], 10)

    def test_timestamped_text_stays_timestamped(self):
        text = "\n".join(
            [
                "000000开场说明",
                "000030继续展开",
                "001000进入第二部分",
            ]
        )
        chunks = split_transcript_into_chunks(
            transcript_text=text,
            target_duration_sec=300,
            min_duration_sec=120,
            max_duration_sec=600,
            boundary_jump_sec=10,
        )

        self.assertGreaterEqual(len(chunks), 1)
        self.assertTrue(all(chunk.has_timestamps for chunk in chunks))


if __name__ == "__main__":
    unittest.main()
