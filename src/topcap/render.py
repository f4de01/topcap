"""成稿 Markdown 渲染：YAML frontmatter + 标题 + 文案。缺失的内容数据不写，绝不填 0。"""
from __future__ import annotations

from pathlib import Path

from .config import safe_name
from .models import Candidate


def _yaml_str(s: str) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def frontmatter(cand: Candidate, *, library: str, transcript_source: str) -> str:
    lines = ["---", f"title: {_yaml_str(cand.title)}", f"source: {cand.source}",
             f"author: {_yaml_str(cand.author)}", f"url: {cand.url}"]
    if cand.published_at:
        lines.append(f"published: {cand.published_at}")
    if cand.duration_sec:
        lines.append(f"duration: {cand.duration_sec}")
    if cand.likes is not None:
        lines.append(f"likes: {cand.likes}")
    lines.append(f"library: {_yaml_str(library)}")
    lines.append(f"transcript_source: {transcript_source}")
    lines.append(f"tags: [topcap, {cand.source}]")
    lines.append("---")
    return "\n".join(lines)


def filename(cand: Candidate) -> str:
    return (f"{cand.index:02d}_{safe_name(cand.source, 8)}_"
            f"{safe_name(cand.title, 40)}_{safe_name(cand.author, 16)}.md")


def render(cand: Candidate, body: str, *, library: str, transcript_source: str) -> str:
    return (frontmatter(cand, library=library, transcript_source=transcript_source)
            + f"\n\n# {cand.title}\n\n{body.strip()}\n")


def compose_parts(parts: list[tuple[str, str]]) -> str:
    """多 P 视频：各 P 以二级标题为小节。单 P 直接返回正文。"""
    if len(parts) == 1:
        return parts[0][1]
    out = []
    for n, (title, text) in enumerate(parts, start=1):
        head = f"## P{n} {title}".rstrip()
        out.append(f"{head}\n\n{text.strip()}")
    return "\n\n".join(out)


def write(cand: Candidate, body: str, vault_dir: Path, *, library: str, transcript_source: str) -> Path:
    vault_dir.mkdir(parents=True, exist_ok=True)
    path = vault_dir / filename(cand)
    path.write_text(render(cand, body, library=library, transcript_source=transcript_source),
                    encoding="utf-8")
    return path
