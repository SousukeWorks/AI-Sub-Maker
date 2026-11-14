"""Exercise real Tk geometry at Windows DPI scales, in source and frozen builds."""
import json
import tempfile
import tkinter as tk
from pathlib import Path
from unittest.mock import patch


def check_layouts():
    import config
    from app import App
    from engine import Settings
    from subtitles import Cue
    from subtitle_jobs import create_job, load_job, save_job

    results = []
    for percent in (100, 125, 150, 200, 250):
        with patch.object(config, "load", return_value=(Settings(), False, "")):
            window = App(scaling=96 / 72 * percent / 100, start_maximized=False)
        callback_errors = []
        window.report_callback_exception = lambda *error: callback_errors.append(error)
        try:
            for width, height in ((1040, 820), (850, 650), (760, 540)):
                window.geometry(f"{width}x{height}")
                window.update()
                positions = []
                assert len(window.settings_tabs.tabs()) == 5
                assert not {'model', 'device', 'model_folder', 'model_cache_folder', 'asr_key', 'prompt', 'precise_timing', 'line_width'} & set(window.vars)
                window.vars['soniox_key'].set('offline-soniox-key')
                window.vars['deepseek_key'].set('offline-text-key')
                assert window.snapshot().soniox_key == 'offline-soniox-key'
                for button in (window.start_button, window.cancel_button, window.output_button):
                    x = button.winfo_rootx() - window.winfo_rootx()
                    y = button.winfo_rooty() - window.winfo_rooty()
                    w, h = button.winfo_width(), button.winfo_height()
                    assert button.winfo_ismapped() and w > 20 and h > 20
                    assert 0 <= x and 0 <= y and x + w <= width and y + h <= height, (percent, width, height, button.cget("text"), x, y, w, h)
                    positions.append((x, y, w, h))
                canvas = window.scroll_area.canvas
                window.scroll_area.ensure_visible(window.image_tool_button)
                window.update()
                tool = window.image_tool_button
                x = tool.winfo_rootx() - canvas.winfo_rootx()
                y = tool.winfo_rooty() - canvas.winfo_rooty()
                assert 0 <= x and x + tool.winfo_width() <= canvas.winfo_width()
                assert -2 <= y and y + tool.winfo_height() <= canvas.winfo_height() + 2
                canvas.yview_moveto(1)
                window.update()
                assert window.start_button.winfo_rooty() - window.winfo_rooty() == positions[0][1]
                log_bottom = window.logs.winfo_rooty() + window.logs.winfo_height()
                assert log_bottom <= canvas.winfo_rooty() + canvas.winfo_height() + 2
                # The scroll bar must allow reaching all fields, including when tabbing.
                entries = [window.temp_entry]
                window.scroll_area.ensure_visible(entries[0])
                window.update()
                top = entries[0].winfo_rooty() - canvas.winfo_rooty()
                assert top >= -2 and top + entries[0].winfo_height() <= canvas.winfo_height() + 2, (percent, width, height, top)
                with patch("app.messagebox.showerror") as alert:
                    window.start_button.invoke()
                    assert alert.call_count == 1
                tabs = window.settings_tabs
                visible_tabs = set()
                for x in range(2, tabs.winfo_width(), 4):
                    try:
                        visible_tabs.add(tabs.index(f"@{x},20"))
                    except tk.TclError:
                        pass
                assert set(range(5)) <= visible_tabs, (percent, width, height, "tab clipping", visible_tabs)
                for index in range(len(tabs.tabs())):
                    window.settings_tabs.select(index)
                    window.update()
                    assert window.start_button.winfo_rooty() - window.winfo_rooty() == positions[0][1]
                window.open_members()
                window.update()
                assert tabs.select() == str(window.wordbook_tab)
                panel = window.member_panel
                assert panel.embedded and panel.scroll_area is None
                for inner in panel.tabs.tabs():
                    panel.tabs.select(inner)
                    window.update()
                    for widget in (panel.search_entry, panel.add_member_button, panel.add_term_button,
                                   panel.delete_button, panel.quick_save_button, panel.save_button):
                        window.scroll_area.ensure_visible(widget)
                        window.update()
                        x = widget.winfo_rootx() - canvas.winfo_rootx()
                        y = widget.winfo_rooty() - canvas.winfo_rooty()
                        assert 0 <= x and x + widget.winfo_width() <= canvas.winfo_width(), (percent, width, 'wordbook clipping', x, widget.winfo_width(), canvas.winfo_width())
                        assert -2 <= y and y + widget.winfo_height() <= canvas.winfo_height() + 2
                window.settings_tabs.select(0)
                window.update()
                window.scroll_area.ensure_visible(window.soniox_test_button)
                window.update()
                button = window.soniox_test_button
                y = button.winfo_rooty() - canvas.winfo_rooty()
                assert -2 <= y and y + button.winfo_height() <= canvas.winfo_height() + 2
                window.scroll_area.ensure_visible(window.soniox_context_button)
                window.update()
                button = window.soniox_context_button
                x = button.winfo_rootx() - canvas.winfo_rootx()
                y = button.winfo_rooty() - canvas.winfo_rooty()
                for control in (window.split_panel.enabled,window.split_panel.time_entry,window.split_panel.song_picker):
                    window.scroll_area.ensure_visible(control)
                    window.update()
                    cx=control.winfo_rootx()-canvas.winfo_rootx()
                    cy=control.winfo_rooty()-canvas.winfo_rooty()
                    assert 0<=cx and cx+control.winfo_width()<=canvas.winfo_width(),('split clipping',percent,width)
                    assert -2<=cy and cy+control.winfo_height()<=canvas.winfo_height()+2,('split clipping',percent,height)
                assert button.winfo_ismapped() and 0 <= x and x + button.winfo_width() <= canvas.winfo_width()
                assert -2 <= y and y + button.winfo_height() <= canvas.winfo_height() + 2
                panel = window.retry_panel
                with tempfile.TemporaryDirectory() as directory:
                    path, document = create_job(None, Path(directory) / "retry.ass", [Cue(0, 1, "日语一"), Cue(2, 3, "日语二")], Settings(deepseek=True))
                    for row in document["cues"]:
                        for stage in ("proofread", "translate"):
                            row[stage].update(status="failed", attempts=1, error="test failure", history=[{"at": "2026-10-20T01:00:00+00:00", "result": "failed", "error": "test failure"}])
                    save_job(path, document)
                    panel.load(path)
                    window.update()
                    for button in (panel.open_button, panel.import_button, panel.selected_button, panel.all_button):
                        window.scroll_area.ensure_visible(button)
                        window.update()
                        x = button.winfo_rootx() - canvas.winfo_rootx()
                        y = button.winfo_rooty() - canvas.winfo_rooty()
                        assert 0 <= x and x + button.winfo_width() <= canvas.winfo_width(), (percent, width, "retry button clipping", button.cget("text"), x, button.winfo_width(), canvas.winfo_width())
                        assert y >= -2 and y + button.winfo_height() <= canvas.winfo_height() + 2
                    window.scroll_area.ensure_visible(panel.details)
                    window.update()
                    assert panel.details.winfo_rooty() + panel.details.winfo_height() <= canvas.winfo_rooty() + canvas.winfo_height() + 2
                    assert panel.details.winfo_rooty() + panel.details.winfo_height() <= panel.winfo_rooty() + panel.winfo_height() + 2
                    calls = []
                    panel.on_retry = lambda path, ids, mode: calls.append((ids, mode))
                    panel.tree.selection_set("1")
                    panel.selected_button.invoke()
                    panel.tree.selection_set("1", "2")
                    panel.selected_button.invoke()
                    panel.all_button.invoke()
                    window.update()
                    assert calls == [([1], "auto"), ([1, 2], "auto"), ([1, 2], "auto")]
                    assert "test failure" in panel.details.get("1.0", "end")
                    window.set_busy(True)
                    assert str(panel.selected_button.cget("state")) == "disabled"
                    window.set_busy(False)
                window.settings_tabs.select(0)
                window.update()
                window.scroll_area.ensure_visible(window.natural_button)
                window.update()
                button = window.natural_button
                x = button.winfo_rootx() - canvas.winfo_rootx()
                y = button.winfo_rooty() - canvas.winfo_rooty()
                assert 0 <= x and x + button.winfo_width() <= canvas.winfo_width()
                assert -2 <= y and y + button.winfo_height() <= canvas.winfo_height() + 2
                results.append({"scale_percent": percent, "window": [width, height], "button_bounds": positions, "buttons_visible": True, "scroll_and_tabs": True, "retry_single_multiple_all": True, "five_tabs_visible": True, 'embedded_wordbook_controls_visible': True})
            assert not callback_errors, callback_errors
        finally:
            window.destroy()
    return results


