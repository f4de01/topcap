import pytest

from topcap.library import merge_candidates, parse_pick
from topcap.models import Candidate
from topcap.render import compose_parts, filename, render


def _cand(i=1, **kw):
    base = dict(index=i, source="抖音", id=str(1000 + i), title=f"标题{i}", author="作者",
                url=f"https://www.douyin.com/video/{1000 + i}")
    base.update(kw)
    return Candidate(**base)


def test_frontmatter_omits_missing_fields():
    md = render(_cand(likes=None, duration_sec=None), "正文", library="库", transcript_source="asr")
    head = md.split("---")[1]
    assert "likes" not in head and "duration" not in head and "published" not in head
    assert 'title: "标题1"' in head
    assert "transcript_source: asr" in head
    assert "tags: [topcap, 抖音]" in head
    assert md.endswith("# 标题1\n\n正文\n")


def test_frontmatter_escapes_quotes():
    md = render(_cand(title='他说"你好"'), "x", library="库", transcript_source="subtitle")
    assert 'title: "他说\\"你好\\""' in md


def test_filename_is_safe():
    name = filename(_cand(title='a/b:c*d?"<>|', author="x"))
    assert name.startswith("01_抖音_abcd_x") and name.endswith(".md")


def test_compose_parts_single_and_multi():
    assert compose_parts([("", "正文")]) == "正文"
    out = compose_parts([("上", "一"), ("下", "二")])
    assert out == "## P1 上\n\n一\n\n## P2 下\n\n二"


def test_merge_keeps_indices_and_dedupes():
    existing = [_cand(1), _cand(2)]
    merged, added = merge_candidates(existing, [_cand(0, id="1002"), _cand(0, id="9")])
    assert added == 1
    assert [c.index for c in merged] == [1, 2, 3]
    assert merged[2].id == "9"


def test_parse_pick():
    assert parse_pick("1,3,5", [1, 2, 3, 4, 5]) == [1, 3, 5]
    assert parse_pick("2-4", [1, 2, 3, 4, 5]) == [2, 3, 4]
    assert parse_pick("all", [1, 2]) == [1, 2]
    assert parse_pick("1，2", [1, 2]) == [1, 2]
    with pytest.raises(ValueError):
        parse_pick("9", [1, 2])
    with pytest.raises(ValueError):
        parse_pick("x", [1])
