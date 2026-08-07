import unittest
from pathlib import Path

from src.main.python.sheng_wen.llm.llm_worker import (
    LLMWorker,
    _count_summary_timestamps,
    _estimate_standard_min_output_chars,
    _estimate_agent_stream_progress,
    _estimate_standard_stream_progress,
    _expected_summary_timestamp_count,
    _limit_summary_timestamp_density,
    _maximum_summary_timestamp_count,
    _report_presentation_needs_repair,
)
from src.main.python.sheng_wen.llm.llm import LLMConfig
from src.main.python.sheng_wen.llm.mock_llm import MockLLM
from src.main.python.sheng_wen.summarization.chunked_summarizer import (
    _estimate_chunk_min_output_chars,
    _maximum_agent_timestamp_count,
    _merge_document_topic,
)
from src.main.python.sheng_wen.summarization.chunker import (
    TranscriptChunk,
    count_transcript_content_chars,
    measure_transcript_complexity,
)
from src.main.python.sheng_wen.summarization.output_cleaner import (
    clean_summary_output,
    count_summary_timestamps,
    limit_summary_timestamp_density,
)
from src.main.python.sheng_wen.summarization.fidelity import (
    assess_fidelity,
    candidate_preserves_fidelity,
    extract_factual_anchors,
)


