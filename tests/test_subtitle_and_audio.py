import numpy as np

from topcap.audio import split_on_silence
from topcap.subtitle import extract_subtitle_text, join_body


def test_subtitle_inline_body():
    payload = {"data": {"body": [{"from": 2.0, "to": 3.0, "content": "第二句"},
                                 {"from": 0.0, "to": 1.0, "content": "第一句"}]}}
    assert extract_subtitle_text(payload, lambda u: None) == "第一句\n第二句"


def test_subtitle_via_track_url_prefers_chinese():
    payload = {"data": {"subtitle": {"subtitles": [
        {"lan": "en-US", "subtitle_url": "//x/en.json"},
        {"lan": "ai-zh", "subtitle_url": "//x/zh.json"},
    ]}}}
    fetched = []

    def fetch(url):
        fetched.append(url)
        return {"body": [{"from": 0, "to": 1, "content": "中文" if "zh" in url else "en"}]}
    assert extract_subtitle_text(payload, fetch) == "中文"
    assert fetched[0].endswith("zh.json")


def test_subtitle_absent():
    assert extract_subtitle_text({"data": {"subtitle": {"subtitles": []}}}, lambda u: None) is None
    assert join_body([]) == ""


def test_split_on_silence_cuts_in_quiet_gap():
    sr = 1000
    loud = np.ones(sr * 20, dtype=np.float32) * 0.5
    quiet = np.zeros(sr * 2, dtype=np.float32)
    audio = np.concatenate([loud, quiet, loud, quiet, loud])    # 64s
    parts = split_on_silence(audio, sr, max_sec=30, min_sec=5)
    assert sum(p.size for p in parts) == audio.size
    assert all(p.size <= 30 * sr for p in parts)
    # 第一刀应落在 20-22s 的静音段里
    assert 20 * sr <= parts[0].size <= 22 * sr


def test_split_short_audio_untouched():
    audio = np.zeros(100, dtype=np.float32)
    assert len(split_on_silence(audio, 16000)) == 1
