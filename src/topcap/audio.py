"""音频解码与切块。PyAV 自带 ffmpeg 库，不需要系统安装 ffmpeg。"""
from __future__ import annotations

from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000


class AudioError(RuntimeError):
    pass


def decode_audio(path: Path) -> np.ndarray:
    """把任意音视频文件解成 16kHz 单声道 float32，范围 [-1, 1]。"""
    import av

    try:
        container = av.open(str(path))
    except Exception as exc:
        raise AudioError(f"打不开媒体文件: {exc}") from exc
    with container:
        stream = next((s for s in container.streams if s.type == "audio"), None)
        if stream is None:
            raise AudioError("文件里没有音轨")
        resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
        chunks: list[np.ndarray] = []
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):
            chunks.append(out.to_ndarray().reshape(-1))
    if not chunks:
        raise AudioError("音轨解码后是空的")
    return np.concatenate(chunks).astype(np.float32) / 32768.0


def split_on_silence(audio: np.ndarray, sr: int = SAMPLE_RATE, *, max_sec: float = 30.0,
                     min_sec: float = 5.0, frame_ms: int = 20) -> list[np.ndarray]:
    """把长音频切成不超过 max_sec 的块，切点选在每个窗口尾部能量最低的帧。

    SenseVoice 是非自回归模型，对三十秒以内的输入效果最稳。切在静音处，
    句子不会被从中间砍断。纯 numpy，无需 VAD 模型。
    """
    if audio.size == 0:
        return []
    max_len = int(max_sec * sr)
    if audio.size <= max_len:
        return [audio]
    frame = max(int(sr * frame_ms / 1000), 1)
    n_frames = audio.size // frame
    energy = (audio[: n_frames * frame].reshape(n_frames, frame) ** 2).mean(axis=1)

    out: list[np.ndarray] = []
    start = 0
    min_len = int(min_sec * sr)
    while audio.size - start > max_len:
        # 在 [start+min_len, start+max_len] 之间找能量最低的帧作切点
        lo = (start + min_len) // frame
        hi = (start + max_len) // frame
        if hi <= lo:
            cut = start + max_len
        else:
            cut = (lo + int(np.argmin(energy[lo:hi]))) * frame
        out.append(audio[start:cut])
        start = cut
    out.append(audio[start:])
    return [c for c in out if c.size > 0]
