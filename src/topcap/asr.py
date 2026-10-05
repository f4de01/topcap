"""语音转写：sherpa-onnx 加载 SenseVoice-Small int8。见 ADR 0001。"""
from __future__ import annotations

import re
import tarfile
import threading
from importlib import metadata
from pathlib import Path
from typing import Callable

import numpy as np

from .audio import SAMPLE_RATE, decode_audio, split_on_silence
from .config import MODEL_TARBALL, MODEL_URL

_TAGS = re.compile(r"<\|[^|]*\|>")
_LOCK = threading.Lock()                 # 推理 CPU 密集，并发只会互相抢核


class ASRError(RuntimeError):
    pass


def model_files(models_dir: Path) -> tuple[Path, Path] | None:
    """模型与 tokens 的路径。没下过返回 None。"""
    root = models_dir / MODEL_TARBALL
    if not root.is_dir():
        return None
    model = next(root.rglob("model.int8.onnx"), None)
    tokens = next(root.rglob("tokens.txt"), None)
    return (model, tokens) if model and tokens else None


def ensure_model(models_dir: Path, log: Callable[[str], None] | None = None,
                 on_progress: Callable[[int, int | None], None] | None = None) -> tuple[Path, Path]:
    """首次使用时从 GitHub release 下载模型包（约 165MB）并解压。"""
    found = model_files(models_dir)
    if found:
        return found
    import httpx

    models_dir.mkdir(parents=True, exist_ok=True)
    tarball = models_dir / f"{MODEL_TARBALL}.tar.bz2"
    say = log or (lambda _m: None)
    say(f"下载语音模型 {MODEL_TARBALL}（约 165MB）…")
    try:
        with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(60.0, read=120.0)) as c:
            with c.stream("GET", MODEL_URL) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length") or 0) or None
                done = 0
                with open(tarball, "wb") as f:
                    for chunk in r.iter_bytes(1 << 16):
                        f.write(chunk)
                        done += len(chunk)
                        if on_progress:
                            on_progress(done, total)
    except Exception as exc:
        tarball.unlink(missing_ok=True)
        raise ASRError(f"模型下载失败：{exc}\n   可手动下载 {MODEL_URL} 解压到 {models_dir}") from exc
    say("解压…")
    with tarfile.open(tarball, "r:bz2") as tf:
        tf.extractall(models_dir, filter="data")
    tarball.unlink(missing_ok=True)
    found = model_files(models_dir)
    if not found:
        raise ASRError(f"模型包解压后找不到 model.int8.onnx / tokens.txt：{models_dir}")
    return found


def provider() -> str:
    """CUDA 版 sherpa-onnx 的版本号形如 1.13.8+cuda12.cudnn9；PyPI 的 CPU 版没有后缀。"""
    try:
        ver = metadata.version("sherpa-onnx")
    except metadata.PackageNotFoundError:
        return "cpu"
    return "cuda" if "cuda" in ver else "cpu"


def _preload_onnxruntime() -> None:
    """Windows 11 的 System32 自带一个旧版 onnxruntime.dll（Windows ML），加载顺序排在包目录前面。

    sherpa-onnx 的扩展一旦连上那份旧库就会报 "requested API version not available" 然后段错误。
    办法是先把包自带的那份按完整路径加载进进程：同名 DLL 只会加载一次，后面的查找直接复用。
    """
    import ctypes
    import sys
    from importlib.util import find_spec

    if sys.platform != "win32":
        return
    spec = find_spec("sherpa_onnx")
    for root in (spec.submodule_search_locations or []) if spec else []:
        dll = Path(root) / "lib" / "onnxruntime.dll"
        if dll.is_file():
            try:
                ctypes.CDLL(str(dll))
            except OSError:
                pass
            return


class Recognizer:
    """懒加载的单例识别器。一个进程只建一次，建一次要一两秒。"""

    def __init__(self, models_dir: Path, num_threads: int = 4):
        self.models_dir = models_dir
        self.num_threads = num_threads
        self._rec = None

    def _load(self):
        if self._rec is None:
            _preload_onnxruntime()
            import sherpa_onnx
            model, tokens = ensure_model(self.models_dir)
            self._rec = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                model=str(model), tokens=str(tokens), use_itn=True,
                num_threads=self.num_threads, provider=provider(), language="auto",
            )
        return self._rec

    def transcribe_array(self, audio: np.ndarray, sr: int = SAMPLE_RATE) -> str:
        if audio.size < sr * 0.5:
            raise ASRError("音频太短，不足半秒")
        with _LOCK:
            rec = self._load()
            streams = []
            for chunk in split_on_silence(audio, sr):
                s = rec.create_stream()
                s.accept_waveform(sr, chunk)
                streams.append(s)
            if len(streams) == 1:
                rec.decode_stream(streams[0])
            else:
                rec.decode_streams(streams)
            pieces = [_TAGS.sub("", s.result.text).strip() for s in streams]
        text = "".join(p for p in pieces if p)
        return text

    def transcribe(self, path: Path) -> str:
        """媒体文件 -> 原始文案。"""
        return self.transcribe_array(decode_audio(path))
