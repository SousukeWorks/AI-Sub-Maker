"""Offline member editor and combined-request / selective-retry checks."""
from dataclasses import replace
import json
from pathlib import Path
from unittest.mock import patch


def sample_library():
    from member_catalog import initial_members
    document = initial_members()
    document['members'] = [member for member in document['members'] if member['japanese_name'] in {'坂川陽香', '水島美結', '倉野尾成美'}]
    next(member for member in document['members'] if member['japanese_name'] == '水島美結')['nicknames_chinese'] = ['水水']
    return document


def run_member_check(folder):
    from engine import Settings
    from member_terms import save_library
    from subtitle_jobs import create_job, load_job, process_job
    from subtitles import Cue
    from workflow_checks import local_api, response
    path = folder / 'fixture-members.json'
    save_library(sample_library(), str(path))
    settings = Settings(deepseek=True, deepseek_key='offline-text-key', member_library=str(path))
    cues = [Cue(0, 1, '坂川陽香さんとひゆかについて。'), Cue(2, 3, 'みずみん、おめでとう。'), Cue(4, 5, '明日は楽しくなると思う。')]
    record, _ = create_job(None, folder / 'member-test.ass', cues, settings)
    def responder(body, number):
        data = json.loads(body['messages'][1]['content'])
        glossary = data['member_glossary']
        assert glossary['rules']['person_names'] == 'use_chinese_name'
        assert glossary['rules']['nicknames'] == 'preserve_japanese_original'
        assert len(glossary['members']) == 3
        assert 'source_url' not in glossary['members'][0] and 'notes' not in glossary['members'][0]
        if number == 1:
            assert len(data['subtitles']) == 3
            return 200, response([{'id': 1, 'japanese': cues[0].text, 'chinese': '坂川陽香和ひゆか。'},
                                  {'id': 2, 'japanese': cues[1].text, 'chinese': '水水，生日快乐。'},
                                  {'id': 3, 'japanese': cues[2].text, 'chinese': '我觉得明天会更开心。'}])
        assert number == 2
        assert [row['id'] for row in data['subtitles']] == [2]
        assert data['subtitles'][0]['tasks'] == ['translate']
        return 200, response([{'id': 2, 'chinese': 'みずみん，生日快乐。'}])
    with local_api(responder) as (url, calls):
        settings = replace(settings, deepseek_url=url)
        first = process_job(record, settings)
        assert len(calls) == 1 and first.remaining == 1
        document = load_job(record)
        assert document['cues'][0]['translation'] == '坂川阳香和ひゆか。'
        assert document['cues'][1]['proofread']['status'] == 'success'
        assert '昵称' in document['cues'][1]['translate']['error']
        success = json.loads(json.dumps(document['cues'][0]))
        result = process_job(record, settings, cue_ids=[2])
        assert len(calls) == 2 and result.remaining == 0
        assert load_job(record)['cues'][0] == success
    return {'seed_members': 49, 'name_translated': True, 'nickname_original_kept': True,
            'lost_nickname_failed_cue_only': True, 'request_sizes': [3, 1],
            'retry_uses_same_member_library': True, 'extra_automatic_calls': 0}


def check_member_layouts():
    import tempfile
    import tkinter as tk
    from member_catalog import initial_members
    from member_terms import save_library, load_library
    from member_ui import MemberEditor
    results = []
    for percent in (100, 125, 150, 200, 250):
        root = tk.Tk()
        root.withdraw()
        root.tk.call('tk', 'scaling', 96 / 72 * percent / 100)
        errors = []
        root.report_callback_exception = lambda *args: errors.append(args)
        try:
            with tempfile.TemporaryDirectory() as directory:
                path = str(Path(directory) / 'members.json')
                save_library(initial_members(), path)
                editor = MemberEditor(root, path)
                for width, height in ((920, 760), (850, 650), (760, 540)):
                    editor.geometry(f'{width}x{height}')
                    root.update()
                    button = editor.save_button
                    y = button.winfo_rooty() - editor.winfo_rooty()
                    assert 0 <= y and y + button.winfo_height() <= height
                    for tab in editor.tabs.tabs():
                        editor.tabs.select(tab)
                        root.update()
                        widgets = [editor.path_label]
                        def visit(parent):
                            for child in parent.winfo_children():
                                if child.winfo_ismapped() and child.winfo_class() in {'TEntry', 'TButton', 'TCheckbutton'}:
                                    widgets.append(child)
                                visit(child)
                        visit(editor.scroll_area.body)
                        canvas = editor.scroll_area.canvas
                        for widget in widgets:
                            editor.scroll_area.ensure_visible(widget)
                            root.update()
                            x = widget.winfo_rootx() - canvas.winfo_rootx()
                            y = widget.winfo_rooty() - canvas.winfo_rooty()
                            assert 0 <= x and x + widget.winfo_width() <= canvas.winfo_width(), (percent, width, str(widget), x, widget.winfo_width(), canvas.winfo_width())
                            assert -2 <= y and y + widget.winfo_height() <= canvas.winfo_height() + 2
                    results.append({'scale_percent': percent, 'window': [width, height], 'save_and_fields_visible': True})
                editor.search.set('ひゆか')
                editor.fill_list()
                root.update()
                assert len(editor.tree.get_children()) == 1
                assert editor.active_member['japanese_name'] == '坂川陽香'
                editor.fields['chinese_name'].set('坂川阳香测试')
                assert editor.save()
                saved = load_library(path)
                assert next(row for row in saved['members'] if row['japanese_name'] == '坂川陽香')['chinese_name'] == '坂川阳香测试'
                editor.destroy()
                root.update()
            assert not errors, errors
        finally:
            root.destroy()
    return results
