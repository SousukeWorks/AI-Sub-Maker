from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
import json
import tempfile
import threading
import unittest

from deepseek_request import request_processing
from engine import Settings
from member_checks import sample_library
from member_terms import apply_member_terms, load_library, member_reference, save_library, validate
from subtitles import Cue
from wordbook_checks import run_wordbook_check
from workflow_checks import response


def term(japanese='ここからだ公演', chinese='从这里开始公演', mode='translate'):
    return {'id': 'fixture-term', 'enabled': True, 'japanese_term': japanese, 'chinese_term': chinese,
            'translation': mode, 'aliases_japanese': [], 'aliases_chinese': []}


class WordbookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.path = self.folder / 'library.json'

    def test_main_tab_crud_maximize_restore_and_processing_snapshot(self):
        report = run_wordbook_check(self.folder)
        self.assertTrue(all(report.values()))

    def test_old_member_library_loads_without_losing_names_or_nicknames(self):
        document = sample_library()
        self.assertNotIn('terms', document)
        self.path.write_text(json.dumps(document), encoding='utf-8')
        loaded = load_library(str(self.path))
        self.assertEqual(loaded['members'], document['members'])
        self.assertEqual(loaded['terms'], [])

    def test_term_validation_rejects_duplicates_bad_modes_and_missing_translation(self):
        for mode in ('duplicate_id', 'duplicate_name', 'mode', 'empty_chinese', 'bad_alias'):
            document = sample_library()
            entry = term()
            if mode == 'duplicate_id':
                entry['id'] = document['members'][0]['id']
            elif mode == 'duplicate_name':
                entry['japanese_term'] = document['members'][0]['japanese_name']
            elif mode == 'mode':
                entry['translation'] = 'unknown'
            elif mode == 'empty_chinese':
                entry['chinese_term'] = ''
            else:
                entry['aliases_japanese'] = 'bad'
            document['terms'] = [entry]
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                validate(document)

    def test_disabled_terms_are_excluded_and_preserve_mode_can_have_empty_chinese(self):
        document = sample_library()
        document['terms'] = [term('AKB48', '', 'preserve'), {**term(), 'id': 'off', 'enabled': False}]
        save_library(document, str(self.path))
        reference = member_reference(Settings(member_library=str(self.path)))
        self.assertEqual(len(reference['terms']), 1)
        self.assertEqual(reference['terms'][0]['translation'], 'preserve')

    def test_explicit_term_translation_and_preservation_checks(self):
        document = sample_library()
        document['terms'] = [term(), {**term('AKB48', '', 'preserve'), 'id': 'preserved'}]
        save_library(document, str(self.path))
        reference = member_reference(Settings(member_library=str(self.path)))
        texts, errors = {(1, 'translate'): '这里是ここからだ公演。', (2, 'translate'): '这是秋叶原四十八。'}, {}
        apply_member_terms(texts, errors, [{'id': 1, 'text': 'ここからだ公演です。'}, {'id': 2, 'text': 'AKB48です。'}],
                           {1: ['translate'], 2: ['translate']}, reference)
        self.assertEqual(texts[1, 'translate'], '这里是从这里开始公演。')
        self.assertNotIn((2, 'translate'), texts)
        self.assertIn('专有词', errors[2, 'translate'])

    def test_ascii_term_does_not_replace_part_of_another_word(self):
        reference = {'members': [], 'terms': [term('MC', '主持') ]}
        texts, errors = {(1, 'translate'): 'MCDONALD 和 MC。'}, {}
        apply_member_terms(texts, errors, [{'id': 1, 'text': 'MCDONALDとMC'}], {1: ['translate']}, reference)
        self.assertEqual(texts[1, 'translate'], 'MCDONALD 和 主持。')

    def test_broken_library_does_not_prevent_opening_the_main_window(self):
        import config
        from app import App
        self.path.write_text('{broken', encoding='utf-8')
        settings = Settings(member_library=str(self.path), soniox_key='offline-key')
        with patch.object(config, 'load', return_value=(settings, False, '')):
            window = App(start_maximized=False)
        try:
            window.update()
            self.assertTrue(window.member_panel.load_error)
            self.assertFalse(window.member_panel.save_if_dirty())
            self.assertEqual(self.path.read_text(), '{broken')
            window.vars['use_member_glossary'].set(False)
            window.vars['translate'].set(False)
            window.vars['deepseek'].set(False)
            self.assertFalse(window.snapshot().use_member_glossary)
            self.assertEqual(self.path.read_text(), '{broken')
        finally:
            window.destroy()

    def test_terms_are_sent_once_and_revision_changes_when_edited(self):
        document = sample_library()
        document['terms'] = [term()]
        save_library(document, str(self.path))
        settings = Settings(member_library=str(self.path))
        before = member_reference(settings)['revision']
        document['terms'][0]['chinese_term'] = '新译法'
        save_library(document, str(self.path))
        with patch('engine.post_json', return_value=response([])) as request:
            result = request_processing([(1, Cue(0, 1, 'ここからだ公演'))], {1: ['translate']}, settings, threading.Event())
        self.assertEqual(request.call_count, 1)
        self.assertEqual(result.metadata['term_count'], 1)
        self.assertNotEqual(result.metadata['member_glossary_revision'], before)


if __name__ == '__main__':
    unittest.main()
