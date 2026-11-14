"""Offline end-to-end Soniox protocol checks, including in the portable EXE."""
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

TOKENS = [
    {"text": "こんにちは", "start_ms": 1100, "end_ms": 1400, "speaker": "1"},
    {"text": "はい。", "start_ms": 1350, "end_ms": 1750, "speaker": "2"},
]


@contextmanager
def fake_soniox(options=None):
    if threading.current_thread() is threading.main_thread():
        import gc
        gc.collect()
    options = options if options is not None else {}
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def handle_request(self):
            length = int(self.headers.get("Content-Length", "0"))
            data = self.rfile.read(length)
            body = json.loads(data) if data and "application/json" in self.headers.get("Content-Type", "") else data
            calls.append((self.command, self.path, body))
            if self.headers.get("Authorization") != "Bearer offline-soniox-key" and self.path != "/v1/chat/completions":
                self.reply(401, {})
                return
            if self.command == "GET" and self.path == "/v1/models":
                self.reply(200, {"models": [{"id": "stt-async-v5"}]})
            elif self.command == "POST" and self.path == "/v1/files":
                assert b'filename="audio.flac"' in body and b"fLaC" in body
                self.reply(201, {"id": "file-test"})
            elif self.command == "POST" and self.path == "/v1/transcriptions":
                if options.get("on_create"):
                    options["on_create"]()
                self.reply(options.get("create_status", 201), {"id": "job-test"})
            elif self.command == "GET" and self.path == "/v1/transcriptions/job-test":
                self.reply(options.get("poll_http", 200), {"status": options.get("status", "completed"), "error_message": "offline failure"})
            elif self.command == "GET" and self.path.endswith("/transcript"):
                self.reply(200, {"tokens": TOKENS})
            elif self.command == "DELETE" and self.path in {"/v1/transcriptions/job-test", "/v1/files/file-test"}:
                self.reply(options.get("delete_status", 204), {})
            elif self.command == "POST" and self.path == "/v1/chat/completions":
                inputs = json.loads(body["messages"][1]["content"])["subtitles"]
                count = sum(route == "/v1/chat/completions" for _, route, _ in calls)
                rows = [{"id": row["id"], "japanese": row["text"], "chinese": "中文" + str(row["id"])} for row in inputs]
                if count == 1:
                    rows[-1].pop("chinese")
                from workflow_checks import response
                self.reply(200, response(rows))
            else:
                self.reply(404, {})

        def reply(self, status, body):
            data = json.dumps(body).encode("utf-8") if status != 204 else b""
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = do_DELETE = handle_request

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def run_soniox_check(folder):
    from diagnostics import create_fixture
    from engine import Settings, run
    from soniox_asr import check_connection, transcribe
    from subtitle_jobs import ProcessingIncomplete, process_job

    source, target = folder / "soniox-source.mp4", folder / "soniox-output.ass"
    create_fixture(source)
    with fake_soniox() as (base, calls):
        settings = Settings( soniox_url=base, soniox_key="offline-soniox-key",
                            deepseek=True, translate=True, deepseek_url=base, deepseek_key="offline-text-key")
        assert "连接成功" in check_connection(settings)
        with patch.dict("sys.modules", {"faster_whisper": None, "onnxruntime": None, "ctranslate2": None}):
            try:
                run(source, target, settings)
                raise AssertionError("Partial translation should be recoverable")
            except ProcessingIncomplete as exc:
                record = exc.result.record
                assert exc.result.remaining == 1
            result = process_job(record, settings, [2])
            assert result.remaining == 0
            before = len(calls)
            cues = transcribe(source, target, settings, threading.Event(), lambda _: None, lambda _: None)
            assert len(calls) == before, "Cached result submitted another request"
        assert [(c.start, c.end, c.speaker) for c in cues] == [(1.1, 1.4, "1"), (1.35, 1.75, "2")]
        data = target.read_text(encoding="utf-8-sig")
        rows = [line.split(",", 9) for line in data.splitlines() if line.startswith("Dialogue:")]
        assert len(rows) == 4
        for japanese, chinese in zip(rows[::2], rows[1::2]):
            assert japanese[3] == "japanese" and chinese[3] == "chinese"
            assert japanese[1:3] == chinese[1:3] and japanese[4] == chinese[4]
        requests = [body for method, route, body in calls if method == "POST" and route == "/v1/transcriptions"]
        assert len(requests) == 1 and requests[0]["language_hints"] == ["ja"]
        assert requests[0]["enable_speaker_diarization"] and "translation" not in requests[0]
        sizes = [len(json.loads(body["messages"][1]["content"])["subtitles"]) for method, route, body in calls if route == "/v1/chat/completions"]
        assert sizes == [2, 1]
        deletes = [route for method, route, _ in calls if method == "DELETE"]
        assert deletes == ["/v1/transcriptions/job-test", "/v1/files/file-test"]
        for path in (record, target.with_suffix(".soniox.json")):
            text = path.read_text(encoding="utf-8-sig")
            assert "offline-soniox-key" not in text and "offline-text-key" not in text
        return {"native_timestamps": True, "speaker_changes_and_overlap": True, "paid_submissions": 1,
                "deepseek_request_sizes": sizes, "cloud_cleanup": True, "checkpoint_without_keys": True}


