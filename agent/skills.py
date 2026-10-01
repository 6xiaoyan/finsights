"""P4.3：Skills 渐进加载（plan 6.7）。

system prompt 只放每个 skill 的 name + description（一句话）；全文由 `load_skill`
工具按需读入上下文，且不再被压缩（L3 白名单在 P4.5 实现）。
目录结构：agent/skills/<name>/SKILL.md，frontmatter 含 name/description。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

SKILLS_ROOT = Path(__file__).resolve().parent / "skills"
_FRONT_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass(frozen=True)
class SkillMeta:
    name: str
    description: str
    path: Path

    def body(self) -> str:
        """去掉 frontmatter 的正文（V4.14：load_skill 返回正文与 SKILL.md 正文一致）。"""
        text = self.path.read_text(encoding="utf-8")
        m = _FRONT_RE.match(text)
        return text[m.end():].strip() if m else text.strip()


def discover_skills(root: Path | str = SKILLS_ROOT) -> dict[str, SkillMeta]:
    """扫描目录得到全部 skill 元信息（按名称排序，保证 system prompt 稳定 V4.16）。"""
    out: dict[str, SkillMeta] = {}
    for md in sorted(Path(root).glob("*/SKILL.md")):
        text = md.read_text(encoding="utf-8")
        m = _FRONT_RE.match(text)
        if not m:
            raise ValueError(f"{md} 缺少 frontmatter（name/description）")
        fields = dict(re.findall(r"^(\w+):\s*(.+)$", m.group(1), re.MULTILINE))
        name = fields.get("name") or md.parent.name
        if "description" not in fields:
            raise ValueError(f"{md} frontmatter 缺少 description")
        out[name] = SkillMeta(name=name, description=fields["description"].strip(), path=md)
    return dict(sorted(out.items()))


def skills_prompt_block(root: Path | str = SKILLS_ROOT) -> str:
    """system prompt 中的 skills 清单：只有 name: description，不含任何正文。"""
    return "\n".join(f"- {s.name}: {s.description}" for s in discover_skills(root).values())


def load_skill_body(name: str, root: Path | str = SKILLS_ROOT) -> str:
    """返回 skill 正文；不存在时报错并附可用列表（V4.14）。"""
    skills = discover_skills(root)
    if name not in skills:
        raise KeyError(f"skill '{name}' 不存在（可用: {', '.join(skills)}）")
    return skills[name].body()
