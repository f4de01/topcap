"""输入识别：一段文字是主题词、内容链接，还是博主主页。纯函数，不联网。

各家 App 的"复制链接"给的都不是链接，是一段话：
「7.87 复制打开抖音，看看【…的作品】 https://v.douyin.com/xxx/ 复制此链接…」。
让调用方自己截 URL 是在安排一件必然出错的活，所以这里收整段文案。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# 只认可打印 ASCII：分享链接一定是 ASCII 的，中文、全角标点、emoji 天然是终止符
_URL_RE = re.compile(r"https?://[!-~]+")
_BARE_RE = re.compile(r"(?<![!-~])((?:v\.douyin|b23\.tv|www\.douyin|space\.bilibili|"
                      r"www\.bilibili|m\.bilibili|bilibili\.com)[!-~]*)")
_TRAIL = "。，、；：！？…）】》」』.,;:!?)]}>\"'"

SHORT_HOSTS = ("v.douyin.com", "b23.tv")

DOUYIN_VIDEO = re.compile(r"douyin\.com/(?:share/)?(?:video|note)/(\d+)")
DOUYIN_USER = re.compile(r"douyin\.com/(?:share/)?user/([A-Za-z0-9_\-]+)")
BILI_VIDEO = re.compile(r"bilibili\.com/video/(BV[0-9A-Za-z]{10})", re.I)
BILI_USER = re.compile(r"space\.bilibili\.com/(\d+)")


def extract_urls(text: str) -> list[str]:
    """从任意文本里把链接捞出来，按出现顺序去重。"""
    found: list[str] = []
    for raw in _URL_RE.findall(text or ""):
        url = raw.rstrip(_TRAIL)
        if url and url not in found:
            found.append(url)
    if not found:
        for bare in _BARE_RE.findall(text or ""):
            url = "https://" + bare.rstrip(_TRAIL)
            if url not in found:
                found.append(url)
    return found


def is_short(url: str) -> bool:
    return any(h in url for h in SHORT_HOSTS)


@dataclass(frozen=True)
class Ref:
    """一条已识别的链接。kind: video | user；source: 抖音 | B站。"""

    kind: str
    source: str
    id: str
    url: str


def classify_url(url: str) -> Ref | None:
    """认一条**已还原**的长链接。短链要先 resolve。"""
    if (m := DOUYIN_VIDEO.search(url)):
        return Ref("video", "抖音", m.group(1), url)
    if (m := DOUYIN_USER.search(url)):
        return Ref("user", "抖音", m.group(1), url)
    if (m := BILI_VIDEO.search(url)):
        return Ref("video", "B站", m.group(1), url)
    if (m := BILI_USER.search(url)):
        return Ref("user", "B站", m.group(1), url)
    return None


def looks_like_topic(text: str) -> bool:
    """没有任何链接就是主题词。"""
    return not extract_urls(text)
