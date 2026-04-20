"""Skill — Markdown 文件定义的技能"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Skill:
    """Skill 定义 — 从 Markdown 文件加载"""

    name: str
    description: str = ""
    tools: list[str] = field(default_factory=list)  # 声明需要的底层工具
    content: str = ""  # Markdown 正文（提示词 + 知识 + 流程）
    file_path: Path | None = None

    @classmethod
    def from_markdown(cls, path: Path) -> Skill:
        """从 Markdown 文件加载 Skill

        支持 YAML frontmatter 格式：
        ---
        name: code_review
        description: 代码审查技能
        tools: [file_read, code_execute]
        ---
        # 正文内容...
        """
        text = path.read_text(encoding="utf-8")
        name = path.stem
        description = ""
        tools: list[str] = []
        content = text

        # 解析 frontmatter
        fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", text, re.DOTALL)
        if fm_match:
            fm_text = fm_match.group(1)
            content = fm_match.group(2)

            # 简单解析 YAML frontmatter（不引入 pyyaml 依赖）
            for line in fm_text.strip().split("\n"):
                line = line.strip()
                if line.startswith("name:"):
                    name = line.split(":", 1)[1].strip().strip("\"'")
                elif line.startswith("description:"):
                    description = line.split(":", 1)[1].strip().strip("\"'")
                elif line.startswith("tools:"):
                    tools_str = line.split(":", 1)[1].strip()
                    if tools_str.startswith("["):
                        # [a, b, c] 格式
                        tools_str = tools_str.strip("[]")
                        tools = [t.strip().strip("\"'") for t in tools_str.split(",") if t.strip()]
                    else:
                        tools = [tools_str.strip("\"'")]

        return cls(
            name=name,
            description=description,
            tools=tools,
            content=content.strip(),
            file_path=path,
        )

    def to_system_prompt(self) -> str:
        """转换为可注入 System Prompt 的内容"""
        parts = []
        if self.description:
            parts.append(f"# {self.name}: {self.description}")
        else:
            parts.append(f"# {self.name}")
        parts.append(self.content)
        return "\n\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "tools": self.tools,
            "file_path": str(self.file_path) if self.file_path else None,
        }
