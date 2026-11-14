import tempfile
import unittest
from pathlib import Path

from sentence_context import allowed_pause, continuation_kind, conversation_context
from sentence_checks import run_sentence_check
from soniox_asr import parse_tokens


class SentenceContextTests(unittest.TestCase):
    def tokens(self, first, second, gap=.8, first_speaker="1", second_speaker="1"):
        return {"tokens": [{"text": first, "start_ms": 100, "end_ms": 800, "speaker": first_speaker},
                           {"text": second, "start_ms": round((.8 + gap) * 1000), "end_ms": round((1.8 + gap) * 1000), "speaker": second_speaker}]}

    def test_same_speaker_unfinished_sentence_and_contextual_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertTrue(run_sentence_check(Path(folder))["unfinished_same_speaker_joined"])

    def test_sentence_endings_and_backchannels_are_not_forced_together(self):
        for text in ("ありがとうございます。", "ありがとうございます", "はい", "うん", "そうですね", "分かりました"):
            with self.subTest(text=text):
                self.assertEqual(len(parse_tokens(self.tokens(text, "次に行きます。"), 5)), 2)

    def test_unfinished_sentence_keeps_a_pause_but_different_speakers_do_not_join(self):
        self.assertEqual(len(parse_tokens(self.tokens("私は", "嬉しかった。", gap=2), 5)), 1)
        self.assertEqual(len(parse_tokens(self.tokens("私は", "嬉しかった。", second_speaker="2"), 5)), 2)
        self.assertEqual(len(parse_tokens(self.tokens("私は", "嬉しかった。", first_speaker=None, second_speaker=None), 5)), 2)
        self.assertEqual(len(parse_tokens(self.tokens("私は", "嬉しかった。", second_speaker=None), 5)), 2)

    def test_pause_hint_does_not_cut_unfinished_sentences_or_change_word_clocks(self):
        payload = self.tokens("私は", "嬉しかった。", gap=1.5)
        report = {}
        self.assertEqual(len(parse_tokens(payload, 5, report=report)), 1)
        self.assertTrue(report['continued_pauses'][0]['long_pause'])
        cues = parse_tokens(payload, 5, continuation_pause=2, report=report)
        self.assertFalse(report['continued_pauses'][0]['long_pause'])
        self.assertEqual(len(cues), 1)
        self.assertEqual((cues[0].start, cues[0].end), (.1, 3.3))
        for value in (True, float("nan"), 9, "1.2"):
            with self.assertRaises(ValueError):
                allowed_pause("私は", value)

    def test_following_read_only_subtitle_is_available_on_single_retry(self):
        rows = [{"id": 1, "start": 0, "end": .5, "text": "私は", "speaker": "1"},
                {"id": 2, "start": 1.1, "end": 1.5, "text": "日向ちゃんの", "speaker": "1"},
                {"id": 3, "start": 2.1, "end": 3, "text": "モノマネをしました。", "speaker": "1"},
                {"id": 4, "start": 3.2, "end": 3.5, "text": "うん。", "speaker": "2"}]
        context = conversation_context(rows, [2])
        self.assertEqual([r["id"] for r in context], [1, 2, 3, 4])
        self.assertTrue(context[1]["possible_continuation"])
        self.assertTrue(context[2]["possible_continuation"])
        self.assertTrue(context[2]["read_only"])
        self.assertFalse(context[3]["possible_continuation"])
        self.assertEqual(context[2]["text"], rows[2]["text"])

    def test_length_and_duration_do_not_force_a_sentence_split(self):
        payload = self.tokens("あ" * 40 + "を", "伝えたいと思います。")
        self.assertEqual(len(parse_tokens(payload, 5)), 1)
        payload = {'tokens': [{'text': 'あ' * 20, 'start_ms': i * 600, 'end_ms': (i + 1) * 600,
                               'speaker': '1'} for i in range(20)]}
        cues = parse_tokens(payload, 13)
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0].end, 12)
        self.assertEqual(len(cues[0].text), 400)

    def test_integer_pause_setting_roundtrips_without_losing_keys(self):
        import config
        from engine import Settings
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as folder, patch("config.data_dir", return_value=Path(folder)):
            config.save(Settings(continuation_pause=2, deepseek_key="offline-context-key"), True)
            settings, remember, warning = config.load()
            self.assertEqual(settings.continuation_pause, 2.0)
            self.assertEqual(settings.deepseek_key, "offline-context-key")
            self.assertTrue(remember)
            self.assertFalse(warning)

    def test_short_suffix_after_a_sung_pause_joins_at_native_times(self):
        for first, second, gap in [('君が蘇', 'る。', 1.26), ('いき', 'ます。', 1.68),
                                   ('思いま', 'す。', .1), ('お願いいたし', 'ます。', .06)]:
            with self.subTest(first=first, second=second):
                report = {}
                payload = self.tokens(first, second, gap=gap)
                cues = parse_tokens(payload, 5, report=report)
                self.assertEqual(len(cues), 1)
                self.assertEqual(cues[0].text, first + second)
                self.assertEqual(cues[0].start, .1)
                self.assertAlmostEqual(cues[0].end, 1.8 + gap)
                if gap > .8:
                    self.assertEqual(report['continued_pause_count'], 1)
                    self.assertEqual(report['suffix_repair_count'], 0)  # Never split in the first place.

    def test_suffixes_do_not_cross_speakers_or_complete_replies(self):
        for kwargs in ({'gap': 1.26, 'second_speaker': '2'},
                       {'gap': 1.26, 'first_speaker': None, 'second_speaker': None},
                       {'gap': 1.26, 'second_speaker': None}):
            self.assertEqual(len(parse_tokens(self.tokens('君が蘇', 'る。', **kwargs), 5)), 2)
        for previous, current in [('そう。', 'です。'), ('終わりました。', 'ます。'),
                                  ('わかりました。', 'る。'), ('私は', 'はい。'), ('君が蘇', 'うん。')]:
            with self.subTest(previous=previous, current=current):
                self.assertEqual(len(parse_tokens(self.tokens(previous, current, gap=1.5), 5)), 2)

    def test_lyric_lines_and_already_translated_cues_are_not_blindly_joined(self):
        from dataclasses import replace
        from sentence_fragments import join_cues
        from subtitles import Cue
        first, second = Cue(0, 1, '君が蘇', speaker='1'), Cue(2.26, 2.32, 'る。', speaker='1')
        joined, repairs = join_cues([first, second])
        self.assertEqual([c.text for c in joined], ['君が蘇る。'])
        self.assertEqual((joined[0].start, joined[0].end), (0, 2.32))
        self.assertEqual(len(repairs), 1)
        self.assertEqual(len(join_cues([replace(first, translation='你苏醒'), second])[0]), 2)
        lyric_cues, _ = join_cues([replace(first, lyric={'line': 1, 'japanese': '君が蘇', 'chinese': '你苏醒'}), second])
        self.assertEqual(len(lyric_cues), 1)
        self.assertEqual(lyric_cues[0].lyric['japanese'], '君が蘇る。')
        self.assertEqual(lyric_cues[0].lyric['chinese'], '')

    def test_length_does_not_block_suffix_attachment(self):
        from sentence_fragments import join_cues
        from subtitles import Cue
        cues, _ = join_cues([Cue(0, 20, '長い説明を' * 30 + 'お願いいたし', speaker='1'),
                             Cue(20.1, 20.4, 'ます。', speaker='1')])
        self.assertEqual(len(cues), 1)
        self.assertTrue(cues[0].text.endswith('お願いいたします。'))


if __name__ == "__main__":
    unittest.main()
