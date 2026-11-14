"""Real local HTTP integration for combined requests and durable selective retries."""
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def response(rows, finish_reason="stop"):
    return {"choices": [{"finish_reason": finish_reason, "message": {"content": json.dumps({"subtitles": rows}, ensure_ascii=False)}}]}


@contextmanager
def local_api(responder):
    # Finalize previous GUI fixtures on their owning thread before allocating
    # objects in an HTTP worker (Tk interpreters cannot be collected there).
    if threading.current_thread() is threading.main_thread():
        import gc
        gc.collect()
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append(body)
            status, result = responder(body, len(received))
            encoded = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", received
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def run_workflow_check(folder: Path):
    from engine import Settings
    from subtitles import Cue
    from subtitle_jobs import create_job, load_job, process_job

    def responder(body, number):
        inputs = json.loads(body["messages"][1]["content"])["subtitles"]
        rows = [{"id": row["id"], "japanese": f"校正{row['id']}。", "chinese": f"中文{row['id']}。"} for row in reversed(inputs)]
        if number == 1:
            rows = [row for row in rows if row["id"] != 3]
            next(row for row in rows if row["id"] == 2).pop("chinese")
            rows.append({"id": 4, "japanese": "重复", "chinese": "重复"})
        return 200, response(rows)

    with local_api(responder) as (base, received):
        settings = Settings(deepseek=True, translate=True, deepseek_url=base, deepseek_key="offline-test-key")
        cues = [Cue(index * 2 + .125, index * 2 + 1.5, f"字幕{index + 1}。") for index in range(101)]
        path, _ = create_job(None, folder / "双语.ass", cues, settings)
        initial = process_job(path, settings)
        assert initial.remaining == 3 and not (folder / "双语.ass").exists()
        saved = load_job(path)
        assert saved["cues"][0]["translate"]["status"] == "success"
        assert saved["cues"][1]["proofread"]["status"] == "success"
        assert saved["cues"][2]["text"] == cues[2].text
        assert saved["cues"][3]["proofread"]["status"] == "failed"
        partial = initial.output.read_text(encoding="utf-8-sig")
        assert "翻译未完成，待重试" in partial and "待校对，尚未翻译" in partial
        one = process_job(path, settings, [2])
        assert one.remaining == 2
        final = process_job(path, settings, [3, 4])
        assert final.remaining == 0 and final.output.name == "双语.ass"
        payloads = [json.loads(body["messages"][1]["content"])["subtitles"] for body in received]
        assert [len(rows) for rows in payloads] == [101, 1, 2], "Unexpected splitting or automatic retry"
        assert payloads[1] == [{"id": 2, "text": "校正2。", "tasks": ["translate"]}]
        assert [row["id"] for row in payloads[2]] == [3, 4]
        assert all(row["tasks"] == ["proofread", "translate"] for row in payloads[0] + payloads[2])
        saved = load_job(path)
        assert saved["state"] == "completed"
        assert saved["cues"][0]["translate"]["attempts"] == 1
        assert saved["cues"][1]["translate"]["attempts"] == 2
        assert len(saved["cues"][3]["proofread"]["history"]) == 2
        assert [(row["start"], row["end"]) for row in saved["cues"]] == [(cue.start, cue.end) for cue in cues]
        text = final.output.read_text(encoding="utf-8-sig")
        events = [line.split(",", 9) for line in text.splitlines() if line.startswith("Dialogue:")]
        assert len(events) == 202
        for japanese, chinese in zip(events[::2], events[1::2]):
            assert japanese[3] == "japanese" and chinese[3] == "chinese"
            assert japanese[1:3] == chinese[1:3]
        assert "offline-test-key" not in path.read_text(encoding="utf-8-sig")
        return {"total_cues": 101, "request_sizes": [101, 1, 2], "remaining": 0,
                "styles": ["japanese", "chinese"], "independent_events": 202,
                "durable_history": True, "original_times_preserved": True}
