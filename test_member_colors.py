from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine import Settings
from member_catalog import initial_members
from member_color_catalog import OFFICIAL_COLORS
from member_colors import annotate_identities, export_palette, official_style, validate_style
from member_terms import load_library, save_library, member_reference
from ass_colors import ass_color
from subtitles import Cue, make_ass
from member_color_checks import run_member_color_check


class MemberColorTests(unittest.TestCase):
    def test_all_49_have_official_color_names_and_sources(self):
        doc = initial_members()
        self.assertEqual(len(OFFICIAL_COLORS), 49)
        for member in doc['members']:
            self.assertTrue(member['penlight_colors'])
            self.assertTrue(member['color_source_url'].startswith('https://www.akb48.co.jp/') or member['color_source_url'].startswith('https://org.akb48.co.jp/'))
            self.assertEqual(member['color_checked_at'], '2026-11-14')
            validate_style(member['subtitle_style'])
        self.assertEqual(OFFICIAL_COLORS['akb-110']['colors'], ['青', '白', '赤'])
        self.assertEqual(OFFICIAL_COLORS['akb-1083']['colors'], ['水色', 'ピンク'])

    def test_tricolor_and_single_color_mapping(self):
        self.assertEqual(official_style('akb-110')['shadow'], '#FF3030')
        self.assertEqual(official_style('akb-192')['shadow'], '#FFFFFF')
        self.assertEqual(official_style('akb-1010')['outline'], '#181818')

    def test_rgb_bgr_and_color_validation(self):
        self.assertEqual(ass_color('#123456'), '&H00563412')
        for value in ('red', '#123', '#XYZ123', '0,0,0\nStyle: Bad'):
            with self.assertRaises(ValueError):
                validate_style({'enabled': True, 'font': value, 'outline': '#000000', 'shadow': '#000000'})

    def test_old_library_gains_defaults_without_overwriting_edits(self):
        with tempfile.TemporaryDirectory() as folder:
            doc = initial_members()
            first = doc['members'][0]
            first['chinese_name'] = '用户修改译名'
            first['subtitle_style']['font'] = '#123456'
            doc['members'][1].pop('subtitle_style')
            path = Path(folder) / 'old.json'
            path.write_text(json.dumps(doc), encoding='utf-8')
            loaded = load_library(str(path))
            self.assertEqual(loaded['members'][0]['chinese_name'], '用户修改译名')
            self.assertEqual(loaded['members'][0]['subtitle_style']['font'], '#123456')
            self.assertTrue(loaded['members'][1]['subtitle_style']['enabled'])

    def test_mentions_do_not_identify_the_speaker_or_global_numeric_ids(self):
        texts = [('1', '岩立沙穂です。'), ('1', '水島美結さんが好きです。'), ('2', '水島美結さんです。'),
                 ('1', '次の話をします。'), ('4', '今日の主役は水島美結です。'), ('4', '【合唱】みんなで歌おう。')]
        doc = {'cues': [{'id': i + 1, 'start': i, 'end': i + .8, 'text': text, 'speaker': speaker} for i, (speaker, text) in enumerate(texts)]}
        annotate_identities(doc, Settings(), {'members': initial_members()['members']})
        self.assertEqual(doc['cues'][0]['member_id'], 'akb-110')
        self.assertEqual(doc['cues'][1]['member_id'], 'akb-110')
        self.assertNotIn('member_id', doc['cues'][2])
        self.assertNotIn('member_id', doc['cues'][3])
        self.assertNotIn('member_id', doc['cues'][4])
        self.assertTrue(doc['cues'][5]['chorus'])

    def test_manual_marker_wins_and_lyrics_are_not_automatically_chorus(self):
        doc = {'cues': [{'id': 1, 'start': 0, 'end': 1, 'text': '水島美結です。', 'speaker': '1',
                        'member_id': 'akb-110', 'role_source': 'manual'},
                       {'id': 2, 'start': 1.1, 'end': 3, 'text': '青い空', 'speaker': '1', 'lyric': {'line': 1}}]}
        annotate_identities(doc, Settings(), {'members': initial_members()['members']})
        self.assertEqual(doc['cues'][0]['member_id'], 'akb-110')
        self.assertFalse(doc['cues'][1].get('chorus'))

    def test_self_intro_long_silence_does_not_assign_later_scene(self):
        doc = {'cues': [{'id': 1, 'start': 0, 'end': 1, 'text': '岩立沙穂です。', 'speaker': '1'},
                       {'id': 2, 'start': 8, 'end': 9, 'text': '新しい場面です。', 'speaker': '1'}]}
        annotate_identities(doc, Settings(), {'members': initial_members()['members']})
        self.assertNotIn('member_id', doc['cues'][1])

    def test_disabled_library_does_not_load_broken_color_file(self):
        settings = Settings(member_library='Z:/missing.json', use_member_glossary=False)
        self.assertEqual(export_palette(settings)['members'], {})

    def test_proofread_self_intro_colors_with_request_library_snapshot(self):
        from subtitle_jobs import create_job, process_job, load_job
        from workflow_checks import response
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            library = folder / 'members.json'
            save_library(initial_members(), str(library))
            settings = Settings(deepseek=True, translate=True, deepseek_key='offline-test', member_library=str(library))
            path, _ = create_job(None, folder / 'proofread.ass', [
                Cue(1.25, 2.5, 'いわたてです。', speaker='1'),
                Cue(2.6, 3.75, '今日はうれしいです。', speaker='1'),
                Cue(4, 5.5, 'みんなで歌おう。', speaker='chorus')], settings)

            def answer(*args, **kwargs):
                sent = json.loads(kwargs['body']['messages'][1]['content'])
                self.assertEqual(len(sent['subtitles']), 3)
                self.assertNotIn('subtitle_style', str(sent['member_glossary']))
                changed = load_library(str(library))
                changed['members'] = []
                save_library(changed, str(library))
                return response([
                    {'id': 1, 'japanese': '岩立沙穂です。', 'chinese': '我是岩立沙穗。'},
                    {'id': 2, 'japanese': '今日はうれしいです。', 'chinese': '今天很开心。'},
                    {'id': 3, 'japanese': 'みんなで歌おう。', 'chinese': '大家一起唱吧。'}])

            with patch('engine.post_json', side_effect=answer) as request:
                result = process_job(path, settings)
            self.assertEqual(request.call_count, 1)
            self.assertEqual(result.remaining, 0)
            rows = load_job(path)['cues']
            self.assertEqual([row.get('member_id') for row in rows], ['akb-110', 'akb-110', None])
            self.assertTrue(rows[2]['chorus'])
            events = [line.split(',', 9) for line in result.output.read_text('utf-8-sig').splitlines() if line.startswith('Dialogue:')]
            self.assertEqual(len(events), 6)
            self.assertTrue(events[0][3].startswith('japanese_岩立沙穂_'))
            self.assertEqual(events[4][3], 'japanese_chorus')
            for jp, zh in zip(events[::2], events[1::2]):
                self.assertEqual(jp[1:3], zh[1:3])

    def test_gui_color_edit_and_existing_record_reexport_without_api(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertTrue(run_member_color_check(Path(folder))['color_only_no_api'])

    def test_colored_ass_import_preserves_roles_and_skips_chinese_styles(self):
        from subtitle_jobs import import_japanese_ass
        with tempfile.TemporaryDirectory() as folder:
            settings = Settings()
            source = Path(folder) / 'colored.ass'
            source.write_text(make_ass([Cue(0, 1, 'こんにちは。', '你好。', member_id='akb-1083'),
                                       Cue(2, 3, 'みんなの歌', '大家的歌', chorus=True)], coloring=export_palette(settings)), encoding='utf-8')
            path, doc = import_japanese_ass(source, Path(folder) / 'import.ass', settings)
            self.assertEqual(len(doc['cues']), 2)
            self.assertEqual(doc['cues'][0]['member_id'], 'akb-1083')
            self.assertTrue(doc['cues'][1]['chorus'])

    def test_stale_editor_cannot_overwrite_new_translation(self):
        from caption_colors import save_coloring, fingerprint
        from subtitle_jobs import create_job, load_job, save_job
        with tempfile.TemporaryDirectory() as folder:
            settings = Settings(translate=False)
            path, doc = create_job(None, Path(folder) / 'a.ass', [Cue(0, 1, 'はい。')], settings)
            old_hash = fingerprint(path)
            new = deepcopy(doc)
            new['cues'][0]['text'] = '更新された字幕。'
            save_job(path, new)
            with self.assertRaises(ValueError):
                save_coloring(path, doc, settings, expected_hash=old_hash)
            self.assertEqual(load_job(path)['cues'][0]['text'], '更新された字幕。')