def check_image_tool_layouts():
    from image_video_app import ImageVideoApp
    results = []
    for percent in (100, 125, 150, 200, 250):
        window = ImageVideoApp(scaling=96 / 72 * percent / 100)
        errors = []
        window.report_callback_exception = lambda *error: errors.append(error)
        try:
            for width, height in ((900, 560), (760, 540), (720, 420)):
                window.geometry(f"{width}x{height}")
                window.update()
                positions = []
                for button in (window.start_button, window.cancel_button, window.output_button):
                    x, y = button.winfo_rootx() - window.winfo_rootx(), button.winfo_rooty() - window.winfo_rooty()
                    assert button.winfo_ismapped()
                    assert 0 <= x and x + button.winfo_width() <= width
                    assert 0 <= y and y + button.winfo_height() <= height
                    positions.append((x, y))
                canvas = window.scroll_area.canvas
                for widget in window.inputs:
                    window.scroll_area.ensure_visible(widget)
                    window.update()
                    y = widget.winfo_rooty() - canvas.winfo_rooty()
                    x = widget.winfo_rootx() - canvas.winfo_rootx()
                    assert -2 <= y and y + widget.winfo_height() <= canvas.winfo_height() + 2, (percent, width, height, widget.winfo_class(), y, widget.winfo_height(), canvas.winfo_height())
                    assert 0 <= x and x + widget.winfo_width() <= canvas.winfo_width(), (percent, width, height, x, widget.winfo_width(), canvas.winfo_width())
                assert [(button.winfo_rootx() - window.winfo_rootx(), button.winfo_rooty() - window.winfo_rooty())
                        for button in (window.start_button, window.cancel_button, window.output_button)] == positions
                results.append({"scale_percent": percent, "window": [width, height], "buttons_visible": True})
            assert not errors, errors
        finally:
            window.destroy()
    return results


if __name__ == "__main__":
    from app import VERSION
    results = check_layouts()
    report = Path(f"test-output/gui-layout-v{VERSION}.json")
    report.parent.mkdir(parents=True, exist_ok=True)
    converter_results = check_image_tool_layouts()
    from lyric_checks import check_lyric_layouts
    lyric_results = check_lyric_layouts()
    from member_checks import check_member_layouts
    member_results = check_member_layouts()
    from member_color_checks import check_caption_color_layouts
    color_results = check_caption_color_layouts()
    report.write_text(json.dumps({"ok": True, "cases": results, "converter_cases": converter_results, 'lyric_cases': lyric_results, 'member_cases': member_results, 'color_cases': color_results}, indent=2), encoding="utf-8")
    print(f"Passed {len(results)} main + {len(converter_results)} converter + {len(lyric_results)} lyric + {len(member_results)} member + {len(color_results)} color DPI/window cases")
