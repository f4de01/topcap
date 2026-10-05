"""配置与目录布局。

配置只有一处：`~/.topcap/config.toml`，由 `topcap init` 写入。环境变量可以逐项覆盖，
方便 CI 和一次性运行。不读当前目录的 .env：CLI 装在 PATH 上、从任意目录调用，
"当前目录"没有稳定含义。
"""
from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

HOME = Path(os.getenv("TOPCAP_HOME") or Path.home() / ".topcap")
CONFIG_FILE = HOME / "config.toml"

# 用 2024-07-17 这份：它是 FunAudioLLM 原版 SenseVoice-Small 的 int8 导出。
# release 里更新的 2025-09-09 版是 ASLP-lab WSYue-ASR 的粤语微调模型，README 写明了来源，
# 实测普通话会吞字、且不出标点，不能用。
MODEL_TARBALL = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"
MODEL_URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{MODEL_TARBALL}.tar.bz2"

# 超过这个时长（秒）的内容要显式 --allow-long。转写约为时长的 1/15，
# 一条两小时的合集光转写就要八分钟，还要下几百兆。
LONG_VIDEO_SEC = 1200
ASR_SPEED = 15.0            # CPU 上 SenseVoice-Small 约 15 倍实时，用来估耗时
AUTHOR_PAGES = 12           # 博主作品翻页上限：几百上千条作品的博主，不设上限会一次烧光额度

_UNSAFE = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def safe_name(text: str, limit: int = 60) -> str:
    """把任意文本压成一个能当文件名用的短串。"""
    cleaned = _UNSAFE.sub("", text or "").strip().strip(".")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:limit] or "untitled"


@dataclass
class Config:
    tikhub_key: str = ""
    tikhub_base: str = "https://api.tikhub.io"
    llm_key: str = ""
    llm_base: str = "https://api.deepseek.com/v1"
    llm_model: str = "deepseek-chat"
    vault: Path = field(default_factory=lambda: HOME / "vault")
    work: Path = field(default_factory=lambda: HOME / "work")
    models: Path = field(default_factory=lambda: HOME / "models")
    proxy: str = ""
    download_slots: int = 2
    llm_concurrency: int = 4
    search_pages: int = 2

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_key)

    def to_toml(self) -> str:
        def q(s: object) -> str:
            return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'
        lines = [
            "# TopCap 配置。由 `topcap init` 生成，可手改。",
            "# 环境变量 TIKHUB_API_KEY / LLM_API_KEY / LLM_BASE_URL / LLM_MODEL / TOPCAP_VAULT / TOPCAP_WORK / TOPCAP_PROXY 可覆盖。",
            "",
            "[tikhub]",
            f"api_key = {q(self.tikhub_key)}",
            f"base_url = {q(self.tikhub_base)}",
            "",
            "[llm]",
            "# 任意 OpenAI 兼容服务。留空则跳过纠错，只做繁转简和清理。",
            '# ollama 也是兼容的：base_url = "http://localhost:11434/v1"',
            f"api_key = {q(self.llm_key)}",
            f"base_url = {q(self.llm_base)}",
            f"model = {q(self.llm_model)}",
            "",
            "[paths]",
            "# 成稿写到这里：填你的 Obsidian 知识库下的一个目录",
            f"vault = {q(self.vault.as_posix())}",
            "# 阶段产物（候选清单、媒体、原始文案）",
            f"work = {q(self.work.as_posix())}",
            f"models = {q(self.models.as_posix())}",
            "",
            "[network]",
            "# 媒体下载走的代理，CDN 限速的最后手段。形如 http://127.0.0.1:7890",
            f"proxy = {q(self.proxy)}",
            f"download_slots = {self.download_slots}",
            f"llm_concurrency = {self.llm_concurrency}",
            "# 抖音搜索默认翻几页，每页约 10 条、0.01 美元",
            f"search_pages = {self.search_pages}",
            "",
        ]
        return "\n".join(lines)


def load_config(path: Path | None = None) -> Config:
    """文件 < 环境变量。文件不存在也能返回一份默认配置，由调用方决定是否报错。"""
    cfg = Config()
    file = path or CONFIG_FILE
    if file.is_file():
        data = tomllib.loads(file.read_text(encoding="utf-8"))
        t, l, p, n = (data.get(k) or {} for k in ("tikhub", "llm", "paths", "network"))
        cfg.tikhub_key = t.get("api_key", cfg.tikhub_key)
        cfg.tikhub_base = t.get("base_url", cfg.tikhub_base)
        cfg.llm_key = l.get("api_key", cfg.llm_key)
        cfg.llm_base = l.get("base_url", cfg.llm_base)
        cfg.llm_model = l.get("model", cfg.llm_model)
        if p.get("vault"):
            cfg.vault = Path(p["vault"]).expanduser()
        if p.get("work"):
            cfg.work = Path(p["work"]).expanduser()
        if p.get("models"):
            cfg.models = Path(p["models"]).expanduser()
        cfg.proxy = n.get("proxy", cfg.proxy)
        cfg.download_slots = int(n.get("download_slots", cfg.download_slots))
        cfg.llm_concurrency = int(n.get("llm_concurrency", cfg.llm_concurrency))
        cfg.search_pages = int(n.get("search_pages", cfg.search_pages))

    env = os.getenv
    cfg.tikhub_key = env("TIKHUB_API_KEY") or cfg.tikhub_key
    cfg.tikhub_base = env("TIKHUB_BASE_URL") or cfg.tikhub_base
    cfg.llm_key = env("LLM_API_KEY") or cfg.llm_key
    cfg.llm_base = env("LLM_BASE_URL") or cfg.llm_base
    cfg.llm_model = env("LLM_MODEL") or cfg.llm_model
    if env("TOPCAP_VAULT"):
        cfg.vault = Path(env("TOPCAP_VAULT")).expanduser()
    if env("TOPCAP_WORK"):
        cfg.work = Path(env("TOPCAP_WORK")).expanduser()
    if env("TOPCAP_PROXY"):
        cfg.proxy = env("TOPCAP_PROXY")
    return cfg


def save_config(cfg: Config, path: Path | None = None) -> Path:
    file = path or CONFIG_FILE
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(cfg.to_toml(), encoding="utf-8")
    return file


@dataclass(frozen=True)
class LibraryPaths:
    """一个库的目录布局：成稿在知识库，阶段产物在工作目录。"""

    name: str
    vault_dir: Path
    work_dir: Path

    @classmethod
    def of(cls, name: str, cfg: Config) -> "LibraryPaths":
        safe = safe_name(name)
        return cls(name=name, vault_dir=cfg.vault / safe, work_dir=cfg.work / safe)

    @property
    def candidates(self) -> Path:
        return self.work_dir / "candidates.json"

    @property
    def library_json(self) -> Path:
        return self.work_dir / "library.json"

    @property
    def failures(self) -> Path:
        return self.work_dir / "failures.json"

    @property
    def raw(self) -> Path:
        return self.work_dir / "raw"

    @property
    def media(self) -> Path:
        return self.work_dir / "media"

    def ensure(self) -> "LibraryPaths":
        for d in (self.vault_dir, self.work_dir, self.raw, self.media):
            d.mkdir(parents=True, exist_ok=True)
        return self
