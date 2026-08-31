"""M3 测试：_find_quote_source 金句溯源（子串/模糊/找不到丢弃）。"""

from __future__ import annotations

from app.services.process_service import _find_quote_source


def _p(seq: int, text: str, start: float, end: float) -> dict:
    return {"seq": seq, "text": text, "start_ts": start, "end_ts": end}


class TestFindQuoteSource:
    def test_exact_substring_matches(self):
        paras = [_p(1, "今天我们聊一下美妆巨头欧莱雅的洗护产品线。", 0, 10)]
        r = _find_quote_source("欧莱雅的洗护产品线", paras)
        assert r is not None
        assert r["seq"] == 1 and r["start_ts"] == 0 and r["end_ts"] == 10

    def test_exact_case_strips_quotes(self):
        paras = [_p(1, "欧莱雅洗发护发产品的销售额增长超过15%。", 0, 10)]
        r = _find_quote_source('"销售额增长超过15%"', paras)
        assert r is not None and r["seq"] == 1

    def test_fuzzy_match_tolerates_asr_drop(self):
        # ASR 丢字：原文 60 字与金句 50 字有 >0.75 相似度
        original = "欧莱雅中国的护发品牌在今年上半年实现了同比超过15%的增长速度，表现亮眼"
        quote = "欧莱雅的护发品牌今年上半年实现了同比超过15%的增长速度，表现亮眼"
        paras = [_p(1, original, 0, 20)]
        r = _find_quote_source(quote, paras, threshold=0.7)
        assert r is not None and r["seq"] == 1

    def test_not_found_returns_none(self):
        paras = [_p(1, "这段内容完全不相关。", 0, 5)]
        assert _find_quote_source("完全不存在的金句原文", paras) is None

    def test_empty_quote_returns_none(self):
        assert _find_quote_source("", []) is None
