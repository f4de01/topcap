"""媒体下载。

CDN 的麻烦不止是"连不上"，更常见的是**连得上但被限速**：下过几个文件之后速率突然从
几百 KB/s 掉到几十 KB/s 甚至卡死。读超时不会触发（服务端一直在吐字节），只靠超时救不了。
所以把"慢"也当成一种失败：掉速即换地址，多地址接力加 Range 续传，先探活再下载。
"""
from __future__ import annotations

import random
import threading
import time
from pathlib import Path
from typing import Callable

import httpx

CHUNK = 1 << 16
READ_TIMEOUT = 20.0
PROBE_TIMEOUT = 6.0
MAX_PROBES = 10
MAX_TURNS = 24
MAX_STALL_SWITCHES = 6
STALL_WINDOW = 5.0
STALL_RATIO = 0.4
STALL_FLOOR = 80 * 1024

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126 Safari/537.36")
BASE_HEADERS = {
    "User-Agent": UA, "Accept": "*/*", "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "identity", "Sec-Fetch-Dest": "video",
    "Sec-Fetch-Mode": "no-cors", "Sec-Fetch-Site": "cross-site",
}


class DownloadError(RuntimeError):
    pass


class _Speed:
    def __init__(self) -> None:
        self.best = 0.0

    def note(self, rate: float) -> None:
        self.best = max(self.best, rate)

    @property
    def floor(self) -> float:
        return max(float(STALL_FLOOR), self.best * STALL_RATIO)


class Downloader:
    """一个 run 共用一个下载器：限制同时在下的文件数，带代理配置。"""

    def __init__(self, slots: int = 2, proxy: str = ""):
        self._gate = threading.Semaphore(max(slots, 1))
        self.proxy = proxy

    def _client(self, timeout: float) -> httpx.Client:
        kwargs = {"timeout": httpx.Timeout(timeout, connect=12.0, read=READ_TIMEOUT),
                  "follow_redirects": True}
        if self.proxy:
            kwargs["proxy"] = self.proxy
        return httpx.Client(**kwargs)

    def download(self, urls: list[str], dest: Path, *, extra_headers: dict[str, str] | None = None,
                 log: Callable[[str], None] | None = None,
                 on_progress: Callable[[int, int | None], None] | None = None) -> Path:
        order = list(dict.fromkeys(u for u in urls if u))
        if not order:
            raise DownloadError("没有可用的下载地址")
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_suffix(dest.suffix + ".part")
        headers = {**BASE_HEADERS, **(extra_headers or {})}
        errors: list[str] = []
        say = log or (lambda _m: None)

        with self._gate, self._client(60.0) as client:
            total, alive = None, False
            for i, url in enumerate(order[:MAX_PROBES]):
                try:
                    total = _probe(url, client, headers)
                    order = order[i:] + order[:i]
                    alive = True
                    break
                except Exception as exc:
                    errors.append(f"探活失败 {type(exc).__name__}: {exc}")
            if not alive:
                hint = f"（经代理 {self.proxy}，请先确认代理可用）" if self.proxy else ""
                raise DownloadError(f"前 {min(MAX_PROBES, len(order))} 个地址都探不通{hint}："
                                    + "; ".join(errors[:3]))

            stalls = 0
            speed = _Speed()
            for turn in range(MAX_TURNS):
                have = part.stat().st_size if part.exists() else 0
                if total is not None and 0 < total <= have:
                    break
                url = order[turn % len(order)]
                try:
                    got, why, rate = _pull(client, url, part, have, total, headers, speed,
                                           watch_speed=stalls < MAX_STALL_SWITCHES,
                                           on_progress=on_progress)
                except Exception as exc:
                    errors.append(f"{type(exc).__name__}: {exc}")
                    time.sleep(min(2 ** (turn % 4), 6) + random.uniform(0, 0.5))
                    continue
                if why == "stall":
                    stalls += 1
                    say(f"    地址 {turn % len(order) + 1} 只有 {rate / 1024:.0f}KB/s，换下一个")
                    if stalls == MAX_STALL_SWITCHES:
                        say("    换了几次都慢，不再切换，把它下完")
                    continue
                if got == 0 and have == 0:
                    errors.append("下到 0 字节")
                    continue
                done = part.stat().st_size if part.exists() else 0
                if total is None or done >= total:
                    part.replace(dest)
                    return dest
                errors.append(f"不完整 {done}/{total}")

        done = part.stat().st_size if part.exists() else 0
        if total is not None and done >= total > 0:
            part.replace(dest)
            return dest
        raise DownloadError(f"下载未完成（{done}/{total}）：" + "; ".join(errors[:5]))


def _probe(url: str, client: httpx.Client, headers: dict[str, str]) -> int | None:
    """一个字节的 Range 请求探活。返回总字节数；拿不到长度但可达时返回 None。"""
    head = {**headers, "Range": "bytes=0-0"}
    with client.stream("GET", url, headers=head, timeout=PROBE_TIMEOUT) as r:
        if r.status_code not in (200, 206):
            raise DownloadError(f"HTTP {r.status_code}")
        rng = r.headers.get("content-range", "")
        if "/" in rng:
            total = rng.rsplit("/", 1)[-1]
            if total.isdigit():
                return int(total)
        length = r.headers.get("content-length")
        return int(length) if length and length.isdigit() and r.status_code == 200 else None


def _pull(client: httpx.Client, url: str, part: Path, have: int, total: int | None,
          headers: dict[str, str], speed: _Speed, watch_speed: bool,
          on_progress: Callable[[int, int | None], None] | None) -> tuple[int, str, float]:
    head = dict(headers)
    if have:
        head["Range"] = f"bytes={have}-"
    t0 = time.monotonic()
    got = 0
    mark_t, mark_b = t0, 0
    with client.stream("GET", url, headers=head) as r:
        if have and r.status_code == 200:
            part.unlink(missing_ok=True)
            have = 0
        elif r.status_code not in (200, 206):
            raise DownloadError(f"HTTP {r.status_code}")
        with open(part, "ab" if have else "wb") as f:
            for chunk in r.iter_bytes(CHUNK):
                f.write(chunk)
                got += len(chunk)
                if on_progress:
                    on_progress(have + got, total)
                now = time.monotonic()
                if now - mark_t < STALL_WINDOW:
                    continue
                rate = (got - mark_b) / (now - mark_t)
                if watch_speed and rate < speed.floor:
                    return got, "stall", rate
                speed.note(rate)
                mark_t, mark_b = now, got
    took = time.monotonic() - t0
    rate = got / max(took, 1e-6)
    if took >= STALL_WINDOW:
        speed.note(rate)
    return got, "eof", rate


def cleanup(path: Path) -> None:
    path.unlink(missing_ok=True)
    path.with_suffix(path.suffix + ".part").unlink(missing_ok=True)
