"""单条内容的加工管线：取文案 -> 落原始文案 -> 纠错分段 -> 写成稿。

文案是唯一的硬指标：内容数据缺失不算失败，文案拿不到才算失败。
每条内容独立 try，一条炸掉不影响同批其余内容。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import media, render
from .asr import Recognizer
from .client import TikHub
from .config import Config, LibraryPaths
from .models import Candidate, Part
from .sources import REGISTRY, refresh_media
from .text import LLM, polish

MIN_CHARS = 20          # 原始文案短于这个数视为无语音，不出成稿


@dataclass
class Result:
    candidate: Candidate
    ok: bool
    md_path: Path | None = None
    stage: str = ""
    error: str = ""
    transcript_source: str = ""
    chars: int = 0
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return {"index": self.candidate.index, "source": self.candidate.source,
                "title": self.candidate.title, "url": self.candidate.url,
                "stage": self.stage, "error": self.error}


class Context:
    """一次 run 共享的资源。"""

    def __init__(self, cfg: Config, client: TikHub, paths: LibraryPaths, library: str, *,
                 keep_media: bool = False, use_subtitle: bool = True,
                 say: Callable[[str], None] | None = None,
                 stage: Callable[[str], None] | None = None):
        self.cfg, self.client, self.paths, self.library = cfg, client, paths, library
        self.keep_media, self.use_subtitle = keep_media, use_subtitle
        self.say = say or (lambda _m: None)
        self.stage = stage or (lambda _m: None)
        self.downloader = media.Downloader(cfg.download_slots, cfg.proxy)
        self.recognizer = Recognizer(cfg.models)
        self.llm = LLM(cfg.llm_key, cfg.llm_base, cfg.llm_model, cfg.llm_concurrency) if cfg.llm_enabled else None


def _transcribe_part(ctx: Context, cand: Candidate, part: Part, n: int) -> str:
    source = REGISTRY[cand.source]
    if cand.source == "B站":
        ctx.stage(f"取地址 P{n}")
        m = source.play_media(ctx.client, cand, part)
    else:
        m = cand.media
        if not m or not m.urls:
            raise RuntimeError("没有可用的播放地址")
    suffix = ".m4a" if m.kind == "audio" else ".mp4"
    dest = ctx.paths.media / f"{cand.index:02d}_{cand.id}_{part.cid}{suffix}"
    ctx.stage(f"下载{'音频' if m.kind == 'audio' else '视频'} P{n}" if len(cand.parts) > 1 else "下载")
    ctx.downloader.download(m.urls, dest, extra_headers=m.headers, log=ctx.say)
    try:
        dur = part.duration_sec or cand.duration_sec
        ctx.stage(f"转写（{dur // 60}:{dur % 60:02d}）" if dur else "转写")
        return ctx.recognizer.transcribe(dest)
    finally:
        if not ctx.keep_media:
            media.cleanup(dest)


def _raw_parts(ctx: Context, cand: Candidate) -> tuple[list[str], str]:
    """返回 (各 P 的原始文案, 文案来源)。"""
    ctx.stage("取详情")
    cand = refresh_media(ctx.client, cand)
    if not cand.parts:
        raise RuntimeError("取不到分 P 信息")
    if ctx.use_subtitle and cand.source == "B站":
        ctx.stage("查字幕")
        texts = REGISTRY[cand.source].subtitles(ctx.client, cand)
        if texts:
            return texts, "subtitle"
        ctx.say("    没有 CC 字幕，改走本地转写")
    return [_transcribe_part(ctx, cand, p, n) for n, p in enumerate(cand.parts, start=1)], "asr"


def process(cand: Candidate, ctx: Context) -> Result:
    stage = "取文案"
    try:
        raws, origin = _raw_parts(ctx, cand)
        raw_all = "\n\n".join(raws)
        raw_path = ctx.paths.raw / f"{cand.index:02d}.txt"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(raw_all, encoding="utf-8")
        if len(raw_all.replace("\n", "").strip()) < MIN_CHARS:
            return Result(cand, False, stage=stage, transcript_source=origin,
                          error=f"文案只有 {len(raw_all.strip())} 字，可能是纯音乐或无人声")

        stage = "纠错分段"
        ctx.stage(f"纠错与分段（{len(raw_all)} 字）")
        context = f"{cand.source}平台，作者「{cand.author}」，标题「{cand.title}」"
        notes: list[str] = []
        finals: list[tuple[str, str]] = []
        for part, raw in zip(cand.parts, raws):
            # 字幕是一行一句、没有标点的碎片。有 LLM 时拼成整段让它补标点再分段；
            # 没有 LLM 时保留逐行形态，比一大段无标点的文字好读。
            source_text = raw.replace("\n", "") if origin == "subtitle" and ctx.llm else raw
            text, rep = polish(source_text, ctx.llm, context=context)
            finals.append((part.title, text))
            if rep.llm_down:
                notes.append(f"LLM 不可用（{rep.llm_error}）；本篇只做了繁转简和清理")
            else:
                if rep.rejected_corrections:
                    notes.append(f"{rep.rejected_corrections} 段纠错被守卫拦下，已回退原文")
                if rep.failed_corrections:
                    notes.append(f"{rep.failed_corrections} 段纠错调用失败，已回退原文")
            if rep.rejected_segmentation:
                notes.append("分段被守卫拦下，已回退未分段文本")
            if rep.skipped and rep.skipped not in notes:
                notes.append(rep.skipped)

        stage = "写成稿"
        body = render.compose_parts(finals)
        path = render.write(cand, body, ctx.paths.vault_dir, library=ctx.library, transcript_source=origin)
        return Result(cand, True, md_path=path, transcript_source=origin,
                      chars=sum(len(t) for _, t in finals), notes=list(dict.fromkeys(notes)))
    except Exception as exc:
        return Result(cand, False, stage=stage, error=f"{type(exc).__name__}: {exc}")
