from __future__ import annotations

from copy import deepcopy

from .protocol import StateOp


DEFAULT_AGENT_KEYS = [
    "关键实体关系",
    "口误映射",
    "关键时间点",
    "已讨论主题",
    "结构层次",
    "写作风格",
    "文档主题",
]


class DynamicStateManager:
    def __init__(self, allowed_agent_keys: list[str] | None = None):
        self._program: dict[str, str] = {}
        self._agent: dict[str, str] = {}
        self._allowed_agent_keys = set(allowed_agent_keys or DEFAULT_AGENT_KEYS)

    def update_program(self, patch: dict[str, str | int | bool]) -> None:
        for key, value in patch.items():
            self._program[str(key)] = str(value)

    def set_program_value(self, key: str, value: str) -> None:
        self._program[key] = value

    def apply_ops(self, ops: list[StateOp], max_value_chars: int = 500) -> list[str]:
        warnings: list[str] = []
        for op in ops:
            key = (op.key or "").strip()
            if not key:
                warnings.append("忽略空键名操作")
                continue
            if key not in self._allowed_agent_keys:
                warnings.append(f"忽略非法智能键值: {key}")
                continue

            if op.action == "set":
                value = _truncate(op.value or "", max_value_chars)
                # 对"已讨论主题"做特殊处理，限制项目数
                if key == "已讨论主题":
                    value = _truncate_discussed_topics(value, max_items=10)
                self._agent[key] = value
            elif op.action == "append":
                appended = (op.value or "").strip()
                if not appended:
                    continue
                current = (self._agent.get(key) or "").strip()
                merged = f"{current}; {appended}" if current else appended
                value = _truncate(_normalize_semicolon_items(merged), max_value_chars)
                # 对"已讨论主题"做特殊处理，限制项目数
                if key == "已讨论主题":
                    value = _truncate_discussed_topics(value, max_items=10)
                self._agent[key] = value
            elif op.action == "remove":
                to_remove = (op.value or "").strip()
                if not to_remove:
                    self._agent.pop(key, None)
                    continue
                current = self._agent.get(key, "")
                if not current:
                    continue
                updated = current.replace(to_remove, "")
                updated = _normalize_semicolon_items(updated)
                if updated:
                    self._agent[key] = _truncate(updated, max_value_chars)
                else:
                    self._agent.pop(key, None)
            else:
                warnings.append(f"忽略未知操作: {op.action}")
        return warnings

    def format_for_prompt(self) -> str:
        lines: list[str] = []
        lines.append("## 程序状态（只读）")
        if not self._program:
            lines.append("- 暂无")
        else:
            for key, value in self._program.items():
                value_str = (value or "").strip()
                if "\n" in value_str:
                    lines.append(f"- **{key}**:")
                    lines.append(value_str)
                else:
                    lines.append(f"- **{key}**: {value_str}")

        lines.append("")
        lines.append("## 智能键值（可更新）")
        if not self._agent:
            lines.append("- 当前为空，可在输出末尾通过 `state_ops` 更新以下键值：")
            for key in DEFAULT_AGENT_KEYS:
                lines.append(f"  - {key}")
        else:
            for key in DEFAULT_AGENT_KEYS:
                value = (self._agent.get(key) or "").strip()
                if not value:
                    continue
                lines.append(f"- **{key}**: {value}")
        return "\n".join(lines)

    def snapshot(self) -> dict[str, dict[str, str]]:
        return {
            "program": deepcopy(self._program),
            "agent": deepcopy(self._agent),
        }


def _normalize_semicolon_items(raw: str) -> str:
    items = [item.strip() for item in (raw or "").split(";") if item.strip()]
    return "; ".join(items)


def _truncate(value: str, max_chars: int) -> str:
    text = value or ""
    if max_chars <= 0:
        return text
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def _truncate_discussed_topics(topics: str, max_items: int = 10) -> str:
    """只保留最近的N个标题项，避免过长"""
    if not topics:
        return topics

    items = [item.strip() for item in topics.split(';') if item.strip()]
    if len(items) > max_items:
        return '; '.join(items[-max_items:])
    return topics

