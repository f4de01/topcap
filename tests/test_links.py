from topcap.links import classify_url, extract_urls, is_short, looks_like_topic


def test_extract_from_douyin_share_text():
    text = "7.87 复制打开抖音，看看【某人的作品】 https://v.douyin.com/iAbCdEf/ 复制此链接，打开Dou音搜索"
    assert extract_urls(text) == ["https://v.douyin.com/iAbCdEf/"]


def test_extract_strips_trailing_chinese_punctuation():
    assert extract_urls("看这个 https://www.bilibili.com/video/BV1mhKv68EPQ，不错") == \
        ["https://www.bilibili.com/video/BV1mhKv68EPQ"]


def test_bare_domain_without_scheme():
    assert extract_urls("space.bilibili.com/12345 这个人") == ["https://space.bilibili.com/12345"]


def test_topic_has_no_url():
    assert looks_like_topic("手冲咖啡入门")
    assert not looks_like_topic("https://b23.tv/abc")


def test_classify_video_and_user():
    assert classify_url("https://www.douyin.com/video/7633708737223576883").kind == "video"
    assert classify_url("https://www.iesdouyin.com/share/video/7633708737223576883").source == "抖音"
    assert classify_url("https://www.douyin.com/user/MS4wLjABAAAA-xyz").kind == "user"
    r = classify_url("https://www.bilibili.com/video/BV1mhKv68EPQ?p=2")
    assert r.source == "B站" and r.id == "BV1mhKv68EPQ"
    assert classify_url("https://space.bilibili.com/99999/video").id == "99999"
    assert classify_url("https://example.com/x") is None


def test_short_hosts():
    assert is_short("https://v.douyin.com/x/")
    assert is_short("https://b23.tv/x")
    assert not is_short("https://www.bilibili.com/video/BV1")
