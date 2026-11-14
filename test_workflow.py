import json
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from deepseek_request import request_processing
from engine import Settings
from media import Cancelled
from subtitles import Cue, make_ass
from subtitle_jobs import (_lock, create_job, import_japanese_ass, load_job, process_job,
                      record_path, save_job)
from workflow_checks import local_api, response, run_workflow_check


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.settings = Settings(deepseek=True, translate=True, deepseek_key="private-key", soniox_key="other-key")

    def job(self, settings=None, count=3):
        return create_job(None, self.folder / "日本語.ass", [Cue(i * 2, i * 2 + 1, f"字幕{i + 1}") for i in range(count)], settings or self.settings)[0]

    def process(self, path, rows, **kwargs):
        with patch("engine.post_json", return_value=response(rows)) as request:
            result = process_job(path, self.settings, **kwargs)
        return result, request

    def test_http_whole_101_cues_single_and_multiple_retry(self):
        result = run_workflow_check(self.folder)
        self.assertEqual(result["request_sizes"], [101, 1, 2])

    def test_total_failure_redacts_keys_and_preserves_existing_final(self):
        path = self.job()
        final = self.folder / "日本語.ass"
        final.write_text("Existing subtitle", encoding="utf-8")
        with patch("engine.post_json", side_effect=RuntimeError("private-key other-key rejected")) as request:
            result = process_job(path, self.settings)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(result.remaining, 3)
        self.assertEqual(final.read_text(encoding="utf-8"), "Existing subtitle")
        saved = load_job(path)
        content = path.read_text(encoding="utf-8-sig")
        self.assertNotIn("private-key", content)
        self.assertNotIn("other-key", content)
        self.assertIn("[隐藏]", content)
        self.assertTrue(all(row["proofread"]["attempts"] == 1 for row in saved["cues"]))

    def test_http_error_makes_one_request_and_can_resume_without_video(self):
        path = self.job(count=1)
        with local_api(lambda body, number: (401, {"error": {"message": "invalid token"}})) as (url, calls):
            result = process_job(path, replace(self.settings, deepseek_url=url))
            self.assertEqual(len(calls), 1)
            self.assertEqual(result.remaining, 1)
        result, request = self.process(path, [{"id": 1, "japanese": "新日语", "chinese": "新中文"}])
        self.assertEqual(result.remaining, 0)
        self.assertEqual(request.call_count, 1)

    def test_translation_failure_keeps_corrected_japanese_and_retries_only_translation(self):
        path = self.job(count=1)
        result, _ = self.process(path, [{"id": 1, "japanese": "校对后"}])
        self.assertEqual(result.remaining, 1)
        result, request = self.process(path, [{"id": 1, "chinese": "完成"}], cue_ids=[1])
        sent = json.loads(request.call_args.kwargs["body"]["messages"][1]["content"])["subtitles"]
        self.assertEqual(sent, [{"id": 1, "text": "校对后", "tasks": ["translate"]}])
        saved = load_job(path)["cues"][0]
        self.assertEqual((saved["text"], saved["translation"]), ("校对后", "完成"))
        self.assertEqual(saved["proofread"]["attempts"], 1)
        self.assertEqual(saved["translate"]["attempts"], 2)

    def test_proofreading_only_then_translation_dependency(self):
        path = self.job(count=1)
        result, request = self.process(path, [], cue_ids=[1], mode="translate")
        self.assertEqual(request.call_count, 0)
        self.assertEqual(result.attempted, 0)
        result, request = self.process(path, [{"id": 1, "japanese": "校对"}], mode="proofread")
        sent = json.loads(request.call_args.kwargs["body"]["messages"][1]["content"])["subtitles"]
        self.assertEqual(sent[0]["tasks"], ["proofread"])
        result, _ = self.process(path, [{"id": 1, "chinese": "翻译"}], mode="translate")
        self.assertEqual(result.remaining, 0)

    def test_disabled_proofreading_translates_unchanged_japanese(self):
        path = self.job(replace(self.settings, deepseek=False), count=1)
        result, request = self.process(path, [{"id": 1, "japanese": "不应使用", "chinese": "翻译"}])
        self.assertEqual(result.remaining, 0)
        row = load_job(path)["cues"][0]
        self.assertEqual(row["text"], "字幕1")
        self.assertEqual(row["proofread"]["status"], "skipped")

    def test_disabled_translation_exports_only_japanese(self):
        path = self.job(replace(self.settings, translate=False), count=1)
        result, _ = self.process(path, [{"id": 1, "japanese": "日语"}])
        events = [line for line in result.output.read_text(encoding="utf-8-sig").splitlines() if line.startswith("Dialogue:")]
        self.assertEqual(len(events), 1)
        self.assertIn(",japanese,", events[0])

    def test_malformed_or_truncated_response_is_durable_without_auto_retry(self):
        for api_result in (response([], finish_reason="length"), {"choices": [{"finish_reason": "stop", "message": {"content": "{broken"}}]}):
            path = self.job()
            with patch("engine.post_json", return_value=api_result) as request:
                result = process_job(path, self.settings)
            self.assertEqual(request.call_count, 1)
            self.assertEqual(result.remaining, 3)
            self.assertTrue(all(row["translate"]["status"] == "failed" for row in load_job(path)["cues"]))

    def test_cancel_during_request_keeps_pending_history_and_can_resume(self):
        path = self.job(count=1)
        with patch("engine.post_json", side_effect=Cancelled):
            with self.assertRaises(Cancelled):
                process_job(path, self.settings)
        saved = load_job(path)
        self.assertEqual(saved["state"], "cancelled")
        self.assertEqual(saved["cues"][0]["proofread"]["status"], "pending")
        self.assertEqual(saved["cues"][0]["proofread"]["history"][0]["result"], "cancelled")
        result, _ = self.process(path, [{"id": 1, "japanese": "日语", "chinese": "中文"}])
        self.assertEqual(result.remaining, 0)

    def test_completed_cues_are_not_requested_again(self):
        path = self.job(count=1)
        self.process(path, [{"id": 1, "japanese": "日语", "chinese": "中文"}])
        result, request = self.process(path, [])
        self.assertEqual(request.call_count, 0)
        self.assertEqual(result.attempted, 0)

    def test_unknown_selection_and_concurrent_write_rejected(self):
        path = self.job()
        for ids in ([], [999], [True]):
            with self.assertRaises(ValueError):
                process_job(path, self.settings, ids)
        with _lock(path):
            with self.assertRaisesRegex(RuntimeError, "另一个窗口"):
                process_job(path, self.settings)

    def test_output_path_traversal_and_invalid_timestamps_rejected(self):
        for filename in ("../outside.ass", "C:\\outside.ass", "bad.txt"):
            path = self.job()
            saved = load_job(path)
            saved["output_file"] = filename
            save_job(path, saved)
            with self.assertRaises(ValueError):
                load_job(path)
        path = self.job()
        saved = load_job(path)
        saved["cues"][0]["end"] = -1
        save_job(path, saved)
        with self.assertRaises(ValueError):
            load_job(path)

    def test_import_old_combined_ass_and_new_separate_events(self):
        old = self.folder / "old.raw.ass"
        old.write_text("Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,日本語\\N{\\rChinese}旧中文\n", encoding="utf-8-sig")
        path, _ = import_japanese_ass(old, self.folder / "recovered.ass", self.settings)
        self.assertEqual(load_job(path)["cues"][0]["text"], "日本語")
        new = self.folder / "new.ass"
        new.write_text(make_ass([Cue(2, 4, "こんにちは", "你好")]), encoding="utf-8-sig")
        path, _ = import_japanese_ass(new, self.folder / "recovered.ass", self.settings)
        rows = load_job(path)["cues"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["text"], "こんにちは")
        self.assertEqual((rows[0]["start"], rows[0]["end"]), (2, 4))

    def test_repeated_run_does_not_destroy_old_record(self):
        path = self.job()
        second = self.job()
        self.assertEqual(path, record_path(self.folder / "日本語.ass"))
        self.assertNotEqual(path, second)
        self.assertTrue(path.exists())

    def test_official_request_reserves_output_for_whole_selection(self):
        items = [(i, Cue(i, i + 1, "あ" * 40)) for i in range(1, 101)]
        tasks = {i: ["proofread", "translate"] for i, _ in items}
        with patch("engine.post_json", return_value=response([])) as request:
            request_processing(items, tasks, self.settings, threading.Event())
        self.assertEqual(request.call_count, 1)
        body = request.call_args.kwargs["body"]
        self.assertGreater(body["max_tokens"], 8192)
        self.assertLessEqual(body["max_tokens"], 393216)
        self.assertEqual(request.call_args.kwargs["read_timeout"], 900)
        self.assertEqual(len(json.loads(body["messages"][1]["content"])["subtitles"]), 100)

    def test_gui_retry_uses_current_api_without_source_or_model(self):
        from app import App
        import config

        path = self.job(count=2)

        def responder(body, number):
            rows = json.loads(body["messages"][1]["content"])["subtitles"]
            return 200, response([{"id": row["id"], "japanese": "日语", "chinese": "中文"} for row in rows])

        with local_api(responder) as (url, received), patch.object(config, "load", return_value=(self.settings, False, "")), patch("app.messagebox.showinfo") as done, patch("app.messagebox.showerror") as error:
            window = App()
            try:
                window.vars["deepseek_url"].set(url)
                window.source.set("Z:/missing-video.mp4")
                window.retry_panel.load(path)
                window.retry_panel.tree.selection_set("1", "2")
                window.retry_panel.selected_button.invoke()
                limit = time.monotonic() + 8
                while window.busy and time.monotonic() < limit:
                    window.update()
                    time.sleep(.02)
                self.assertFalse(window.busy)
                self.assertEqual(len(received), 1)
                self.assertEqual(done.call_count, 1)
                self.assertEqual(error.call_count, 0)
                self.assertEqual(window.retry_panel.document["state"], "completed")
                self.assertEqual(window.last_output, self.folder / "日本語.ass")
                self.assertEqual(str(window.start_button.cget("state")), "normal")
            finally:
                window.destroy()


if __name__ == "__main__":
    unittest.main()
