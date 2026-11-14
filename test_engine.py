import tempfile
import unittest
from pathlib import Path

from subtitles import Cue, ass_time, atomic_write, centiseconds, make_ass, ass_text


class SubtitleTests(unittest.TestCase):
    def test_bilingual_lines_share_one_timeline(self):
        japanese = '今日は皆さんと一緒にゆっくりお話をして、楽しい思い出をたくさん作りたいと思います。'
        chinese = '今天想和大家一起慢慢聊天，多创造一些开心的回忆，希望大家也能度过愉快的时光。'
        ass = make_ass([Cue(1.23, 4.56, japanese, chinese)])
        rows = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
        self.assertEqual(len(rows), 2)
        self.assertEqual([row.split(",")[3] for row in rows], ["japanese", "chinese"])
        self.assertEqual(rows[0].split(",")[1:3], rows[1].split(",")[1:3])
        self.assertEqual(rows[0].split(',', 9)[-1], japanese)
        self.assertEqual(rows[1].split(',', 9)[-1], chinese)
        self.assertTrue(all(r'\N' not in row for row in rows))
        self.assertIn('WrapStyle: 2', ass)
        self.assertGreater(int(rows[0].split(",")[7]), 54)
        self.assertNotIn("Style: Default", ass)
        self.assertIn("Style: japanese,Yu Gothic", ass)
        self.assertIn("Style: chinese,Microsoft YaHei", ass)

    def test_chinese_text_cannot_inject_ass_tags(self):
        ass = make_ass([Cue(1, 2, "日本語", r"中文{\p1}\N")])
        self.assertIn(",chinese,", ass)
        self.assertNotIn(r"{\p1}", ass)

    def test_single_line_chinese_keeps_japanese_at_the_same_height(self):
        one = make_ass([Cue(0, 1, "日本語", "中" * 5)])
        many = make_ass([Cue(0, 1, "日本語", "中" * 100)])
        margin = lambda ass: int(next(line for line in ass.splitlines() if line.startswith("Dialogue:")).split(",")[7])
        self.assertEqual(margin(many), margin(one))
        self.assertGreater(margin(many), 54)
        self.assertNotIn(r'\N', many)
    def test_rounding_carries_across_minutes_and_hours(self):
        self.assertEqual(ass_time(centiseconds(59.995)), "0:01:00.00")
        self.assertEqual(ass_time(centiseconds(3599.995)), "1:00:00.00")

    def test_short_cue_remains_visible_after_rounding(self):
        self.assertIn("0:00:01.00,0:00:01.01", make_ass([Cue(1.001, 1.002, "あ")]))

    def test_ass_override_injection_is_literal(self):
        text = ass_text(r"{\p1}m 0 0 l 99 99\N日本語")
        self.assertNotIn("{", text)
        self.assertNotIn(r"\p1", text)
        self.assertIn("日本語", text)

    def test_unicode_output_round_trips(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "日本語.ass"
            atomic_write(path, make_ass([Cue(0, 1, "今日は晴れです。")]))
            self.assertIn("今日は晴れです。", path.read_text(encoding="utf-8-sig"))
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])


    def test_invalid_timing_is_rejected(self):
        for start, end in [(0, float("nan")), (-1, 2), (1, 1), (0, float("inf"))]:
            with self.assertRaises(ValueError):
                Cue(start, end, "あ")


