import tempfile
import unittest
from pathlib import Path

from soniox_asr import parse_tokens
from japanese_sentences import dependency
from complete_sentence_checks import run_complete_sentence_check


def speech(parts, gaps, speaker='1'):
    clock, tokens = 100, []
    for text, gap in zip(parts, gaps):
        clock += round(gap * 1000)
        for char in text:
            tokens.append({'text': char, 'start_ms': clock, 'end_ms': clock + 60, 'speaker': speaker})
            clock += 100
    return {'tokens': tokens}, clock / 1000 + 1


class CompleteSentenceTests(unittest.TestCase):
    def test_pause_inside_a_word_or_dependent_phrase_never_forces_a_cut(self):
        pairs = [('明るく輝いた星', 'になる。'), ('早く準備しよう', 'よ。'),
                 ('友達にはなれない', 'んだ。'), ('道ですれ違', 'った。'),
                 ('経験を笑い話', 'にする。'), ('椅子に座', 'った。'),
                 ('大きな船の船長', 'になった。'), ('放課後も教室に残', 'って話した。'),
                 ('楽しい未来', 'になるよ。'), ('何を思ったのだろ', 'う。'),
                 ('この手は離し', 'はしない。'), ('聞いてほしい', 'んだ。'),
                 ('楽しい気持ちでいっぱい', 'になる。'), ('優しい人間', 'になりたいです。'),
                 ('友達にはなれ', 'ないんだ。'), ('温かい', '気持ちになる。'),
                 ('明るく輝いた', '星になる。'),
                 ('青い空を見上げて', 'うれしい気持ちになる。')]
        for left, right in pairs:
            for gap in (.9, 1.7, 2.46, 4):
                with self.subTest(left=left, right=right, gap=gap):
                    payload, duration = speech([left, right], [0, gap])
                    report = {}
                    cues = parse_tokens(payload, duration, report=report)
                    self.assertEqual([c.text for c in cues], [left + right])
                    self.assertEqual(report['suffix_repair_count'], 0)
                    self.assertEqual(cues[0].end, payload['tokens'][-1]['end_ms'] / 1000)

    def test_quoted_question_is_not_an_outer_sentence_end(self):
        payload, duration = speech(['お題は「何を作りますか？」', 'です。次に行きます。'], [0, .2])
        cues = parse_tokens(payload, duration)
        self.assertEqual([c.text for c in cues], ['お題は「何を作りますか？」です。', '次に行きます。'])

    def test_clear_ends_and_independent_replies_stay_separate(self):
        for parts in (['話が終わりました。', '次の話をします。'], ['私はまだ', 'はい。'],
                      ['ありがとうございます', '次に行きます。']):
            payload, duration = speech(parts, [0, 2])
            self.assertEqual(len(parse_tokens(payload, duration)), 2)

    def test_unknown_or_different_speakers_do_not_join_by_grammar(self):
        for speaker in ('2', None):
            payload, duration = speech(['今日の私は', '楽しい。'], [0, 2])
            for token in payload['tokens'][-4:]:
                token['speaker'] = speaker
            self.assertGreaterEqual(len(parse_tokens(payload, duration)), 2)

    def test_morphology_uses_following_words_in_context(self):
        self.assertTrue(dependency('友達にはなれ', 'ないんだ。'))
        self.assertTrue(dependency('温かい', '気持ちになる。'))
        self.assertTrue(dependency('雨が降る', '予定です。'))

    def test_lyrics_and_bilingual_translation_are_checked_as_one_pipeline(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertTrue(run_complete_sentence_check(Path(folder))['sentence_built_before_translation'])
