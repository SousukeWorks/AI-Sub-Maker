from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from engine import Settings
from lyrics import save_library
from member_catalog import initial_members
from member_terms import save_library as save_members
from soniox_context import MAX_CONTEXT_BYTES, build_context, context_size


class SonioxContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name).resolve()
        self.lyrics = self.folder / 'lyrics.json'
        self.members = self.folder / 'members.json'
        save_members(initial_members(), str(self.members))
        self.settings = Settings(lyrics_library=str(self.lyrics), lyric_stage='test-stage', member_library=str(self.members))

    def library(self, songs):
        doc = {'schema': 1, 'stages': [{'id': 'test-stage', 'name': 'テスト公演', 'songs': songs},
                                    {'id': 'other-stage', 'name': '別の公演', 'songs': [
                                        {'id': 'other', 'title': '別の曲', 'japanese': 'UNSELECTED_PRIVATE_WORD', 'chinese': ''}]}]}
        save_library(doc, str(self.lyrics))

    def test_all_songs_are_scanned_for_english_before_excerpt_limit(self):
        songs = [{'id': f'song-{i}', 'title': f'曲目{i}',
                  'japanese': '\n'.join(f'これは曲目{i}の歌詞例{j}です。' for j in range(80)), 'chinese': ''}
                 for i in range(18)]
        songs[-1]['japanese'] += '\nMaybe\nMaybe\n'
        self.library(songs)
        context, summary = build_context(self.settings)
        self.assertIn('Maybe', context['terms'])
        self.assertTrue(all(song['title'] in context['terms'] for song in songs))
        self.assertLessEqual(context_size(context), MAX_CONTEXT_BYTES)
        self.assertTrue(summary['lyrics_sampled'])
        self.assertGreaterEqual(context['text'].count('[曲目'), 18)
        self.assertNotIn('UNSELECTED_PRIVATE_WORD', json.dumps(context))

    def test_enabled_japanese_names_and_nicknames_only(self):
        self.library([])
        members = initial_members()
        first = members['members'][0]
        first['notes'] = 'PRIVATE_NOTE_MUST_NOT_BE_SENT'
        first['chinese_name'] = '中文不应发送'
        members['members'][1]['enabled'] = False
        members['terms'] = [{'id': 'word-test', 'japanese_term': 'ライブテスト', 'chinese_term': '',
                             'translation': 'preserve', 'enabled': True}]
        save_members(members, str(self.members))
        context, _ = build_context(self.settings)
        self.assertIn(first['japanese_name'], context['terms'])
        self.assertIn(first['nicknames_japanese'][0], context['terms'])
        self.assertIn('ライブテスト', context['terms'])
        self.assertNotIn(members['members'][1]['japanese_name'], context['terms'])
        self.assertNotIn('中文不应发送', json.dumps(context, ensure_ascii=False))
        self.assertNotIn('PRIVATE_NOTE_MUST_NOT_BE_SENT', json.dumps(context))

    def test_disabled_context_does_not_read_libraries(self):
        context, summary = build_context(replace(self.settings, soniox_send_context=False,
                                                lyrics_library='Z:/missing.json', member_library='Z:/missing.json'))
        self.assertIsNone(context)
        self.assertFalse(summary['enabled'])

    def test_disabled_wordbook_does_not_read_it_or_send_chinese_lyrics(self):
        self.library([{'id': 'song', 'title': 'Test song', 'japanese': 'Maybe\nこれは歌詞の例です',
                       'chinese': 'PRIVATE_CHINESE_ONE\nPRIVATE_CHINESE_TWO'}])
        context, _ = build_context(replace(self.settings, use_member_glossary=False, member_library='Z:/missing.json'))
        self.assertIn('Maybe', context['terms'])
        self.assertNotIn('PRIVATE_CHINESE', json.dumps(context))
        self.assertNotIn('岩立沙穂', context['terms'])

    def test_context_is_deterministic_and_deduplicates_repeated_lyrics(self):
        self.library([{'id': 'song', 'title': 'Test song', 'japanese': 'Maybe\nMaybe\nこれは歌詞の例です', 'chinese': ''}])
        first, summary = build_context(self.settings)
        second, summary2 = build_context(self.settings)
        self.assertEqual(first, second)
        self.assertEqual(summary, summary2)
        self.assertEqual(summary['lyric_lines_available'], 2)
        self.assertEqual(summary['lyric_lines_sent'], 2)
        self.assertEqual(first['terms'].count('Maybe'), 1)

    def test_gui_http_snapshot_disable_and_cache_without_live_api(self):
        from soniox_checks import run_soniox_context_check
        self.assertTrue(run_soniox_context_check(self.folder)['context_sent_in_paid_request'])
