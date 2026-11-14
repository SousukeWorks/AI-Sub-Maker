import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from image_video import export_image_video
from image_video_checks import create_mp3_picture, run_mp3_image_check, run_standalone_gui_check
from media import Cancelled


class ImageVideoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def test_real_mp3_to_subtitles_and_picture_video(self):
        self.assertTrue(run_mp3_image_check(self.folder)["mp3_to_soniox_ass"])

    def test_standalone_gui_converts_without_subtitle_settings_or_apis(self):
        self.assertTrue(run_standalone_gui_check(self.folder)["standalone_gui"])

    def test_dedicated_executable_opens_only_converter(self):
        import app
        with patch("app.sys.argv", ["Mp3ImageToMp4.exe"]), patch("app.sys.frozen", True, create=True), \
                patch("app.sys.executable", "F:/tools/Mp3ImageToMp4.exe"), \
                patch("app.App", side_effect=AssertionError("Standalone opened subtitle GUI")), \
                patch("image_video_app.ImageVideoApp") as converter:
            app.main()
            converter.assert_called_once_with("", "")
            converter.return_value.mainloop.assert_called_once()

    def test_main_gui_launches_converter_as_separate_process(self):
        import config
        from app import App
        from engine import Settings
        with patch.object(config, "load", return_value=(Settings(), False, "")), \
                patch.object(config, "local_soniox_key", return_value=""), patch("subprocess.Popen") as launch:
            window = App()
            try:
                window.update()
                window.image_tool_button.invoke()
                self.assertEqual(launch.call_count, 1)
                self.assertIn("--image-video", launch.call_args.args[0])
                self.assertFalse(window.busy)
            finally:
                window.destroy()

    def test_cancel_keeps_existing_output_and_cleans_temporary_file(self):
        source, image = create_mp3_picture(self.folder)
        target = self.folder / "existing.mp4"
        target.write_bytes(b"original")
        cancel = threading.Event()
        with self.assertRaises(Cancelled):
            export_image_video(source, image, target, cancel=cancel, progress=lambda _: cancel.set())
        self.assertEqual(target.read_bytes(), b"original")
        self.assertFalse(list(self.folder.glob(".picture-video-*")))

    def test_cannot_replace_source_and_missing_track_is_rejected(self):
        source, image = create_mp3_picture(self.folder)
        with self.assertRaises(ValueError):
            export_image_video(source, image, source, cancel=threading.Event())
        with self.assertRaisesRegex(ValueError, "音轨"):
            export_image_video(source, image, self.folder / "x.mp4", 3, cancel=threading.Event())



if __name__ == "__main__":
    unittest.main()
