from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from punctuation_checks import PARTS, make_tokens, run_punctuation_check
from punctuation_segments import tail_only
from sentence_fragments import join_cues
from soniox_asr import parse_tokens
from subtitles import Cue


class PunctuationTests(unittest.TestCase):
    def test_long_talk_splits_by_commas_with_fillers_and_enumerations(self):
        payload, duration = make_tokens()
        report = {}
        cues = parse_tokens(payload, duration, report=report)
        self.assertEqual([c.text for c in cues], PARTS)
        self.assertEqual(''.join(c.text for c in cues), ''.join(PARTS))
        self.assertEqual(report['source_sentence_count'], 1)
        self.assertIsNone(report['length_limits'])

    def test_full_lyrics_request_and_bilingual_export_pipeline(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertTrue(run_punctuation_check(Path(folder))['lyrics_do_not_remerge_clauses'])

    def test_clause_starting_with_a_particle_is_not_a_bare_suffix(self):
        self.assertTrue(tail_only('になる。'))
        self.assertTrue(tail_only('んだ。'))
        self.assertTrue(tail_only('になりたいです。'))
        self.assertFalse(tail_only('には友達が集まっている。'))
        self.assertFalse(tail_only('でも、新しい写真を見ました。'))
        cues = [Cue(0, 1, '楽しかったので、', speaker='1'), Cue(1.1, 3, '新しい写真を見ました。', speaker='1')]
        self.assertEqual(len(join_cues(cues)[0]), 2)

    def test_lyric_line_continuations_do_not_swallow_following_clauses(self):
        a = Cue(0, 1, '青い空を見上げて', speaker='1', lyric={'stage_id': 's', 'song_id': 'a', 'line': 1, 'japanese': '青い空を見上げて'})
        b = Cue(1.2, 3, '仲間と新しい町へ歩こう', speaker='1', lyric={'stage_id': 's', 'song_id': 'a', 'line': 2, 'japanese': '仲間と新しい町へ歩こう'})
        self.assertEqual(len(join_cues([a, b])[0]), 2)
        tail = replace(b, text='る。', lyric=None)
        self.assertEqual(len(join_cues([replace(a, text='君が蘇'), tail])[0]), 1)

    def test_an_asr_comma_inside_a_suffix_does_not_orphan_it(self):
        for left, right in [('お願いいたし、', 'ます。'), ('思いま、', 'す。'), ('楽しい気持ち、', 'になる。')]:
            with self.subTest(left=left):
                payload, duration = make_tokens([left, right])
                self.assertEqual([c.text for c in parse_tokens(payload, duration)], [left + right])

    def test_semicolons_and_colons_use_native_boundaries(self):
        parts = ['今度は町を歩いて；', '駅に着いたら休もう：', '明日も集まろう。']
        payload, duration = make_tokens(parts)
        self.assertEqual([c.text for c in parse_tokens(payload, duration)], parts)

    def test_number_punctuation_is_not_a_clause_boundary(self):
        for text in ('時刻は12:30にして、', '金額は1,000円にして、'):
            payload, duration = make_tokens([text, '明日も相談しよう。'])
            self.assertEqual([c.text for c in parse_tokens(payload, duration)], [text, '明日も相談しよう。'])

    def test_quoted_question_can_have_display_clauses_without_orphaning_desu(self):
        payload, duration = make_tokens(['お題は「好きな料理を選んで、', '誰と食べますか？」です。'])
        self.assertEqual([c.text for c in parse_tokens(payload, duration)],
                         ['お題は「好きな料理を選んで、', '誰と食べますか？」です。'])

    def test_single_native_token_has_no_invented_internal_clock(self):
        payload = {'tokens': [{'text': 'まず公園を歩いて、次に写真を撮ろう。', 'start_ms': 100, 'end_ms': 5000, 'speaker': '1'}]}
        cues = parse_tokens(payload, 6)
        self.assertEqual(len(cues), 1)
        self.assertEqual((cues[0].start, cues[0].end), (.1, 5))
