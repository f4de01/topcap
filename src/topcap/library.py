"""库的候选清单读写。序号只增不改：agent 记下的 `--pick 3` 必须在下一次运行仍指向同一条内容。"""
from __future__ import annotations

from .config import LibraryPaths
from .models import Candidate, Library, read_json, write_json

INBOX = "收件箱"


def merge_candidates(existing: list[Candidate], new: list[Candidate]) -> tuple[list[Candidate], int]:
    """把新候选追加到已有清单后面，按 (采集源, id) 去重，序号接着编。返回 (合并后, 新增数)。"""
    seen = {(c.source, c.id) for c in existing}
    out = list(existing)
    next_index = max((c.index for c in existing), default=0) + 1
    added = 0
    for c in new:
        key = (c.source, c.id)
        if key in seen:
            continue
        seen.add(key)
        c.index = next_index
        next_index += 1
        out.append(c)
        added += 1
    return out, added


def load_candidates(paths: LibraryPaths) -> list[Candidate]:
    return [Candidate.from_json(r) for r in read_json(paths.candidates, [])]


def save_candidates(paths: LibraryPaths, cands: list[Candidate]) -> None:
    write_json(paths.candidates, [c.to_json() for c in cands])


def load_library(paths: LibraryPaths) -> Library | None:
    data = read_json(paths.library_json)
    return Library(**data) if data else None


def save_library(paths: LibraryPaths, lib: Library) -> None:
    write_json(paths.library_json, lib.to_json())


def parse_pick(spec: str, available: list[int]) -> list[int]:
    """'1,3,5' / '2-6' / 'all' -> 序号列表。不存在的序号直接报错，不静默跳过。"""
    spec = (spec or "").strip().lower()
    if not spec:
        raise ValueError("要给 --pick，例如 --pick 1,3,5 或 --pick all")
    if spec == "all":
        return list(available)
    out: list[int] = []
    for token in spec.replace("，", ",").split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            a, b = token.split("-", 1)
            if not (a.strip().isdigit() and b.strip().isdigit()):
                raise ValueError(f"看不懂的序号范围：{token}")
            out.extend(range(int(a), int(b) + 1))
        elif token.isdigit():
            out.append(int(token))
        else:
            raise ValueError(f"看不懂的序号：{token}")
    missing = [i for i in out if i not in available]
    if missing:
        raise ValueError(f"候选清单里没有这些序号：{missing}")
    return list(dict.fromkeys(out))
