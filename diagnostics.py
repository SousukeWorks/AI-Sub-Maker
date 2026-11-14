"""Offline integration smoke test, also executable inside the packaged application."""
from __future__ import annotations

import json
import tempfile
import threading
import traceback
from fractions import Fraction
from pathlib import Path


def create_fixture(path: Path):
    import av
    import numpy as np

    with av.open(str(path), "w") as output:
        video = output.add_stream("mpeg4", rate=10)
        video.width, video.height = 320, 180
        video.pix_fmt = "yuv420p"
        audio = output.add_stream("aac", rate=16000)
        audio.layout = "mono"
        for index in range(40):
            frame = av.VideoFrame.from_ndarray(np.full((180, 320, 3), (20, 35, 60), dtype=np.uint8), format="rgb24")
            frame.pts = index
            for packet in video.encode(frame):
                output.mux(packet)
        for packet in video.encode(None):
            output.mux(packet)
        # A two-second tone starts one second after the video's start.
        for index in range(0, 32000, 1024):
            count = min(1024, 32000 - index)
            samples = (np.sin(np.arange(index, index + count) * 2 * np.pi * 440 / 16000) * 0.2).astype(np.float32).reshape(1, -1)
            frame = av.AudioFrame.from_ndarray(samples, format="flt", layout="mono")
            frame.sample_rate = 16000
            frame.pts = 16000 + index
            frame.time_base = Fraction(1, 16000)
            for packet in audio.encode(frame):
                output.mux(packet)
        for packet in audio.encode(None):
            output.mux(packet)


def smoke_test(report: Path):
    results = {}
    try:
        import av
        import numpy as np
        import requests
        import ssl
        import tkinter as tk

        from config import protect
        from media import extract_flac
        from subtitles import Cue, atomic_write, make_ass

        tls_context = ssl.create_default_context()
        assert tls_context.check_hostname and tls_context.verify_mode == ssl.CERT_REQUIRED
        results["https_tls_context"] = "ok"

        root = tk.Tk()
        root.withdraw()
        root.update()
        root.destroy()
        results["gui"] = "ok"
        from gui_checks import check_layouts, check_image_tool_layouts
        results["gui_layout"] = check_layouts()
        results["image_tool_layout"] = check_image_tool_layouts()
        from lyric_checks import check_lyric_layouts
        results['lyric_editor_layout'] = check_lyric_layouts()
        from member_checks import check_member_layouts
        results['member_editor_layout'] = check_member_layouts()
        from member_color_checks import check_caption_color_layouts
        results['caption_color_layout'] = check_caption_color_layouts()
        assert protect(protect("test-日本語"), decrypt=True) == "test-日本語"
        results["dpapi"] = "ok"
        with tempfile.TemporaryDirectory(prefix="jp-ass-test-") as directory:
            folder = Path(directory)
            source, target = folder / "日本語テスト.mp4", folder / "audio.flac"
            create_fixture(source)
            duration = extract_flac(source, target, 0, threading.Event(), lambda _: None)
            with av.open(str(target)) as container:
                samples = np.concatenate([frame.to_ndarray().flatten() for frame in container.decode(audio=0)])
            assert 2.9 < duration < 3.2, duration
            assert np.max(np.abs(samples[:14000])) == 0, "Audio start offset was lost"
            assert np.max(np.abs(samples[20000:30000])) > 1000
            results['mp4_to_lossless_flac_offset'] = {'duration': duration, 'offset_preserved': True}
            output = folder / "テスト.ass"
            atomic_write(output, make_ass([Cue(1.23, 3.45, "こんにちは、日本語の字幕です。" * 4, "这是中文字幕，需要完整保持在同一行显示。" * 4)]))
            assert "0:00:01.23" in output.read_text(encoding="utf-8-sig")
            events = [line.split(',', 9) for line in output.read_text('utf-8-sig').splitlines() if line.startswith('Dialogue:')]
            assert len(events) == 2 and all(r'\N' not in row[-1] for row in events)
            assert events[0][1:3] == events[1][1:3]
            results['bilingual_no_forced_line_breaks'] = True
            results["ass_utf8"] = "ok"
            from workflow_checks import run_workflow_check
            results["combined_request_and_selective_retry"] = run_workflow_check(folder)
            from soniox_checks import run_soniox_check, run_soniox_gui_check, run_soniox_context_check
            results["soniox_native_timing_and_bilingual_retry"] = run_soniox_check(folder)
            results["soniox_default_gui"] = run_soniox_gui_check(folder)
            results['soniox_recognition_context'] = run_soniox_context_check(folder)
            from image_video_checks import run_mp3_image_check, run_standalone_gui_check
            results["mp3_and_image_mp4"] = run_mp3_image_check(folder)
            results["independent_mp3_picture_gui"] = run_standalone_gui_check(folder)
            from sentence_checks import run_sentence_check
            results["sentence_continuation"] = run_sentence_check(folder)
            from complete_sentence_checks import run_complete_sentence_check
            results['complete_sentences_with_lyrics_and_translation'] = run_complete_sentence_check(folder)
            from punctuation_checks import run_punctuation_check
            results['punctuation_clauses_and_bilingual_timing'] = run_punctuation_check(folder)
            from utterance_checks import run_utterance_check
            results['missing_punctuation_and_bilingual_timing'] = run_utterance_check(folder)
            from phrase_checks import run_phrase_check
            results['readable_phrases_and_complete_lyrics'] = run_phrase_check(folder)
            from split_checks import run_split_check, run_split_gui_check
            results['two_part_soniox_and_whole_translation'] = run_split_check(folder)
            results['two_part_controls'] = run_split_gui_check(folder)
            from member_color_checks import run_member_color_check
            results['member_and_chorus_colors'] = run_member_color_check(folder)
            from lyric_checks import run_lyric_check
            results['lyric_and_response_recovery'] = run_lyric_check(folder)
            from member_checks import run_member_check
            results['member_names_and_nicknames'] = run_member_check(folder)
            from wordbook_checks import run_wordbook_check
            results['embedded_wordbook_and_maximized_start'] = run_wordbook_check(folder)
            from cloud_only_checks import run_cloud_only_check
            results['soniox_only_and_legacy_migration'] = run_cloud_only_check(folder / 'cloud-only')
        from importlib.metadata import version
        import sys
        forbidden = ('faster_whisper', 'ctranslate2', 'onnxruntime', 'tokenizers', 'huggingface_hub', 'nvidia')
        assert not any(value is not None and any(name == prefix or name.startswith(prefix + '.') for prefix in forbidden)
                       for name, value in sys.modules.items())
        results['removed_local_dependencies_not_imported'] = True
        results['versions'] = {'av': av.__version__, 'numpy': np.__version__, 'requests': requests.__version__, 'Janome': version('Janome')}
        results["ok"] = True
    except Exception:
        results["ok"] = False
        results["error"] = traceback.format_exc()
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    if not results["ok"]:
        raise SystemExit(1)
