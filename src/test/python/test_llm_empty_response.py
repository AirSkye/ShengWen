import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, project_root)

from src.main.python.sheng_wen.llm.litellm_client import LiteLLMClient
from src.main.python.sheng_wen.llm.llm import LLMConfig, LLMMessage
from src.main.python.sheng_wen.llm.llm_worker import LLMWorker


class EmptyAsyncStream:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class EmptyLLM:
    def __init__(self):
        self.call_count = 0

    async def response(self, messages, resp_callback, stream=True, timeout=60):
        self.call_count += 1


class TestLiteLLMEmptyResponse(unittest.IsolatedAsyncioTestCase):
    async def test_empty_stream_retries_with_non_streaming_request(self):
        config = LLMConfig(
            base_url="https://example.invalid/v1",
            api_key="test-key",
            model_id="test-model",
        )
        client = LiteLLMClient(config)
        non_streaming_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="fallback content"))]
        )
        received = []

        with patch(
            "src.main.python.sheng_wen.llm.litellm_client.litellm.acompletion",
            new=AsyncMock(side_effect=[EmptyAsyncStream(), non_streaming_response]),
        ) as completion:
            await client.response(
                messages=[LLMMessage(role="user", content="test")],
                resp_callback=received.append,
            )

        self.assertEqual(received, ["fallback content"])
        self.assertEqual(completion.await_count, 2)
        self.assertTrue(completion.await_args_list[0].kwargs["stream"])
        self.assertFalse(completion.await_args_list[1].kwargs["stream"])


class TestLLMWorkerEmptySummary(unittest.IsolatedAsyncioTestCase):
    async def test_empty_summary_marks_task_failed_without_writing_output(self):
        empty_llm = EmptyLLM()
        worker = LLMWorker("empty-summary-test", empty_llm)
        worker.system_prompt = "test prompt"

        with tempfile.TemporaryDirectory() as temp_dir:
            transcript_path = Path(temp_dir) / "transcript.txt"
            output_path = Path(temp_dir) / "summary.md"
            transcript_path.write_text("测试文本。", encoding="utf-8")

            with patch.object(worker, "_mark_failed", new=AsyncMock()) as mark_failed:
                await worker.process_task(
                    {
                        "intermediate_file_path": str(transcript_path),
                        "output_file": str(output_path),
                        "summary_mode": "standard",
                    }
                )

        self.assertEqual(empty_llm.call_count, 2)
        mark_failed.assert_awaited_once()
        self.assertIn("空摘要", mark_failed.await_args.args[1])
        self.assertFalse(output_path.exists())


if __name__ == "__main__":
    unittest.main()
