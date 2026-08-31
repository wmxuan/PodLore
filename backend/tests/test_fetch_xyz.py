"""M1 测试：小宇宙 URL 解析 + __NEXT_DATA__ 提取 + 元数据归一化。"""

from __future__ import annotations

import pytest

from app.infra.fetch_xyz import (
    extract_episode_meta,
    extract_next_data,
    extract_series_name,
    parse_episode_url,
)

EID = "6a7b23ba17676351c570589d"


class TestParseEpisodeUrl:
    def test_plain_url(self):
        assert parse_episode_url(f"https://www.xiaoyuzhoufm.com/episode/{EID}") == EID

    def test_url_with_query_and_hash(self):
        url = f"https://www.xiaoyuzhoufm.com/episode/{EID}?s=xyz&h=1#share"
        assert parse_episode_url(url) == EID

    def test_rich_text_paste(self):
        text = f"【声动早咖啡】美妆巨头 https://www.xiaoyuzhoufm.com/episode/{EID} 快来听听"
        assert parse_episode_url(text) == EID

    def test_invalid_url_raises(self):
        with pytest.raises(ValueError):
            parse_episode_url("https://www.xiaoyuzhoufm.com/podcast/abc")

    def test_non_episode_path_raises(self):
        with pytest.raises(ValueError):
            parse_episode_url("https://example.com/episode/not-hex")


class TestExtractNextData:
    def test_extract_from_real_page(self, fixture_html):
        data = extract_next_data(fixture_html)
        episode = data["props"]["pageProps"]["episode"]
        assert episode["eid"] == EID
        assert episode["title"].startswith("美妆巨头")

    def test_missing_next_data_raises(self):
        with pytest.raises(ValueError):
            extract_next_data("<html><body>登录页</body></html>")


class TestExtractEpisodeMeta:
    @pytest.fixture()
    def meta(self, fixture_html) -> dict:
        return extract_episode_meta(extract_next_data(fixture_html), html=fixture_html)

    def test_core_fields(self, meta):
        assert meta["eid"] == EID
        assert meta["title"] == "美妆巨头集体盯上头发，洗护生意为何又热起来？"
        assert meta["duration"] == 897
        assert meta["audio_url"].startswith("https://media.xyzcdn.net/")
        assert meta["audio_url"].endswith(".m4a")
        assert meta["audio_size"] == 14523310
        assert meta["pub_date"] == "2026-08-11T23:00:00.000Z"

    def test_shownotes_html(self, meta):
        assert meta["shownotes_html"] and "<p>" in meta["shownotes_html"]

    def test_podcast_info(self, meta):
        assert meta["podcast"]["title"] == "声动早咖啡"
        assert meta["podcast"]["author"] == "声动活泼"
        assert meta["pid"] == meta["podcast"]["pid"]

    def test_popularity_fields(self, meta):
        for key in ("play_count", "clap_count", "favorite_count", "comment_count"):
            assert isinstance(meta[key], int) and meta[key] >= 0

    def test_cover_from_podcast_image(self, meta):
        # 实测结构：podcast.image.picUrl（非 podcast.cover）
        assert meta["cover_url"].startswith("https://image.xyzcdn.net/")

    def test_series_name_none_without_ep_prefix(self, meta):
        assert meta["series_name"] is None  # 该标题无 EP{n} 前缀


class TestSeriesName:
    def test_ep_prefix_extracted(self):
        title = "EP85 声动早咖啡｜美妆巨头盯上头发"
        assert extract_series_name(title) == "声动早咖啡"

    def test_no_prefix_returns_none(self):
        assert extract_series_name("普通标题") is None


class TestCoverFallback:
    def test_og_image_fallback(self, fixture_html):
        # 合成 next_data：podcast 无 image → 兜底 og:image
        data = extract_next_data(fixture_html)
        episode = data["props"]["pageProps"]["episode"]
        episode["podcast"]["image"] = None
        og = 'https://image.xyzcdn.net/from-og.jpeg'
        html = f'<html><head><meta property="og:image" content="{og}"></head></html>'
        meta = extract_episode_meta(data, html=html)
        assert meta["cover_url"] == og

    def test_cover_none_when_nothing_available(self, fixture_html):
        data = extract_next_data(fixture_html)
        episode = data["props"]["pageProps"]["episode"]
        episode["podcast"]["image"] = None
        meta = extract_episode_meta(data, html=None)
        assert meta["cover_url"] is None
