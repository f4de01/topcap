from topcap.text import chunks, guard_correction, normalize, split_at_anchors


def test_normalize_t2s_and_punctuation():
    assert normalize("這是繁體。。。測試") == "这是繁体。测试"
    assert normalize("a  b　c\n\n\n\nd") == "a b c\n\nd"


def test_chunks_split_at_sentence_end():
    text = "第一句。第二句！第三句？第四句。"
    out = chunks(text, size=8)
    assert "".join(out) == text
    assert all(len(c) <= 8 or c.count("。") + c.count("！") + c.count("？") <= 1 for c in out)


def test_guard_rejects_rewrite():
    original = "这是一段大约有二十个字的原始文案内容。"
    assert guard_correction(original, "这是一段文案。") == (original, True)
    assert guard_correction(original, "") == (original, True)
    fixed = "这是一段大约有二十个字的原始文案内容！"
    assert guard_correction(original, fixed) == (fixed, False)


def test_split_at_anchors_never_changes_characters():
    text = "今天讲三件事。第一件事是关于咖啡的，第二件事是关于茶的。最后说一下水。"
    parts = split_at_anchors(text, ["第二件事是关", "最后说一下"])
    assert "".join(parts) == text
    assert len(parts) == 3
    assert parts[1].startswith("第二件事")


def test_split_at_anchors_ignores_bad_anchors():
    text = "没有这些锚点的文本。"
    assert split_at_anchors(text, ["不存在的锚点", "ab"]) == [text]


def test_chunks_hard_splits_text_without_punctuation():
    text = "字" * 3000
    out = chunks(text, size=1200)
    assert "".join(out) == text
    assert max(len(c) for c in out) <= 1200