def run_soniox_gui_check(folder):
    """Press the real GUI button after loading an old local-Whisper profile."""
    import time
    import config
    from app import App
    from diagnostics import create_fixture
    from engine import Settings

    source, target = folder / "gui-soniox-source.mp4", folder / "gui-soniox.ass"
    create_fixture(source)
    with fake_soniox() as (base, calls):
        legacy = Settings(translate=False, soniox_url=base, soniox_key="offline-soniox-key")
        with patch.object(config, "load", return_value=(legacy, False, "")), patch.object(config, "save"), \
                patch("app.messagebox.showinfo") as done, patch("app.messagebox.showerror") as error:
            window = App()
            try:
                window.update()
                window.withdraw()
                assert not hasattr(window, 'mode_picker')
                assert 'prompt' not in window.vars and 'model' not in window.vars
                assert "Soniox" in window.start_button.cget("text")
                assert "Soniox" in window.title()
                window.source.set(str(source))
                window.target.set(str(target))
                window.start_button.invoke()
                deadline = time.monotonic() + 15
                while window.busy and time.monotonic() < deadline:
                    window.update()
                    time.sleep(.01)
                window.update()
                assert not window.busy and done.call_count == 1 and not error.called
                assert target.is_file()
                log = window.logs.get("1.0", "end")
                assert "本次识别：Soniox" in log and "原生时间轴" in log
                assert not target.with_suffix(".timing.json").exists()
                assert target.with_suffix(".soniox.json").exists()
                assert sum(method == "POST" and route == "/v1/transcriptions" for method, route, _ in calls) == 1
                json_source = folder / "zero-duration.soniox.json"
                json_source.write_text(json.dumps({"schema": 1, "status": "completed", "duration": 3,
                    "source": str(source), "transcript": {"tokens": TOKENS + [
                        {"text": "あ", "start_ms": 2000, "end_ms": 2000, "speaker": "1"}]}}, ensure_ascii=False), encoding="utf-8")
                before = len(calls)
                window.vars["deepseek"].set(True)
                window.vars["translate"].set(True)
                with patch("retry_ui.filedialog.askopenfilename", return_value=str(json_source)), \
                        patch("retry_ui.filedialog.asksaveasfilename", return_value=str(folder / "json-import.ass")):
                    window.retry_panel.import_button.invoke()
                assert len(calls) == before and not error.called
                assert len(window.retry_panel.document["cues"]) == 3
                assert window.retry_panel.document["timing_review_cues"] == 1
                return {"ordinary_launch": "soniox", "single_backend": True,
                        "local_asr_calls": 0, "local_alignment_calls": 0, "button_to_soniox_http": True,
                        "zero_duration_json_import_without_asr": True}
            finally:
                window.destroy()


