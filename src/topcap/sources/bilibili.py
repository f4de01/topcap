"""B站。有 CC 字幕接口（ADR 0005）；音频是独立的 DASH 流，约为视频的十分之一体积。"""
from __future__ import annotations

from urllib.parse import urlparse, urlunparse

from ..client import TikHub, TikHubError
from ..config import AUTHOR_PAGES
from ..links import Ref
from ..models import Author, Candidate, Media, Part
from . import clock_to_sec, iso_date, register, strip_tags, title_from
from ..subtitle import extract_subtitle_text

SEARCH = "/api/v1/bilibili/web/fetch_general_search"
DETAIL = "/api/v1/bilibili/web/fetch_one_video"
PLAYURL = "/api/v1/bilibili/web/fetch_video_playurl"
SUBTITLE = "/api/v1/bilibili/web/fetch_video_subtitle"
USER_POSTS_APP = "/api/v1/bilibili/app/fetch_user_videos"
USER_POSTS_WEB = "/api/v1/bilibili/web/fetch_user_post_videos"

HEADERS = {"Referer": "https://www.bilibili.com/"}

# upos 地址可以换 CDN 厂商：同一个 path 换 host 就是同一个文件的另一个来源。
# B站原生只给 2 个地址，一个被限速就没得换；实测这些镜像 Content-Length 一致，可跨镜像续传。
UPOS_MIRRORS = (
    "upos-sz-mirrorcos.bilivideo.com", "upos-sz-mirrorcosov.bilivideo.com",
    "upos-sz-mirrorcoso1.bilivideo.com", "upos-sz-mirrorali.bilivideo.com",
    "upos-sz-mirroraliov.bilivideo.com", "upos-sz-mirroralib.bilivideo.com",
    "upos-sz-mirrorhw.bilivideo.com", "upos-sz-mirrorhwo1.bilivideo.com",
    "upos-sz-mirror08c.bilivideo.com", "upos-sz-mirror08h.bilivideo.com",
    "upos-sz-estgoss.bilivideo.com",
)


class Bilibili:
    name = "B站"

    def search(self, client: TikHub, keyword: str, pages: int) -> list[Candidate]:
        out: list[Candidate] = []
        seen: set[str] = set()
        for page in range(1, max(pages, 1) + 1):
            try:
                payload = client.get(SEARCH, keyword=keyword, order="totalrank",
                                     page=page, page_size=20)
            except Exception:
                if not out:
                    raise
                break
            rows = ((payload.get("data") or {}).get("data") or {}).get("result") or []
            if not rows:
                break
            for row in rows:
                if row.get("type") != "video" or not row.get("bvid") or row["bvid"] in seen:
                    continue
                seen.add(row["bvid"])
                out.append(Candidate(
                    index=0, source=self.name, id=row["bvid"],
                    title=title_from(strip_tags(row.get("title") or "")),
                    author=(row.get("author") or "").strip() or "未知作者",
                    url=f"https://www.bilibili.com/video/{row['bvid']}",
                    likes=row.get("like"), duration_sec=clock_to_sec(row.get("duration")),
                    published_at=iso_date(row.get("pubdate")), media=None,
                ))
        return out

    def from_id(self, client: TikHub, id: str) -> Candidate:
        d = _detail(client, id)
        st = d.get("stat") or {}
        cand = Candidate(
            index=0, source=self.name, id=d["bvid"],
            title=title_from(strip_tags(d.get("title") or "")),
            author=((d.get("owner") or {}).get("name") or "").strip() or "未知作者",
            url=f"https://www.bilibili.com/video/{d['bvid']}",
            likes=st.get("like"), duration_sec=clock_to_sec(d.get("duration")),
            published_at=iso_date(d.get("pubdate")), media=None,
        )
        cand.parts = _parts(d)
        return cand

    def refresh(self, client: TikHub, cand: Candidate) -> Candidate:
        """补齐分 P 信息。播放地址按 P 取，见 play_media。"""
        if not cand.parts:
            cand.parts = _parts(_detail(client, cand.id))
        return cand

    def play_media(self, client: TikHub, cand: Candidate, part: Part) -> Media:
        play = client.get(PLAYURL, bv_id=cand.id, cid=part.cid)
        pu = ((play.get("data") or {}).get("data")) or {}
        dash = pu.get("dash") or {}
        audio = dash.get("audio") or []
        if audio:
            best = max(audio, key=lambda a: int(a.get("bandwidth") or 0))
            urls = [u for u in [best.get("baseUrl")] + list(best.get("backupUrl") or []) if u]
            if urls:
                return Media(urls=_pool(urls), kind="audio", headers=dict(HEADERS))
        video = dash.get("video") or []
        if video:
            best = min(video, key=lambda v: int(v.get("bandwidth") or 1 << 30))
            urls = [u for u in [best.get("baseUrl")] + list(best.get("backupUrl") or []) if u]
            if urls:
                return Media(urls=_pool(urls), kind="video", headers=dict(HEADERS))
        durl = pu.get("durl") or []
        if durl:
            urls = [u for u in [durl[0].get("url")] + list(durl[0].get("backup_url") or []) if u]
            return Media(urls=_pool(urls), kind="video", headers=dict(HEADERS))
        raise RuntimeError("播放地址响应里没有可用流")

    def author(self, client: TikHub, ref: Ref) -> Author:
        # 不调 fetch_user_profile：实测对普通 uid 返回 400。名字从作品列表的第一条取。
        return Author(self.name, ref.id, "", f"https://space.bilibili.com/{ref.id}", None)

    def works(self, client: TikHub, author: Author, limit: int) -> list[Candidate]:
        out: list[Candidate] = []
        seen: set[str] = set()
        for page in range(1, AUTHOR_PAGES + 1):
            if len(out) >= limit:
                break
            rows, total = _works_page(client, author.id, page)
            author.works = total or author.works
            if not rows:
                break
            for row in rows:
                bvid = row.get("bvid")
                if not bvid or bvid in seen:
                    continue
                seen.add(bvid)
                name = (row.get("author") or "").strip() or "未知作者"
                if not author.name:
                    author.name = _owner_name(client, [r.get("bvid") for r in rows], author.id) or name
                out.append(Candidate(
                    index=0, source=self.name, id=bvid,
                    title=title_from(strip_tags(row.get("title") or "")),
                    author=name, url=f"https://www.bilibili.com/video/{bvid}",
                    likes=row.get("like"), duration_sec=clock_to_sec(row.get("duration")),
                    published_at=iso_date(row.get("ctime")), media=None,
                ))
                if len(out) >= limit:
                    break
            if total and page * 20 >= total:
                break
        return out

    def subtitles(self, client: TikHub, cand: Candidate) -> list[str] | None:
        """逐 P 取字幕。任一 P 没有就返回 None，整条内容走转写，不混用。"""
        if not cand.parts:
            return None
        aid = getattr(cand, "_aid", None) or _aid_of(client, cand)
        texts: list[str] = []
        for part in cand.parts:
            try:
                payload = client.get(SUBTITLE, a_id=aid, c_id=part.cid)
            except TikHubError:
                return None
            text = extract_subtitle_text(payload, _fetch_json)
            if not text:
                return None
            texts.append(text)
        return texts


