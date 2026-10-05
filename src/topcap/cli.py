"""命令行入口：init / search / run / doctor。

退出码约定：0 成功；1 错误；2 闸门拦下（需要人确认后加参数重跑）。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .config import ASR_SPEED, CONFIG_FILE, LONG_VIDEO_SEC, Config, LibraryPaths, load_config, save_config
from .models import Candidate, Library, write_json

app = typer.Typer(add_completion=False, no_args_is_help=True, rich_markup_mode=None,
                  help="TopCap：把抖音、B站视频批量转成完整文案的 Markdown，归档进 Obsidian。")
con = Console(highlight=False)
err = Console(stderr=True, highlight=False)

for stream in (sys.stdout, sys.stderr):      # Windows 控制台默认 GBK，中文和符号会炸
    try:
        stream.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _fmt_dur(sec: int | None) -> str:
    if not sec:
        return "-"
    return f"{sec // 60}:{sec % 60:02d}"


def _fmt_num(n: int | None) -> str:
    if n is None:
        return "-"
    if n >= 10_000:
        return f"{n / 10_000:.1f}w"
    return str(n)


def _cfg_or_die(need_tikhub: bool = True) -> Config:
    cfg = load_config()
    if need_tikhub and not cfg.tikhub_key:
        err.print(f"[red]还没有 TikHub API key。[/red]先运行 [bold]topcap init[/bold]，"
                  f"或设置环境变量 TIKHUB_API_KEY。配置文件：{CONFIG_FILE}")
        raise typer.Exit(1)
    return cfg


def _client(cfg: Config):
    from .client import TikHub
    return TikHub(cfg.tikhub_key, cfg.tikhub_base)


def _print_candidates(cands: list[Candidate], title: str) -> None:
    table = Table(title=title, show_lines=False, pad_edge=False)
    for col, justify in (("#", "right"), ("源", "left"), ("时长", "right"), ("点赞", "right"),
                         ("标题", "left"), ("作者", "left")):
        table.add_column(col, justify=justify, overflow="fold")
    for c in cands:
        flag = " ⚠" if (c.duration_sec or 0) > LONG_VIDEO_SEC else ""
        table.add_row(str(c.index), c.source, _fmt_dur(c.duration_sec) + flag,
                      _fmt_num(c.likes), c.title, c.author)
    con.print(table)
    if any((c.duration_sec or 0) > LONG_VIDEO_SEC for c in cands):
        con.print(f"[yellow]⚠ 超过 {LONG_VIDEO_SEC // 60} 分钟的内容，加工前需 --allow-long[/yellow]")


# --------------------------------------------------------------------------- init

@app.command()
def init() -> None:
    """交互式写入配置：TikHub key、LLM、知识库目录；并可下载语音模型。"""
    cfg = load_config()
    con.print(f"配置文件：{CONFIG_FILE}\n")
    cfg.tikhub_key = typer.prompt("TikHub API key（https://user.tikhub.io 获取）",
                                  default=cfg.tikhub_key or "", show_default=bool(cfg.tikhub_key),
                                  hide_input=True) or cfg.tikhub_key
    con.print("\n纠错与分段用任意 OpenAI 兼容服务，默认 DeepSeek。留空则只做繁转简和清理。")
    cfg.llm_key = typer.prompt("LLM API key（可留空）", default=cfg.llm_key or "",
                               show_default=bool(cfg.llm_key), hide_input=True) or ""
    if cfg.llm_key:
        cfg.llm_base = typer.prompt("LLM base_url", default=cfg.llm_base)
        cfg.llm_model = typer.prompt("LLM model", default=cfg.llm_model)
    con.print("\n成稿写到哪：填 Obsidian 知识库里的一个目录（不存在会创建）。")
    vault = typer.prompt("知识库目录", default=str(cfg.vault))
    cfg.vault = Path(vault).expanduser()
    path = save_config(cfg)
    cfg.vault.mkdir(parents=True, exist_ok=True)
    con.print(f"\n[green]✓[/green] 已写入 {path}")

    from .asr import model_files
    if model_files(cfg.models) is None:
        if typer.confirm("\n现在下载语音识别模型吗（SenseVoice-Small int8，约 165MB）？", default=True):
            _download_model(cfg)
    con.print("\n下一步：topcap search \"<主题词 | 视频链接 | 博主主页>\"")


def _download_model(cfg: Config) -> None:
    from rich.progress import BarColumn, DownloadColumn, Progress, TextColumn, TransferSpeedColumn
    from .asr import ensure_model
    with Progress(TextColumn("{task.description}"), BarColumn(), DownloadColumn(),
                  TransferSpeedColumn(), console=con) as prog:
        task = prog.add_task("下载模型", total=None)

        def on_progress(done: int, total: int | None) -> None:
            prog.update(task, completed=done, total=total)
        ensure_model(cfg.models, log=lambda m: prog.console.print(m), on_progress=on_progress)
    con.print(f"[green]✓[/green] 模型就绪：{cfg.models}")


# --------------------------------------------------------------------------- search

@app.command()
def search(
    text: list[str] = typer.Argument(..., help="主题词、视频链接、博主主页链接，或整段分享文案"),
    topic: Optional[str] = typer.Option(None, "--topic", "-t", help="链接输入时指定入库名，默认「收件箱」"),
    pages: Optional[int] = typer.Option(None, "--pages", "-p", help="主题搜索翻几页（每页约 10 条）"),
    limit: int = typer.Option(200, "--limit", help="博主库最多列多少条作品"),
) -> None:
    """列出候选清单，不下载任何媒体。之后用 run --pick 挑选加工。"""
    from .library import INBOX, load_candidates, load_library, merge_candidates, save_candidates, save_library
    from .sources import REGISTRY, parse_input, search_all
    from .sources.douyin import SEARCH_PRICE_USD

    cfg = _cfg_or_die()
    raw = " ".join(text).strip()
    try:
        kind, val = parse_input(raw)
    except ValueError as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    with _client(cfg) as client:
        if kind == "topic":
            keyword = str(val)
            n_pages = pages or cfg.search_pages
            lib = Library(name=keyword, kind="topic", query=keyword)
            cost = n_pages * SEARCH_PRICE_USD + n_pages * 0.001
            con.print(f"搜索「{keyword}」，抖音 + B站各 {n_pages} 页，预计花费约 ${cost:.3f}")

            def on_done(name: str, n: int | None, error: str | None) -> None:
                con.print(f"  {name}: {'失败 ' + error if error else f'{n} 条'}")
            found = search_all(client, keyword, n_pages, on_done)
        elif kind == "video":
            ref = val
            lib_name = topic or INBOX
            lib = Library(name=lib_name, kind="topic", query=lib_name)
            try:
                found = [REGISTRY[ref.source].from_id(client, ref.id)]
            except Exception as exc:
                err.print(f"[red]取不到这条内容：{exc}[/red]")
                raise typer.Exit(1)
        else:
            ref = val
            src = REGISTRY[ref.source]
            author = src.author(client, ref)
            found = src.works(client, author, limit)
            if not found:
                err.print("[red]这位博主没有可加工的作品[/red]")
                raise typer.Exit(1)
            lib_name = f"@{author.name or found[0].author}"
            existing = load_library(LibraryPaths.of(lib_name, cfg))
            if existing and existing.kind == "author" and existing.author_id != author.id:
                lib_name = f"{lib_name}·{ref.source}"       # 跨源同名博主，追加后缀
            lib = Library(name=lib_name, kind="author", query=ref.url,
                          author_source=ref.source, author_id=author.id)
            total = f"，共 {author.works} 条作品" if author.works else ""
            con.print(f"博主「{lib_name}」（{ref.source}{total}），列出 {len(found)} 条")

    paths = LibraryPaths.of(lib.name, cfg).ensure()
    existing_cands = load_candidates(paths)
    merged, added = merge_candidates(existing_cands, found)
    save_candidates(paths, merged)
    if load_library(paths) is None:
        save_library(paths, lib)

    if not merged:
        err.print("[yellow]没有找到可加工的内容[/yellow]")
        raise typer.Exit(1)
    _print_candidates(merged, f"库「{lib.name}」候选清单（本次新增 {added} 条，共 {len(merged)} 条）")
    con.print(f"\n清单：{paths.candidates}")
    con.print(f"下一步：topcap run \"{lib.name}\" --pick 1,3,5   或 --pick all")


# --------------------------------------------------------------------------- run

@app.command()
def run(
    library: str = typer.Argument(..., help="库名（search 时显示的名字）"),
    pick: str = typer.Option(..., "--pick", help="序号：1,3,5 或 2-6 或 all"),
    allow_long: bool = typer.Option(False, "--allow-long", help=f"允许加工超过 {LONG_VIDEO_SEC // 60} 分钟的内容"),
    keep_media: bool = typer.Option(False, "--keep-media", help="转写完保留媒体文件"),
    no_subtitle: bool = typer.Option(False, "--no-subtitle", help="B站不用 CC 字幕，一律本地转写"),
) -> None:
    """加工选中的候选：取文案、纠错分段、写成稿到知识库。"""
    from .library import load_candidates, parse_pick
    from .pipeline import Context, process

    cfg = _cfg_or_die()
    paths = LibraryPaths.of(library, cfg)
    cands = load_candidates(paths)
    if not cands:
        err.print(f"[red]库「{library}」没有候选清单。[/red]先运行 topcap search")
        raise typer.Exit(1)
    try:
        chosen_idx = parse_pick(pick, [c.index for c in cands])
    except ValueError as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    chosen = [c for c in cands if c.index in chosen_idx]

    long_ones = [c for c in chosen if (c.duration_sec or 0) > LONG_VIDEO_SEC]
    if long_ones and not allow_long:
        est = sum(c.duration_sec or 0 for c in long_ones) / ASR_SPEED
        err.print(f"[yellow]有 {len(long_ones)} 条超过 {LONG_VIDEO_SEC // 60} 分钟，"
                  f"光转写预计约 {est / 60:.0f} 分钟：[/yellow]")
        for c in long_ones:
            err.print(f"  #{c.index} {_fmt_dur(c.duration_sec)} {c.title}")
        err.print("确认要加工的话，加 --allow-long 重跑；或从 --pick 里去掉它们。")
        raise typer.Exit(2)

    paths.ensure()
    if not cfg.llm_enabled:
        con.print("[yellow]未配置 LLM key：只做繁转简和清理，不纠错不分段[/yellow]")
    con.print(f"加工 {len(chosen)} 条，成稿写到 {paths.vault_dir}\n")

    results = []
    failures = []
    t0 = time.monotonic()
    with _client(cfg) as client:
        for n, cand in enumerate(chosen, start=1):
            head = f"[{n}/{len(chosen)}] #{cand.index} {cand.source} {cand.title}"
            with con.status(head) as status:
                ctx = Context(cfg, client, paths, library, keep_media=keep_media, use_subtitle=not no_subtitle,
                              say=lambda m: con.print(f"[dim]{m}[/dim]"),
                              stage=lambda s: status.update(f"{head}  [dim]{s}[/dim]"))
                res = process(cand, ctx)
            results.append(res)
            if res.ok:
                origin = "字幕" if res.transcript_source == "subtitle" else "转写"
                con.print(f"[green]✓[/green] {head}  {res.chars} 字（{origin}）→ {res.md_path.name}")
                for note in res.notes:
                    con.print(f"    [yellow]注意：{note}[/yellow]")
            else:
                failures.append(res.to_json())
                con.print(f"[red]✗[/red] {head}  {res.stage}失败：{res.error}")

    write_json(paths.failures, failures)
    ok = sum(1 for r in results if r.ok)
    con.print(f"\n完成：{ok} 成功，{len(failures)} 失败，用时 {(time.monotonic() - t0) / 60:.1f} 分钟")
    if failures:
        con.print(f"失败清单：{paths.failures}")
    raise typer.Exit(0 if ok else 1)


# --------------------------------------------------------------------------- doctor

@app.command()
def doctor() -> None:
    """自检：配置、依赖、模型、目录。不联网、不下载。"""
    cfg = load_config()
    rows: list[tuple[str, str, str]] = []

    def ok(name: str, detail: str = "") -> None:
        rows.append(("[green]✓[/green]", name, detail))

    def bad(name: str, detail: str = "") -> None:
        rows.append(("[red]✗[/red]", name, detail))

    def warn(name: str, detail: str = "") -> None:
        rows.append(("[yellow]![/yellow]", name, detail))

    ok("topcap", f"v{__version__}，Python {sys.version.split()[0]}")
    (ok if CONFIG_FILE.is_file() else warn)("配置文件", str(CONFIG_FILE) + ("" if CONFIG_FILE.is_file() else "（不存在，运行 topcap init）"))
    (ok if cfg.tikhub_key else bad)("TikHub key", "已配置" if cfg.tikhub_key else "缺失")
    (ok if cfg.llm_enabled else warn)("LLM", f"{cfg.llm_model} @ {cfg.llm_base}" if cfg.llm_enabled else "未配置，成稿不纠错不分段")
    (ok if cfg.vault.is_dir() else warn)("知识库目录", str(cfg.vault) + ("" if cfg.vault.is_dir() else "（不存在，首次 run 会创建）"))
    ok("工作目录", str(cfg.work))

    try:
        import av  # noqa: F401
        ok("PyAV", av.__version__)
    except Exception as exc:
        bad("PyAV", f"导入失败：{exc}")
    try:
        from importlib import metadata
        from .asr import provider
        ver = metadata.version("sherpa-onnx")
        ok("sherpa-onnx", f"{ver}，推理后端 {provider()}")
        if provider() == "cpu":
            warn("GPU", "当前是 CPU 推理。有 NVIDIA 显卡可按 README 安装 CUDA 版 sherpa-onnx")
    except Exception as exc:
        bad("sherpa-onnx", f"导入失败：{exc}")
    from .asr import model_files
    from .config import MODEL_URL
    found = model_files(cfg.models)
    (ok if found else bad)("语音模型", str(found[0].parent) if found else f"未下载。运行 topcap init，或手动下载解压到 {cfg.models}\n    {MODEL_URL}")

    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(); table.add_column(); table.add_column(overflow="fold")
    for mark, name, detail in rows:
        table.add_row(mark, name, detail)
    con.print(table)
    if any(r[0].startswith("[red]") for r in rows):
        raise typer.Exit(1)


@app.callback(invoke_without_command=True)
def _main(ctx: typer.Context, version: bool = typer.Option(False, "--version", "-V")) -> None:
    if version:
        con.print(f"topcap {__version__}")
        raise typer.Exit()


if __name__ == "__main__":
    app()
