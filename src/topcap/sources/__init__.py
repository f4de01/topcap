"""采集源注册表与输入解析。

每个采集源实现同一组操作（见 Source 协议）。这里负责：把用户输入解析成主题词、
内容链接或博主主页；按固定顺序并发搜索各源；按需刷新播放地址。
"""
from __future__ import annotations

import html
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable, Protocol

import httpx

from ..client import TikHub
from ..links import Ref, classify_url, extract_urls, is_short
from ..models import Author, Candidate


class Source(Protocol):
    name: str

    def search(self, client: TikHub, keyword: str, pages: int) -> list[Candidate]: ...
    def from_id(self, client: TikHub, id: str) -> Candidate: ...
    def refresh(self, client: TikHub, cand: Candidate) -> Candidate: ...
    def author(self, client: TikHub, ref: Ref) -> Author: ...
    def works(self, client: TikHub, author: Author, limit: int) -> list[Candidate]: ...
    def subtitles(self, client: TikHub, cand: Candidate) -> list[str] | None: ...


REGISTRY: dict[str, Source] = {}
ORDER = ("抖音", "B站")        # 候选序号按这个顺序拼装，必须可复现


def register(source: Source) -> Source:
    REGISTRY[source.name] = source
    return source


# --------------------------------------------------------------------------- 工具

_TAG = re.compile(r"<[^>]+>")
_HASHTAG = re.compile(r"[#＃][^\s#＃]+")
_BRACKET_TAG = re.compile(r"[\[【][^\]】]{0,20}[\]】]\s*$")
_SENTENCE = re.compile(r"[。！？!?\n]")


def iso_date(ts: int | None) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone().strftime("%Y-%m-%d")


def strip_tags(text: str) -> str:
    """搜索结果的标题带 <em class=keyword> 高亮标签。"""
    return html.unescape(_TAG.sub("", text or "")).strip()


def clock_to_sec(value) -> int | None:
    """把 '9:37' / '00:26' / 26 统一成秒。"""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value) or None
    parts = str(value).split(":")
    if not all(p.strip().isdigit() for p in parts):
        return None
    total = 0
    for p in parts:
        total = total * 60 + int(p)
    return total or None


def title_from(text: str, limit: int = 40) -> str:
    """短视频平台没有独立标题字段，得从整段文案里切出一个像标题的开头。"""
    t = _HASHTAG.sub("", text or "").strip()
    t = _BRACKET_TAG.sub("", t).strip()
    if not t:
        return "无标题"
    head = _SENTENCE.split(t)[0].strip()
    if len(head) > limit:
        space = head.find(" ", 8)
        if 8 <= space <= limit:
            head = head[:space]
    head = head.strip(" ，,、-—|")
    return (head[:limit] or t[:limit]) or "无标题"


BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126 Safari/537.36")


def resolve_short(url: str) -> str:
    """短链跟一次跳转。分享出来的链接十有八九是短链。"""
    if not is_short(url):
        return url
    try:
        with httpx.Client(follow_redirects=True, timeout=15.0) as c:
            return str(c.get(url, headers={"User-Agent": BROWSER_UA}).url)
    except Exception:
        return url


# --------------------------------------------------------------------------- 输入解析

def parse_input(text: str) -> tuple[str, str | Ref]:
    """把用户输入分成三类：("topic", 关键词) / ("video", Ref) / ("user", Ref)。

    只做识别，不调 TikHub。短链在这里还原，因为分类依赖长链接的路径形态。
    """
    urls = extract_urls(text)
    if not urls:
        keyword = (text or "").strip()
        if not keyword:
            raise ValueError("输入是空的")
        return "topic", keyword
    url = resolve_short(urls[0])
    ref = classify_url(url)
    if ref is None:
        raise ValueError(f"认不出这个链接属于哪个平台：{url[:80]}\n"
                         "   支持：抖音视频 / 抖音主页 douyin.com/user/…、"
                         "B站视频 bilibili.com/video/BV…、B站主页 space.bilibili.com/…")
    return ref.kind, ref


def search_all(client: TikHub, keyword: str, pages: int,
               on_done: Callable[[str, int | None, str | None], None] | None = None,
               ) -> list[Candidate]:
    """并发搜索各源，按固定顺序拼装。任何一个源炸掉都不影响其余源。"""
    def one(name: str) -> list[Candidate]:
        try:
            found = REGISTRY[name].search(client, keyword, pages)
        except Exception as exc:
            if on_done:
                on_done(name, None, f"{type(exc).__name__}: {exc}")
            return []
        if on_done:
            on_done(name, len(found), None)
        return found

    names = [n for n in ORDER if n in REGISTRY]
    with ThreadPoolExecutor(max_workers=len(names)) as pool:
        batches = list(pool.map(one, names))
    return [c for batch in batches for c in batch]


def refresh_media(client: TikHub, cand: Candidate) -> Candidate:
    """确保候选带着新鲜的播放地址和分 P 信息。"""
    if cand.media and cand.media.fresh(time.time()) and cand.parts:
        return cand
    return REGISTRY[cand.source].refresh(client, cand)


from . import bilibili as _bilibili  # noqa: E402  注册副作用
from . import douyin as _douyin  # noqa: E402

__all__ = ["REGISTRY", "ORDER", "parse_input", "search_all", "refresh_media",
           "resolve_short", "iso_date", "strip_tags", "clock_to_sec", "title_from"]
