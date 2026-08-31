"""M2 测试：asr.py 纯逻辑（切片范围 / rich 标签清洗 / 段落化合并规则）。"""

from __future__ import annotations

from app.infra.asr import _slice_ranges, segment_to_paras, strip_rich


class TestStripRich:
    def test_removes_all_tags(self):
        raw = "<|zh|><|NEUTRAL|><|Speech|>你好，世界。"
        assert strip_rich(raw) == "你好，世界。"

    def test_plain_text_unchanged(self):
        assert strip_rich("普通文本") == "普通文本"


class TestSliceRanges:
    def test_short_audio_single_slice(self):
        assert _slice_ranges(300) == [(0.0, 300.0)]

    def test_exact_one_slice(self):
        assert _slice_ranges(600) == [(0.0, 600.0)]

    def test_long_audio_slices(self):
        assert _slice_ranges(1500) == [(0.0, 600.0), (600.0, 1200.0), (1200.0, 1500.0)]

    def test_empty_audio(self):
        assert _slice_ranges(0) == []


class TestSegmentToParas:
    def _seg(self, i: int, text: str, start: float, end: float) -> dict:
        return {"text": text, "start": start, "end": end}

    def test_merge_keeps_first_start_last_end(self):
        # 3 段各 30 字（句号结尾）→ 合并成 1 段（90 字 ≥ min_chars）
        segs = [
            self._seg(0, "第一句内容。", 0.0, 3.0),
            self._seg(1, "第二句内容。", 3.0, 6.0),
            self._seg(2, "第三句内容。", 6.0, 9.0),
        ]
        # 30 字 < 50 → 需要凑满 50：两句 60 字成段
        paras = segment_to_paras(segs, min_chars=50, max_chars=200)
        assert len(paras) == 1
        assert paras[0]["start"] == 0.0   # 首段 start
        assert paras[0]["end"] == 9.0     # 末段 end
        assert paras[0]["text"] == "第一句内容。第二句内容。第三句内容。"

    def test_timestamps_monotonic(self):
        # 多段长文本 → 多段落，时间戳必须单调递增
        segs = [
            self._seg(i, f"这是第{i}段的句子内容，用来凑字数。" * 3, i * 60.0, i * 60.0 + 30.0)
            for i in range(6)
        ]
        paras = segment_to_paras(segs, min_chars=50, max_chars=200)
        starts = [p["start"] for p in paras]
        ends = [p["end"] for p in paras]
        assert all(a < b for a, b in zip(ends, starts[1:]))  # 前段 end < 后段 start
        assert all(a <= b for a, b in zip(starts, starts[1:]))

    def test_oversized_single_segment_kept_intact(self):
        # 单个 ASR 分段超长（>200 字）：实现语义是「合并成段」而非硬切文本，
        # 超长段独立成段、内容完整保留（硬切会破坏句子，交由 ASR 分段粒度控制）
        long_text = "字" * 250
        paras = segment_to_paras([self._seg(0, long_text, 0.0, 30.0)], max_chars=200)
        assert len(paras) == 1
        assert paras[0]["text"] == long_text
        assert paras[0]["start"] == 0.0 and paras[0]["end"] == 30.0

    def test_tail_remnant_becomes_para(self):
        segs = [
            self._seg(0, "第一段句子。" * 10, 0.0, 30.0),  # 60 字成段
            self._seg(1, "尾巴。", 30.0, 32.0),            # 3 字 < min_chars
        ]
        paras = segment_to_paras(segs, min_chars=50, max_chars=200)
        assert len(paras) == 2
        assert paras[1]["text"] == "尾巴。"
        assert paras[1]["start"] == 30.0

    def test_empty_input(self):
        assert segment_to_paras([]) == []

    def test_unsorted_input_sorted_by_start(self):
        segs = [
            self._seg(1, "后说的句子。" * 10, 30.0, 60.0),
            self._seg(0, "先说的句子。" * 10, 0.0, 30.0),
        ]
        paras = segment_to_paras(segs, min_chars=50, max_chars=200)
        assert paras[0]["start"] == 0.0  # 内部按 start 排序
