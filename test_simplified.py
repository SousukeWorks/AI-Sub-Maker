from dataclasses import asdict
from pathlib import Path
import io
import json
import tempfile
import threading
import unittest
from unittest.mock import patch

from engine import Settings, audio_temp_dir, endpoint
from cloud_only_checks import run_cloud_only_check


class CloudOnlyTests(unittest.TestCase):
    def test_migration_and_real_gui_keep_keys_and_remove_obsolete_controls(self):
        with tempfile.TemporaryDirectory() as folder:
            result = run_cloud_only_check(Path(folder))
            self.assertTrue(result['single_soniox_backend'])
            self.assertTrue(result['no_hidden_legacy_prompt_sent'])

    def test_blank_temp_directory_uses_output_disk_without_creating_default_cache(self):
        with patch('engine.data_dir', side_effect=AssertionError('Must not use C drive for audio')):
            self.assertIsNone(audio_temp_dir(Settings()))

    def test_custom_temp_directory_and_invalid_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'chosen'
            self.assertEqual(audio_temp_dir(Settings(temp_folder=str(target))), target)
            self.assertTrue(target.is_dir())
            file = Path(folder) / 'file'
            file.write_text('x')
            for value in ('relative/path', str(file)):
                with self.assertRaises(ValueError):
                    audio_temp_dir(Settings(temp_folder=value))

    def test_unwritable_temp_directory_does_not_fall_back_to_system_disk(self):
        with tempfile.TemporaryDirectory() as folder, patch('pathlib.Path.mkdir', side_effect=PermissionError('blocked')):
            with self.assertRaises(RuntimeError):
                audio_temp_dir(Settings(temp_folder=str(Path(folder) / 'no-access')))

    def test_old_retry_glossary_is_not_applied_to_new_requests(self):
        from subtitle_jobs import create_job, load_job, process_job, save_job
        from subtitles import Cue
        from workflow_checks import response
        with tempfile.TemporaryDirectory() as folder:
            settings = Settings(deepseek_key='offline-key')
            path, doc = create_job(None, Path(folder) / 'old.ass', [Cue(0, 1, 'こんにちは。')], settings)
            doc['options']['glossary'] = 'OLD_UNUSED_PROMPT'
            save_job(path, doc)
            with patch('engine.post_json', return_value=response([{'id': 1, 'chinese': '你好。'}])) as request:
                result = process_job(path, settings)
            self.assertFalse(result.remaining)
            self.assertNotIn('OLD_UNUSED_PROMPT', json.dumps(request.call_args.kwargs['body']))
            self.assertEqual(load_job(path)['cues'][0]['translation'], '你好。')

    def test_legacy_paid_fingerprint_can_reuse_result_without_a_new_submission(self):
        from diagnostics import create_fixture
        from soniox_asr import _signature, transcribe
        from soniox_checks import fake_soniox
        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder) / 'a.mp4', Path(folder) / 'a.ass'
            create_fixture(source)
            with fake_soniox() as (base, calls):
                settings = Settings(soniox_url=base, soniox_key='offline-soniox-key', translate=False)
                args = (source, target, settings, threading.Event(), lambda _: None, lambda _: None)
                transcribe(*args)
                path = target.with_suffix('.soniox.json')
                state = json.loads(path.read_text('utf-8-sig'))
                state['signature'] = _signature(source, settings, base, legacy_context='旧词条')
                path.write_text(json.dumps(state), encoding='utf-8')
                settings._legacy_context_for_cache = '旧词条'
                before = len(calls)
                self.assertEqual(len(transcribe(*args)), 2)
                self.assertEqual(len(calls), before)
                body = next(body for method, route, body in calls if method == 'POST' and route == '/v1/transcriptions')
                self.assertNotIn('旧词条', json.dumps(body, ensure_ascii=False))
                self.assertNotIn('prompt', asdict(settings))

    def test_removed_local_cli_flag_cannot_launch_a_model(self):
        import app
        with patch('sys.argv', ['app.py', '--local']), patch('sys.stderr', new=io.StringIO()), patch('app.App') as window:
            with self.assertRaises(SystemExit):
                app.main()
            window.assert_not_called()

    def test_api_endpoints_keep_https_validation(self):
        self.assertEqual(endpoint('https://example.test/v1', 'chat/completions'), 'https://example.test/v1/chat/completions')
        self.assertEqual(endpoint('https://example.test/chat/completions/', 'chat/completions'), 'https://example.test/chat/completions')
        with self.assertRaises(ValueError):
            endpoint('http://example.test', 'chat/completions')
