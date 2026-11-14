"""Exercise the embedded wordbook, default maximize and real saved request data."""
import json
from pathlib import Path
from unittest.mock import patch


def run_wordbook_check(folder):
    import tkinter as tk
    import config
    from app import App
    from deepseek_request import request_processing
    from engine import Settings
    from member_checks import sample_library
    from member_terms import load_library, save_library
    from subtitles import Cue
    from workflow_checks import response
    import threading

    path = folder / 'wordbook.json'
    save_library(sample_library(), str(path))
    settings = Settings(soniox_key='offline-key', deepseek=True, deepseek_key='offline-key', member_library=str(path))
    with patch.object(config, 'load', return_value=(settings, False, '')), patch.object(config, 'data_dir', return_value=folder):
        window = App()
        try:
            window.update()
            assert window.state() == 'zoomed', window.state()
            assert not window.attributes('-fullscreen')
            before = [child for child in window.winfo_children() if isinstance(child, tk.Toplevel)]
            window.open_members()
            window.update()
            assert window.settings_tabs.select() == str(window.wordbook_tab)
            assert [child for child in window.winfo_children() if isinstance(child, tk.Toplevel)] == before
            panel = window.member_panel
            assert panel.embedded and panel.scroll_area is None
            with patch('member_ui.simpledialog.askstring', return_value='テスト人物'):
                panel.add_member_button.invoke()
            panel.fields['chinese_name'].set('测试人物')
            panel.enabled.set(True)
            assert panel.save()
            added_id = panel.active_member['id']
            assert any(row['id'] == added_id for row in load_library(str(path))['members'])
            panel.fields['chinese_name'].set('测试人物新译名')
            assert panel.save()
            assert next(row for row in load_library(str(path))['members'] if row['id'] == added_id)['chinese_name'] == '测试人物新译名'
            with patch('member_ui.messagebox.askyesno', return_value=True):
                panel.delete_button.invoke()
            assert panel.save()
            assert not any(row['id'] == added_id for row in load_library(str(path))['members'])
            with patch('member_ui.simpledialog.askstring', return_value='ここからだ公演'):
                panel.add_term_button.invoke()
            assert panel.active_kind == 'terms'
            panel.fields['chinese_name'].set('从这里开始公演')
            panel.enabled.set(True)
            assert panel.save()
            term_id = panel.active_member['id']
            panel.fields['chinese_name'].set('从此开始公演')
            # Starting processing saves the current in-place edit first.
            snapshot = window.snapshot()
            assert load_library(str(path))['terms'][0]['chinese_term'] == '从此开始公演'
            with patch('engine.post_json', return_value=response([])) as request:
                result = request_processing([(1, Cue(0, 1, 'ここからだ公演です。'))], {1: ['translate']}, snapshot, threading.Event())
            assert request.call_count == 1
            payload = json.loads(request.call_args.kwargs['body']['messages'][1]['content'])
            assert payload['member_glossary']['terms'][0]['chinese_term'] == '从此开始公演'
            assert result.metadata['term_count'] == 1
            with patch('member_ui.simpledialog.askstring', return_value='AKB48'):
                panel.add_term_button.invoke()
            panel.translation.set('保留日文原文')
            panel.enabled.set(True)
            assert panel.save()
            assert len(load_library(str(path))['terms']) == 2
            with patch('member_ui.messagebox.askyesno', return_value=True):
                panel.delete_button.invoke()
            assert panel.save()
            assert [row['id'] for row in load_library(str(path))['terms']] == [term_id]
            window.state('normal')
            window.geometry('850x650')
            window.update()
            window.settings_tabs.select(0)
            window.update()
            window.open_members()
            window.update()
            assert window.state() == 'normal'
            return {'startup_maximized': True, 'can_restore_window': True,
                    'wordbook_in_main_tab': True, 'no_editor_popup': True,
                    'person_add_edit_delete': True, 'term_add_edit_delete': True,
                    'save_pending_edits_before_processing': True, 'saved_terms_in_single_request': True}
        finally:
            window.destroy()
