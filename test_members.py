from dataclasses import replace
from pathlib import Path
import json
import tempfile
import threading
import unittest
from unittest.mock import patch

from deepseek_request import request_processing
from engine import Settings
from member_catalog import initial_members
from member_checks import run_member_check, sample_library
from member_terms import member_reference, load_library, save_library, apply_member_terms, validate
from subtitles import Cue
from workflow_checks import response


class MemberTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.path = self.folder / 'members.json'
        save_library(sample_library(), str(self.path))
        self.settings = Settings(deepseek=True, deepseek_key='offline-key', member_library=str(self.path))

    def test_seed_is_independent_and_contains_verified_names_and_nicknames(self):
        document = initial_members()
        self.assertEqual(len(document['members']), 49)
        validate(document)
        self.assertTrue(all(row['chinese_name'] and row['reading_japanese'] and row['nicknames_japanese'] and row['source_url'].startswith('https://www.akb48.co.jp/') for row in document['members']))
        document['members'][0]['japanese_name'] = 'changed'
        self.assertNotEqual(initial_members()['members'][0]['japanese_name'], 'changed')

    def test_combined_call_and_selected_retry_preserve_nickname_and_translate_name(self):
        report = run_member_check(self.folder)
        self.assertEqual(report['request_sizes'], [3, 1])
        self.assertEqual(report['extra_automatic_calls'], 0)

    def test_edited_library_is_used_in_next_request_and_disabled_member_excluded(self):
        document = load_library(str(self.path))
        document['members'][0]['enabled'] = False
        document['members'][1]['chinese_name'] = '坂川阳香新译名'
        save_library(document, str(self.path))
        with patch('engine.post_json', return_value=response([])) as request:
            request_processing([(1, Cue(0, 1, '测试'))], {1: ['translate']}, self.settings, threading.Event())
        payload = json.loads(request.call_args.kwargs['body']['messages'][1]['content'])
        members = payload['member_glossary']['members']
        self.assertEqual(len(members), 2)
        self.assertEqual(members[0]['chinese_name'], '坂川阳香新译名')
        self.assertNotIn('source_url', members[0])

    def test_global_disable_omits_library_and_ignores_missing_file(self):
        settings = replace(self.settings, use_member_glossary=False, member_library=str(self.folder / 'missing.json'))
        self.assertIsNone(member_reference(settings))
        with patch('engine.post_json', return_value=response([])) as request:
            request_processing([(1, Cue(0, 1, '测试'))], {1: ['translate']}, settings, threading.Event())
        payload = json.loads(request.call_args.kwargs['body']['messages'][1]['content'])
        self.assertNotIn('member_glossary', payload)

    def test_invalid_or_ambiguous_member_names_are_rejected_before_api(self):
        for mode in ('duplicate_id', 'duplicate_name', 'empty_chinese', 'bad_list'):
            document = sample_library()
            if mode == 'duplicate_id':
                document['members'][1]['id'] = document['members'][0]['id']
            elif mode == 'duplicate_name':
                document['members'][1]['japanese_name'] = document['members'][0]['japanese_name']
            elif mode == 'empty_chinese':
                document['members'][0]['chinese_name'] = ''
            else:
                document['members'][0]['nicknames_japanese'] = 'not a list'
            self.path.write_text(json.dumps(document), encoding='utf-8')
            with self.subTest(mode=mode), patch('engine.post_json') as request, self.assertRaises(ValueError):
                request_processing([(1, Cue(0, 1, '测试'))], {1: ['translate']}, self.settings, threading.Event())
            request.assert_not_called()

    def test_disabled_new_member_can_wait_for_a_chinese_name(self):
        document = sample_library()
        document['members'][0].update(enabled=False, chinese_name='')
        save_library(document, str(self.path))
        self.assertEqual(len(member_reference(self.settings)['members']), 2)

    def test_member_library_path_roundtrip_keeps_key_and_old_settings(self):
        import config
        with patch.object(config, 'data_dir', return_value=self.folder):
            config.save(self.settings, True)
            settings, remembered, warning = config.load()
        self.assertTrue(remembered)
        self.assertEqual(settings.member_library, str(self.path))
        self.assertTrue(settings.use_member_glossary)
        self.assertEqual(settings.deepseek_key, 'offline-key')
        self.assertFalse(warning)

    def test_proofreading_cannot_expand_a_clear_nickname(self):
        texts, errors = {(1, 'proofread'): '坂川陽香が来ます。'}, {}
        apply_member_terms(texts, errors, [{'id': 1, 'text': 'ひゆかが来ます。'}], {1: ['proofread']}, member_reference(self.settings))
        self.assertNotIn((1, 'proofread'), texts)
        self.assertIn('昵称', errors[1, 'proofread'])

    def test_two_character_common_word_is_not_forced_to_be_a_nickname(self):
        texts, errors = {(1, 'translate'): '明天会更开心。'}, {}
        apply_member_terms(texts, errors, [{'id': 1, 'text': '明日は楽しくなる。'}], {1: ['translate']}, member_reference(self.settings))
        self.assertFalse(errors)
        self.assertIn((1, 'translate'), texts)

    def test_short_nickname_with_honorific_is_preserved(self):
        document = initial_members()
        reference = {'members': [row for row in document['members'] if row['japanese_name'] == '下尾みう']}
        texts, errors = {(1, 'translate'): '美羽来了。'}, {}
        apply_member_terms(texts, errors, [{'id': 1, 'text': 'みうちゃんが来た。'}], {1: ['translate']}, reference)
        self.assertNotIn((1, 'translate'), texts)
        self.assertIn('みう', errors[1, 'translate'])

    def test_greeting_does_not_contain_a_member_nickname(self):
        reference = {'members': [{'japanese_name': '橋本恵理子', 'chinese_name': '桥本惠理子',
                                  'nicknames_japanese': ['えりちゃん']}]}
        texts, errors = {(1, 'translate'): '欢迎回来。'}, {}
        apply_member_terms(texts, errors, [{'id': 1, 'text': 'おかえりちゃん'}], {1: ['translate']}, reference)
        self.assertFalse(errors)
        self.assertEqual(texts[1, 'translate'], '欢迎回来。')
        texts, errors = {(1, 'translate'): '惠理来了。'}, {}
        apply_member_terms(texts, errors, [{'id': 1, 'text': '今日はえりちゃんが来た。'}], {1: ['translate']}, reference)
        self.assertIn((1, 'translate'), errors)

    def test_nickname_inside_full_name_does_not_prevent_name_translation(self):
        document = initial_members()
        reference = {'members': [row for row in document['members'] if row['japanese_name'] == '奥本カイリ']}
        texts, errors = {(1, 'translate'): '奥本カイリ来了。'}, {}
        apply_member_terms(texts, errors, [{'id': 1, 'text': '奥本カイリが来ました。'}], {1: ['translate']}, reference)
        self.assertFalse(errors)
        self.assertEqual(texts[1, 'translate'], '奥本海里来了。')

    def test_conflicting_full_name_alias_is_not_blindly_replaced(self):
        document = sample_library()
        for member in document['members'][:2]:
            member['aliases_japanese'] = ['共通の別名']
        reference = {'members': document['members']}
        texts, errors = {(1, 'translate'): '共通の別名来了。'}, {}
        apply_member_terms(texts, errors, [{'id': 1, 'text': '共通の別名が来ました。'}], {1: ['translate']}, reference)
        self.assertEqual(texts[1, 'translate'], '共通の別名来了。')


if __name__ == '__main__':
    unittest.main()
