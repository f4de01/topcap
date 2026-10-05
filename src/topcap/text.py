"""文案修复：繁转简、纠错、分段。见 ADR 0003。

纠错和分段是两次独立调用。分段只允许插入换行，模型只给段首锚点、正文由代码切，
守卫是结构性的；纠错必须改字，只能用长度阈值兜底。
"""
from __future__ import annotations

import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import httpx
from opencc import OpenCC

_cc = OpenCC("t2s")

LENGTH_TOLERANCE = 0.15       # 纠错后长度变化超过这个比例就当作改写，回退原文
CHUNK_CHARS = 1200
RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 3
BACKOFF = 1.5
BREAK_AFTER = 6
COOL_DOWN = 60.0

_WS = re.compile(r"\s+")
_SENTENCE_END = re.compile(r"(?<=[。！？!?；;\n])")

CORRECT_PROMPT = """你是中文文案校对员。请修正下面这段口语转写文本中的用字错误。

严格遵守：
1. 只修正错别字、同音字误写、中英文混写中拼错的英文单词
2. 补全缺失的标点，但不要改变已有的断句
3. 绝对不要改写句子、不要调整语序、不要删除重复、不要压缩内容、不要补充原文没有的词
4. 逐字输出修正后的全文，不要任何解释、前言或标记
{context}
原文："""

CONTEXT_HINT = """
这段文本来自以下内容，请据此判断专有名词和同音字的正确写法：
{context}
"""

SEGMENT_PROMPT = """请为下面这段文本划分自然段落。

不要输出文本本身。只输出每个新段落**开头的前 8 个字**，一行一个，不加编号和引号。
第一段的开头不用输出。在话题转换处分段，每段大致 3-8 句。

如果整段文本不需要再分段，输出一个减号 `-`。

文本："""


@dataclass
class PolishReport:
    corrected_chunks: int = 0
    rejected_corrections: int = 0       # 守卫拦下：模型回了，但像改写
    failed_corrections: int = 0         # 调用失败：模型没回
    segmented: bool = False
    rejected_segmentation: bool = False
    skipped: str | None = None
    llm_down: bool = False
    llm_error: str = ""                 # 上游不可用的原因，例如认证失败


class LLMAuthError(RuntimeError):
    """401/403：key 不对。重试没有意义，整次 run 只报一次。"""


class _Breaker:
    """上游整体挂掉时停止重试。计的是单次请求，不是单次 complete()。"""

    def __init__(self, limit: int = BREAK_AFTER, cooldown: float = COOL_DOWN):
        self.limit, self.cooldown = limit, cooldown
        self._fails = 0
        self._opened = 0.0
        self._lock = threading.Lock()

    @property
    def tripped(self) -> bool:
        with self._lock:
            if self._fails < self.limit:
                return False
            if time.time() - self._opened > self.cooldown:
                self._fails = self.limit - 1
                return False
            return True

    def note(self, ok: bool) -> None:
        with self._lock:
            if ok:
                self._fails = 0
            else:
                self._fails += 1
                if self._fails == self.limit:
                    self._opened = time.time()


def normalize(text: str) -> str:
    """确定性清理：繁转简、统一空白、去掉重复标点。零风险，永远执行。"""
    text = _cc.convert(text or "")
    text = text.replace("　", " ").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"([，。！？、；：])\1{1,}", r"\1", text)
    return text.strip()


def chunks(text: str, size: int = CHUNK_CHARS) -> list[str]:
    """按句末标点切块，尽量不在句子中间断开。

    完全没有标点的文本（字幕拼接、极少数转写）没有句末可切，就按 size 硬切，
    否则一块几千字会撞上模型输出上限。
    """
    parts: list[str] = []
    for p in _SENTENCE_END.split(text):
        while len(p) > size:
            parts.append(p[:size])
            p = p[size:]
        if p:
            parts.append(p)
    out, buf = [], ""
    for part in parts:
        if buf and len(buf) + len(part) > size:
            out.append(buf)
            buf = part
        else:
            buf += part
    if buf:
        out.append(buf)
    return out or [text]


def split_at_anchors(chunk: str, anchors: list[str]) -> list[str]:
    """按锚点切分。锚点是模型给的段首片段，必须在原文里按序找到才生效。"""
    cuts: list[int] = []
    pos = 0
    for anchor in anchors:
        a = _WS.sub("", anchor)
        if len(a) < 4:
            continue
        idx = chunk.find(anchor, pos)
        if idx < 0:
            idx = chunk.find(anchor[:6], pos)
        if idx <= pos:
            continue
        cuts.append(idx)
        pos = idx
    if not cuts:
        return [chunk]
    parts, prev = [], 0
    for c in cuts:
        parts.append(chunk[prev:c])
        prev = c
    parts.append(chunk[prev:])
    return [p.strip() for p in parts if p.strip()]


