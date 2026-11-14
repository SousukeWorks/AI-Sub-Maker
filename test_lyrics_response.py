import json
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from deepseek_response import decode_rows
from engine import Settings
from lyrics import apply_to_tokens, find_matches, load_library, normalized, save_library
from soniox_asr import import_result, parse_tokens
from stage_catalog import initial_library
from subtitle_jobs import create_job, load_job, process_job
from subtitles import Cue, make_ass
from text_cleanup import clean_start


LINES = ['青色の宇宙船で月まで出かけよう', '猫の隊長が明日の地図を広げた']  # Invented fixtures.


def fixture_stage():
    return {'id': 'test', 'name': '自作テスト公演', 'songs': [
        {'id': 'song', 'title': '自作テスト曲', 'japanese': '\n'.join(LINES), 'chinese': ''}]}


def fixture_tokens(lines=LINES):
    tokens = [{'text': '皆さんこんにちは。', 'start_ms': 0, 'end_ms': 900, 'speaker': '1'}]
    clock = 2000
    for line in lines:
        for char in line:
            tokens.append({'text': char, 'start_ms': clock, 'end_ms': clock + 150, 'speaker': str(clock % 3)})
            clock += 200
        clock += 600
    tokens.append({'text': 'ありがとうございました。', 'start_ms': clock + 1000, 'end_ms': clock + 1900, 'speaker': '2'})
    return tokens, (clock + 2500) / 1000


class LyricResponseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.library = self.folder / 'lyrics.json'
        save_library({'schema': 1, 'stages': [fixture_stage()]}, str(self.library))
        self.settings = Settings(deepseek=True, deepseek_key='private-key', lyrics_library=str(self.library), lyric_stage='test')

    def test_catalog_has_five_current_stages_without_copyrighted_lyrics(self):
        document = initial_library()
        self.assertEqual(len(document['stages']), 5)
        self.assertEqual(sum(len(stage['songs']) for stage in document['stages']), 86)
        self.assertTrue(all(not song['japanese'] for stage in document['stages'] for song in stage['songs']))

    def test_save_reload_custom_library_and_lrc(self):
        from lyrics import lyric_lines
        self.assertEqual(load_library(str(self.library))['stages'][0], fixture_stage())
        self.assertEqual(lyric_lines('[ar:自作]\n[00:01.10]。第一行\n[01:02.50][01:04.50]第二行'), ['第一行', '第二行'])
        with self.assertRaises(ValueError):
            load_library(str(self.folder / 'missing.json'))

    def test_two_lyric_lines_use_native_boundaries_and_preserve_mc(self):
        tokens, duration = fixture_tokens()
        cues, report = apply_to_tokens({'tokens': tokens}, duration, self.settings)
        self.assertEqual(report['matched_lines'], 2)
        matched = [cue for cue in cues if cue.lyric]
        self.assertEqual([cue.text for cue in matched], LINES)
        self.assertEqual((matched[0].start, matched[0].end), (2, (2000 + (len(LINES[0]) - 1) * 200 + 150) / 1000))
        self.assertEqual(normalized(''.join(cue.text for cue in cues)), normalized(''.join(token['text'] for token in tokens)))
        self.assertTrue(cues[0].text.startswith('皆さん'))
        self.assertEqual(cues[-1].text, 'ありがとうございました。')

    def test_one_isolated_match_or_long_silence_does_not_replace_mc(self):
        tokens, duration = fixture_tokens(LINES[:1])
        _, report = apply_to_tokens({'tokens': tokens}, duration, self.settings)
        self.assertEqual(report['matched_lines'], 0)
        units = [{'text': LINES[0], 'start': 1, 'end': 3}, {'text': LINES[1], 'start': 30, 'end': 32}]
        self.assertEqual(find_matches(units, fixture_stage()), [])

    def test_native_tail_tolerance_never_exports_lyrics_past_media_end(self):
        tokens, _ = fixture_tokens()
        tokens = tokens[:-1]
        duration = tokens[-1]['end_ms'] / 1000 - .05
        cues, report = apply_to_tokens({'tokens': tokens}, duration, self.settings)
        self.assertEqual(report['matched_lines'], 2)
        self.assertEqual(cues[-1].end, duration)

    def test_fuzzy_spelling_repair_requires_supported_sequence(self):
        tokens, duration = fixture_tokens([LINES[0].replace('宇宙船', '宇宙線'), LINES[1]])
        cues, report = apply_to_tokens({'tokens': tokens}, duration, self.settings)
        self.assertEqual(report['matched_lines'], 2)
        self.assertEqual(next(cue.text for cue in cues if cue.lyric), LINES[0])

    def test_repeated_chorus_uses_each_recorded_time_and_no_invented_verse(self):
        tokens, duration = fixture_tokens(LINES + LINES)
        cues, report = apply_to_tokens({'tokens': tokens}, duration, self.settings)
        self.assertEqual(report['matched_lines'], 4)
        self.assertEqual([cue.text for cue in cues if cue.lyric], LINES + LINES)

    def test_soniox_json_import_resegments_without_any_network(self):
        tokens, duration = fixture_tokens()
        source = self.folder / 'saved.soniox.json'
        content = json.dumps({'schema': 1, 'status': 'completed', 'duration': duration, 'transcript': {'tokens': tokens}})
        source.write_text(content)
        with patch('soniox_asr.Client.request', side_effect=AssertionError('Network called')):
            path, job = import_result(source, self.folder / 'out.ass', self.settings)
        self.assertEqual(sum(bool(row.get('lyric')) for row in job['cues']), 2)
        self.assertEqual(source.read_text(), content)
        self.assertTrue((self.folder / 'out.lyrics-timing.json').exists())
        self.assertEqual(load_job(path)['cues'], job['cues'])

    def test_bad_library_fails_before_creating_paid_job(self):
        from soniox_asr import transcribe
        document = load_library(str(self.library))
        document['stages'][0]['songs'][0]['chinese'] = '只有一行'
        save_library(document, str(self.library))
        with patch('soniox_asr.Client.request') as request, self.assertRaisesRegex(ValueError, '行数不一致'):
            transcribe(self.folder / 'audio.mp4', self.folder / 'out.ass', self.settings, threading.Event(), lambda _: None, lambda _: None)
        request.assert_not_called()

    def job(self, texts=('字幕一', '字幕二', '字幕三')):
        return create_job(None, self.folder / 'job.ass', [Cue(i * 2, i * 2 + 1, text) for i, text in enumerate(texts)], self.settings)[0]

    def api(self, text, finish='stop'):
        return {'model': 'test', 'usage': {'completion_tokens': 42}, 'choices': [{'finish_reason': finish, 'message': {'content': text}}]}

    def test_corrupt_object_does_not_fail_other_complete_rows(self):
        path = self.job()
        content = '{"subtitles":[{"id":1,"japanese":"日一","chinese":"中一"},{"id":2,"japanese":"坏" "chinese":"坏"},{"id":3,"japanese":"日三","chinese":"中三"}]}'
        with patch('engine.post_json', return_value=self.api(content)) as request:
            result = process_job(path, self.settings)
        self.assertEqual((result.remaining, request.call_count), (1, 1))
        job = load_job(path)
        self.assertEqual([row['proofread']['status'] for row in job['cues']], ['success', 'failed', 'success'])
        diagnostic = json.loads((self.folder / job['responses'][-1]).read_text(encoding='utf-8-sig'))
        self.assertEqual(diagnostic['content'], content)
        self.assertEqual(diagnostic['usage']['completion_tokens'], 42)
        self.assertNotIn('private-key', json.dumps(diagnostic))

    def test_truncated_output_retains_valid_prefix_and_retries_only_missing_ids(self):
        path = self.job()
        content = '{"subtitles":[{"id":1,"japanese":"。日一","chinese":"，中一"},{"id":2,"japanese":"未结束'
        with patch('engine.post_json', return_value=self.api(content, 'length')) as request:
            result = process_job(path, self.settings)
        self.assertEqual((result.remaining, request.call_count), (2, 1))
        job = load_job(path)
        self.assertEqual(job['cues'][0]['text'], '日一')
        self.assertEqual(job['cues'][0]['translation'], '中一')
        followup = json.dumps({'subtitles': [{'id': i, 'japanese': '日文', 'chinese': '中文'} for i in (2, 3)]})
        with patch('engine.post_json', return_value=self.api(followup)) as request:
            self.assertEqual(process_job(path, self.settings).remaining, 0)
        sent = json.loads(request.call_args.kwargs['body']['messages'][1]['content'])['subtitles']
        self.assertEqual([row['id'] for row in sent], [2, 3])

    def test_duplicate_ids_are_rejected_even_in_damaged_response(self):
        path = self.job()
        content = '{"subtitles":[{"id":1,"japanese":"甲","chinese":"乙"},{"id":1,"japanese":"丙","chinese":"丁"},'
        with patch('engine.post_json', return_value=self.api(content)):
            result = process_job(path, self.settings)
        self.assertEqual(result.remaining, 3)
        self.assertIn('重复', load_job(path)['cues'][0]['proofread']['error'])

    def test_scanner_handles_escaped_quotes_and_braces_without_guessing_broken_string(self):
        row = {'id': 1, 'japanese': '括弧 } と "引用"', 'chinese': '反斜杠 \\'}
        text = '{"subtitles":[' + json.dumps(row) + ',{"id":2,"japanese":"切断'
        rows, issue = decode_rows(text)
        self.assertEqual(rows, [row])
        self.assertTrue(issue)

    def test_old_punctuation_only_rows_are_suppressed_without_changing_ids(self):
        path = self.job()
        job = load_job(path)
        job['cues'][1]['text'] = '。！？、'
        from subtitle_jobs import save_job
        save_job(path, job)
        content = json.dumps({'subtitles': [{'id': i, 'japanese': '日文', 'chinese': '中文'} for i in (1, 3)]})
        with patch('engine.post_json', return_value=self.api(content)) as request:
            result = process_job(path, replace(self.settings, lyric_stage=''))
        self.assertEqual(result.remaining, 0)
        job = load_job(path)
        self.assertEqual([row['id'] for row in job['cues']], [1, 2, 3])
        self.assertTrue(job['cues'][1]['suppressed'])
        self.assertEqual(len(json.loads(request.call_args.kwargs['body']['messages'][1]['content'])['subtitles']), 2)

    def test_leading_punctuation_is_removed_at_final_export_in_both_languages(self):
        self.assertEqual(clean_start('  。、！？「テスト。'), 'テスト。')
        output = make_ass([Cue(0, 1, '。テスト。', '，。测试！'), Cue(2, 3, '。')])
        rows = [line for line in output.splitlines() if line.startswith('Dialogue:')]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].split(',', 9)[-1], 'テスト。')
        self.assertEqual(rows[1].split(',', 9)[-1], '测试！')

    def test_lyric_reference_is_preserved_even_if_model_rewrites_it(self):
        path = self.job(LINES)
        content = json.dumps({'subtitles': [{'id': i, 'japanese': '模型改写', 'chinese': '翻译'} for i in (1, 2)]})
        with patch('engine.post_json', return_value=self.api(content)) as request:
            result = process_job(path, self.settings)
        self.assertEqual(result.remaining, 0)
        self.assertEqual([row['text'] for row in load_job(path)['cues']], LINES)
        data = json.loads(request.call_args.kwargs['body']['messages'][1]['content'])['subtitles']
        self.assertEqual(data[0]['lyric_reference']['japanese'], LINES[0])


if __name__ == '__main__':
    unittest.main()