class SummaryFidelityTest(unittest.TestCase):
    def test_summary_style_defaults_to_classic_and_prefers_payload(self):
        worker = LLMWorker(
            name="test-worker",
            llm_client=MockLLM(LLMConfig(base_url="http://localhost", api_key="", model_id="mock")),
        )

        self.assertEqual(worker._resolve_summary_style({}, None), "classic")
        self.assertEqual(worker._resolve_summary_style({}, {"summary_style": "report"}), "report")
        self.assertEqual(
            worker._resolve_summary_style(
                {"summary_style": "classic"},
                {"summary_style": "report"},
            ),
            "classic",
        )

    def test_report_prompt_is_only_appended_for_report_style(self):
        worker = LLMWorker(
            name="test-worker",
            llm_client=MockLLM(LLMConfig(base_url="http://localhost", api_key="", model_id="mock")),
        )
        addon_path = Path(__file__).with_name("summary_style_report_prompt.md")
        addon_path.write_text("REPORT_ONLY_RULE", encoding="utf-8")
        self.addCleanup(lambda: addon_path.unlink(missing_ok=True))

        classic = worker._build_system_prompt("BASE_RULE", "classic", addon_path)
        report = worker._build_system_prompt("BASE_RULE", "report", addon_path)

        self.assertEqual(classic, "BASE_RULE")
        self.assertIn("BASE_RULE", report)
        self.assertIn("REPORT_ONLY_RULE", report)

    def test_content_char_count_excludes_timestamp_prefixes(self):
        transcript = "000001你好 世界\n000002继续 说明"

        self.assertEqual(count_transcript_content_chars(transcript), 8)
        self.assertEqual(measure_transcript_complexity(transcript)["content_char_count"], 8)

    def test_standard_detail_target_uses_spoken_content(self):
        plain = "重要内容" * 1000
        timestamped = "\n".join(f"000001{plain[index:index + 80]}" for index in range(0, len(plain), 80))

        self.assertEqual(
            _estimate_standard_min_output_chars(plain, 5),
            _estimate_standard_min_output_chars(timestamped, 5),
        )
        self.assertGreater(
            _estimate_standard_min_output_chars(plain, 5),
            _estimate_standard_min_output_chars(plain, 4),
        )

    def test_auto_metrics_include_timestamp_free_content_count(self):
        worker = LLMWorker(
            name="test-worker",
            llm_client=MockLLM(LLMConfig(base_url="http://localhost", api_key="", model_id="mock")),
        )

        metrics = worker._collect_auto_mode_metrics(
            task_data={"audio_duration": 0},
            transcript_text="000001第一段\n000002第二段",
        )

        self.assertEqual(metrics["content_char_count"], 6)

    def test_agent_detail_target_increases_with_detail_level(self):
        text = "\n".join(f"000001关键问答与案例{index}" for index in range(500))
        chunk = TranscriptChunk(0, text, 1, 1200, 500)

        self.assertGreater(
            _estimate_chunk_min_output_chars(chunk, 5),
            _estimate_chunk_min_output_chars(chunk, 4),
        )

    def test_document_topic_replaces_duplicate_leading_topics(self):
        summary = "{{旧主题}}\n{{另一个旧主题}}\n\n## 1. 正文"

        merged = _merge_document_topic(summary, "{{准确的新主题}}")

        self.assertTrue(merged.startswith("{{准确的新主题}}"))
        self.assertEqual(merged.count("{{"), 1)
        self.assertIn("## 1. 正文", merged)

    def test_output_cleaner_removes_fence_and_trailing_process_note(self):
        summary = "```html\n{{主题}}\n```\n\n## 1. 正文\n\n（全文精编还原，覆盖原文有效信息点远超85%。）"

        cleaned = clean_summary_output(summary)

        self.assertTrue(cleaned.startswith("{{主题}}"))
        self.assertNotIn("```html", cleaned)
        self.assertNotIn("覆盖原文有效信息", cleaned)

    def test_timestamp_coverage_helpers(self):
        summary = "第一段 (见 00:01:02)\n第二段 (00:03:04-00:04:05)"

        self.assertEqual(_count_summary_timestamps(summary), 3)
        self.assertEqual(_expected_summary_timestamp_count(6000), 10)
        self.assertEqual(_maximum_summary_timestamp_count(6000), 15)

    def test_standard_stream_progress_starts_after_content_and_caps_before_completion(self):
        self.assertEqual(_estimate_standard_stream_progress("", 1000), 0)
        self.assertEqual(_estimate_standard_stream_progress("有效正文", 1000), 3)
        self.assertEqual(_estimate_standard_stream_progress("内容" * 500, 1000), 70)
        self.assertGreater(_estimate_standard_stream_progress("内容" * 1000, 1000), 70)
        self.assertLess(_estimate_standard_stream_progress("内容" * 1000, 1000), 95)

    def test_agent_stream_progress_is_nonzero_during_first_chunk(self):
        self.assertEqual(_estimate_agent_stream_progress(0, 4, ""), 0)
        self.assertEqual(_estimate_agent_stream_progress(0, 4, "正在生成"), 3)
        self.assertEqual(_estimate_agent_stream_progress(2, 4, "已有正文"), 50)

    def test_long_report_requires_headings_callouts_and_structured_component(self):
        dense_report = "连续正文" * 1100
        self.assertTrue(_report_presentation_needs_repair(dense_report))

        structured_report = "\n".join(
            [
                "{{主题}}",
                "## 1. 背景",
                "> [!CONTEXT]",
                "> 背景说明",
                "## 2. 定义",
                "> [!DEFINITION]",
                "> 概念定义",
                "## 3. 过程",
                "> [!PROCESS]",
                "> 1. 第一步",
                "> 2. 第二步",
                "> [!KEY]",
                "> 关键结论",
                dense_report,
            ]
        )
        self.assertFalse(_report_presentation_needs_repair(structured_report))

    def test_timestamp_density_limit_keeps_evenly_spaced_annotations(self):
        summary = "\n".join(
            f"第{index}段关键信息 (见 00:00:{index:02d})"
            for index in range(12)
        )

        reduced = _limit_summary_timestamp_density(summary, 5)

        self.assertEqual(_count_summary_timestamps(reduced), 5)
        self.assertIn("00:00:00", reduced)
        self.assertIn("00:00:11", reduced)

    def test_standard_timestamp_coverage_can_limit_overdense_annotations(self):
        summary = "\n".join(
            f"第{index}段内容 (见 00:00:{index:02d})"
            for index in range(40)
        )

        limited = _limit_summary_timestamp_density(summary, _maximum_summary_timestamp_count(8000))

        self.assertEqual(_count_summary_timestamps(limited), 20)

    def test_agent_timestamp_density_limit_preserves_document_coverage(self):
        summary = "\n".join(
            f"第{index}段关键信息 (见 00:00:{index:02d})"
            for index in range(20)
        )

        reduced = limit_summary_timestamp_density(summary, 7)

        self.assertEqual(count_summary_timestamps(reduced), 7)
        self.assertEqual(_maximum_agent_timestamp_count(28022), 81)

    def test_factual_anchors_ignore_timestamp_prefixes(self):
        transcript = "000001模型在3到5个步骤内完成，预算为7000元。\n000002使用‘可信可控’作为边界。"
        anchors = extract_factual_anchors(transcript)

        self.assertIn("3到5个", anchors)
        self.assertIn("7000元", anchors)
        self.assertIn("可信可控", anchors)
        self.assertNotIn("000001", anchors)

    def test_factual_anchors_join_long_numbers_split_by_timestamps(self):
        transcript = "000836就是720326954\n0008387609135002的平方等于四"

        anchors = extract_factual_anchors(transcript)

        self.assertIn("7203269547609135002", anchors)
        self.assertNotIn("720326954", anchors)

    def test_fidelity_treats_chinese_and_arabic_unit_numbers_as_equivalent(self):
        transcript = "000001这个理论被讨论30年，提出者研究物理60年。"
        summary = "这个理论被讨论三十年，提出者研究物理六十年。"

        report = assess_fidelity(transcript, summary)

        self.assertEqual(report.coverage_ratio, 1.0)

    def test_fidelity_report_and_candidate_guard(self):
        transcript = "000001覆盖3到5个步骤，成本7000元，使用GPT-4。"
        baseline = "覆盖多个步骤，成本7000元。"
        candidate = "覆盖3到5个步骤，成本7000元，并使用GPT-4。"

        report = assess_fidelity(transcript, baseline)
        self.assertLess(report.coverage_ratio, 1.0)
        self.assertTrue(candidate_preserves_fidelity(transcript, baseline, candidate))


if __name__ == "__main__":
    unittest.main()
