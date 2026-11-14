from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from engine import Settings
from lyrics import display_lines, save_library, chinese_lines, load_library
from lyric_context import stage_reference
from soniox_asr import Client, ApiError, parse_tokens
from soniox_context import build_context
from utterance_checks import fixture
from phrase_checks import LYRIC_PARTS, LYRIC_LINES, RAP, library_data, run_phrase_check


class PhraseTests(unittest.TestCase):
    def test_connected_actions_split_without_spaces_or_punctuation(self):
        parts = ['朝から来てくださって', 'そして歌ってくださって', '写真を見てくださって', '本当にありがとうございます']
        payload, duration = fixture(parts, blanks=False)
        report = {}
        cues = parse_tokens(payload, duration, report=report)
        self.assertEqual([c.text for c in cues], parts)
        self.assertIsNone(report['length_limits'])

    def test_leading_asr_whitespace_does_not_shift_word_boundary(self):
        parts = [' 朝から来てくださって', 'そして歌ってくださって', '本当にありがとうございます']
        payload, duration = fixture(parts)
        self.assertEqual([c.text for c in parse_tokens(payload,duration)], [p.strip() for p in parts])

    def test_nominalized_clause_keeps_its_particle(self):
        parts = ['演目を選んだのが', '未経験の公演だったのと', 'あとは皆さんと会いたいです']
        payload, duration = fixture(parts, blanks=False)
        self.assertEqual([c.text for c in parse_tokens(payload,duration)], parts)

    def test_long_modifier_can_be_a_display_unit_without_splitting_a_word(self):
        parts = ['皆さんを元気に笑顔にできる', '歌手になりたいと思っています']
        payload, duration = fixture(parts, blanks=False)
        self.assertEqual([c.text for c in parse_tokens(payload,duration)], parts)

    def test_short_attributive_or_auxiliary_chain_stays_connected(self):
        for text in ('雨が降る予定です。', '明るく輝いた星になる。', '落ち着いて話した。', '楽しい気持ちでいっぱいになる。'):
            payload,duration=fixture([text])
            self.assertEqual([c.text for c in parse_tokens(payload,duration)], [text])

    def test_song_uses_four_phrases_even_without_library(self):
        payload,duration=fixture(LYRIC_LINES)
        self.assertEqual([c.text for c in parse_tokens(payload,duration)], LYRIC_PARTS)

    def test_negative_adjective_is_not_orphaned_after_multiple_topics(self):
        parts=['明るい庭には 雑草はひとつもない','Hello Hello','また遊びに来てください']
        payload,duration=fixture(parts)
        self.assertEqual([c.text for c in parse_tokens(payload,duration)],parts)

    def test_lyric_lines_group_dependencies_and_english_repetition(self):
        groups=display_lines(library_data()['stages'][0]['songs'][1])
        self.assertEqual([r['japanese'] for r in groups], LYRIC_PARTS)
        self.assertEqual([(r['line'],r['line_end']) for r in groups], [(1,2),(3,4),(5,5),(6,7)])

    def test_full_lyric_requests_preserve_order_and_rap(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'lyrics.json'
            data=library_data()
            data['stages'][0]['songs'][0]['japanese'] += '\n' + RAP*150
            save_library(data,str(p))
            settings=Settings(lyrics_library=str(p),lyric_stage='phrases')
            reference=stage_reference(settings)
            context,summary=build_context(settings)
            for song in reference['songs']:
                self.assertIn(song['japanese'],context['text'])
            self.assertEqual(summary['lyric_lines_sent'],summary['lyric_lines_available'])
            self.assertFalse(summary['lyrics_sampled'])
            self.assertGreater(len(context['text'].encode('utf-8')),7000)

    def test_server_context_rejection_is_clear_without_retry(self):
        with patch('soniox_asr.requests.request') as request:
            response=MagicMock(status_code=400)
            request.return_value=response
            with self.assertRaisesRegex(ApiError,'8000 tokens'):
                Client(Settings(soniox_key='offline-key')).request('POST','transcriptions',body={'context':{'text':RAP}})
            self.assertEqual(request.call_count,1)

    def test_full_requests_and_four_bilingual_native_intervals(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertTrue(run_phrase_check(Path(folder))['full_lyrics_both_apis'])

    def test_partial_translation_retains_empty_line_positions(self):
        song={'title':'自作曲','japanese':'第一行\n第二行\n第三行','chinese':'第一译\n\n第三译','chinese_partial':True}
        self.assertEqual(chinese_lines(song),['第一译','','第三译'])

    def test_incomplete_group_is_translated_instead_of_using_half_a_translation(self):
        song={'title':'自作曲','japanese':'星の広場には\n誰も迷わない','chinese':'星星广场上\n','chinese_partial':True}
        self.assertEqual(display_lines(song)[0]['chinese'],'')

    def test_partial_translation_survives_editor_save(self):
        import tkinter as tk
        from lyrics_ui import LyricsEditor
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'lyrics.json'
            data=library_data()
            song=data['stages'][0]['songs'][0]
            song['chinese']='\n第二行\n\n第四行\n'
            song['chinese_partial']=True
            save_library(data,str(p))
            root=tk.Tk();root.withdraw()
            try:
                editor=LyricsEditor(root,str(p),'phrases')
                editor.withdraw()
                self.assertTrue(editor.save())
                self.assertEqual(load_library(str(p))['stages'][0]['songs'][0]['chinese'],song['chinese'])
            finally:
                root.destroy()
