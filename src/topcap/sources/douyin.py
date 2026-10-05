"""抖音。没有字幕接口，一律本地转写。CDN 要 Referer，链接约一天有效。"""
from __future__ import annotations

from ..client import TikHub, TikHubError
from ..config import AUTHOR_PAGES
from ..links import Ref
from ..models import Author, Candidate, Media, Part
from . import iso_date, register, title_from

SEARCH = "/api/v1/douyin/search/fetch_video_search_v1"
DETAIL = "/api/v1/douyin/web/fetch_one_video"
USER_ID = "/api/v1/douyin/web/get_sec_user_id"
USER_PROFILE = "/api/v1/douyin/web/handler_user_profile"
USER_POSTS_APP = "/api/v1/douyin/app/v3/fetch_user_post_videos"
USER_POSTS_WEB = "/api/v1/douyin/web/fetch_user_post_videos"

HEADERS = {"Referer": "https://www.douyin.com/"}
SEARCH_PRICE_USD = 0.01      # 搜索系列接口每次调用固定价，不打折


class Douyin:
    name = "抖音"

    def search(self, client: TikHub, keyword: str, pages: int) -> list[Candidate]:
        out: list[Candidate] = []
        cursor, search_id, backtrace = 0, "", ""
        seen: set[str] = set()
        for _page in range(max(pages, 1)):
            try:
                payload = client.post(
                    SEARCH, keyword=keyword, cursor=cursor, sort_type="0",
                    publish_time="0", filter_duration="0", content_type="1",   # 只要视频
                    search_id=search_id, backtrace=backtrace,
                )
            except Exception:
                if not out:
                    raise
                break                      # 翻页失败就用手上的结果
            data = payload.get("data") or {}
            rows = data.get("data") or []
            if not rows:
                break
            for row in rows:
                info = row.get("aweme_info")
                cand = to_candidate(info) if info else None
                if cand and cand.id not in seen:
                    seen.add(cand.id)
                    out.append(cand)
            if not data.get("has_more"):
                break
            cursor = data.get("cursor", cursor)
            backtrace = data.get("backtrace", "") or ""
            # search_id 要从 log_pb.impr_id 取；extra.search_request_id 实测恒为空
            search_id = ((data.get("log_pb") or {}).get("impr_id")
                         or (data.get("extra") or {}).get("search_request_id") or search_id)
        return out

    def from_id(self, client: TikHub, id: str) -> Candidate:
        info = _detail(client, id)
        cand = to_candidate(info)
        if cand is None:
            raise ValueError("这条抖音内容已失效、私密或没有可播放地址")
        return cand

    def refresh(self, client: TikHub, cand: Candidate) -> Candidate:
        info = _detail(client, cand.id)
        video = info.get("video") or {}
        urls = ((video.get("play_addr") or {}).get("url_list")) or []
        if not urls:
            raise RuntimeError("这条抖音内容现在取不到播放地址")
        cand.media = Media(urls=list(urls), kind="video",
                           expires_at=video.get("cdn_url_expired"), headers=dict(HEADERS))
        if not cand.parts:
            cand.parts = [Part(cid=cand.id, duration_sec=cand.duration_sec)]
        return cand

    def author(self, client: TikHub, ref: Ref) -> Author:
        sec = ref.id
        try:   # 短链还原出来的 sec_user_id 有时被转义过，交给官方接口抽更稳
            sec = ((client.get(USER_ID, url=ref.url).get("data") or {}).get("sec_user_id")) or sec
        except Exception:
            pass
        user = ((client.get(USER_PROFILE, sec_user_id=sec).get("data") or {}).get("user") or {})
        return Author(self.name, sec, (user.get("nickname") or "").strip() or "未知博主",
                      f"https://www.douyin.com/user/{sec}", user.get("aweme_count"))

    def works(self, client: TikHub, author: Author, limit: int) -> list[Candidate]:
        out: list[Candidate] = []
        cursor, seen = "0", set()
        for _page in range(AUTHOR_PAGES):
            if len(out) >= limit:
                break
            data = _posts_page(client, author.id, cursor)
            rows = data.get("aweme_list") or []
            if not rows:
                break
            for info in rows:
                cand = to_candidate(info)
                if cand and cand.id not in seen:
                    seen.add(cand.id)
                    out.append(cand)
                    if len(out) >= limit:
                        break
            if not data.get("has_more"):
                break
            cursor = str(data.get("max_cursor") or cursor)
        return out

    def subtitles(self, client: TikHub, cand: Candidate) -> list[str] | None:
        return None


def _detail(client: TikHub, aweme_id: str) -> dict:
    data = client.get(DETAIL, aweme_id=aweme_id).get("data") or {}
    info = data.get("aweme_detail") or data.get("aweme_details") or data
    if isinstance(info, list):
        info = info[0] if info else {}
    return info or {}


def _posts_page(client: TikHub, sec: str, cursor: str) -> dict:
    """App 接口优先（官方说 Web 版不稳），失败回退 Web。"""
    try:
        return client.get(USER_POSTS_APP, sec_user_id=sec, max_cursor=cursor, count=20).get("data") or {}
    except TikHubError:
        return client.get(USER_POSTS_WEB, sec_user_id=sec, max_cursor=cursor, count=20).get("data") or {}


def to_candidate(info: dict) -> Candidate | None:
    """把一条 aweme 结构转成候选。删除、私密、审核中、没有播放地址的不进清单。"""
    status = info.get("status") or {}
    if status.get("is_delete") or status.get("is_private") or status.get("is_prohibited"):
        return None
    video = info.get("video") or {}
    urls = ((video.get("play_addr") or {}).get("url_list")) or []
    if not urls:
        return None                       # 图文作品没有 play_addr，这里一并排除
    aweme_id = str(info.get("aweme_id") or "")
    st = info.get("statistics") or {}
    duration_ms = info.get("duration") or video.get("duration") or 0
    duration = int(duration_ms / 1000) if duration_ms else None
    return Candidate(
        index=0, source="抖音", id=aweme_id,
        title=title_from(info.get("item_title") or info.get("desc") or ""),
        author=((info.get("author") or {}).get("nickname") or "").strip() or "未知作者",
        url=f"https://www.douyin.com/video/{aweme_id}",
        likes=st.get("digg_count"), duration_sec=duration,
        published_at=iso_date(info.get("create_time")),
        parts=[Part(cid=aweme_id, duration_sec=duration)],
        media=Media(urls=list(urls), kind="video", expires_at=video.get("cdn_url_expired"),
                    headers=dict(HEADERS)),
    )


register(Douyin())
