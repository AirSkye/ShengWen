from __future__ import annotations

import re

from .chunker import TranscriptChunk
from .state_manager import DynamicStateManager
from .protocol import strip_state_instruction_blocks


def generate_structure_overview(previous_chunk_summaries: list[str]) -> str:
    if not previous_chunk_summaries:
        return ""

    lines: list[str] = []
    for idx, summary in enumerate(previous_chunk_summaries):
        cleaned = strip_state_instruction_blocks(summary)
        headers = re.findall(r"^(#{1,6})\s+(.+)$", cleaned, flags=re.MULTILINE)
        if not headers:
            continue
        lines.append(f"### 块 {idx + 1}")
        for level_mark, text in headers:
            level = len(level_mark)
            indent = "  " * max(0, level - 1)
            lines.append(f"{indent}- {text.strip()}")
    return "\n".join(lines)


def _detail_level_label(detail_level: int) -> str:
    labels = {
        1: "精简",
        2: "适中",
        3: "详细",
        4: "很详细",
        5: "尽量完整",
    }
    return labels.get(max(1, min(5, int(detail_level))), "详细")


def build_chunk_user_prompt(
    chunk: TranscriptChunk,
    chunk_index: int,
    total_chunks: int,
    dynamic_state: DynamicStateManager,
    structure_overview: str,
    detail_level: int = 4,
    min_output_chars: int | None = None,
) -> str:
    lines: list[str] = []
    lines.append("## 当前处理状态")
    lines.append(f"- 总共 {total_chunks} 块，当前处理第 {chunk_index + 1} 块")
    if chunk.has_timestamps:
        lines.append(f"- 当前块时间范围: `{chunk.time_range_hms}`")
    else:
        lines.append(f"- 当前块类型: `{chunk.time_range_hms}`")
        lines.append("- 本块原文没有时间戳：正文中不要添加、猜测或伪造任何时间戳。")

    if chunk_index == 0:
        lines.append("- 这是第一块，请在文首添加主题标签 `{{...}}`。")
    elif chunk_index == total_chunks - 1:
        lines.append("- 这是最后一块，请自然收尾。")
    else:
        lines.append("- 这是中间块，请保持自然续写与风格一致。")

    lines.append("")
    lines.append("## 详细度要求")
    lines.append(f"- 详细度等级: {max(1, min(5, int(detail_level)))}/5（{_detail_level_label(detail_level)}）。")
    if min_output_chars and min_output_chars > 0:
        lines.append(f"- 本块正文不要过度压缩，目标不少于 {min_output_chars} 个汉字/字符；信息密集时可以更长。")
    if int(detail_level) >= 5:
        lines.append("- 详细度 5 的保真门槛：本块每个新的数字、范围、单位、专有名词、例子、条件、反例、问答回合、因果转折和不确定表述都要留在对应上下文；只删没有信息增量的口头禅、寒暄和机械重复。")
        lines.append("- 不要靠重复结论凑长度，也不要把事实锚点单独列成清单；补充内容必须来自当前块转录，且保持发言者、顺序与语气归属。")
    lines.append("- 本块输出是精编还原稿的一部分，不是提纲；要写清背景、过程、因果、例子、对比、转折和结果。")
    lines.append("- 逐段覆盖本块不同的有效信息点，只有语义真正重复时才合并，不能省略新的条件、数字、案例或回应。")
    lines.append("- 对话、访谈、圆桌、播客、连麦或答疑必须保留‘提问—回答—追问/补充/反对—回应’链条和发言者归属；没有明确姓名时不要臆造主持人或嘉宾。")
    lines.append("- 演讲、公开课、有声书、教程、操作演示、案例复盘、影视/产品解读与新闻叙事，要分别保住论证、步骤、因果或‘素材依据—分析—结论’链条；混合直播按自然话题切换。")
    lines.append("- 优先保留数字、专有名词、类型清单、案例条件、反例、鲜明比喻和关键原话；删除寒暄、口头禅、机械复述与无信息操作。")
    lines.append("- 多人转录没有显式说话人标签时，只有当前语境能唯一确认才使用姓名；明显换人但身份不唯一时使用中性角色，不延续上一段姓名，也不补写单位或头衔。")
    lines.append("- 如果上一块的小节还没有自然收束，不要直接跳到新的一级章节；先补完当前小节的剩余内容。")

    lines.append("")
    lines.append(dynamic_state.format_for_prompt())

    if structure_overview and chunk_index > 0:
        lines.append("")
        lines.append("## 已总结结构概览")
        lines.append("```markdown")
        lines.append(structure_overview)
        lines.append("→ 当前在这里")
        lines.append("```")

    lines.append("")
    lines.append("## 当前块转录文本")
    lines.append("```plaintext")
    lines.append(chunk.text)
    lines.append("```")
    return "\n".join(lines)
