"""Real MP3 decode, Soniox protocol and still-image MP4 export verification."""
import math
import threading
from fractions import Fraction


def create_mp3_picture(folder):
    import av
    import numpy as np
    source, image = folder / "音声.mp3", folder / "画面.png"
    rate, total = 44100, round(44100 * 3.37)
    with av.open(str(source), "w", format="mp3") as output:
        stream = output.add_stream("libmp3lame", rate=rate)
        stream.layout, stream.bit_rate = "stereo", 192000
        for start in range(0, total, 1152):
            count = min(1152, total - start)
            clock = np.arange(start, start + count) / rate
            samples = np.stack([np.sin(clock * 2 * np.pi * 440), np.sin(clock * 2 * np.pi * 660)]).astype(np.float32) * .2
            frame = av.AudioFrame.from_ndarray(samples, format="fltp", layout="stereo")
            frame.sample_rate, frame.pts, frame.time_base = rate, start, Fraction(1, rate)
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode(None):
            output.mux(packet)
    with av.open(str(image), "w", format="image2") as output:
        stream = output.add_stream("png", rate=1)
        stream.width, stream.height, stream.pix_fmt = 320, 160, "rgb24"
        frame = av.VideoFrame.from_ndarray(np.full((160, 320, 3), (200, 40, 60), dtype=np.uint8), format="rgb24")
        for packet in stream.encode(frame):
            output.mux(packet)
        for packet in stream.encode(None):
            output.mux(packet)
    return source, image


def run_mp3_image_check(folder):
    import av
    import numpy as np
    from engine import Settings, run
    from image_video import export_image_video
    from soniox_checks import fake_soniox
    from unittest.mock import patch

    source, image = create_mp3_picture(folder)
    target = folder / "静态画面.mp4"
    duration = export_image_video(source, image, target, cancel=threading.Event())
    assert abs(duration - 3.37) < .01, duration
    with av.open(str(target)) as container:
        assert container.streams.video[0].codec_context.name == "h264"
        assert container.streams.audio[0].codec_context.name == "aac"
        assert container.streams.audio[0].codec_context.sample_rate == 44100
        assert len(container.streams.audio[0].codec_context.layout.channels) == 2
        assert abs(container.duration / av.time_base - duration) < .05, container.duration
        frames = list(container.decode(video=0))
        assert len(frames) == math.ceil(duration)
        for frame in frames:
            assert (frame.width, frame.height) == (1920, 1080)
            pixels = frame.to_ndarray(format="rgb24")
            assert np.max(pixels[20, 960]) < 10, "Image aspect ratio was stretched"
            assert np.max(np.abs(pixels[540, 960].astype(int) - (200, 40, 60))) < 12
    with av.open(str(target)) as container:
        audio = list(container.decode(audio=0))
        assert audio and max(float(np.max(np.abs(frame.to_ndarray()))) for frame in audio) > .1
    with fake_soniox() as (base, calls):
        count = run(source, folder / "mp3.ass", Settings( soniox_url=base, soniox_key="offline-soniox-key", translate=False))
        assert count == 2
    return {"mp3_to_soniox_ass": True, "picture_mp4": True, "codecs": ["h264", "aac"],
            "resolution": [1920, 1080], "audio_channels": 2, "sample_rate": 44100,
            "duration": duration, "aspect_ratio_preserved": True}


def run_standalone_gui_check(folder):
    # Prior GUI fixtures can leave cyclic Tk objects awaiting collection.
    # Collect them on the GUI thread before a codec worker allocates objects;
    # Tk variables cannot be finalized from a background thread.
    import gc
    gc.collect()
    import time
    from unittest.mock import patch
    from image_video_app import ImageVideoApp
    source, picture = create_mp3_picture(folder)
    target = folder / "independent.mp4"
    with patch("config.load", side_effect=AssertionError("Converter read subtitle settings")), \
            patch("engine.run", side_effect=AssertionError("Converter called ASR")), \
            patch("requests.request", side_effect=AssertionError("Converter called API")), \
            patch("image_video_app.messagebox.showinfo") as done, \
            patch("image_video_app.messagebox.showerror") as error:
        window = ImageVideoApp()
        try:
            window.update()
            window.withdraw()
            window.audio.set(str(source))
            window.picture.set(str(picture))
            window.target.set(str(target))
            window.start_button.invoke()
            deadline = time.monotonic() + 15
            while window.busy and time.monotonic() < deadline:
                window.update()
                time.sleep(.01)
            assert not window.busy and done.call_count == 1 and not error.called, error.call_args
            assert target.is_file() and window.last_output == target
            return {"standalone_gui": True, "needs_subtitle_window": False,
                    "reads_subtitle_settings": False, "api_calls": 0, "output": "mp4"}
        finally:
            window.destroy()
