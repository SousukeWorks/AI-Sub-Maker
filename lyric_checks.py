"""Offline lyric editor and native-token integration, including frozen builds."""
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch


def run_lyric_check(folder):
    import tkinter as tk
    from lyrics_ui import LyricsEditor
    from engine import Settings
    from lyrics import save_library, load_library
    from soniox_asr import import_result
    from subtitle_jobs import process_job, load_job
    from workflow_checks import local_api, response

    lines = ['青色の宇宙船で月まで出かけよう', '猫の隊長が明日の地図を広げた']
    path = folder / 'test-lyrics.json'
    save_library({'schema': 1, 'stages': [{'id': 'fixture', 'name': '自作公演', 'songs': [
        {'id': 'fixture-song', 'title': '自作曲', 'japanese': '\n'.join(lines), 'chinese': ''}]}]}, str(path))
    root = tk.Tk()
    root.withdraw()
    selected = []
    try:
        editor = LyricsEditor(root, str(path), 'fixture', lambda *args: selected.append(args))
        root.update()
        assert editor.active_song['title'] == '自作曲'
        editor.texts['japanese'].delete('1.0', 'end')
        editor.texts['japanese'].insert('1.0', '\n'.join(lines))
        assert editor.save()
        assert selected[-1] == (str(path), 'fixture')
        assert load_library(str(path))['stages'][0]['songs'][0]['japanese'] == '\n'.join(lines)
        editor.destroy()
        # Destroying an editor must remove only its global scroll callbacks.
        root.event_generate('<FocusIn>')
        root.update()
    finally:
        root.destroy()
    tokens, clock = [], 1000
    for line in lines:
        for word in line:
            tokens.append({'text': word, 'start_ms': clock, 'end_ms': clock + 100, 'speaker': '1'})
            clock += 150
        clock += 500
    source = folder / 'lyrics.soniox.json'
    source.write_text(json.dumps({'schema': 1, 'status': 'completed', 'duration': (clock + 1000) / 1000,
                                 'transcript': {'tokens': tokens}}), encoding='utf-8')
    settings = Settings(deepseek=True, deepseek_key='offline-key', lyrics_library=str(path), lyric_stage='fixture')
    with patch('soniox_asr.Client.request', side_effect=AssertionError('Paid API called')):
        record, job = import_result(source, folder / 'lyrics-output.ass', settings)
    assert len(job['cues']) == 2 and all(row.get('lyric') for row in job['cues'])
    def responder(body, number):
        assert number == 1
        rows = json.loads(body['messages'][1]['content'])['subtitles']
        assert len(rows) == 2 and all(row.get('lyric_reference') for row in rows)
        first = json.dumps({'id': 1, 'japanese': '不应采用的改写', 'chinese': '。测试译文'})
        partial = '{"subtitles":[' + first + ',{"id":2,"japanese":"切断'
        return 200, {'model': 'fixture', 'choices': [{'finish_reason': 'length', 'message': {'content': partial}}]}
    with local_api(responder) as (url, calls):
        result = process_job(record, replace(settings, deepseek_url=url))
        assert len(calls) == 1 and result.remaining == 1
    saved = load_job(record)
    assert saved['cues'][0]['text'] == lines[0]
    assert saved['cues'][0]['translation'] == '测试译文'
    assert saved['responses'] and (folder / saved['responses'][-1]).is_file()
    return {'matched_lines': 2, 'native_boundaries': True, 'editor_save_reload': True,
            'single_request': True, 'valid_prefix_preserved': True, 'missing_cues_remaining': 1, 'paid_asr_calls': 0}


def check_lyric_layouts():
    import tempfile
    import tkinter as tk
    from lyrics_ui import LyricsEditor
    from lyrics import save_library
    from stage_catalog import initial_library
    results = []
    for percent in (100, 125, 150, 200, 250):
        root = tk.Tk()
        root.withdraw()
        root.tk.call('tk', 'scaling', 96 / 72 * percent / 100)
        errors = []
        root.report_callback_exception = lambda *args: errors.append(args)
        try:
            with tempfile.TemporaryDirectory() as directory:
                path = str(Path(directory) / 'library.json')
                save_library(initial_library(), path)
                editor = LyricsEditor(root, path)
                for width, height in ((920, 720), (850, 650), (760, 540)):
                    editor.geometry(f'{width}x{height}')
                    root.update()
                    button = editor.save_button
                    y = button.winfo_rooty() - editor.winfo_rooty()
                    assert y >= 0 and y + button.winfo_height() <= height
                    canvas = editor.scroll_area.canvas
                    widgets = [editor.stage_picker, editor.path_label]
                    def visit(parent):
                        for child in parent.winfo_children():
                            if child.winfo_class() == 'TButton' and child.winfo_ismapped():
                                widgets.append(child)
                            visit(child)
                    visit(editor.scroll_area.body)
                    for widget in widgets:
                        editor.scroll_area.ensure_visible(widget)
                        root.update()
                        x = widget.winfo_rootx() - canvas.winfo_rootx()
                        y = widget.winfo_rooty() - canvas.winfo_rooty()
                        assert 0 <= x and x + widget.winfo_width() <= canvas.winfo_width(), (percent, width, str(widget), x, widget.winfo_width(), canvas.winfo_width())
                        assert -2 <= y and y + widget.winfo_height() <= canvas.winfo_height() + 2
                    results.append({'scale_percent': percent, 'window': [width, height], 'save_and_controls_visible': True})
                editor.destroy()
                root.update()
            assert not errors, errors
        finally:
            root.destroy()
    return results
