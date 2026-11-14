from pathlib import Path
import re
import tempfile
import unittest

from japanese_sentences import complete
from soniox_asr import parse_tokens
from utterance_checks import PARTS, fixture, run_utterance_check


class UtteranceTests(unittest.TestCase):
    def test_missing_punctuation_uses_complete_utterances(self):
        payload, duration = fixture()
        self.assertEqual([c.text for c in parse_tokens(payload, duration)], PARTS)

    def test_native_silence_and_space_do_not_extend_captions(self):
        parts = ['花子 お誕生日 おめでとう', 'これからも 大好き', 'ありがとうございました']
        payload, duration = fixture(parts, gap=16000)
        cues = parse_tokens(payload, duration)
        self.assertEqual([c.text for c in cues], parts)
        audible = [t for t in payload['tokens'] if t['text'].strip()]
        self.assertTrue(all(c.start in {t['start_ms']/1000 for t in audible} and
                            c.end in {t['end_ms']/1000 for t in audible} for c in cues))
        self.assertTrue(all(cues[i+1].start - cues[i].end >= 16 for i in range(2)))

    def test_questions_and_conjunctive_clauses_are_recognized(self):
        self.assertTrue(complete('まだ質問はありませんか'))
        parts = ['私がやりたいことは散歩なんですけど', 'よく友達の写真を見ていて',
                 'みんなが楽しそうだと思ったので', '今度は一緒に行きます']
        payload, duration = fixture(parts)
        self.assertEqual([c.text for c in parse_tokens(payload, duration)], parts)

    def test_polite_end_is_not_cut_from_question_or_connecting_particle(self):
        for text in ('この話でいいですか', '晴れなんですけど', '駅へ行きますよね'):
            with self.subTest(text=text):
                payload, duration = fixture([text, 'ありがとうございます'], blanks=False)
                expected = [text + 'ありがとうございます'] if text.endswith('けど') else [text, 'ありがとうございます']
                self.assertEqual([c.text for c in parse_tokens(payload, duration)], expected)

    def test_spaces_do_not_separate_dependent_tails_or_attributive_phrases(self):
        for parts in (['青い空を見上げて', 'うれしい気持ちになる。'],
                      ['温かい', '気持ちになる。'], ['雨が降る', '予定です。'],
                      ['お願いし', 'ます。'], ['優しい人間', 'になりたいです。']):
            payload, duration = fixture(parts, gap=2500)
            cues = parse_tokens(payload, duration)
            self.assertEqual(len(cues), 1, [c.text for c in cues])
            self.assertEqual(re.sub(r'\s+', '', cues[0].text), ''.join(parts))

    def test_unsplittable_native_token_and_open_quote_keep_native_clock(self):
        text = '今日は晴れです 明日も来てください ありがとうございました'
        payload = {'tokens': [{'text': text, 'start_ms': 100, 'end_ms': 40000, 'speaker': '1'}]}
        cues = parse_tokens(payload, 41)
        self.assertEqual([(c.start, c.end, c.text) for c in cues], [(.1, 40, text)])
        payload, duration = fixture(['お題は「何を作りますか', '好きなものですか」と聞きました。'])
        self.assertEqual(len(parse_tokens(payload, duration)), 1)

    def test_no_fixed_duration_or_character_cutoff(self):
        text = 'あ' * 450
        payload, duration = fixture([text])
        self.assertEqual([c.text for c in parse_tokens(payload, duration)], [text])

    def test_nominal_age_reply_does_not_span_applause_or_take_next_sentence(self):
        payload, duration = fixture(['二十三歳', '本日はありがとうございました'], gap=24000)
        self.assertEqual([c.text for c in parse_tokens(payload, duration)],
                         ['二十三歳', '本日はありがとうございました'])

    def test_late_punctuation_stays_with_text_without_extending_speech(self):
        payload, duration = fixture(['ありがとう', '。'], gap=2000, blanks=False)
        cues = parse_tokens(payload, duration)
        self.assertEqual([c.text for c in cues], ['ありがとう。'])
        self.assertEqual(cues[0].end, payload['tokens'][4]['end_ms']/1000)

    def test_bilingual_export_after_segmentation_uses_one_request(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertTrue(run_utterance_check(Path(folder))['bilingual_times_match'])
