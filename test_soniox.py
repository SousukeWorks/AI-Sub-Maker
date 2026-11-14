import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import config
from diagnostics import create_fixture
from engine import Settings
from media import Cancelled, extract_flac, pcm_blocks
from soniox_asr import ApiError, Client, api_url, parse_tokens, transcribe, valid_id
from soniox_checks import TOKENS, fake_soniox, run_soniox_check, run_soniox_gui_check
from subtitles import Cue


class SonioxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.source, self.target = self.folder / "source.mp4", self.folder / "out.ass"
        create_fixture(self.source)
        self.cancel = threading.Event()

    def execute(self, base, **kwargs):
        settings = Settings( soniox_url=base, soniox_key="offline-soniox-key", translate=False, **kwargs)
        return transcribe(self.source, self.target, settings, self.cancel, lambda _: None, lambda _: None)

    def state(self):
        return json.loads(self.target.with_suffix(".soniox.json").read_text(encoding="utf-8-sig"))

    def test_full_bilingual_and_retry_flow(self):
        self.assertTrue(run_soniox_check(self.folder)["native_timestamps"])

    def test_ordinary_gui_ignores_legacy_local_engine_and_calls_soniox(self):
        self.assertTrue(run_soniox_gui_check(self.folder)["button_to_soniox_http"])

    def test_missing_paid_key_stops_gui_before_any_request(self):
        from app import App
        with patch.object(config, 'load', return_value=(Settings(translate=False), False, '')), \
                patch.object(config, 'local_soniox_key', return_value=''), \
                patch('app.messagebox.showerror') as error, patch('requests.Session.request') as request:
            window = App()
            try:
                window.source.set(str(self.source))
                window.target.set(str(self.target))
                window.start_button.invoke()
                self.assertTrue(error.called)
                self.assertIn('Soniox', str(error.call_args))
                self.assertFalse(window.busy)
                request.assert_not_called()
            finally:
                window.destroy()

    def test_soniox_failure_has_no_local_fallback(self):
        from engine import run
        with fake_soniox({'create_status': 402}) as (base, calls), \
                patch.dict('sys.modules', {'faster_whisper': None, 'onnxruntime': None}):
            with self.assertRaisesRegex(ApiError, '余额'):
                run(self.source, self.target, Settings(soniox_url=base,
                    soniox_key='offline-soniox-key', translate=False))
            self.assertEqual(sum(method == 'POST' and route == '/v1/transcriptions' for method, route, _ in calls), 1)

    def test_flac_is_lossless_and_preserves_video_offset(self):
        import av
        import numpy as np
        flac = self.folder / "a.flac"
        duration = extract_flac(self.source, flac, 0, self.cancel, lambda _: None)
        with av.open(str(flac)) as container:
            actual = np.concatenate([frame.to_ndarray().flatten() for frame in container.decode(audio=0)])
        expected = np.frombuffer(b"".join(pcm_blocks(self.source, 0, self.cancel)), dtype=np.int16)
        self.assertAlmostEqual(duration, len(expected) / 16000)
        np.testing.assert_array_equal(actual, expected)
        self.assertTrue(np.all(actual[:14000] == 0))

    def test_native_clock_and_speaker_change_with_overlap(self):
        result = parse_tokens({"tokens": TOKENS}, 3)
        self.assertEqual(result, [Cue(1.1, 1.4, "こんにちは", speaker="1"), Cue(1.35, 1.75, "はい。", speaker="2")])

    def test_pause_and_zero_duration_punctuation(self):
        rows = [{"text": "おはよう", "start_ms": 100, "end_ms": 300},
                {"text": "。", "start_ms": 300, "end_ms": 300},
                {"text": "はい", "start_ms": 900, "end_ms": 1100}]
        self.assertEqual(parse_tokens({"tokens": rows}, 2), [Cue(.1, .3, "おはよう。"), Cue(.9, 1.1, "はい")])

    def test_translation_tokens_and_end_markers_are_not_subtitles(self):
        rows = TOKENS + [{"text": "译文", "translation_status": "translation"}, {"text": "<end>"}]
        self.assertEqual(len(parse_tokens({"tokens": rows}, 3)), 2)

    def test_invalid_clock_rejected_instead_of_fabricated(self):
        for value in (None, True, -1, float("nan"), 9000):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_tokens({"tokens": [{"text": "あ", "start_ms": value, "end_ms": 1000}]}, 3)
        with self.assertRaises(ValueError):
            parse_tokens({"tokens": [{"text": "あ", "start_ms": 200, "end_ms": 100}]}, 3)

    def test_zero_duration_word_is_not_dropped(self):
        rows = [{"text": "こん", "start_ms": 100, "end_ms": 300, "speaker": "1"},
                {"text": "にちは。", "start_ms": 300, "end_ms": 300, "speaker": "1"}]
        report = {}
        self.assertEqual(parse_tokens({"tokens": rows}, 2, report=report), [Cue(.1, .3, "こんにちは。", speaker="1")])
        self.assertEqual(report["zero_duration_text_tokens"], 1)
        self.assertEqual(report["review_cues"], 0)

    def test_point_only_speaker_turn_is_retained_and_flagged(self):
        rows = [{"text": "はい！", "start_ms": 100, "end_ms": 100, "speaker": "2"},
                {"text": "こんにちは。", "start_ms": 300, "end_ms": 700, "speaker": "1"}]
        report = {}
        cues = parse_tokens({"tokens": rows}, 2, report=report)
        self.assertEqual([c.text for c in cues], ["はい！", "こんにちは。"])
        self.assertEqual(cues[0].speaker, "2")
        self.assertEqual((cues[0].start, cues[0].end), (.1, .3))
        self.assertEqual(report["review_cues"], 1)
        self.assertEqual(report["review"][0]["native_point"], .1)

    def test_point_prefix_merges_only_with_nearby_same_speaker(self):
        rows = [{"text": "こん。", "start_ms": 100, "end_ms": 100, "speaker": "1"},
                {"text": "にちは。", "start_ms": 200, "end_ms": 500, "speaker": "1"}]
        report = {}
        self.assertEqual(parse_tokens({"tokens": rows}, 1, report=report), [Cue(.1, .5, "こん。にちは。", speaker="1")])
        self.assertEqual(report["review_cues"], 0)

    def test_point_only_at_end_of_audio_gets_visible_review_interval(self):
        report = {}
        cues = parse_tokens({"tokens": [{"text": "はい", "start_ms": 1000, "end_ms": 1000}]}, 1, report=report)
        self.assertGreater(cues[0].end, cues[0].start)
        self.assertEqual(cues[0].end, 1)
        self.assertEqual(report["review_cues"], 1)

    def test_import_completed_json_keeps_all_text_and_makes_retry_without_asr(self):
        from soniox_asr import import_result
        from subtitles import make_ass
        rows = TOKENS + [{"text": "あ", "start_ms": 2000, "end_ms": 2000, "speaker": "1"}]
        source = self.folder / "old.soniox.json"
        original = json.dumps({"schema": 1, "status": "completed", "source": str(self.source),
                               "duration": 3, "transcript": {"tokens": rows}}, ensure_ascii=False)
        source.write_text(original, encoding="utf-8")
        with patch("soniox_asr.Client.request", side_effect=AssertionError("Import called cloud")), \
                patch.dict("sys.modules", {"faster_whisper": None}):
            path, job = import_result(source, self.target, Settings(deepseek=True, translate=True))
        self.assertTrue(path.is_file())
        self.assertEqual("".join(row["text"] for row in job["cues"]), "こんにちははい。あ")
        self.assertEqual(source.read_text(encoding="utf-8"), original)
        self.assertTrue(self.target.with_suffix(".soniox-timing.json").is_file())
        self.assertEqual(job["timing_review_cues"], 1)

    def test_cached_zero_tokens_do_not_resubmit_paid_task(self):
        with fake_soniox() as (base, calls):
            self.execute(base)
            state = self.state()
            state["transcript"]["tokens"].append({"text": "あ", "start_ms": 2000, "end_ms": 2000, "speaker": "1"})
            self.target.with_suffix(".soniox.json").write_text(json.dumps(state), encoding="utf-8")
            before = len(calls)
            cues = self.execute(base)
            self.assertTrue(any(c.text == "あ" for c in cues))
            self.assertEqual(len(calls), before)

    def test_missing_key_and_insecure_remote_url_rejected(self):
        with self.assertRaisesRegex(ValueError, "Key"):
            Client(Settings())
        for base in ("http://remote.test/v1", "https://user:pass@remote.test", "https://remote.test?key=x"):
            with self.assertRaises(ValueError):
                api_url(base)
        self.assertEqual(api_url("https://api.soniox.com"), "https://api.soniox.com/v1")
        with self.assertRaises(RuntimeError):
            valid_id("../files/other-user")

    def test_balance_failure_is_not_automatically_retried(self):
        options = {"create_status": 402}
        with fake_soniox(options) as (base, calls):
            with self.assertRaisesRegex(ApiError, "余额"):
                self.execute(base)
            self.assertEqual(self.state()["status"], "uploaded")
            self.assertEqual(sum(route == "/v1/transcriptions" for _, route, _ in calls), 1)
            options["create_status"] = 201
            self.execute(base)
            self.assertEqual(sum(route == "/v1/files" for method, route, _ in calls if method == "POST"), 1)

    def test_unknown_submission_never_automatically_charges_again(self):
        with fake_soniox({"create_status": 503}) as (base, calls):
            with self.assertRaises(ApiError):
                self.execute(base)
            with self.assertRaisesRegex(RuntimeError, "避免重复收费"):
                self.execute(base)
            self.assertEqual(self.state()["status"], "create_unknown")
            self.assertEqual(sum(route == "/v1/transcriptions" for _, route, _ in calls), 1)

    def test_cancel_after_create_saves_id_and_resumes_original_task(self):
        with fake_soniox({"on_create": self.cancel.set}) as (base, calls):
            with self.assertRaises(Cancelled):
                self.execute(base)
            self.assertEqual(self.state()["job_id"], "job-test")
            self.cancel.clear()
            self.assertEqual(len(self.execute(base)), 2)
            self.assertEqual(sum(route == "/v1/transcriptions" for _, route, _ in calls), 1)

    def test_poll_failure_keeps_job_to_resume(self):
        options = {"poll_http": 503}
        with fake_soniox(options) as (base, calls):
            with self.assertRaises(ApiError):
                self.execute(base)
            options["poll_http"] = 200
            self.execute(base)
            self.assertEqual(sum(route == "/v1/transcriptions" for _, route, _ in calls), 1)

    def test_changed_input_cannot_reuse_existing_paid_record(self):
        with fake_soniox() as (base, calls):
            self.execute(base)
            with self.assertRaisesRegex(ValueError, "新的 ASS"):
                self.execute(base, audio_index=1)

    def test_cleanup_failure_does_not_discard_valid_transcript(self):
        with fake_soniox({"delete_status": 503}) as (base, calls):
            self.assertEqual(len(self.execute(base)), 2)
            self.assertEqual(self.state()["status"], "completed")

    def test_soniox_key_uses_dpapi_and_is_never_plaintext(self):
        key = "private-soniox-test-key"
        with patch("config.data_dir", return_value=self.folder):
            config.save(Settings(soniox_key=key), True)
            text = (self.folder / "settings.json").read_text(encoding="utf-8-sig")
            self.assertNotIn(key, text)
            self.assertEqual(config.load()[0].soniox_key, key)
            config.save(Settings(soniox_key=key), False)
            self.assertNotIn("soniox_key", (self.folder / "settings.json").read_text(encoding="utf-8-sig"))
            self.assertEqual(config.load()[0].soniox_key, "")

    def test_local_encrypted_bootstrap(self):
        path = self.folder / "soniox-key.dpapi"
        path.write_text(config.protect("bootstrap-test-key"), encoding="ascii")
        with patch.object(config, "__file__", str(self.folder / "config.py")), patch("config.sys.frozen", False, create=True):
            self.assertEqual(config.local_soniox_key(), "bootstrap-test-key")
            self.assertEqual(config.local_soniox_key(consume=True), "bootstrap-test-key")
            self.assertFalse(path.exists())

    def test_failed_remote_job_is_saved_and_cleaned_without_resubmitting(self):
        with fake_soniox({"status": "error"}) as (base, calls):
            with self.assertRaisesRegex(RuntimeError, "识别失败"):
                self.execute(base)
            self.assertEqual(self.state()["status"], "error")
            self.assertTrue(self.state()["job_id_deleted"])
            self.assertTrue(self.state()["file_id_deleted"])
            with self.assertRaisesRegex(RuntimeError, "任务失败"):
                self.execute(base)
            self.assertEqual(sum(route == "/v1/transcriptions" for _, route, _ in calls), 1)


if __name__ == "__main__":
    unittest.main()