class LLM:
    def __init__(self, key: str, base: str, model: str, concurrency: int = 4, timeout: float = 90.0):
        self.key, self.base, self.model = key, base.rstrip("/"), model
        self.concurrency = max(concurrency, 1)
        self.timeout = timeout
        self._client: httpx.Client | None = None
        self._lock = threading.Lock()
        self._gate = threading.Semaphore(self.concurrency)
        self.breaker = _Breaker()
        self.auth_error = ""

    @property
    def down(self) -> bool:
        return bool(self.auth_error) or self.breaker.tripped

    @property
    def enabled(self) -> bool:
        return bool(self.key)

    def _http(self) -> httpx.Client:
        if self._client is None:
            with self._lock:
                if self._client is None:
                    self._client = httpx.Client(
                        base_url=self.base, headers={"Authorization": f"Bearer {self.key}"},
                        timeout=self.timeout, limits=httpx.Limits(max_connections=self.concurrency * 2),
                    )
        return self._client

    def close(self) -> None:
        with self._lock:
            if self._client is not None:
                self._client.close()
                self._client = None

    def complete(self, prompt: str) -> str:
        if self.auth_error:
            raise LLMAuthError(self.auth_error)
        if self.breaker.tripped:
            raise RuntimeError("LLM 连续失败过多，已停止重试")
        body = {"model": self.model, "temperature": 0, "max_tokens": 8192,
                "messages": [{"role": "user", "content": prompt}]}
        last: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            with self._gate:
                try:
                    r = self._http().post("/chat/completions", json=body)
                    if r.status_code in (401, 403):
                        self.auth_error = f"LLM 认证失败（HTTP {r.status_code}），请检查 key 与 base_url"
                        raise LLMAuthError(self.auth_error)
                    if r.status_code not in RETRY_STATUS:
                        r.raise_for_status()
                        self.breaker.note(True)
                        return r.json()["choices"][0]["message"]["content"].strip()
                    last = httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request, response=r)
                except httpx.RequestError as exc:
                    last = exc
            self.breaker.note(False)
            if self.breaker.tripped:
                break
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(BACKOFF * 2 ** attempt + random.uniform(0, 0.4))
        raise last or RuntimeError("LLM 调用失败")

    def map(self, work, items: list):
        if len(items) < 2:
            return [work(x) for x in items]
        with ThreadPoolExecutor(max_workers=min(len(items), self.concurrency)) as pool:
            return list(pool.map(work, items))


def correct(text: str, llm: LLM, report: PolishReport, context: str = "") -> str:
    """纠错。守卫：某块长度变化超阈值就丢弃这块的改写，回退原文。"""
    hint = CONTEXT_HINT.format(context=context) if context else ""
    prompt = CORRECT_PROMPT.format(context=hint)

    def fix(chunk: str) -> tuple[str, str]:
        """返回 (结果, 状态)。状态：ok / rejected / failed。"""
        try:
            fixed = llm.complete(prompt + chunk)
        except Exception:
            return chunk, "failed"
        text, rejected = guard_correction(chunk, fixed)
        return text, "rejected" if rejected else "ok"

    out = []
    for fixed, state in llm.map(fix, chunks(text)):
        out.append(fixed)
        if state == "ok":
            report.corrected_chunks += 1
        elif state == "rejected":
            report.rejected_corrections += 1
        else:
            report.failed_corrections += 1
    return "".join(out)


def guard_correction(original: str, fixed: str, tolerance: float = LENGTH_TOLERANCE) -> tuple[str, bool]:
    """返回 (采用的文本, 是否被拦下)。纯函数，便于测试。"""
    delta = abs(len(fixed) - len(original)) / max(len(original), 1)
    if not fixed or delta > tolerance:
        return original, True
    return fixed, False


def segment(text: str, llm: LLM, report: PolishReport) -> str:
    """分段。模型只输出段首锚点，正文始终由我们自己切。"""
    fingerprint = _WS.sub("", text)
    blocks = chunks(text, CHUNK_CHARS)

    def anchors_for(block: str) -> list[str]:
        try:
            reply = llm.complete(SEGMENT_PROMPT + block)
        except Exception:
            return []
        lines = [ln.strip().strip("\"'“”「」") for ln in reply.splitlines() if ln.strip()]
        return [a for a in lines if a and a != "-"]

    out: list[str] = []
    for block, anchors in zip(blocks, llm.map(anchors_for, blocks)):
        out.extend(split_at_anchors(block, anchors))
    result = "\n\n".join(p for p in out if p)
    if _WS.sub("", result) != fingerprint:
        report.rejected_segmentation = True
        return text
    report.segmented = True
    return result


def polish(raw: str, llm: LLM | None, *, context: str = "") -> tuple[str, PolishReport]:
    """原始文案 -> 成稿文案。没有 key 时自动降级为只做确定性清理。"""
    report = PolishReport()
    text = normalize(raw)
    if llm is None or not llm.enabled:
        report.skipped = "未配置 LLM key，只做了繁转简和清理"
        return text, report
    text = normalize(correct(text, llm, report, context))
    if not llm.down:
        text = segment(text, llm, report)
    report.llm_down = llm.down
    report.llm_error = llm.auth_error or ("连续失败过多，已停止重试" if llm.breaker.tripped else "")
    return text, report
