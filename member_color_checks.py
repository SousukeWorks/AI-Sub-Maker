"""Color editor, manual assignments and color-only ASS integration tests."""
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import patch


def run_member_color_check(folder: Path):
    import tkinter as tk
    import config
    from app import App
    from engine import Settings
    from member_catalog import initial_members
    from member_terms import save_library, load_library
    from caption_colors import CaptionColorsEditor
    from subtitle_jobs import create_job, load_job, save_job
    from subtitles import Cue

    library = folder / 'color-members.json'
    save_library(initial_members(), str(library))
    settings = Settings(deepseek=True, translate=True, deepseek_key='offline-color-key',
                        soniox_key='offline-color-key', member_library=str(library))
    cues = [Cue(1, 2, '岩立沙穂です。', speaker='1'), Cue(2.1, 3, '今日はうれしいです。', speaker='1'),
            Cue(3.2, 5.6, 'みんなで歌おう。', speaker='2')]
    path, doc = create_job(None, folder / 'member-color.ass', cues, settings)
    for i, row in enumerate(doc['cues']):
        row['translation'] = f'测试译文{i + 1}'
        for step in ('proofread', 'translate'):
            row[step].update(status='success', attempts=1)
    doc['state'] = 'completed'
    save_job(path, doc)
    before = deepcopy(doc['cues'])
    with patch.object(config, 'load', return_value=(settings, False, '')), patch.object(config, 'data_dir', return_value=folder), \
            patch('engine.post_json', side_effect=AssertionError('Color-only export requested API')):
        window = App(start_maximized=False)
        try:
            window.update()
            panel = window.member_panel
            panel.tree.selection_set('akb-110')
            panel.select_member()
            panel.tabs.select(panel.tabs.tabs()[-1])
            window.update()
            panel.color_fields.values['font'].set('#123456')
            panel.color_fields.values['outline'].set('#ABCDEF')
            with patch('color_widgets.colorchooser.askcolor', return_value=((1, 2, 3), '#010203')):
                panel.color_fields.buttons['shadow'].invoke()
            panel.color_enabled.set(True)
            assert panel.save()
            assert load_library(str(library))['members'][0]['subtitle_style']['shadow'] == '#010203'
            window.retry_panel.load(path)
            window.caption_colors_button.invoke()
            window.update()
            editor = next(child for child in window.winfo_children() if isinstance(child, CaptionColorsEditor))
            editor.tree.selection_set('3')
            editor.role.set('合唱')
            editor.apply_button.invoke()
            editor.save_button.invoke()
            window.update()
            assert not editor.dirty
            saved = load_job(path)
            assert saved['cues'][2]['chorus'] and saved['cues'][2]['role_source'] == 'manual'
            assert saved['cues'][0]['member_id'] == 'akb-110'
            for a, b in zip(before, saved['cues']):
                for key in ('id', 'start', 'end', 'text', 'translation', 'proofread', 'translate'):
                    assert a[key] == b[key]
            text = (folder / 'member-color.ass').read_text('utf-8-sig')
            styles = {line.split(': ', 1)[1].split(',')[0]: line.split(': ', 1)[1].split(',')
                      for line in text.splitlines() if line.startswith('Style: ')}
            events = [line.split(',', 9) for line in text.splitlines() if line.startswith('Dialogue:')]
            assert len(styles) == 102 and len(events) == 6
            assert styles[events[0][3]][3:7] == ['&H00563412', '&H00563412', '&H00EFCDAB', '&H00030201']
            assert events[4][3] == 'japanese_chorus' and events[5][3] == 'chinese_chorus'
            assert styles['japanese_chorus'][3] == '&H00B469FF' and styles['japanese_chorus'][5] == '&H00FFFFFF'
            for jp, zh in zip(events[::2], events[1::2]):
                assert jp[1:3] == zh[1:3]
            editor.destroy()
        finally:
            window.destroy()
    return {'official_49_styles_editable': True, 'tricolor_font_outline_shadow': True,
            'chorus_pink_white': True, 'manual_role_gui_to_ass': True, 'bilingual_style_pairs': True,
            'color_only_no_api': True, 'text_times_ids_and_history_preserved': True}


def check_caption_color_layouts():
    import tempfile
    import tkinter as tk
    from caption_colors import CaptionColorsEditor
    from engine import Settings
    from member_catalog import initial_members
    from member_terms import save_library
    from subtitle_jobs import create_job
    from subtitles import Cue

    results = []
    for percent in (100, 125, 150, 200, 250):
        root = tk.Tk()
        root.withdraw()
        root.tk.call('tk', 'scaling', 96 / 72 * percent / 100)
        errors = []
        root.report_callback_exception = lambda *args: errors.append(args)
        try:
            with tempfile.TemporaryDirectory() as directory:
                folder = Path(directory)
                library = folder / 'members.json'
                save_library(initial_members(), str(library))
                settings = Settings(member_library=str(library), deepseek=False, translate=False)
                path, _ = create_job(None, folder / 'layout.ass', [Cue(0, 1, 'テスト')], settings)
                editor = CaptionColorsEditor(root, path, lambda: settings)
                try:
                    for width, height in ((1040, 780), (850, 650), (760, 540)):
                        editor.geometry(f'{width}x{height}')
                        root.update()
                        fixed = []
                        for button in (editor.save_button, editor.save_as_button):
                            x = button.winfo_rootx() - editor.winfo_rootx()
                            y = button.winfo_rooty() - editor.winfo_rooty()
                            assert 0 <= x and x + button.winfo_width() <= width
                            assert 0 <= y and y + button.winfo_height() <= height
                            fixed.append((x, y))
                        canvas = editor.scroll_area.canvas
                        widgets = [editor.search_entry, editor.role_picker, editor.apply_button, editor.turn_check,
                                   editor.auto_button, editor.refresh_button, editor.color_preview.canvas]
                        for widget in widgets:
                            editor.scroll_area.ensure_visible(widget)
                            root.update()
                            x = widget.winfo_rootx() - canvas.winfo_rootx()
                            y = widget.winfo_rooty() - canvas.winfo_rooty()
                            assert 0 <= x and x + widget.winfo_width() <= canvas.winfo_width(), (percent, width, str(widget), x, widget.winfo_width(), canvas.winfo_width())
                            assert -2 <= y and y + widget.winfo_height() <= canvas.winfo_height() + 2, (percent, height, y, widget.winfo_height(), canvas.winfo_height())
                        assert fixed == [(b.winfo_rootx() - editor.winfo_rootx(), b.winfo_rooty() - editor.winfo_rooty()) for b in (editor.save_button, editor.save_as_button)]
                        results.append({'scale_percent': percent, 'window': [width, height], 'save_role_and_preview_visible': True})
                    assert not errors, errors
                finally:
                    editor.destroy()
        finally:
            root.destroy()
    return results
