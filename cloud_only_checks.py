"""Offline migration, lean GUI and removed-prompt checks, including the EXE."""
from dataclasses import asdict, fields
import json
from pathlib import Path
import threading
from unittest.mock import patch


def run_cloud_only_check(folder: Path):
    import config
    from app import App
    from engine import Settings, data_dir
    from deepseek_request import request_processing
    from subtitles import Cue
    from workflow_checks import response

    folder.mkdir(parents=True, exist_ok=True)
    with patch.dict('os.environ', {'LOCALAPPDATA': str(folder)}, clear=True):
        assert data_dir() == folder / 'AI-Sub-Maker'
        (folder / 'JpAssMaker').mkdir()
        assert data_dir() == folder / 'JpAssMaker'
        (folder / 'AI-Sub-Maker').mkdir()
        assert data_dir() == folder / 'AI-Sub-Maker'
    with patch.dict('os.environ', {'AI_SUB_MAKER_DATA_DIR': str(folder / 'custom')}, clear=True):
        assert data_dir() == folder / 'custom'
    legacy = {'version': 1, 'remember_keys': True,
              'settings': {'engine': 'local', 'model': 'large-v3', 'device': 'cuda',
                           'model_cache_folder': str(folder / 'old-models'),
                           'prompt': '旧提示只校验缓存，不参与请求', 'asr_url': 'http://legacy.invalid',
                           'font': 'Yu Gothic', 'font_size': 48, 'line_width': 24},
              'soniox_key': config.protect('offline-soniox-key'),
              'deepseek_key': config.protect('offline-text-key'), 'asr_key': 'obsolete-not-dpapi'}
    profile = folder / 'settings.json'
    profile.write_text(json.dumps(legacy, ensure_ascii=False), encoding='utf-8')
    with patch.object(config, 'data_dir', return_value=folder):
        settings, remember, warning = config.load()
        assert remember and not warning and settings.soniox_key == 'offline-soniox-key'
        assert settings.deepseek_key == 'offline-text-key'
        assert settings.temp_folder == str(folder / 'old-models' / '_temp')
        names = {field.name for field in fields(Settings)}
        assert not names & {'engine', 'model', 'device', 'prompt', 'model_cache_folder', 'asr_key', 'precise_timing', 'line_width'}
        with patch.object(config, 'load', return_value=(settings, remember, warning)):
            window = App(start_maximized=False)
        try:
            window.update()
            assert [window.settings_tabs.tab(page, 'text') for page in window.settings_tabs.tabs()] == [
                'Soniox', '翻译', '样式', '失败重试', '词库']
            assert not hasattr(window, 'mode_picker') and not hasattr(window, 'retime_button')
            assert not hasattr(window, 'video_export_button')
            assert set(window.vars) == names
            selected = folder / 'audio-temp'
            with patch('app.filedialog.askdirectory', return_value=str(selected)):
                window.choose_temp_folder()
            assert selected.is_dir() and not (folder / 'old-models').exists()
            loaded, remembered, _ = config.load()
            assert remembered and loaded.soniox_key == 'offline-soniox-key'
            assert loaded.deepseek_key == 'offline-text-key' and loaded.temp_folder == str(selected)
            document = json.loads(profile.read_text('utf-8-sig'))
            assert set(document['settings']) == names - set(config.SECRET_FIELDS) and 'asr_key' not in document
        finally:
            window.destroy()
    settings._legacy_context_for_cache = legacy['settings']['prompt']
    with patch('engine.post_json', return_value=response([{'id': 1, 'chinese': '你好。'}])) as request:
        request_processing([(1, Cue(0, 1, 'こんにちは。'))], {1: ['translate']}, settings, threading.Event())
    payload = json.loads(request.call_args.kwargs['body']['messages'][1]['content'])
    assert 'glossary' not in payload
    assert legacy['settings']['prompt'] not in json.dumps(payload, ensure_ascii=False)
    return {'single_soniox_backend': True, 'five_tabs': True, 'removed_local_controls_and_prompt': True,
            'legacy_keys_preserved': True, 'obsolete_asr_secret_ignored': True,
            'temp_directory_on_selected_disk': True, 'no_hidden_legacy_prompt_sent': True,
            'renamed_app_uses_legacy_profile_when_available': True}