def run_soniox_context_check(folder):
    """Exercise the GUI through the actual HTTP payload with no live API."""
    import time
    import config
    from app import App
    from diagnostics import create_fixture
    from engine import Settings
    from lyrics import save_library
    from member_catalog import initial_members
    from member_terms import save_library as save_members
    from soniox_asr import transcribe

    folder = folder.resolve()
    library, members = folder / 'context-lyrics.json', folder / 'context-members.json'
    lyric_data = {'schema': 1, 'stages': [{'id': 'context-stage', 'name': '識別テスト', 'songs': [
        {'id': 'context-song', 'title': 'Context song', 'japanese': 'Maybe\nこれは歌詞の例です。\n次の歌詞も参考です。', 'chinese': ''}]}]}
    save_library(lyric_data, str(library))
    save_members(initial_members(), str(members))
    source, target = folder / 'context-source.mp4', folder / 'context-output.ass'
    create_fixture(source)
    with fake_soniox() as (base, calls):
        settings = Settings(translate=False, soniox_url=base, soniox_key='offline-soniox-key',
                            lyric_stage='context-stage', lyrics_library=str(library), member_library=str(members))
        with patch.object(config, 'load', return_value=(settings, False, '')), patch.object(config, 'save'), \
                patch('app.messagebox.showinfo'), patch('app.messagebox.showerror') as error:
            window = App(start_maximized=False)
            try:
                window.update()
                window.withdraw()
                assert window.vars['soniox_send_context'].get()
                window.soniox_context_button.invoke()
                assert not window.vars['soniox_send_context'].get()
                window.soniox_context_button.invoke()
                window.source.set(str(source))
                window.target.set(str(target))
                window.start_button.invoke()
                deadline = time.monotonic() + 20
                while window.busy and time.monotonic() < deadline:
                    window.update()
                    time.sleep(.01)
                window.update()
                assert not window.busy and not error.called, error.call_args
            finally:
                window.destroy()
        requests = [body for method, route, body in calls if method == 'POST' and route == '/v1/transcriptions']
        assert len(requests) == 1
        context = requests[0]['context']
        assert 'Maybe' in context['terms'] and '岩立沙穂' in context['terms'] and 'さっほー' in context['terms']
        assert lyric_data['stages'][0]['songs'][0]['japanese'] in context['text']
        state_path = target.with_suffix('.soniox.json')
        state = json.loads(state_path.read_text('utf-8-sig'))
        assert state['recognition_context'] == context
        assert state['recognition_context_summary']['terms_sent'] == len(context['terms'])
        assert 'offline-soniox-key' not in state_path.read_text('utf-8-sig')
        before = len(calls)
        lyric_data['stages'][0]['songs'][0]['japanese'] = 'ChangedWord\n異なる歌詞の例です。'
        save_library(lyric_data, str(library))
        messages = []
        transcribe(source, target, settings, threading.Event(), lambda _: None, messages.append)
        assert len(calls) == before
        assert json.loads(state_path.read_text('utf-8-sig'))['recognition_context'] == context
        assert any('仅对新识别任务生效' in message for message in messages)
        settings.soniox_send_context = False
        transcribe(source, folder / 'context-disabled.ass', settings, threading.Event(), lambda _: None, lambda _: None)
        requests = [body for method, route, body in calls if method == 'POST' and route == '/v1/transcriptions']
        assert len(requests) == 2 and 'context' not in requests[-1]
    return {'context_sent_in_paid_request': True, 'stage_lyrics_and_wordbook': True,
            'gui_toggle_to_http': True, 'saved_snapshot_matches_request': True,
            'complete_ordered_context': True, 'no_automatic_paid_resubmission': True, 'disable_omits_context': True}
