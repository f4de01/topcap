"""贯穿管线的数据结构。"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Media:
    """一条内容的可下载地址。"""

    urls: list[str] = field(default_factory=list)
    kind: str = "video"                      # video | audio
    expires_at: int | None = None            # unix 秒；None 一律当作已过期
    headers: dict[str, str] = field(default_factory=dict)

    def fresh(self, now: float, margin: int = 120) -> bool:
        return bool(self.urls) and self.expires_at is not None and now + margin < self.expires_at


@dataclass
class Part:
    """B站多 P 视频的一个分 P。单 P 视频也有且只有一个 Part。"""

    cid: str
    title: str = ""
    duration_sec: int | None = None


@dataclass
class Candidate:
    """搜索阶段产出、已通过存在性校验的内容条目。"""

    index: int
    source: str                              # 抖音 | B站
    id: str                                  # aweme_id | bvid
    title: str
    author: str
    url: str
    likes: int | None = None
    duration_sec: int | None = None
    published_at: str | None = None          # YYYY-MM-DD
    parts: list[Part] = field(default_factory=list)
    media: Media | None = None

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        if self.media is not None and not self.media.urls:
            d["media"] = None
        return d

    @classmethod
    def from_json(cls, r: dict[str, Any]) -> "Candidate":
        media = r.get("media")
        return cls(
            index=r["index"], source=r["source"], id=r["id"], title=r["title"],
            author=r["author"], url=r["url"], likes=r.get("likes"),
            duration_sec=r.get("duration_sec"), published_at=r.get("published_at"),
            parts=[Part(**p) for p in r.get("parts") or []],
            media=Media(**media) if media else None,
        )


@dataclass
class Author:
    """一个博主。source 决定用哪套接口，id 是那个平台自己的主键。"""

    source: str
    id: str
    name: str
    url: str
    works: int | None = None


@dataclass
class Library:
    """一个库的身份：主题库或博主库。"""

    name: str
    kind: str                                # topic | author
    query: str = ""                          # 主题词，或博主主页链接
    author_source: str = ""                  # 博主库：所在采集源
    author_id: str = ""                      # 博主库：平台主键

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))
