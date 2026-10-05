"""B站字幕解析。纯函数，网络请求通过回调注入，便于测试。

TikHub 的字幕接口文档没有写全响应结构。B站原生 player/v2 的形态是
`subtitle.subtitles[] {lan, lan_doc, subtitle_url}`，字幕正文要再请求一次得到
`{body: [{from, to, content}]}`。TikHub 可能直接返回正文，也可能只返回列表。
这里对两种都做防御：先在响应里找 body，找不到再找 subtitle_url 去取。
"""
from __future__ import annotations

from typing import Callable

FetchJson = Callable[[str], dict | None]

# 优先中文：自动生成的中文字幕 lan 是 ai-zh，人工是 zh-CN / zh-Hans
_PREFER = ("zh-cn", "zh-hans", "zh", "ai-zh")


def _find_body(node, depth: int = 0) -> list[dict] | None:
    """递归找形如 [{"from":..,"to":..,"content":..}] 的列表。"""
    if depth > 6:
        return None
    if isinstance(node, list):
        if node and all(isinstance(x, dict) and "content" in x for x in node):
            return node
        for x in node:
            if (hit := _find_body(x, depth + 1)):
                return hit
    elif isinstance(node, dict):
        if "body" in node and isinstance(node["body"], list):
            if (hit := _find_body(node["body"], depth + 1)):
                return hit
        for v in node.values():
            if (hit := _find_body(v, depth + 1)):
                return hit
    return None


def _find_tracks(node, depth: int = 0) -> list[dict]:
    """找所有带 subtitle_url 的字典。"""
    out: list[dict] = []
    if depth > 6:
        return out
    if isinstance(node, dict):
        if node.get("subtitle_url"):
            out.append(node)
        for v in node.values():
            out.extend(_find_tracks(v, depth + 1))
    elif isinstance(node, list):
        for x in node:
            out.extend(_find_tracks(x, depth + 1))
    return out


def _rank(track: dict) -> int:
    lan = str(track.get("lan") or "").lower()
    for i, p in enumerate(_PREFER):
        if lan.startswith(p):
            return i
    return len(_PREFER)


def join_body(body: list[dict]) -> str:
    """按时间顺序拼接字幕行。每行一句，交给后面的纠错和分段去成文。"""
    rows = sorted(body, key=lambda r: float(r.get("from") or 0))
    lines = [str(r.get("content") or "").strip() for r in rows]
    return "\n".join(ln for ln in lines if ln)


def extract_subtitle_text(payload: dict, fetch_json: FetchJson) -> str | None:
    """从字幕接口响应里拿到纯文本；没有字幕返回 None。"""
    body = _find_body(payload)
    if body:
        return join_body(body) or None
    tracks = _find_tracks(payload)
    if not tracks:
        return None
    for track in sorted(tracks, key=_rank):
        data = fetch_json(str(track["subtitle_url"]))
        if data and (body := _find_body(data)):
            text = join_body(body)
            if text:
                return text
    return None