def _fetch_json(url: str) -> dict | None:
    import httpx
    if url.startswith("//"):
        url = "https:" + url
    try:
        r = httpx.get(url, headers={**HEADERS, "User-Agent": "Mozilla/5.0"}, timeout=20)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def _detail(client: TikHub, bvid: str) -> dict:
    d = ((client.get(DETAIL, bv_id=bvid).get("data") or {}).get("data")) or {}
    if not d.get("bvid"):
        raise ValueError("取不到这条 B站 视频的详情，可能已删除或需要登录")
    return d


def _aid_of(client: TikHub, cand: Candidate) -> str:
    aid = str(_detail(client, cand.id).get("aid") or "")
    cand._aid = aid                         # type: ignore[attr-defined]
    return aid


def _parts(d: dict) -> list[Part]:
    pages = d.get("pages") or []
    if not pages:
        cid = d.get("cid")
        return [Part(cid=str(cid), title="", duration_sec=clock_to_sec(d.get("duration")))] if cid else []
    return [Part(cid=str(p.get("cid")), title=(p.get("part") or "").strip(),
                 duration_sec=clock_to_sec(p.get("duration"))) for p in pages if p.get("cid")]


def _owner_name(client: TikHub, bvids: list[str | None], uid: str, tries: int = 3) -> str:
    """UP 主本人的昵称。

    作品列表的 author 字段在联合投稿时是「某某 等联合创作」，不是本人；联合投稿的详情里
    owner 也可能是合作方。所以逐条看详情，直到 owner.mid 等于这位 UP 主的 uid，最多看三条。
    """
    for bvid in [b for b in bvids if b][:tries]:
        try:
            owner = _detail(client, bvid).get("owner") or {}
        except Exception:
            continue
        if str(owner.get("mid")) == str(uid) and (owner.get("name") or "").strip():
            return owner["name"].strip()
    return ""


def _works_page(client: TikHub, uid: str, page: int) -> tuple[list[dict], int]:
    """App 接口稳定且直接给秒数时长；Web 接口实测成片 400，留作兜底。"""
    try:
        inner = ((client.get(USER_POSTS_APP, user_id=uid, page=page, ps=20)
                  .get("data") or {}).get("data")) or {}
        return list(inner.get("item") or []), int(inner.get("count") or 0)
    except TikHubError:
        inner = ((client.get(USER_POSTS_WEB, uid=uid, pn=page, ps=20, order="pubdate")
                  .get("data") or {}).get("data")) or {}
        rows = [{**r, "duration": clock_to_sec(r.get("length")), "ctime": r.get("created")}
                for r in (((inner.get("list") or {}).get("vlist")) or [])]
        return rows, int((inner.get("page") or {}).get("count") or 0)


def _pool(urls: list[str]) -> list[str]:
    """把原始地址扩成一个够轮换的地址池。原始地址保持在最前面。"""
    out, seen = list(urls), set(urls)
    anchor = next((u for u in urls if "bilivideo" in urlparse(u).netloc), None)
    if anchor is None:
        return out
    p = urlparse(anchor)
    for host in UPOS_MIRRORS:
        if host == p.netloc:
            continue
        alt = urlunparse(("https", host, p.path, p.params, p.query, p.fragment))
        if alt not in seen:
            seen.add(alt)
            out.append(alt)
    return out


register(Bilibili())
